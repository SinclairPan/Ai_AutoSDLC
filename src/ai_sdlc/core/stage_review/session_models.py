"""Session 可重建投影、事件、操作和服务结果模型。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ai_sdlc.core.stage_review.artifact_compat import (
    ArtifactCompatibility,
    JsonValue,
    fill_artifact_digest,
    freeze_json_mapping,
)
from ai_sdlc.core.stage_review.finding_models import FindingScope
from ai_sdlc.core.stage_review.finding_trust_models import InitialReviewSeal
from ai_sdlc.core.stage_review.resource_builders import parse_utc
from ai_sdlc.core.stage_review.resource_models import ResourceAmounts
from ai_sdlc.core.stage_review.session_artifact_models import (
    ArtifactRef,
    MacroRebaselineRequest,
    ProgressRecord,
    ReviewPass,
    ReviewPassRef,
    RoleReplanCounter,
)
from ai_sdlc.core.stage_review.session_budget_projection import (
    _validate_session_budget_projection as validate_session_budget_projection,
)
from ai_sdlc.core.stage_review.session_close_projection import (
    SessionCloseProjectionAccessors,
)
from ai_sdlc.core.stage_review.session_close_projection import (
    _validate_session_close_projection as validate_session_close_projection,
)
from ai_sdlc.core.stage_review.session_contracts import (
    SessionEventKind,
    SessionState,
)

_MODEL_CONFIG = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

class SessionProjectionData(BaseModel):
    model_config = _MODEL_CONFIG

    scope: FindingScope
    state: SessionState
    policy_digest: str
    optimization_snapshot_digest: str
    risk_profile_lineage_id: str
    active_candidate_digest: str
    active_risk_profile_digest: str
    active_plan_digest: str
    active_binding_set_digest: str
    active_cohort_id: str
    active_cohort_initial_head_digest: str
    resource_reservation_id: str
    resource_reservation_digest: str
    resource_fencing_epoch: int = Field(ge=1)
    resource_usage: ResourceAmounts
    budget_revision: int = Field(default=0, ge=0)
    budget_grant_ids: tuple[str, ...] = ()
    budget_grant_digests: tuple[str, ...] = ()
    reconciled_budget_grant_ids: tuple[str, ...] = ()
    reconciled_budget_grant_digests: tuple[str, ...] = ()
    pending_budget_grant_command_id: str = ""
    budget_resume_state: SessionState | None = None
    last_budget_grant_operation_id: str = ""
    budget_grant_operation_effect_digest: str = ""
    budget_grant_failure_code: str = ""
    finding_ledger_digest: str
    cohort_refs: tuple[ArtifactRef, ...]
    pass_refs: tuple[ReviewPassRef, ...] = ()
    initial_seal_refs: tuple[ArtifactRef, ...] = ()
    sealed_cohort_ids: tuple[str, ...] = ()
    superseded_cohort_ids: tuple[str, ...] = ()
    invalidated_pass_ids: tuple[str, ...] = ()
    progress_records: tuple[ProgressRecord, ...] = ()
    role_replan_counts: tuple[RoleReplanCounter, ...] = ()
    no_progress_streak: int = Field(default=0, ge=0)
    pending_role_gap_capability_ids: tuple[str, ...] = ()
    macro_rebaseline_request: MacroRebaselineRequest | None = None
    revoked_plan_digests: tuple[str, ...] = ()
    active_close_certificate_id: str = ""
    active_close_certificate_digest: str = ""
    active_close_claim_id: str = ""
    active_close_claim_digest: str = ""
    close_consumption_receipt_id: str = ""
    close_consumption_receipt_digest: str = ""
    close_governance_decision_digest: str = ""
    close_failure_reason: str = ""

    @model_validator(mode="after")
    def _validate_projection(self) -> SessionProjectionData:
        canonical_groups = (
            self.sealed_cohort_ids,
            self.superseded_cohort_ids,
            self.invalidated_pass_ids,
            self.pending_role_gap_capability_ids,
            self.revoked_plan_digests,
        )
        if any(group != tuple(sorted(set(group))) for group in canonical_groups):
            raise ValueError("session set-like projection values must be canonical")
        cohort_ids = tuple(item.artifact_id for item in self.cohort_refs)
        pass_ids = tuple(item.pass_id for item in self.pass_refs)
        if len(cohort_ids) != len(set(cohort_ids)) or len(pass_ids) != len(
            set(pass_ids)
        ):
            raise ValueError("session artifact references are duplicated")
        lineages = tuple(
            item.risk_profile_lineage_id for item in self.role_replan_counts
        )
        if lineages != tuple(sorted(set(lineages))):
            raise ValueError("session role replan counters must be canonical")
        validate_session_budget_projection(self)
        validate_session_close_projection(self)
        return self


class StageReviewSession(SessionCloseProjectionAccessors, ArtifactCompatibility):
    model_config = _MODEL_CONFIG

    schema_version: Literal["stage-review-session.v1"] = "stage-review-session.v1"
    revision: int = Field(ge=1)
    head_event_id: str
    head_event_digest: str
    projection: SessionProjectionData
    session_digest: str = ""

    @property
    def scope(self) -> FindingScope:
        return self.projection.scope

    @property
    def state(self) -> SessionState:
        return self.projection.state

    @property
    def policy_digest(self) -> str:
        return self.projection.policy_digest

    @property
    def optimization_snapshot_digest(self) -> str:
        return self.projection.optimization_snapshot_digest

    @property
    def risk_profile_lineage_id(self) -> str:
        return self.projection.risk_profile_lineage_id

    @property
    def active_candidate_digest(self) -> str:
        return self.projection.active_candidate_digest

    @property
    def active_risk_profile_digest(self) -> str:
        return self.projection.active_risk_profile_digest

    @property
    def active_plan_digest(self) -> str:
        return self.projection.active_plan_digest

    @property
    def active_binding_set_digest(self) -> str:
        return self.projection.active_binding_set_digest

    @property
    def active_cohort_id(self) -> str:
        return self.projection.active_cohort_id

    @property
    def active_cohort_initial_head_digest(self) -> str:
        return self.projection.active_cohort_initial_head_digest

    @property
    def resource_reservation_id(self) -> str:
        return self.projection.resource_reservation_id

    @property
    def resource_reservation_digest(self) -> str:
        return self.projection.resource_reservation_digest

    @property
    def resource_fencing_epoch(self) -> int:
        return self.projection.resource_fencing_epoch

    @property
    def resource_usage(self) -> ResourceAmounts:
        return self.projection.resource_usage

    @property
    def budget_revision(self) -> int:
        return self.projection.budget_revision

    @property
    def budget_grant_ids(self) -> tuple[str, ...]:
        return self.projection.budget_grant_ids

    @property
    def budget_grant_digests(self) -> tuple[str, ...]:
        return self.projection.budget_grant_digests

    @property
    def reconciled_budget_grant_ids(self) -> tuple[str, ...]:
        return self.projection.reconciled_budget_grant_ids

    @property
    def reconciled_budget_grant_digests(self) -> tuple[str, ...]:
        return self.projection.reconciled_budget_grant_digests

    @property
    def pending_budget_grant_command_id(self) -> str:
        return self.projection.pending_budget_grant_command_id

    @property
    def budget_resume_state(self) -> SessionState | None:
        return self.projection.budget_resume_state

    @property
    def last_budget_grant_operation_id(self) -> str:
        return self.projection.last_budget_grant_operation_id

    @property
    def budget_grant_operation_effect_digest(self) -> str:
        return self.projection.budget_grant_operation_effect_digest

    @property
    def budget_grant_failure_code(self) -> str:
        return self.projection.budget_grant_failure_code

    @property
    def finding_ledger_digest(self) -> str:
        return self.projection.finding_ledger_digest

    @property
    def cohort_refs(self) -> tuple[ArtifactRef, ...]:
        return self.projection.cohort_refs

    @property
    def pass_refs(self) -> tuple[ReviewPassRef, ...]:
        return self.projection.pass_refs

    @property
    def initial_seal_refs(self) -> tuple[ArtifactRef, ...]:
        return self.projection.initial_seal_refs

    @property
    def sealed_cohort_ids(self) -> tuple[str, ...]:
        return self.projection.sealed_cohort_ids

    @property
    def superseded_cohort_ids(self) -> tuple[str, ...]:
        return self.projection.superseded_cohort_ids

    @property
    def invalidated_pass_ids(self) -> tuple[str, ...]:
        return self.projection.invalidated_pass_ids

    @property
    def progress_records(self) -> tuple[ProgressRecord, ...]:
        return self.projection.progress_records

    @property
    def role_replan_counts(self) -> tuple[RoleReplanCounter, ...]:
        return self.projection.role_replan_counts

    @property
    def no_progress_streak(self) -> int:
        return self.projection.no_progress_streak

    @property
    def pending_role_gap_capability_ids(self) -> tuple[str, ...]:
        return self.projection.pending_role_gap_capability_ids

    @property
    def macro_rebaseline_request(self) -> MacroRebaselineRequest | None:
        return self.projection.macro_rebaseline_request

    @property
    def revoked_plan_digests(self) -> tuple[str, ...]:
        return self.projection.revoked_plan_digests

    def role_replan_count(self, lineage_id: str) -> int:
        return next(
            (
                item.count
                for item in self.role_replan_counts
                if item.risk_profile_lineage_id == lineage_id
            ),
            0,
        )

    @model_validator(mode="after")
    def _validate_digest(self) -> StageReviewSession:
        return fill_artifact_digest(self, "session_digest")


class SessionEvent(ArtifactCompatibility):
    model_config = _MODEL_CONFIG

    schema_version: Literal["stage-review-session-event.v1"] = (
        "stage-review-session-event.v1"
    )
    scope: FindingScope
    sequence: int = Field(ge=1)
    event_id: str
    event_kind: SessionEventKind
    command_id: str
    command_digest: str
    previous_event_id: str = ""
    previous_event_digest: str = ""
    occurred_at: str
    projection_after: SessionProjectionData
    artifact_refs: tuple[ArtifactRef, ...] = ()
    event_digest: str = ""

    @model_validator(mode="after")
    def _validate_event(self) -> SessionEvent:
        parse_utc(self.occurred_at)
        if self.scope != self.projection_after.scope:
            raise ValueError("session event scope differs from projection")
        return fill_artifact_digest(self, "event_digest")


class SessionOperation(ArtifactCompatibility):
    model_config = _MODEL_CONFIG

    schema_version: Literal["stage-review-operation.v1"] = "stage-review-operation.v1"
    scope: FindingScope
    command_type: str
    command_payload: dict[str, JsonValue]
    command_id: str
    idempotency_key: str
    command_digest: str
    expected_revision: int = Field(ge=0)
    expected_event_kinds: tuple[SessionEventKind, ...]
    prepared_at: str
    operation_digest: str = ""

    @model_validator(mode="after")
    def _validate_operation(self) -> SessionOperation:
        parse_utc(self.prepared_at)
        object.__setattr__(
            self,
            "command_payload",
            freeze_json_mapping(self.command_payload),
        )
        if not self.expected_event_kinds:
            raise ValueError("session operation requires an event sequence")
        return fill_artifact_digest(self, "operation_digest")


class SessionOperationPointer(BaseModel):
    """Session 内唯一可变执行指针；Operation 与 Event 本身仍保持不可变。"""

    model_config = _MODEL_CONFIG

    schema_version: Literal["stage-review-operation-pointer.v1"] = (
        "stage-review-operation-pointer.v1"
    )
    scope: FindingScope
    command_id: str
    operation_digest: str
    phase: Literal["prepared", "effects_started"] = "prepared"


class SessionOperationRejection(ArtifactCompatibility):
    """无外部副作用的失败预检终态，用于区分崩溃遗留 Operation。"""

    model_config = _MODEL_CONFIG

    schema_version: Literal["stage-review-operation-rejection.v1"] = (
        "stage-review-operation-rejection.v1"
    )
    scope: FindingScope
    command_id: str
    operation_digest: str
    rejection_digest: str = ""

    @model_validator(mode="after")
    def _validate_rejection(self) -> SessionOperationRejection:
        return fill_artifact_digest(self, "rejection_digest")


class SessionMutationResult(BaseModel):
    model_config = _MODEL_CONFIG

    session: StageReviewSession
    review_pass: ReviewPass | None = None
    initial_review_seal: InitialReviewSeal | None = None
    macro_rebaseline_request: MacroRebaselineRequest | None = None
    idempotent_replay: bool = False


def replace_projection(
    projection: SessionProjectionData,
    **updates: object,
) -> SessionProjectionData:
    return SessionProjectionData.model_validate(
        {**projection.model_dump(mode="json"), **updates}
    )
