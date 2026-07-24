"""Prospective Shadow Assignment、离线执行与 Late Critical 外送。"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ai_sdlc.core.stage_review.artifact_compat import (
    ArtifactCompatibility,
    fill_artifact_digest,
)
from ai_sdlc.core.stage_review.artifacts import (
    SharedStateIntegrityError,
    ShortFileLock,
    bind_repository_project,
    create_json_exclusive,
    read_json_object,
    resolve_canonical_shared_state,
)
from ai_sdlc.core.stage_review.provider_journal import (
    ProviderInvocationDriver,
    ProviderInvocationJournal,
    ProviderSubmission,
)
from ai_sdlc.core.stage_review.provider_journal_builders import (
    build_provider_invocation_request,
)
from ai_sdlc.core.stage_review.provider_journal_driver import ProviderOutputValidator
from ai_sdlc.core.stage_review.provider_journal_models import (
    ProviderInvocationRequest,
    ProviderJournalResult,
    ProviderRecoveryCapabilities,
)
from ai_sdlc.core.stage_review.registry_versions import require_machine_id
from ai_sdlc.core.stage_review.resource_builders import stable_id
from ai_sdlc.core.stage_review.resource_ledger_models import ResourceReservation
from ai_sdlc.core.stage_review.resource_models import ResourceAmounts
from ai_sdlc.core.stage_review.resources import ResourceGovernor


class ShadowSessionInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    session_id: str
    session_sequence: int = Field(ge=1)
    initial_candidate_digest: str
    risk_profile_digest: str
    visible_evidence_digest: str
    active_baseline_result_digest: str
    baseline_snapshot_digest: str
    usage_estimation_policy_version: str
    usage_estimation_policy_digest: str

    @field_validator("session_id")
    @classmethod
    def _identity_is_stable(cls, value: str) -> str:
        return require_machine_id(value, "shadow session identity")


class OptimizationShadowAssignment(ArtifactCompatibility):
    schema_version: Literal["optimization-shadow-assignment.v1"] = (
        "optimization-shadow-assignment.v1"
    )
    artifact_kind: Literal["optimization-shadow-assignment"] = (
        "optimization-shadow-assignment"
    )
    assignment_id: str
    project_id: str
    epoch_id: str
    finalist_candidate_digest: str
    session_id: str
    session_sequence: int = Field(ge=1)
    initial_candidate_digest: str
    risk_profile_digest: str
    visible_evidence_digest: str
    active_baseline_result_digest: str
    baseline_snapshot_digest: str
    usage_estimation_policy_version: str
    usage_estimation_policy_digest: str
    assignment_digest: str = ""

    @field_validator("assignment_id", "project_id", "epoch_id", "session_id")
    @classmethod
    def _identity_is_stable(cls, value: str) -> str:
        return require_machine_id(value, "shadow assignment identity")

    @model_validator(mode="after")
    def _verify_digest(self) -> Self:
        return fill_artifact_digest(self, "assignment_digest")


class ShadowProviderSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider_id: str
    request_digest: str
    anticipated_usage: ResourceAmounts
    capabilities: ProviderRecoveryCapabilities


class ShadowLateCriticalSignal(ArtifactCompatibility):
    schema_version: Literal["shadow-late-critical-signal.v1"] = (
        "shadow-late-critical-signal.v1"
    )
    artifact_kind: Literal["shadow-late-critical-signal"] = (
        "shadow-late-critical-signal"
    )
    assignment_id: str
    assignment_digest: str
    submission_digest: str
    late_critical_event_digest: str
    signal_digest: str = ""

    @model_validator(mode="after")
    def _verify_digest(self) -> Self:
        return fill_artifact_digest(self, "signal_digest")


class ProspectiveShadowResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    assignment: OptimizationShadowAssignment
    invocation_result: ProviderJournalResult
    late_critical_event_digest: str = ""


LateCriticalRecorder = Callable[[OptimizationShadowAssignment, ProviderSubmission], str]


class OptimizationShadowAssignmentStore:
    def __init__(
        self,
        root: Path,
        *,
        project_id: str,
        lock_timeout_seconds: float = 2,
    ) -> None:
        self.project_id = require_machine_id(project_id, "project_id")
        shared = resolve_canonical_shared_state(root, self.project_id)
        bind_repository_project(shared, self.project_id)
        self.root = shared / "offline-optimization"
        self.lock_timeout_seconds = lock_timeout_seconds

    def assign(
        self,
        *,
        epoch_id: str,
        finalist_candidate_digest: str,
        session: ShadowSessionInput,
        epoch_session_sequence_high_watermark: int,
    ) -> OptimizationShadowAssignment:
        trusted = ShadowSessionInput.model_validate(session.model_dump(mode="json"))
        if trusted.session_sequence <= epoch_session_sequence_high_watermark:
            raise SharedStateIntegrityError("shadow input is not a post-epoch session")
        assignment = _build_assignment(
            self.project_id, epoch_id, finalist_candidate_digest, trusted
        )
        path = self.root / "shadow-assignments" / f"{trusted.session_id}.json"
        with self._locked("shadow-assignment"):
            if create_json_exclusive(path, assignment.model_dump(mode="json")):
                return assignment
            existing = OptimizationShadowAssignment.model_validate(
                read_json_object(path)
            )
            if existing != assignment:
                raise SharedStateIntegrityError(
                    "shadow session was already assigned to another finalist"
                )
            return existing

    def late_critical_signal(
        self, assignment_id: str
    ) -> ShadowLateCriticalSignal | None:
        path = self._signal_path(assignment_id)
        if not path.is_file():
            return None
        return ShadowLateCriticalSignal.model_validate(read_json_object(path))

    def read_session(self, session_id: str) -> OptimizationShadowAssignment | None:
        stable = require_machine_id(session_id, "session_id")
        path = self.root / "shadow-assignments" / f"{stable}.json"
        if not path.is_file():
            return None
        return OptimizationShadowAssignment.model_validate(read_json_object(path))

    def record_late_critical(
        self,
        assignment: OptimizationShadowAssignment,
        submission: ProviderSubmission,
        recorder: LateCriticalRecorder,
    ) -> ShadowLateCriticalSignal:
        with self._locked(f"late-critical-{assignment.assignment_id}"):
            existing = self.late_critical_signal(assignment.assignment_id)
            if existing is not None:
                return existing
            event_digest = recorder(assignment, submission).strip()
            if not event_digest:
                raise SharedStateIntegrityError(
                    "late critical recorder returned no event"
                )
            signal = ShadowLateCriticalSignal(
                assignment_id=assignment.assignment_id,
                assignment_digest=assignment.assignment_digest,
                submission_digest=submission.submission_digest,
                late_critical_event_digest=event_digest,
            )
            if not create_json_exclusive(
                self._signal_path(assignment.assignment_id),
                signal.model_dump(mode="json"),
            ):
                raise SharedStateIntegrityError("late critical signal collided")
            return signal

    def _signal_path(self, assignment_id: str) -> Path:
        return self.root / "late-critical-signals" / f"{assignment_id}.json"

    @contextmanager
    def _locked(self, name: str) -> Iterator[None]:
        with ShortFileLock(
            self.root / "locks" / f"{name}.lock",
            timeout_seconds=self.lock_timeout_seconds,
        ):
            yield


class ProspectiveShadowService:
    def __init__(
        self,
        *,
        store: OptimizationShadowAssignmentStore,
        journal: ProviderInvocationJournal,
        resource_governor: ResourceGovernor,
        late_critical_recorder: LateCriticalRecorder,
    ) -> None:
        self.store = store
        self.journal = journal
        self.resources = resource_governor
        self.late_critical_recorder = late_critical_recorder

    def evaluate(
        self,
        *,
        epoch_id: str,
        finalist_candidate_digest: str,
        session: ShadowSessionInput,
        epoch_session_sequence_high_watermark: int,
        provider: ShadowProviderSpec,
        driver: ProviderInvocationDriver,
        validator: ProviderOutputValidator,
        reservation_id: str,
        lease_owner: str,
    ) -> ProspectiveShadowResult:
        assignment = self.store.assign(
            epoch_id=epoch_id,
            finalist_candidate_digest=finalist_candidate_digest,
            session=session,
            epoch_session_sequence_high_watermark=epoch_session_sequence_high_watermark,
        )
        reservation = self.resources.get_reservation(reservation_id)
        if (
            reservation.pool != "offline_optimization"
            or reservation.state != "final"
            or reservation.lease_owner != lease_owner
        ):
            raise SharedStateIntegrityError("shadow reservation is unavailable")
        invocation = self._resolve_invocation(assignment, provider, reservation)
        prepared = self.journal.prepare(invocation, lease_owner=lease_owner)
        if prepared.invocation is None:
            raise SharedStateIntegrityError("shadow provider preparation failed")
        result = self.journal.resume(
            invocation.invocation_id,
            driver=driver,
            validator=validator,
            lease_owner=lease_owner,
        )
        signal = self._record_confirmed_critical(assignment, result)
        return ProspectiveShadowResult(
            assignment=assignment,
            invocation_result=result,
            late_critical_event_digest=(
                "" if signal is None else signal.late_critical_event_digest
            ),
        )

    def _resolve_invocation(
        self,
        assignment: OptimizationShadowAssignment,
        provider: ShadowProviderSpec,
        reservation: ResourceReservation,
    ) -> ProviderInvocationRequest:
        epoch_id = assignment.epoch_id
        idempotency_key = stable_id("shadow-query", assignment.assignment_id)
        invocation_id = stable_id(
            "provider-invocation",
            assignment.project_id,
            reservation.stage_review_session_id,
            provider.provider_id,
            idempotency_key,
        )
        existing = self.journal.get(invocation_id)
        if existing is not None:
            return existing.request
        return build_provider_invocation_request(
            project_id=assignment.project_id,
            work_item_id=reservation.work_item_id,
            stage_review_session_id=reservation.stage_review_session_id,
            owner_scope_id=f"offline-optimization.{epoch_id}",
            candidate_digest=assignment.finalist_candidate_digest,
            assignment_digest=assignment.assignment_digest,
            epoch_id=epoch_id,
            provider_id=provider.provider_id,
            request_digest=provider.request_digest,
            reservation_id=reservation.reservation_id,
            expected_reservation_digest=reservation.reservation_digest,
            expected_fencing_token=reservation.fencing_token,
            anticipated_usage=provider.anticipated_usage,
            capabilities=provider.capabilities,
            command_id=stable_id("shadow-query-command", assignment.assignment_id),
            idempotency_key=idempotency_key,
            authorization_scope="optimization_shadow",
        )

    def _record_confirmed_critical(
        self,
        assignment: OptimizationShadowAssignment,
        result: ProviderJournalResult,
    ) -> ShadowLateCriticalSignal | None:
        submission = result.submission
        if submission is None or not _is_confirmed_critical(submission):
            return None
        return self.store.record_late_critical(
            assignment, submission, self.late_critical_recorder
        )


def _build_assignment(
    project_id: str,
    epoch_id: str,
    finalist_digest: str,
    session: ShadowSessionInput,
) -> OptimizationShadowAssignment:
    return OptimizationShadowAssignment(
        assignment_id=stable_id("optimization-shadow", project_id, session.session_id),
        project_id=project_id,
        epoch_id=epoch_id,
        finalist_candidate_digest=finalist_digest,
        **session.model_dump(mode="python"),
    )


def _is_confirmed_critical(submission: ProviderSubmission) -> bool:
    payload = submission.output_payload
    return (
        payload.get("severity") in {"P0", "P1"}
        and payload.get("evidence_confirmed") is True
        and isinstance(payload.get("finding_authority_digest"), str)
        and bool(str(payload["finding_authority_digest"]).strip())
    )
