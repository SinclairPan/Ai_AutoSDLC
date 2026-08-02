"""不可变 Prospective Shadow 对照结果及其本地存储。"""

from __future__ import annotations

from collections.abc import Callable
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
    portable_content_digest_name,
    read_json_object,
    resolve_canonical_shared_state,
)
from ai_sdlc.core.stage_review.optimization.accounting import (
    OfflineOptimizationAccounting,
)
from ai_sdlc.core.stage_review.optimization.controller_models import (
    OptimizationEpoch,
)
from ai_sdlc.core.stage_review.optimization.controller_store import (
    OptimizationControllerStore,
)
from ai_sdlc.core.stage_review.optimization.finding_lineage import (
    FindingEventLineageReader,
)
from ai_sdlc.core.stage_review.optimization.models import (
    OptimizationCandidate,
)
from ai_sdlc.core.stage_review.optimization.observations import (
    OptimizationObservationStore,
)
from ai_sdlc.core.stage_review.optimization.pipeline_effects import (
    EpochRuntimeAuthorizer,
)
from ai_sdlc.core.stage_review.optimization.shadow import (
    OptimizationShadowAssignment,
    OptimizationShadowAssignmentStore,
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


class PublishedShadowObservation(ArtifactCompatibility):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["published-shadow-observation.v1"] = (
        "published-shadow-observation.v1"
    )
    artifact_kind: Literal["published-shadow-observation"] = (
        "published-shadow-observation"
    )
    observation: OptimizationShadowObservation
    runtime_bundle_manifest_digest: str
    provider_request_artifact_digest: str
    provider_journal_last_event_digest: str
    epoch_fencing_epoch: int
    epoch_claim_digest: str
    publication_digest: str = ""

    @model_validator(mode="after")
    def _verify_publication(self) -> Self:
        lineage = (
            self.runtime_bundle_manifest_digest,
            self.provider_request_artifact_digest,
            self.provider_journal_last_event_digest,
            self.epoch_claim_digest,
        )
        if self.epoch_fencing_epoch < 1 or any(
            not item.strip() for item in lineage
        ):
            raise ValueError("shadow publication epoch authority is incomplete")
        return fill_artifact_digest(self, "publication_digest")


class OptimizationShadowObservationStore:
    def __init__(
        self,
        root: Path,
        *,
        project_id: str,
        journal: ProviderInvocationJournal | None = None,
    ) -> None:
        self.project_id = require_machine_id(project_id, "project_id")
        self.worktree_root = root.resolve()
        shared = resolve_canonical_shared_state(root, self.project_id)
        bind_repository_project(shared, self.project_id)
        self.root = shared / "offline-optimization" / "shadow-observations"
        self._accounting = OfflineOptimizationAccounting(
            root,
            project_id=self.project_id,
        )
        self._journal = journal
        self._controller = OptimizationControllerStore(
            root,
            project_id=self.project_id,
            lock_timeout_seconds=5,
        )
        self._assignments = OptimizationShadowAssignmentStore(
            root,
            project_id=self.project_id,
        )
        self._observations = OptimizationObservationStore(
            root,
            project_id=self.project_id,
        )
        self._findings = FindingEventLineageReader(
            root,
            project_id=self.project_id,
        )

    def read_assignment(
        self, assignment_id: str
    ) -> OptimizationShadowObservation | None:
        stable = require_machine_id(assignment_id, "assignment_id")
        directory = self.root / stable
        if not directory.is_dir():
            return None
        trusted = tuple(
            publication
            for path in sorted(directory.glob("*.json"))
            for publication in (
                PublishedShadowObservation.model_validate(
                    read_json_object(path)
                ),
            )
            if self._publication_has_trusted_receipt(publication)
        )
        if len(trusted) > 1:
            raise SharedStateIntegrityError(
                "multiple trusted shadow publications detected"
            )
        return trusted[0].observation if trusted else None

    def publisher(
        self,
        *,
        epoch: OptimizationEpoch,
        journal: ProviderInvocationJournal,
        authorize_effect: Callable[[], None],
    ) -> ShadowEvidencePublisher:
        return ShadowEvidencePublisher(
            store=self,
            epoch=epoch,
            journal=journal,
            authorize_effect=authorize_effect,
        )

    def _canonical_publication(
        self,
        *,
        assignment: OptimizationShadowAssignment,
        candidate: OptimizationCandidate,
        epoch: OptimizationEpoch,
        journal: ProviderInvocationJournal,
        provider_invocation_id: str,
        observed_at: str,
        epoch_fencing_epoch: int,
        epoch_claim_digest: str,
    ) -> PublishedShadowObservation:
        from ai_sdlc.core.stage_review.optimization.shadow_labels import (
            labeled_shadow_outcomes,
        )
        from ai_sdlc.core.stage_review.optimization.shadow_provider import (
            OptimizationShadowProviderOutput,
        )

        trusted_assignment = OptimizationShadowAssignment.model_validate(
            assignment.model_dump(mode="json")
        )
        trusted_candidate = OptimizationCandidate.model_validate(
            candidate.model_dump(mode="json")
        )
        persisted_assignment = self._assignments.read_session(
            trusted_assignment.session_id
        )
        if persisted_assignment != trusted_assignment:
            raise SharedStateIntegrityError(
                "shadow publication assignment is not committed"
            )
        invocation = journal.get(provider_invocation_id)
        submission = journal.get_submission(provider_invocation_id)
        if invocation is None or submission is None or invocation.state != "committed":
            raise SharedStateIntegrityError(
                "shadow publication provider invocation is not committed"
            )
        _verify_invocation_lineage(trusted_assignment, invocation)
        baseline_matches = tuple(
            item
            for item in self._observations.read_session(
                trusted_assignment.session_id
            )
            if item.observation_digest
            == trusted_assignment.active_baseline_result_digest
        )
        if len(baseline_matches) != 1:
            raise SharedStateIntegrityError(
                "shadow baseline result is unavailable"
            )
        output = OptimizationShadowProviderOutput.model_validate(
            submission.output_payload
        )
        outcomes = labeled_shadow_outcomes(
            candidate=trusted_candidate,
            baseline_observation=baseline_matches[0],
            review=output.review,
            finding_events=self._findings.events(
                trusted_assignment.session_id
            ),
        )
        observation = _build_shadow_observation(
            trusted_assignment,
            baseline=outcomes[0],
            challenger=outcomes[1],
            evaluation_binding_id=(
                f"evaluation-binding.{trusted_assignment.assignment_id}"
            ),
            evaluation_provider_id=invocation.request.provider_id,
            provider_invocation_id=invocation.invocation_id,
            provider_submission_digest=submission.submission_digest,
            accounted_usage=submission.accounted_usage,
            validation_digest=invocation.validation_digest,
            resource_settlement_event_digest=(
                invocation.resource_settlement_event_digest
            ),
            label_source_digests=outcomes[2],
            observed_at=observed_at,
        )
        request = invocation.request
        lineage = (
            observation.project_id == self.project_id == epoch.project_id,
            observation.epoch_id == request.epoch_id == epoch.epoch_id,
            observation.finalist_candidate_digest
            == request.candidate_digest
            == epoch.finalist_candidate_digest,
            observation.assignment_digest == request.assignment_digest,
            request.authorization_scope == "optimization_shadow",
            trusted_candidate.candidate_digest
            == epoch.finalist_candidate_digest,
            request.runtime_bundle_manifest_digest
            == epoch.runtime_bundle_manifest_digest,
            request.request_artifact_digest == submission.request_artifact_digest,
            observation.provider_submission_digest
            == submission.submission_digest
            == invocation.submission_digest,
            observation.validation_digest == invocation.validation_digest,
            observation.resource_settlement_event_digest
            == invocation.resource_settlement_event_digest,
        )
        if not all(lineage):
            raise SharedStateIntegrityError(
                "shadow publication lineage diverged"
            )
        return PublishedShadowObservation(
            observation=observation,
            runtime_bundle_manifest_digest=(
                request.runtime_bundle_manifest_digest
            ),
            provider_request_artifact_digest=request.request_artifact_digest,
            provider_journal_last_event_digest=invocation.last_event_digest,
            epoch_fencing_epoch=epoch_fencing_epoch,
            epoch_claim_digest=epoch_claim_digest,
        )

    def _persist_authorized(
        self,
        publication: PublishedShadowObservation,
    ) -> OptimizationShadowObservation:
        observation = publication.observation
        claim_name = portable_content_digest_name(
            publication.epoch_claim_digest
        )
        path = (
            self.root
            / observation.assignment_id
            / f"{claim_name}.json"
        )
        if self._accounting.persist_json_exclusive(
            path,
            publication.model_dump(mode="json"),
        ):
            return observation
        existing = PublishedShadowObservation.model_validate(
            read_json_object(path)
        )
        if existing != publication:
            raise SharedStateIntegrityError(
                "shadow observation identity already has other content"
            )
        return existing.observation

    def _publication_has_trusted_receipt(
        self,
        publication: PublishedShadowObservation,
    ) -> bool:
        observation = publication.observation
        if observation.project_id != self.project_id:
            raise SharedStateIntegrityError(
                "shadow observation project identity diverged"
            )
        receipt_id = stable_id(
            "optimization-epoch-effect-receipt",
            "shadow_observation",
            publication.publication_digest,
            publication.epoch_claim_digest,
        )
        receipt = self._controller.effect_receipt(receipt_id)
        if receipt is None or self._journal is None:
            return False
        self._controller.verify_effect_receipt(receipt)
        if not all(
            (
                receipt.effect_kind == "shadow_observation",
                receipt.effect_digest == publication.publication_digest,
                receipt.epoch_id == observation.epoch_id,
                receipt.runtime_bundle_manifest_digest
                == publication.runtime_bundle_manifest_digest,
                receipt.epoch_fencing_epoch
                == publication.epoch_fencing_epoch,
                receipt.epoch_claim_digest == publication.epoch_claim_digest,
                receipt.provider_journal_last_event_digest
                == publication.provider_journal_last_event_digest,
            )
        ):
            raise SharedStateIntegrityError(
                "shadow publication receipt lineage diverged"
            )
        self._verify_committed_publication(publication)
        return True

    def _verify_committed_publication(
        self,
        publication: PublishedShadowObservation,
    ) -> None:
        journal = self._journal
        if journal is None:
            raise SharedStateIntegrityError(
                "shadow publication journal verifier is unavailable"
            )
        observation = publication.observation
        invocation = journal.get(observation.provider_invocation_id)
        submission = journal.get_submission(observation.provider_invocation_id)
        if invocation is None or submission is None or invocation.state != "committed":
            raise SharedStateIntegrityError(
                "shadow publication provider invocation is not committed"
            )
        if not all(
            (
                invocation.last_event_digest
                == publication.provider_journal_last_event_digest,
                invocation.request.request_artifact_digest
                == publication.provider_request_artifact_digest,
                invocation.request.runtime_bundle_manifest_digest
                == publication.runtime_bundle_manifest_digest,
                invocation.request.assignment_digest
                == observation.assignment_digest,
                invocation.submission_digest
                == submission.submission_digest
                == observation.provider_submission_digest,
                invocation.validation_digest == observation.validation_digest,
                invocation.resource_settlement_event_digest
                == observation.resource_settlement_event_digest,
            )
        ):
            raise SharedStateIntegrityError(
                "shadow publication journal lineage diverged"
            )


class ShadowEvidencePublisher:
    """把 Shadow 证据发布绑定到单一 epoch/runtime commit capability。"""

    def __init__(
        self,
        *,
        store: OptimizationShadowObservationStore,
        epoch: OptimizationEpoch,
        journal: ProviderInvocationJournal,
        authorize_effect: Callable[[], None],
    ) -> None:
        if not isinstance(authorize_effect, EpochRuntimeAuthorizer):
            raise TypeError(
                "shadow publisher requires EpochRuntimeAuthorizer"
            )
        fencing = getattr(authorize_effect, "epoch_fencing_epoch", 0)
        claim = str(getattr(authorize_effect, "epoch_claim_digest", ""))
        if not isinstance(fencing, int) or fencing < 1 or not claim:
            raise TypeError("shadow publisher requires epoch commit authority")
        if not callable(getattr(authorize_effect, "commit", None)):
            raise TypeError("shadow publisher requires atomic commit authority")
        self._store = store
        self._epoch = OptimizationEpoch.model_validate(
            epoch.model_dump(mode="json")
        )
        self._journal = journal
        self._authorize_effect = authorize_effect
        self._epoch_fencing_epoch = fencing
        self._epoch_claim_digest = claim
        self._observed_at = authorize_effect.shadow_observed_at(self._epoch)

    def publish(
        self,
        *,
        assignment: OptimizationShadowAssignment,
        candidate: OptimizationCandidate,
        provider_invocation_id: str,
    ) -> OptimizationShadowObservation:
        publication = self._store._canonical_publication(
            assignment=assignment,
            candidate=candidate,
            epoch=self._epoch,
            journal=self._journal,
            provider_invocation_id=provider_invocation_id,
            observed_at=self._observed_at,
            epoch_fencing_epoch=self._epoch_fencing_epoch,
            epoch_claim_digest=self._epoch_claim_digest,
        )
        result, receipt = self._authorize_effect.commit_shadow_observation(
            epoch=self._epoch,
            publication_digest=publication.publication_digest,
            provider_journal_last_event_digest=(
                publication.provider_journal_last_event_digest
            ),
            operation=lambda: self._store._persist_authorized(publication),
        )
        if receipt.effect_digest != publication.publication_digest:
            raise SharedStateIntegrityError(
                "shadow publication receipt diverged"
            )
        return result


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
