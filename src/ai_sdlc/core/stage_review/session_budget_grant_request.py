"""ResourceGovernor 消费的不可变 Session requested 证明。"""

from __future__ import annotations

from typing import Literal

from pydantic import ConfigDict, model_validator

from ai_sdlc.core.stage_review.artifact_compat import (
    ArtifactCompatibility,
    fill_artifact_digest,
)
from ai_sdlc.core.stage_review.session_artifact_models import ArtifactRef
from ai_sdlc.core.stage_review.session_budget_approval_models import (
    BudgetGrantApproval,
)
from ai_sdlc.core.stage_review.session_models import SessionEvent, SessionOperation

_MODEL_CONFIG = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class BudgetGrantRequestProof(ArtifactCompatibility):
    """把审批、命令 Operation 与 requested Event 固定为一个证明。"""

    model_config = _MODEL_CONFIG

    schema_version: Literal["budget-grant-request-proof.v1"] = (
        "budget-grant-request-proof.v1"
    )
    approval: BudgetGrantApproval
    request_operation: SessionOperation
    requested_event: SessionEvent
    proof_digest: str = ""

    @model_validator(mode="after")
    def _validate_proof(self) -> BudgetGrantRequestProof:
        operation = self.request_operation
        event = self.requested_event
        approval = self.approval
        payload = operation.command_payload
        valid = (
            operation.command_type == "BudgetGrantRequestCommand",
            operation.expected_event_kinds == ("budget_grant_requested",),
            event.event_kind == "budget_grant_requested",
            event.command_id == operation.command_id,
            event.command_digest == operation.command_digest,
            event.scope == approval.scope == operation.scope,
            event.projection_after.resource_reservation_id
            == approval.final_reservation_id,
            event.projection_after.resource_reservation_digest
            == approval.final_reservation_digest,
            event.projection_after.resource_fencing_epoch
            == approval.final_fencing_token,
            event.projection_after.pending_budget_grant_command_id
            == operation.command_id,
            event.projection_after.budget_revision
            == approval.expected_budget_revision,
            event.artifact_refs
            == (
                ArtifactRef(
                    artifact_id=approval.approval_id,
                    artifact_digest=approval.approval_digest,
                ),
            ),
            payload.get("approval_digest") == approval.approval_digest,
            payload.get("expected_budget_revision")
            == approval.expected_budget_revision,
            payload.get("increment") == approval.increment.model_dump(mode="json"),
            event.sequence == operation.expected_revision + 1,
        )
        if not all(valid):
            raise ValueError("budget grant request proof lineage is invalid")
        return fill_artifact_digest(self, "proof_digest")
