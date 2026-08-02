"""Binding Authority、Host、运行实例与 Attempt 合同。"""

from __future__ import annotations

from typing import Literal, Self

from pydantic import ConfigDict, Field, field_validator, model_validator

from ai_sdlc.core.stage_review.binding_availability_models import (
    ProviderAvailabilityAttestation,
)
from ai_sdlc.core.stage_review.binding_digests import (
    binding_attempt_operation_digest,
    binding_attempt_request_digest,
    binding_authority_digest,
    host_capability_digest,
    isolation_evidence_digest,
    provider_descriptor_digest,
    runtime_allocation_digest,
)
from ai_sdlc.core.stage_review.contracts import RiskSeverity, StageReviewArtifactModel
from ai_sdlc.core.stage_review.panel_models import EnforcementMode
from ai_sdlc.core.stage_review.provider_journal_models import (
    ProviderRecoveryCapabilities,
)
from ai_sdlc.core.stage_review.provider_route_models import ProviderExecutionRoute
from ai_sdlc.core.stage_review.resource_builders import parse_utc, stable_id
from ai_sdlc.core.stage_review.resource_models import ResourceAmounts

IsolationGrade = Literal["enforced", "detected_only", "unproven"]
IndependenceGrade = Literal[
    "session_independent",
    "provider_independent",
    "model_diversity_proven",
]
BindingResultCode = Literal[
    "bound",
    "actor_unavailable",
    "independence_unproven",
    "provider_policy_blocked",
    "session_creation_failed",
    "visibility_barrier_failed",
]
RebindReason = Literal[
    "initial_binding",
    "provider_unavailable",
    "actor_unavailable_retry",
    "session_creation_retry",
]


