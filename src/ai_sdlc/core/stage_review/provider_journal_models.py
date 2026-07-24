"""Provider Invocation Journal 的不可变工件与可重建投影。"""

from __future__ import annotations

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ai_sdlc.core.stage_review.contracts import StageReviewArtifactModel
from ai_sdlc.core.stage_review.provider_execution_evidence import (
    provider_execution_evidence_root_digest,
)
from ai_sdlc.core.stage_review.provider_journal_digests import (
    canonical_provider_output,
    event_digest,
    projection_digest,
    provider_output_digest,
    request_artifact_digest,
    submission_digest,
)
from ai_sdlc.core.stage_review.provider_usage_models import AccountedProviderUsage
from ai_sdlc.core.stage_review.resource_builders import stable_id
from ai_sdlc.core.stage_review.resource_models import (
    ResourceAmounts,
    is_complete_provider_actual_usage,
)

ProviderInvocationState = Literal[
    "prepared",
    "dispatched",
    "refused",
    "submitted",
    "executed_invalid",
    "validated",
    "committed",
]
ProviderJournalResultCode = Literal[
    "prepared",
    "committed",
    "needs_user",
    "retry_wait",
    "invalid_request",
    "invalid_resource_binding",
    "provider_output_invalid",
    "state_corrupt",
    "lock_unavailable",
    "dispatch_unauthorized",
]
class ProviderRecoveryCapabilities(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    idempotency_support: bool
    invocation_query_support: bool
    cost_metering_support: bool


class ProviderInvocationRequest(StageReviewArtifactModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    artifact_kind: Literal["provider-invocation-request"] = (
        "provider-invocation-request"
    )
    invocation_id: str
    project_id: str
    work_item_id: str
    stage_review_session_id: str
    owner_scope_id: str
    candidate_digest: str
    assignment_digest: str
    authorization_scope: (
        Literal["generic", "optimization_shadow", "reviewer_binding"] | None
    ) = None
    epoch_id: str = ""
    provider_id: str
    request_digest: str
    reservation_id: str
    expected_reservation_digest: str
    expected_fencing_token: int = Field(ge=1)
    anticipated_usage: ResourceAmounts
    capabilities: ProviderRecoveryCapabilities
    command_id: str
    idempotency_key: str
    request_artifact_digest: str

    @field_validator(
        "project_id",
        "work_item_id",
        "stage_review_session_id",
        "owner_scope_id",
        "candidate_digest",
        "assignment_digest",
        "provider_id",
        "request_digest",
        "reservation_id",
        "expected_reservation_digest",
        "command_id",
        "idempotency_key",
    )
    @classmethod
    def _require_identity(cls, value: str) -> str:
        if not value.strip() or value != value.strip():
            raise ValueError("provider invocation identity is invalid")
        return value

    @model_validator(mode="after")
    def _verify_request(self) -> Self:
        is_reviewer_assignment = self.assignment_digest.startswith(
            "reviewer-assignment:sha256:"
        )
        effective_scope = self.authorization_scope or "generic"
        if is_reviewer_assignment != (effective_scope == "reviewer_binding"):
            raise ValueError("provider invocation assignment authority is invalid")
        if not _complete_anticipated_usage(self.anticipated_usage):
            raise ValueError("provider invocation anticipated usage is incomplete")
        expected_id = stable_id(
            "provider-invocation",
            self.project_id,
            self.stage_review_session_id,
            self.provider_id,
            self.idempotency_key,
        )
        if self.invocation_id != expected_id:
            raise ValueError("provider invocation identity is inconsistent")
        if self.request_artifact_digest != request_artifact_digest(self):
            raise ValueError("provider invocation request digest is inconsistent")
        return self


class ProviderSubmission(StageReviewArtifactModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    artifact_kind: Literal["provider-submission"] = "provider-submission"
    invocation_id: str
    idempotency_key: str
    request_artifact_digest: str
    provider_id: str
    provider_call_id: str
    output_payload: dict[str, object]
    output_digest: str
    accounted_usage: AccountedProviderUsage
    submission_digest: str
    isolation_receipt_digests: tuple[str, ...] = ()
    egress_receipt_digests: tuple[str, ...] = ()
    execution_evidence_root_digest: str = ""

    @property
    def isolation_receipt_digest(self) -> str:
        return (
            self.isolation_receipt_digests[-1] if self.isolation_receipt_digests else ""
        )

    @field_validator("output_payload", mode="before")
    @classmethod
    def _freeze_output(cls, value: object) -> dict[str, object]:
        return canonical_provider_output(value)

    @model_validator(mode="after")
    def _verify_submission(self) -> Self:
        identities = (
            self.invocation_id,
            self.idempotency_key,
            self.request_artifact_digest,
            self.provider_id,
            self.provider_call_id,
        )
        if any(not item.strip() or item != item.strip() for item in identities):
            raise ValueError("provider submission identity is invalid")
        if not is_complete_provider_actual_usage(self.accounted_usage.amounts):
            raise ValueError("provider submission actual usage is incomplete")
        if self.isolation_receipt_digests != tuple(
            dict.fromkeys(self.isolation_receipt_digests)
        ) or any(not item.strip() for item in self.isolation_receipt_digests):
            raise ValueError("provider submission isolation receipt lineage is invalid")
        if self.egress_receipt_digests != tuple(
            dict.fromkeys(self.egress_receipt_digests)
        ) or any(not item.strip() for item in self.egress_receipt_digests):
            raise ValueError("provider submission egress receipt lineage is invalid")
        expected_root = provider_execution_evidence_root_digest(
            self.isolation_receipt_digests,
            self.egress_receipt_digests,
        )
        if self.execution_evidence_root_digest != expected_root:
            raise ValueError("provider submission execution evidence root is invalid")
        if self.output_digest != provider_output_digest(self.output_payload):
            raise ValueError("provider output digest is inconsistent")
        if self.submission_digest != submission_digest(self):
            raise ValueError("provider submission digest is inconsistent")
        return self


class ProviderQueryResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    query_status: Literal["not_found", "in_progress", "submitted"]
    submission: ProviderSubmission | None = None

    @model_validator(mode="after")
    def _verify_result(self) -> Self:
        if (self.query_status == "submitted") != (self.submission is not None):
            raise ValueError("provider query result is incomplete")
        return self


class ProviderInvocationEvent(StageReviewArtifactModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    artifact_kind: Literal["provider-invocation-event"] = "provider-invocation-event"
    invocation_id: str
    sequence: int = Field(ge=1, le=5)
    state: ProviderInvocationState
    event_id: str
    previous_event_digest: str
    request: ProviderInvocationRequest
    authorized_reservation_digest: str
    submission_digest: str = ""
    isolation_receipt_digests: tuple[str, ...] = ()
    egress_receipt_digests: tuple[str, ...] = ()
    execution_evidence_root_digest: str = ""
    validation_digest: str = ""
    resource_settlement_operation_id: str = ""
    settlement_reservation_digest: str = ""
    resource_settlement_event_digest: str = ""
    event_digest: str

    @model_validator(mode="after")
    def _verify_event(self) -> Self:
        state_matches = _state_matches_revision(self.state, self.sequence)
        expected_id = stable_id(
            "provider-invocation-event", self.invocation_id, self.state
        )
        if not state_matches or self.event_id != expected_id:
            raise ValueError("provider invocation event identity is inconsistent")
        if self.request.invocation_id != self.invocation_id:
            raise ValueError("provider invocation event request is inconsistent")
        if not self.authorized_reservation_digest.strip():
            raise ValueError("provider authorization reservation digest is required")
        _verify_event_payload(self)
        if self.event_digest != event_digest(self):
            raise ValueError("provider invocation event digest is inconsistent")
        return self


class ProviderInvocation(StageReviewArtifactModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    artifact_classification = "rebuildable-projection"

    artifact_kind: Literal["provider-invocation"] = "provider-invocation"
    request: ProviderInvocationRequest
    state: ProviderInvocationState
    revision: int = Field(ge=1, le=5)
    authorized_reservation_digest: str
    submission_digest: str = ""
    isolation_receipt_digests: tuple[str, ...] = ()
    egress_receipt_digests: tuple[str, ...] = ()
    execution_evidence_root_digest: str = ""
    validation_digest: str = ""
    resource_settlement_operation_id: str = ""
    settlement_reservation_digest: str = ""
    resource_settlement_event_digest: str = ""
    last_event_digest: str
    projection_digest: str

    @property
    def invocation_id(self) -> str:
        return self.request.invocation_id

    @property
    def isolation_receipt_digest(self) -> str:
        return (
            self.isolation_receipt_digests[-1] if self.isolation_receipt_digests else ""
        )

    @model_validator(mode="after")
    def _verify_projection(self) -> Self:
        if not _state_matches_revision(self.state, self.revision):
            raise ValueError("provider invocation projection state is inconsistent")
        if self.projection_digest != projection_digest(self):
            raise ValueError("provider invocation projection digest is inconsistent")
        if (
            self.execution_evidence_root_digest
            != provider_execution_evidence_root_digest(
                self.isolation_receipt_digests,
                self.egress_receipt_digests,
            )
        ):
            raise ValueError("provider invocation execution evidence root is invalid")
        return self


class ProviderJournalResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    result_code: ProviderJournalResultCode
    invocation: ProviderInvocation | None = None
    submission: ProviderSubmission | None = None


def _complete_anticipated_usage(amounts: ResourceAmounts) -> bool:
    return (
        amounts.provider_calls == 1
        and amounts.tokens > 0
        and amounts.cost > 0
        and amounts.active_wall_clock > 0
        and amounts.parallelism == 1
    )


def _verify_event_payload(event: ProviderInvocationEvent) -> None:
    if event.state == "refused":
        _verify_terminal_event(event, requires_submission=False)
        return
    if event.state == "executed_invalid":
        _verify_terminal_event(event, requires_submission=True)
        return
    requires_submission = event.sequence >= 3
    requires_validation = event.sequence >= 4
    requires_settlement = event.sequence == 5
    secured_execution = event.request.authorization_scope in {
        "optimization_shadow",
        "reviewer_binding",
    }
    expected = (
        bool(event.submission_digest) == requires_submission,
        bool(event.isolation_receipt_digests)
        == (
            requires_submission
            and event.request.authorization_scope == "reviewer_binding"
        ),
        bool(event.execution_evidence_root_digest)
        == (requires_submission and secured_execution),
        event.execution_evidence_root_digest
        == provider_execution_evidence_root_digest(
            event.isolation_receipt_digests,
            event.egress_receipt_digests,
        ),
        bool(event.validation_digest) == requires_validation,
        bool(event.resource_settlement_operation_id) == requires_settlement,
        bool(event.settlement_reservation_digest) == requires_settlement,
        bool(event.resource_settlement_event_digest) == requires_settlement,
        bool(event.previous_event_digest) == (event.sequence > 1),
    )
    if not all(expected):
        raise ValueError("provider invocation event payload is incomplete")


def _verify_terminal_event(
    event: ProviderInvocationEvent,
    *,
    requires_submission: bool,
) -> None:
    secured_execution = event.request.authorization_scope in {
        "optimization_shadow",
        "reviewer_binding",
    }
    expected = (
        event.sequence == (4 if requires_submission else 3),
        bool(event.submission_digest) == requires_submission,
        bool(event.validation_digest) is False,
        bool(event.resource_settlement_operation_id),
        bool(event.settlement_reservation_digest),
        bool(event.resource_settlement_event_digest),
        bool(event.previous_event_digest),
        bool(event.execution_evidence_root_digest)
        == (
            secured_execution
            and bool(event.isolation_receipt_digests or event.egress_receipt_digests)
        ),
        event.execution_evidence_root_digest
        == provider_execution_evidence_root_digest(
            event.isolation_receipt_digests,
            event.egress_receipt_digests,
        ),
    )
    if not all(expected):
        raise ValueError("provider terminal event payload is incomplete")


def _state_matches_revision(state: ProviderInvocationState, revision: int) -> bool:
    allowed: dict[int, frozenset[ProviderInvocationState]] = {
        1: frozenset({"prepared"}),
        2: frozenset({"dispatched"}),
        3: frozenset({"submitted", "refused"}),
        4: frozenset({"validated", "executed_invalid"}),
        5: frozenset({"committed"}),
    }
    return state in allowed.get(revision, frozenset())
