"""不可变 Prospective Shadow 对照结果及其本地存储。"""

from __future__ import annotations

from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from ai_sdlc.core.stage_review.artifact_compat import (
    ArtifactCompatibility,
    fill_artifact_digest,
)
from ai_sdlc.core.stage_review.artifacts import (
    SharedStateIntegrityError,
    bind_repository_project,
    create_json_exclusive,
    read_json_object,
    resolve_canonical_shared_state,
)
from ai_sdlc.core.stage_review.optimization.shadow import (
    OptimizationShadowAssignment,
)
from ai_sdlc.core.stage_review.provider_journal import ProviderInvocationJournal
from ai_sdlc.core.stage_review.provider_journal_models import ProviderInvocation
from ai_sdlc.core.stage_review.provider_usage_models import AccountedProviderUsage
from ai_sdlc.core.stage_review.registry_versions import require_machine_id
from ai_sdlc.core.stage_review.resource_builders import parse_utc, stable_id

ShadowTerminalOutcome = Literal[
    "consumed",
    "needs_user",
    "blocked",
    "timed_out",
    "abandoned",
    "hard_budget_exhausted",
    "unknown_or_censored",
]


class ShadowOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    critical_detected: bool = False
    late_critical: bool = False
    reviewer_coverage_leak: bool = False
    false_positive: bool = False
    reversal: bool = False
    stage_reopened: bool = False
    unconfirmed_finding: bool = False
    terminal_outcome: ShadowTerminalOutcome