class ProviderBindingDescriptor(StageReviewArtifactModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    artifact_kind: Literal["provider-binding-descriptor"] = (
        "provider-binding-descriptor"
    )
    descriptor_id: str
    provider_id: str
    equivalence_class_id: str
    model_family: str
    role_contract_digests: tuple[str, ...]
    capability_ids: tuple[str, ...]
    provider_tags: tuple[str, ...]
    tool_allowlist: tuple[str, ...]
    recovery_capabilities: ProviderRecoveryCapabilities
    execution_route: ProviderExecutionRoute
    isolation_backend: str
    network_enforcement: bool
    supported_independence_grade: IndependenceGrade
    provider_policy_evidence_digest: str
    descriptor_digest: str

    @model_validator(mode="after")
    def _verify_descriptor(self) -> Self:
        _require_sorted_unique(
            self.role_contract_digests,
            self.capability_ids,
            self.provider_tags,
            self.tool_allowlist,
        )
        _require_text(
            self.descriptor_id,
            self.provider_id,
            self.equivalence_class_id,
            self.model_family,
            self.isolation_backend,
            self.provider_policy_evidence_digest,
        )
        if self.descriptor_digest != provider_descriptor_digest(self):
            raise ValueError("provider descriptor digest does not match content")
        return self


class BindingAuthoritySnapshot(StageReviewArtifactModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    artifact_kind: Literal["binding-authority-snapshot"] = "binding-authority-snapshot"
    snapshot_id: str
    plan_digest: str
    plan_finalization_digest: str
    request_digest: str
    optimization_snapshot_digest: str
    registry_digest: str
    selection_policy_digest: str
    risk_level: RiskSeverity
    enforcement_mode: EnforcementMode
    provider_descriptors: tuple[ProviderBindingDescriptor, ...]
    attestor_id: str
    attestor_version: str
    attestation_evidence_digest: str
    snapshot_digest: str

    @model_validator(mode="after")
    def _verify_snapshot(self) -> Self:
        digests = tuple(item.descriptor_digest for item in self.provider_descriptors)
        if not digests or digests != tuple(sorted(set(digests))):
            raise ValueError("binding authority provider pool is not canonical")
        _require_text(
            self.snapshot_id,
            self.plan_digest,
            self.plan_finalization_digest,
            self.request_digest,
            self.optimization_snapshot_digest,
            self.registry_digest,
            self.selection_policy_digest,
            self.attestor_id,
            self.attestor_version,
            self.attestation_evidence_digest,
        )
        expected_id = stable_id(
            "binding-authority",
            self.plan_digest,
            self.optimization_snapshot_digest,
        )
        if self.snapshot_id != expected_id:
            raise ValueError("binding authority snapshot identity is invalid")
        if self.snapshot_digest != binding_authority_digest(self):
            raise ValueError("binding authority digest does not match content")
        return self


class HostCapabilitySnapshot(StageReviewArtifactModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    artifact_kind: Literal["host-capability-snapshot"] = "host-capability-snapshot"
    snapshot_id: str
    host_adapter_id: str
    host_adapter_version: str
    host_session_id: str
    capability_ids: tuple[str, ...]
    capability_source: str
    evidence_digest: str
    backend_id: str = ""
    backend_contract_version: str = ""
    backend_release_manifest_digest: str = ""
    backend_runtime_identity_digest: str = ""
    previous_snapshot_digest: str = ""
    authorization_transition: str
    issued_at: str
    expires_at: str
    snapshot_digest: str

    @field_validator("issued_at", "expires_at")
    @classmethod
    def _timestamp_is_utc(cls, value: str) -> str:
        parse_utc(value)
        return value

    @model_validator(mode="after")
    def _verify_snapshot(self) -> Self:
        if self.capability_ids != tuple(sorted(set(self.capability_ids))):
            raise ValueError("host capabilities are not canonical")
        if parse_utc(self.expires_at) <= parse_utc(self.issued_at):
            raise ValueError("host capability snapshot expiry is invalid")
        _require_text(
            self.snapshot_id,
            self.host_adapter_id,
            self.host_adapter_version,
            self.host_session_id,
            self.capability_source,
            self.evidence_digest,
            self.authorization_transition,
        )
        expected_id = stable_id(
            "host-capability",
            self.host_adapter_id,
            self.host_session_id,
            self.evidence_digest,
            self.expires_at,
        )
        if self.snapshot_id != expected_id:
            raise ValueError("host snapshot identity is invalid")
        if self.snapshot_digest != host_capability_digest(self):
            raise ValueError("host capability digest does not match content")
        return self


class ReviewerRuntimeAllocation(StageReviewArtifactModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    artifact_kind: Literal["reviewer-runtime-allocation"] = (
        "reviewer-runtime-allocation"
    )
    allocation_id: str
    slot_id: str
    actor_id: str
    session_id: str
    provider_descriptor_digest: str
    provider_id: str
    equivalence_class_id: str
    physical_provider_id: str
    physical_equivalence_class_id: str
    model_family: str
    candidate_manifest_digest: str
    candidate_snapshot_id: str
    working_directory_id: str
    disposable_home_id: str
    disposable_config_id: str
    disposable_credential_view_id: str
    output_directory_id: str
    allocation_operation_id: str
    allocation_digest: str

    @model_validator(mode="after")
    def _verify_allocation(self) -> Self:
        _require_text(
            self.allocation_id,
            self.slot_id,
            self.actor_id,
            self.session_id,
            self.provider_descriptor_digest,
            self.provider_id,
            self.equivalence_class_id,
            self.physical_provider_id,
            self.physical_equivalence_class_id,
            self.model_family,
            self.candidate_manifest_digest,
            self.candidate_snapshot_id,
            self.working_directory_id,
            self.disposable_home_id,
            self.disposable_config_id,
            self.disposable_credential_view_id,
            self.output_directory_id,
            self.allocation_operation_id,
        )
        if self.allocation_digest != runtime_allocation_digest(self):
            raise ValueError("runtime allocation digest does not match content")
        return self


class IsolationExecutionEvidence(StageReviewArtifactModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    artifact_kind: Literal["isolation-execution-evidence"] = (
        "isolation-execution-evidence"
    )
    evidence_id: str
    operation_id: str
    allocation_digest: str
    host_snapshot_digest: str
    visibility_barrier_id: str
    isolation_grade: IsolationGrade
    isolation_backend: str
    candidate_snapshot_isolated: bool
    candidate_write_enforced: bool
    peer_outputs_hidden: bool
    disposable_home: bool
    disposable_config: bool
    disposable_credentials: bool
    output_isolated: bool
    user_home_protected: bool
    global_config_protected: bool
    network_policy_enforced: bool
    sentinel_environment_disposable: bool
    evidence_bundle_digest: str
    isolation_evidence_digest: str

    @model_validator(mode="after")
    def _verify_evidence(self) -> Self:
        _require_text(
            self.evidence_id,
            self.operation_id,
            self.allocation_digest,
            self.host_snapshot_digest,
            self.visibility_barrier_id,
            self.isolation_backend,
            self.evidence_bundle_digest,
        )
        if self.isolation_evidence_digest != isolation_evidence_digest(self):
            raise ValueError("isolation evidence digest does not match content")
        return self


class BindingAttemptRequest(StageReviewArtifactModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    artifact_kind: Literal["binding-attempt-request"] = "binding-attempt-request"
    request_id: str
    project_id: str
    work_item_id: str
    stage_review_session_id: str
    candidate_manifest_digest: str
    input_packet_digest: str
    visibility_barrier_id: str
    plan_digest: str
    plan_finalization_digest: str
    final_reservation_id: str
    final_reservation_digest: str
    resource_fencing_token: int = Field(ge=1)
    budget_policy_digest: str
    attempt_index: int = Field(ge=1)
    previous_binding_set_digest: str = ""
    expected_cohort_id: str = ""
    expected_pass_head_digest: str = ""
    rebind_reason: RebindReason
    provider_availability_attestation_digest: str = ""
    unavailable_provider_ids: tuple[str, ...] = ()
    provider_retry_delta: int = Field(default=0, ge=0, le=1)
    operation_id: str
    request_digest: str

    @model_validator(mode="after")
    def _verify_request(self) -> Self:
        if self.unavailable_provider_ids != tuple(
            sorted(set(self.unavailable_provider_ids))
        ):
            raise ValueError("unavailable providers are not canonical")
        is_initial_attempt = self.attempt_index == 1
        has_previous_binding = bool(self.previous_binding_set_digest)
        if is_initial_attempt and has_previous_binding:
            raise ValueError("binding attempt previous lineage is invalid")
        if has_previous_binding and (
            not self.expected_cohort_id or not self.expected_pass_head_digest
        ):
            raise ValueError("rebind requires expected Cohort and Pass head")
        if not has_previous_binding and (
            self.expected_cohort_id or self.expected_pass_head_digest
        ):
            raise ValueError("binding retry cannot claim Cohort or Pass lineage")
        expected_retry = int(
            self.rebind_reason in {"provider_unavailable", "session_creation_retry"}
        )
        if self.provider_retry_delta != expected_retry:
            raise ValueError("binding provider retry delta is not derived")
        if is_initial_attempt and self.rebind_reason != "initial_binding":
            raise ValueError("initial binding reason is invalid")
        if has_previous_binding and self.rebind_reason != "provider_unavailable":
            raise ValueError("provider rebind reason is invalid")
        retry_reasons = {
            "actor_unavailable_retry",
            "session_creation_retry",
        }
        if (
            not is_initial_attempt
            and not has_previous_binding
            and self.rebind_reason not in retry_reasons
        ):
            raise ValueError("binding retry reason is invalid")
        if self.request_digest != binding_attempt_request_digest(self):
            raise ValueError("binding attempt request digest does not match content")
        return self


class BindingAttemptOperation(StageReviewArtifactModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    artifact_kind: Literal["binding-attempt-operation"] = "binding-attempt-operation"
    operation_id: str
    request: BindingAttemptRequest
    authority_snapshot: BindingAuthoritySnapshot
    availability_attestation: ProviderAvailabilityAttestation | None = None
    resource_delta: ResourceAmounts
    resource_operation_id: str
    operation_digest: str

    @model_validator(mode="after")
    def _verify_operation(self) -> Self:
        expected = ResourceAmounts(
            binding_attempts=1,
            provider_retries=self.request.provider_retry_delta,
        )
        if self.operation_id != self.request.operation_id:
            raise ValueError("binding operation identity is inconsistent")
        if self.resource_delta != expected:
            raise ValueError("binding operation resource delta is inconsistent")
        if self.operation_digest != binding_attempt_operation_digest(self):
            raise ValueError("binding attempt operation digest does not match content")
        return self


def _require_sorted_unique(*values: tuple[str, ...]) -> None:
    if any(items != tuple(sorted(set(items))) for items in values):
        raise ValueError("binding descriptor collection is not canonical")


def _require_text(*values: str) -> None:
    if any(not item.strip() or item != item.strip() for item in values):
        raise ValueError("binding identity cannot be empty or padded")
