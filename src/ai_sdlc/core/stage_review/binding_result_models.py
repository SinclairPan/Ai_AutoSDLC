"""BindingSet、RebindDirective、结果与派发授权合同。"""

from __future__ import annotations

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ai_sdlc.core.stage_review.binding_digests import (
    binding_result_digest,
    dispatch_assignment_digest,
    rebind_directive_digest,
    reviewer_binding_digest,
    reviewer_binding_set_digest,
)
from ai_sdlc.core.stage_review.binding_independence import (
    validate_canonical_independence_proofs,
)
from ai_sdlc.core.stage_review.binding_models import (
    BindingResultCode,
    IndependenceGrade,
    IsolationGrade,
)
from ai_sdlc.core.stage_review.contracts import StageReviewArtifactModel
from ai_sdlc.core.stage_review.panel_models import EnforcementMode
from ai_sdlc.core.stage_review.provider_journal_models import (
    ProviderRecoveryCapabilities,
)
from ai_sdlc.core.stage_review.provider_transport_models import (
    ProviderExecutionIdentity,
)
from ai_sdlc.core.stage_review.resource_builders import stable_id


class ReviewerBinding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    binding_id: str
    slot_id: str
    slot_kind: str
    role_profile_id: str
    role_contract_digest: str
    capability_ids: tuple[str, ...]
    actor_id: str
    provider_id: str
    model_family: str
    session_id: str
    provider_descriptor_digest: str
    equivalence_class_id: str
    physical_provider_id: str
    physical_equivalence_class_id: str
    execution_identity: ProviderExecutionIdentity
    transport_profile_digest: str
    transport_contract_digest: str
    transport_authority_digest: str
    allocation_digest: str
    input_packet_digest: str
    tool_allowlist: tuple[str, ...]
    isolation_evidence_digest: str
    isolation_grade: IsolationGrade
    isolation_backend: str
    supported_independence_grade: IndependenceGrade
    visibility_barrier_id: str
    binding_status: Literal["active"] = "active"
    recovery_capabilities: ProviderRecoveryCapabilities
    eligible_for_enforce_quorum: bool
    binding_digest: str

    @model_validator(mode="after")
    def _verify_binding(self) -> Self:
        identity = self.execution_identity
        identity_lineage = (
            identity.execution_scope == "reviewer_binding",
            identity.provider_id == self.provider_id,
            identity.provider_descriptor_digest == self.provider_descriptor_digest,
            identity.equivalence_class_id == self.equivalence_class_id,
            identity.model_family == self.model_family,
            set(self.capability_ids).issubset(identity.capability_ids),
            identity.recovery_capabilities == self.recovery_capabilities,
            identity.physical_provider_id == self.physical_provider_id,
            identity.physical_equivalence_class_id
            == self.physical_equivalence_class_id,
            bool(self.transport_profile_digest),
            bool(self.transport_contract_digest),
            bool(self.transport_authority_digest),
        )
        if not all(identity_lineage):
            raise ValueError("reviewer binding execution identity diverged")
        if self.binding_digest != reviewer_binding_digest(
            self.model_dump(exclude={"binding_digest"}, mode="json")
        ):
            raise ValueError("reviewer binding digest does not match content")
        return self