class OptimizationShadowObservation(ArtifactCompatibility):
    schema_version: Literal["optimization-shadow-observation.v1"] = (
        "optimization-shadow-observation.v1"
    )
    artifact_kind: Literal["optimization-shadow-observation"] = (
        "optimization-shadow-observation"
    )
    observation_id: str
    project_id: str
    epoch_id: str
    finalist_candidate_digest: str
    assignment_id: str
    assignment_digest: str
    session_id: str
    active_baseline_result_digest: str
    baseline: ShadowOutcome
    challenger: ShadowOutcome
    evaluation_binding_id: str
    evaluation_provider_id: str
    provider_invocation_id: str
    provider_submission_digest: str
    accounted_usage: AccountedProviderUsage
    usage_estimation_policy_version: str
    usage_estimation_policy_digest: str
    validation_digest: str
    resource_settlement_event_digest: str
    label_source_digests: tuple[str, ...]
    observed_at: str
    observation_digest: str = ""

    @field_validator(
        "observation_id",
        "project_id",
        "epoch_id",
        "assignment_id",
        "session_id",
        "evaluation_binding_id",
        "evaluation_provider_id",
        "provider_invocation_id",
    )
    @classmethod
    def _identity_is_stable(cls, value: str) -> str:
        return require_machine_id(value, "shadow observation identity")

    @field_validator("label_source_digests")
    @classmethod
    def _labels_are_complete(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value:
            raise ValueError("shadow observation label source is required")
        if value != tuple(sorted(set(value))) or any(not item.strip() for item in value):
            raise ValueError("shadow observation label sources must be canonical")
        return value

    @field_validator("observed_at")
    @classmethod
    def _time_is_utc(cls, value: str) -> str:
        parse_utc(value)
        return value

    @model_validator(mode="after")
    def _verify_lineage(self) -> Self:
        lineage = (
            self.finalist_candidate_digest,
            self.assignment_digest,
            self.active_baseline_result_digest,
            self.provider_submission_digest,
            self.validation_digest,
            self.resource_settlement_event_digest,
            self.usage_estimation_policy_version,
            self.usage_estimation_policy_digest,
        )
        if any(not item.strip() for item in lineage):
            raise ValueError("shadow observation lineage is incomplete")
        basis = self.accounted_usage.basis
        if basis.token_source == "estimated" and (
            basis.estimation_policy_version != self.usage_estimation_policy_version
            or basis.estimation_policy_digest != self.usage_estimation_policy_digest
        ):
            raise ValueError("shadow observation usage policy lineage diverged")
        return fill_artifact_digest(self, "observation_digest")


class OptimizationShadowObservationStore:
    def __init__(self, root: Path, *, project_id: str) -> None:
        self.project_id = require_machine_id(project_id, "project_id")
        shared = resolve_canonical_shared_state(root, self.project_id)
        bind_repository_project(shared, self.project_id)
        self.root = shared / "offline-optimization" / "shadow-observations"

    def append(
        self, observation: OptimizationShadowObservation
    ) -> OptimizationShadowObservation:
        trusted = OptimizationShadowObservation.model_validate(
            observation.model_dump(mode="json")
        )
        if trusted.project_id != self.project_id:
            raise SharedStateIntegrityError(
                "shadow observation project identity diverged"
            )
        path = self.root / f"{trusted.assignment_id}.json"
        if create_json_exclusive(path, trusted.model_dump(mode="json")):
            return trusted
        existing = OptimizationShadowObservation.model_validate(
            read_json_object(path)
        )
        if existing != trusted:
            raise SharedStateIntegrityError(
                "shadow observation identity already has other content"
            )
        return existing

    def read_assignment(
        self, assignment_id: str
    ) -> OptimizationShadowObservation | None:
        stable = require_machine_id(assignment_id, "assignment_id")
        path = self.root / f"{stable}.json"
        if not path.is_file():
            return None
        return OptimizationShadowObservation.model_validate(read_json_object(path))

    def record_committed(
        self,
        assignment: OptimizationShadowAssignment,
        *,
        journal: ProviderInvocationJournal,
        provider_invocation_id: str,
        baseline: ShadowOutcome,
        challenger: ShadowOutcome,
        evaluation_binding_id: str,
        label_source_digests: tuple[str, ...],
        observed_at: str,
    ) -> OptimizationShadowObservation:
        invocation = journal.get(provider_invocation_id)
        submission = journal.get_submission(provider_invocation_id)
        if invocation is None or submission is None or invocation.state != "committed":
            raise SharedStateIntegrityError(
                "shadow observation provider invocation is not committed"
            )
        _verify_invocation_lineage(assignment, invocation)
        return self.append(
            _build_shadow_observation(
                assignment,
                baseline=baseline,
                challenger=challenger,
                evaluation_binding_id=evaluation_binding_id,
                evaluation_provider_id=invocation.request.provider_id,
                provider_invocation_id=invocation.invocation_id,
                provider_submission_digest=submission.submission_digest,
                accounted_usage=submission.accounted_usage,
                validation_digest=invocation.validation_digest,
                resource_settlement_event_digest=(
                    invocation.resource_settlement_event_digest
                ),
                label_source_digests=label_source_digests,
                observed_at=observed_at,
            )
        )


def _build_shadow_observation(
    assignment: OptimizationShadowAssignment,
    *,
    baseline: ShadowOutcome,
    challenger: ShadowOutcome,
    evaluation_binding_id: str,
    evaluation_provider_id: str,
    provider_invocation_id: str,
    provider_submission_digest: str,
    accounted_usage: AccountedProviderUsage,
    validation_digest: str,
    resource_settlement_event_digest: str,
    label_source_digests: tuple[str, ...],
    observed_at: str,
) -> OptimizationShadowObservation:
    return OptimizationShadowObservation(
        observation_id=stable_id(
            "optimization-shadow-observation", assignment.assignment_id
        ),
        project_id=assignment.project_id,
        epoch_id=assignment.epoch_id,
        finalist_candidate_digest=assignment.finalist_candidate_digest,
        assignment_id=assignment.assignment_id,
        assignment_digest=assignment.assignment_digest,
        session_id=assignment.session_id,
        active_baseline_result_digest=assignment.active_baseline_result_digest,
        baseline=baseline,
        challenger=challenger,
        evaluation_binding_id=evaluation_binding_id,
        evaluation_provider_id=evaluation_provider_id,
        provider_invocation_id=provider_invocation_id,
        provider_submission_digest=provider_submission_digest,
        accounted_usage=accounted_usage,
        usage_estimation_policy_version=(
            assignment.usage_estimation_policy_version
        ),
        usage_estimation_policy_digest=assignment.usage_estimation_policy_digest,
        validation_digest=validation_digest,
        resource_settlement_event_digest=resource_settlement_event_digest,
        label_source_digests=tuple(sorted(set(label_source_digests))),
        observed_at=observed_at,
    )


def _verify_invocation_lineage(
    assignment: OptimizationShadowAssignment,
    invocation: ProviderInvocation,
) -> None:
    request = invocation.request
    expected = (
        request.project_id == assignment.project_id,
        request.epoch_id == assignment.epoch_id,
        request.candidate_digest == assignment.finalist_candidate_digest,
        request.assignment_digest == assignment.assignment_digest,
        bool(invocation.validation_digest),
        bool(invocation.resource_settlement_event_digest),
    )
    if not all(expected):
        raise SharedStateIntegrityError(
            "shadow observation provider lineage diverged"
        )
