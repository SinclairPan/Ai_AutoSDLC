"""Session 创建/终态观测与 Snapshot Binding 的不可变输入事实。"""

from __future__ import annotations

from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

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
from ai_sdlc.core.stage_review.registry_versions import require_machine_id
from ai_sdlc.core.stage_review.resource_builders import parse_utc, stable_id
from ai_sdlc.core.stage_review.resource_models import ResourceAmounts

ObservationKind = Literal[
    "created",
    "consumed",
    "blocked",
    "superseded",
    "hard_budget_exhausted",
    "needs_user",
    "crashed",
    "timed_out",
    "abandoned",
    "integrity_failure",
    "open_censored",
]
TERMINAL_OBSERVATION_KINDS: frozenset[ObservationKind] = frozenset(
    {
        "consumed",
        "blocked",
        "superseded",
        "hard_budget_exhausted",
        "needs_user",
        "crashed",
        "timed_out",
        "abandoned",
        "integrity_failure",
        "open_censored",
    }
)


class TerminalObservationLineage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    binding_set_digest: str = ""
    risk_profile_digest: str = ""
    cohort_id: str = ""
    finding_ledger_digest: str = ""
    convergence_outcome_digest: str = ""
    label_source_digests: tuple[str, ...] = ()
    resource_usage: ResourceAmounts = Field(default_factory=ResourceAmounts)