class BindingIndependenceProof(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    left_slot_id: str
    right_slot_id: str
    independence_grade: IndependenceGrade
    reason_id: str


class ReviewerBindingSet(StageReviewArtifactModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    artifact_kind: Literal["reviewer-binding-set"] = "reviewer-binding-set"
    binding_set_id: str
    project_id: str
    work_item_id: str
    stage_review_session_id: str
    candidate_manifest_digest: str
    plan_digest: str
    plan_finalization_digest: str
    final_reservation_id: str
    final_reservation_digest: str
    resource_fencing_token: int = Field(ge=1)
    charged_reservation_digest: str
    resource_operation_id: str
    resource_event_digest: str
    budget_policy_digest: str
    authority_snapshot_digest: str
    host_snapshot_digest: str
    attempt_operation_id: str
    attempt_operation_digest: str
    attempt_index: int = Field(ge=1)
    previous_binding_set_digest: str = ""
    enforcement_mode: EnforcementMode
    execution_mode: Literal["enforce_eligible", "shadow_only"]
    bindings: tuple[ReviewerBinding, ...]
    unbound_slot_ids: tuple[str, ...] = ()
    independence_proofs: tuple[BindingIndependenceProof, ...] = ()
    binding_set_digest: str

    @model_validator(mode="after")
    def _verify_set(self) -> Self:
        slot_ids = tuple(item.slot_id for item in self.bindings)
        if slot_ids != tuple(sorted(set(slot_ids))):
            raise ValueError("binding set slots are not canonical")
        if self.unbound_slot_ids != tuple(sorted(set(self.unbound_slot_ids))):
            raise ValueError("binding set unbound slots are not canonical")
        if set(slot_ids) & set(self.unbound_slot_ids):
            raise ValueError("binding set slot cannot be bound and unbound")
        required = tuple(item for item in self.bindings if item.slot_kind == "required")
        expected_mode = (
            "enforce_eligible"
            if required and all(item.eligible_for_enforce_quorum for item in required)
            else "shadow_only"
        )
        if self.execution_mode != expected_mode:
            raise ValueError("binding set execution mode is inconsistent")
        validate_canonical_independence_proofs(
            self.bindings,
            self.independence_proofs,
        )
        if self.binding_set_digest != reviewer_binding_set_digest(self):
            raise ValueError("binding set digest does not match content")
        return self


class RebindDirective(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    directive_id: str
    previous_binding_set_digest: str
    new_binding_set_digest: str
    expected_cohort_id: str
    expected_pass_head_digest: str
    rebind_reason: str
    unavailable_provider_ids: tuple[str, ...]
    requires_session_cas: Literal[True] = True
    directive_digest: str

    @model_validator(mode="after")
    def _verify_directive(self) -> Self:
        if self.directive_digest != rebind_directive_digest(self):
            raise ValueError("rebind directive digest does not match content")
        return self


class ReviewerBindingResult(StageReviewArtifactModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    artifact_kind: Literal["reviewer-binding-result"] = "reviewer-binding-result"
    result_code: BindingResultCode
    operation_id: str
    binding_set: ReviewerBindingSet | None = None
    rebind_directive: RebindDirective | None = None
    reason_ids: tuple[str, ...] = ()
    diagnostic_evidence_digests: tuple[str, ...] = ()
    result_digest: str

    @model_validator(mode="after")
    def _verify_result(self) -> Self:
        if (self.result_code == "bound") != (self.binding_set is not None):
            raise ValueError("bound result requires exactly one BindingSet")
        if (self.rebind_directive is not None) != (
            self.binding_set is not None
            and bool(self.binding_set.previous_binding_set_digest)
        ):
            raise ValueError("rebind directive shape is inconsistent")
        if self.reason_ids != tuple(sorted(set(self.reason_ids))):
            raise ValueError("binding result reasons are not canonical")
        if self.diagnostic_evidence_digests != tuple(
            sorted(set(self.diagnostic_evidence_digests))
        ):
            raise ValueError("binding diagnostic evidence is not canonical")
        if self.result_digest != binding_result_digest(self):
            raise ValueError("binding result digest does not match content")
        return self


class ReviewerDispatchAssignment(StageReviewArtifactModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    artifact_kind: Literal["reviewer-dispatch-assignment"] = (
        "reviewer-dispatch-assignment"
    )
    assignment_id: str
    binding_set_digest: str
    binding_digest: str
    host_snapshot_digest: str
    isolation_evidence_digest: str
    cohort_id: str
    expected_pass_head_digest: str
    slot_id: str
    candidate_manifest_digest: str
    provider_id: str
    provider_descriptor_digest: str
    provider_execution_identity_digest: str
    physical_provider_id: str
    physical_equivalence_class_id: str
    transport_profile_digest: str
    transport_contract_digest: str
    transport_authority_digest: str
    model_family: str
    session_id: str
    recovery_capabilities: ProviderRecoveryCapabilities
    assignment_digest: str

    @model_validator(mode="after")
    def _verify_assignment(self) -> Self:
        expected_id = stable_id(
            "reviewer-dispatch",
            self.binding_set_digest,
            self.binding_digest,
            self.host_snapshot_digest,
            self.cohort_id,
            self.expected_pass_head_digest,
        )
        if self.assignment_id != expected_id:
            raise ValueError("dispatch assignment identity is inconsistent")
        if self.assignment_digest != dispatch_assignment_digest(self):
            raise ValueError("dispatch assignment digest does not match content")
        return self


class ReviewerDispatchResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    result_code: BindingResultCode
    assignment: ReviewerDispatchAssignment | None = None
    reason_ids: tuple[str, ...] = ()
    requires_rebind: bool = False

    @model_validator(mode="after")
    def _verify_result(self) -> Self:
        if (self.result_code == "bound") != (self.assignment is not None):
            raise ValueError("bound dispatch result requires an assignment")
        return self