class OptimizationSessionObservation(ArtifactCompatibility):
    schema_version: Literal["optimization-session-observation.v1"] = (
        "optimization-session-observation.v1"
    )
    artifact_kind: Literal["optimization-session-observation"] = (
        "optimization-session-observation"
    )
    observation_id: str
    project_id: str
    session_id: str
    initial_candidate_digest: str
    sequence: int = Field(ge=1)
    observation_kind: ObservationKind
    occurred_at: str
    stage_key: str
    risk_level: str
    candidate_size_bucket: str
    provider_ids: tuple[str, ...] = ()
    active_snapshot_digest: str
    terminal_reason: str = ""
    finding_event_digests: tuple[str, ...] = ()
    binding_set_digest: str = ""
    risk_profile_digest: str = ""
    cohort_id: str = ""
    finding_ledger_digest: str = ""
    convergence_outcome_digest: str = ""
    label_source_digests: tuple[str, ...] = ()
    resource_usage: ResourceAmounts = Field(default_factory=ResourceAmounts)
    observation_digest: str = ""

    @field_validator("observation_id", "project_id", "session_id")
    @classmethod
    def _identity_is_stable(cls, value: str) -> str:
        return require_machine_id(value, "optimization observation identity")

    @field_validator("occurred_at")
    @classmethod
    def _time_is_utc(cls, value: str) -> str:
        parse_utc(value)
        return value

    @field_validator("provider_ids", "finding_event_digests", "label_source_digests")
    @classmethod
    def _sets_are_canonical(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if value != tuple(sorted(set(value))):
            raise ValueError("optimization observation set must be sorted and unique")
        return value

    @model_validator(mode="after")
    def _verify_digest(self) -> Self:
        return fill_artifact_digest(self, "observation_digest")


class CommittedSessionBinding(ArtifactCompatibility):
    schema_version: Literal["committed-session-binding.v1"] = (
        "committed-session-binding.v1"
    )
    artifact_kind: Literal["committed-session-binding"] = "committed-session-binding"
    project_id: str
    session_id: str
    initial_candidate_digest: str
    stage_key: str
    risk_level: str
    candidate_size_bucket: str
    provider_ids: tuple[str, ...] = ()
    binding_set_digest: str = ""
    role_profile_ids: tuple[str, ...] = ()
    reviewer_slot_ids: tuple[str, ...] = ()
    capability_ids: tuple[str, ...] = ()
    binding_digests: tuple[str, ...] = ()
    resource_reservation_digest: str = ""
    active_snapshot_digest: str
    control_sequence: int = Field(ge=1)
    control_event_digest: str
    committed_at: str
    schema_compatible: bool = True
    lineage_complete: bool = True
    binding_digest: str = ""

    @field_validator("project_id", "session_id")
    @classmethod
    def _identity_is_stable(cls, value: str) -> str:
        return require_machine_id(value, "session binding identity")

    @field_validator("committed_at")
    @classmethod
    def _time_is_utc(cls, value: str) -> str:
        parse_utc(value)
        return value

    @field_validator(
        "provider_ids",
        "role_profile_ids",
        "reviewer_slot_ids",
        "capability_ids",
        "binding_digests",
    )
    @classmethod
    def _providers_are_canonical(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if value != tuple(sorted(set(value))):
            raise ValueError("session binding providers must be sorted and unique")
        return value

    @model_validator(mode="after")
    def _verify_digest(self) -> Self:
        return fill_artifact_digest(self, "binding_digest")


class OptimizationObservationStore:
    def __init__(self, root: Path, *, project_id: str) -> None:
        self.project_id = require_machine_id(project_id, "project_id")
        self.shared_root = resolve_canonical_shared_state(root, self.project_id)
        bind_repository_project(self.shared_root, self.project_id)
        self.observation_root = (
            self.shared_root / "offline-optimization" / "session-observations"
        )

    def append(
        self,
        observation: OptimizationSessionObservation,
    ) -> OptimizationSessionObservation:
        trusted = OptimizationSessionObservation.model_validate(
            observation.model_dump(mode="json")
        )
        if trusted.project_id != self.project_id:
            raise SharedStateIntegrityError("observation project identity diverged")
        path = self._path(trusted)
        payload = trusted.model_dump(mode="json")
        if create_json_exclusive(path, payload):
            return trusted
        existing = OptimizationSessionObservation.model_validate(read_json_object(path))
        if existing != trusted:
            raise SharedStateIntegrityError(
                "observation identity already has other content"
            )
        return existing

    def read_session(
        self, session_id: str
    ) -> tuple[OptimizationSessionObservation, ...]:
        stable = require_machine_id(session_id, "session_id")
        directory = self.observation_root / stable
        if not directory.is_dir():
            return ()
        return tuple(
            OptimizationSessionObservation.model_validate(read_json_object(path))
            for path in sorted(directory.glob("*.json"))
        )

    def read_all(self) -> tuple[OptimizationSessionObservation, ...]:
        if not self.observation_root.is_dir():
            return ()
        values = (
            OptimizationSessionObservation.model_validate(read_json_object(path))
            for path in self.observation_root.glob("*/*.json")
        )
        return tuple(
            sorted(values, key=lambda item: (item.sequence, item.observation_id))
        )

    def _path(self, value: OptimizationSessionObservation) -> Path:
        name = f"{value.sequence:020d}-{value.observation_id}.json"
        return self.observation_root / value.session_id / name


class CommittedSessionBindingStore:
    def __init__(self, root: Path, *, project_id: str) -> None:
        self.project_id = require_machine_id(project_id, "project_id")
        shared_root = resolve_canonical_shared_state(root, self.project_id)
        bind_repository_project(shared_root, self.project_id)
        self.root = shared_root / "offline-optimization" / "session-bindings"

    def append(self, binding: CommittedSessionBinding) -> CommittedSessionBinding:
        trusted = CommittedSessionBinding.model_validate(
            binding.model_dump(mode="json")
        )
        if trusted.project_id != self.project_id:
            raise SharedStateIntegrityError("binding project identity diverged")
        path = self.root / f"{trusted.session_id}.json"
        if create_json_exclusive(path, trusted.model_dump(mode="json")):
            return trusted
        existing = CommittedSessionBinding.model_validate(read_json_object(path))
        if existing != trusted:
            raise SharedStateIntegrityError("committed session binding diverged")
        return existing

    def read_all(self) -> tuple[CommittedSessionBinding, ...]:
        if not self.root.is_dir():
            return ()
        values = (
            CommittedSessionBinding.model_validate(read_json_object(path))
            for path in self.root.glob("*.json")
        )
        return tuple(sorted(values, key=lambda item: item.control_sequence))


def _build_terminal_observation(
    binding: CommittedSessionBinding,
    observation_kind: ObservationKind,
    *,
    sequence: int,
    occurred_at: str,
    terminal_reason: str,
    finding_event_digests: tuple[str, ...] = (),
    lineage: TerminalObservationLineage | None = None,
) -> OptimizationSessionObservation:
    if observation_kind not in TERMINAL_OBSERVATION_KINDS:
        raise ValueError("terminal observation kind is required")
    resolved = lineage or TerminalObservationLineage()
    return OptimizationSessionObservation(
        observation_id=stable_id(
            "session-terminal-observation",
            binding.session_id,
            observation_kind,
        ),
        project_id=binding.project_id,
        session_id=binding.session_id,
        initial_candidate_digest=binding.initial_candidate_digest,
        sequence=sequence,
        observation_kind=observation_kind,
        occurred_at=occurred_at,
        stage_key=binding.stage_key,
        risk_level=binding.risk_level,
        candidate_size_bucket=binding.candidate_size_bucket,
        provider_ids=binding.provider_ids,
        active_snapshot_digest=binding.active_snapshot_digest,
        terminal_reason=terminal_reason,
        finding_event_digests=tuple(sorted(set(finding_event_digests))),
        binding_set_digest=resolved.binding_set_digest,
        risk_profile_digest=resolved.risk_profile_digest,
        cohort_id=resolved.cohort_id,
        finding_ledger_digest=resolved.finding_ledger_digest,
        convergence_outcome_digest=resolved.convergence_outcome_digest,
        label_source_digests=tuple(sorted(set(resolved.label_source_digests))),
        resource_usage=resolved.resource_usage,
    )


def _materialize_open_censored_observations(
    bindings: tuple[CommittedSessionBinding, ...],
    store: OptimizationObservationStore,
    *,
    sequence_high_watermark: int,
    occurred_at: str,
) -> None:
    for binding in bindings:
        if binding.control_sequence > sequence_high_watermark:
            continue
        existing = store.read_session(binding.session_id)
        kinds = {item.observation_kind for item in existing}
        if "created" not in kinds or kinds & TERMINAL_OBSERVATION_KINDS:
            continue
        sequence = max(item.sequence for item in existing) + 1
        store.append(
            _build_terminal_observation(
                binding,
                "open_censored",
                sequence=sequence,
                occurred_at=occurred_at,
                terminal_reason="dataset_boundary_without_terminal_outcome",
            )
        )
