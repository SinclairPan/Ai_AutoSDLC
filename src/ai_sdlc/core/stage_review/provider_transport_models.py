"""受控 Provider 传输合同、一次性 Egress Permit 与 Receipt。"""

from __future__ import annotations

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ai_sdlc.core.stage_review.canonical import (
    CanonicalizationPolicy,
    canonical_digest,
)
from ai_sdlc.core.stage_review.contracts import StageReviewArtifactModel
from ai_sdlc.core.stage_review.provider_journal_models import (
    ProviderRecoveryCapabilities,
)
from ai_sdlc.core.stage_review.provider_usage_models import AccountedProviderUsage
from ai_sdlc.core.stage_review.resource_builders import parse_utc, stable_id

_RUNTIME = frozenset({"created_at", "created_by", "ai_sdlc_version"})
ProviderExecutionScope = Literal["generic", "optimization_shadow", "reviewer_binding"]


class ProviderTransportExecutionError(RuntimeError):
    """Provider 已执行但未产生可信响应，并携带可结算用量。"""

    def __init__(
        self,
        message: str,
        *,
        accounted_usage: AccountedProviderUsage | None = None,
    ) -> None:
        super().__init__(message)
        self.accounted_usage = accounted_usage


class TrustedProviderTransportAuthority(StageReviewArtifactModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    artifact_kind: Literal["trusted-provider-transport-authority"] = (
        "trusted-provider-transport-authority"
    )
    contract_id: str
    contract_version: str
    endpoint_id: str
    workflow_ref: str
    evidence_digest: str
    authority_digest: str

    @model_validator(mode="after")
    def _verify_authority(self) -> Self:
        _require_text(
            self.contract_id,
            self.contract_version,
            self.endpoint_id,
            self.workflow_ref,
            self.evidence_digest,
        )
        if self.authority_digest != _digest(self, "authority_digest"):
            raise ValueError("provider transport authority digest is invalid")
        return self


class ProviderExecutionIdentity(BaseModel):
    """Provider 逻辑身份、执行适配器与真实出口的不可变绑定。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    execution_scope: ProviderExecutionScope
    provider_id: str
    provider_descriptor_digest: str
    equivalence_class_id: str
    model_family: str
    capability_ids: tuple[str, ...]
    recovery_capabilities: ProviderRecoveryCapabilities
    provider_adapter_id: str
    provider_adapter_version: str
    driver_factory_id: str
    driver_factory_version: str
    broker_id: str
    physical_provider_id: str
    physical_equivalence_class_id: str
    identity_digest: str

    @model_validator(mode="after")
    def _verify_identity(self) -> Self:
        _require_text(
            self.provider_id,
            self.provider_descriptor_digest,
            self.equivalence_class_id,
            self.model_family,
            self.provider_adapter_id,
            self.provider_adapter_version,
            self.driver_factory_id,
            self.driver_factory_version,
            self.broker_id,
            self.physical_provider_id,
            self.physical_equivalence_class_id,
        )
        if self.capability_ids != tuple(sorted(set(self.capability_ids))):
            raise ValueError("provider execution capabilities are not canonical")
        if self.identity_digest != _digest(self, "identity_digest"):
            raise ValueError("provider execution identity digest is invalid")
        return self


class TrustedProviderTransportContract(StageReviewArtifactModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    artifact_kind: Literal["trusted-provider-transport-contract"] = (
        "trusted-provider-transport-contract"
    )
    contract_id: str
    contract_version: str
    endpoint_id: str
    transport_kind: Literal["controlled_ipc"] = "controlled_ipc"
    local_reviewer_network_policy: Literal["deny_all"] = "deny_all"
    execution_identity: ProviderExecutionIdentity
    authority_artifact_digest: str
    contract_digest: str

    @model_validator(mode="after")
    def _verify_contract(self) -> Self:
        _require_text(
            self.contract_id,
            self.contract_version,
            self.endpoint_id,
            self.authority_artifact_digest,
        )
        if not self.endpoint_id.startswith("ipc://"):
            raise ValueError("provider transport requires a controlled IPC endpoint")
        if self.execution_identity.provider_id == "":
            raise ValueError("provider execution scope is missing")
        if self.contract_digest != _digest(self, "contract_digest"):
            raise ValueError("provider transport contract digest is invalid")
        return self


class ProviderTransportEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    invocation_id: str
    assignment_digest: str
    provider_id: str
    execution_identity_digest: str
    request_digest: str
    turn_index: int = Field(ge=1)
    idempotency_key: str
    credential_view_digest: str
    backend_epoch: str
    active_wall_clock_limit: float = Field(gt=0)
    payload: dict[str, object]

    @model_validator(mode="after")
    def _verify_envelope(self) -> Self:
        _require_text(
            self.invocation_id,
            self.assignment_digest,
            self.provider_id,
            self.execution_identity_digest,
            self.idempotency_key,
            self.credential_view_digest,
            self.backend_epoch,
        )
        if self.request_digest != provider_payload_digest(self.payload):
            raise ValueError("provider transport request digest is invalid")
        return self


class ProviderEgressPermit(StageReviewArtifactModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    artifact_kind: Literal["provider-egress-permit"] = "provider-egress-permit"
    permit_id: str
    invocation_id: str
    assignment_digest: str
    provider_id: str
    execution_identity: ProviderExecutionIdentity
    request_digest: str
    turn_index: int = Field(ge=1)
    idempotency_key: str
    credential_view_digest: str
    backend_epoch: str
    active_wall_clock_limit: float = Field(gt=0)
    endpoint_id: str
    transport_contract_digest: str
    transport_authority_digest: str
    issued_at: str
    expires_at: str
    nonce: str
    permit_digest: str

    @field_validator("issued_at", "expires_at")
    @classmethod
    def _timestamp_is_utc(cls, value: str) -> str:
        parse_utc(value)
        return value

    @model_validator(mode="after")
    def _verify_permit(self) -> Self:
        _require_text(
            self.permit_id,
            self.invocation_id,
            self.assignment_digest,
            self.provider_id,
            self.request_digest,
            self.endpoint_id,
            self.transport_contract_digest,
            self.transport_authority_digest,
            self.idempotency_key,
            self.credential_view_digest,
            self.backend_epoch,
            self.nonce,
        )
        if parse_utc(self.expires_at) <= parse_utc(self.issued_at):
            raise ValueError("provider egress permit expiry is invalid")
        if self.provider_id != self.execution_identity.provider_id:
            raise ValueError("provider egress permit execution scope is invalid")
        if self.permit_digest != _digest(self, "permit_digest"):
            raise ValueError("provider egress permit digest is invalid")
        return self


class ProviderEgressReceipt(StageReviewArtifactModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    artifact_kind: Literal["provider-egress-receipt"] = "provider-egress-receipt"
    receipt_id: str
    permit_digest: str
    invocation_id: str
    assignment_digest: str
    provider_id: str
    execution_identity: ProviderExecutionIdentity
    request_digest: str
    turn_index: int = Field(ge=1)
    idempotency_key: str
    credential_view_digest: str
    backend_epoch: str
    endpoint_id: str
    transport_contract_digest: str
    transport_authority_digest: str
    response_digest: str
    transport_contract_attested: bool
    remote_provider_exercised: bool
    recorded_at: str
    receipt_digest: str

    @field_validator("recorded_at")
    @classmethod
    def _receipt_timestamp_is_utc(cls, value: str) -> str:
        parse_utc(value)
        return value

    @model_validator(mode="after")
    def _verify_receipt(self) -> Self:
        _require_text(
            self.receipt_id,
            self.permit_digest,
            self.invocation_id,
            self.assignment_digest,
            self.provider_id,
            self.request_digest,
            self.endpoint_id,
            self.transport_contract_digest,
            self.transport_authority_digest,
            self.response_digest,
            self.idempotency_key,
            self.credential_view_digest,
            self.backend_epoch,
        )
        if not self.transport_contract_attested:
            raise ValueError("provider transport contract is not attested")
        if self.provider_id != self.execution_identity.provider_id:
            raise ValueError("provider egress receipt execution scope is invalid")
        if self.receipt_digest != _digest(self, "receipt_digest"):
            raise ValueError("provider egress receipt digest is invalid")
        return self


class ProviderTransportExchangeResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    response: dict[str, object]
    receipt: ProviderEgressReceipt


def _build_transport_contract(
    *,
    contract_id: str,
    contract_version: str,
    endpoint_id: str,
    authority: TrustedProviderTransportAuthority,
    execution_identity: ProviderExecutionIdentity,
) -> TrustedProviderTransportContract:
    trusted = TrustedProviderTransportAuthority.model_validate(
        authority.model_dump(mode="json")
    )
    if (trusted.contract_id, trusted.contract_version, trusted.endpoint_id) != (
        contract_id,
        contract_version,
        endpoint_id,
    ):
        raise ValueError("provider transport authority lineage is invalid")
    values = {
        "contract_id": contract_id,
        "contract_version": contract_version,
        "endpoint_id": endpoint_id,
        "execution_identity": execution_identity,
        "authority_artifact_digest": trusted.authority_digest,
    }
    draft = TrustedProviderTransportContract.model_construct(
        contract_id=contract_id,
        contract_version=contract_version,
        endpoint_id=endpoint_id,
        execution_identity=execution_identity,
        authority_artifact_digest=trusted.authority_digest,
        contract_digest="",
    )
    return TrustedProviderTransportContract.model_validate(
        {**values, "contract_digest": _digest(draft, "contract_digest")}
    )


def _build_provider_execution_identity(
    *,
    execution_scope: ProviderExecutionScope,
    provider_id: str,
    provider_descriptor_digest: str,
    equivalence_class_id: str,
    model_family: str,
    capability_ids: tuple[str, ...],
    recovery_capabilities: ProviderRecoveryCapabilities,
    provider_adapter_id: str,
    provider_adapter_version: str,
    driver_factory_id: str,
    driver_factory_version: str,
    broker_id: str,
    physical_provider_id: str,
    physical_equivalence_class_id: str,
) -> ProviderExecutionIdentity:
    values = locals()
    draft = ProviderExecutionIdentity.model_construct(**values, identity_digest="")
    return ProviderExecutionIdentity.model_validate(
        {**values, "identity_digest": _digest(draft, "identity_digest")}
    )


def _build_transport_authority(
    *,
    contract_id: str,
    contract_version: str,
    endpoint_id: str,
    workflow_ref: str,
    evidence_digest: str,
) -> TrustedProviderTransportAuthority:
    values = {
        "contract_id": contract_id,
        "contract_version": contract_version,
        "endpoint_id": endpoint_id,
        "workflow_ref": workflow_ref,
        "evidence_digest": evidence_digest,
    }
    draft = TrustedProviderTransportAuthority.model_construct(
        contract_id=contract_id,
        contract_version=contract_version,
        endpoint_id=endpoint_id,
        workflow_ref=workflow_ref,
        evidence_digest=evidence_digest,
        authority_digest="",
    )
    return TrustedProviderTransportAuthority.model_validate(
        {**values, "authority_digest": _digest(draft, "authority_digest")}
    )


def provider_payload_digest(payload: dict[str, object]) -> str:
    return canonical_digest(payload, CanonicalizationPolicy())


def _transport_artifact_digest(value: object, field: str) -> str:
    return _digest(value, field)


def _transport_artifact_id(kind: str, *values: str) -> str:
    return stable_id(kind, *values)


def _digest(value: object, field: str) -> str:
    return canonical_digest(
        value,
        CanonicalizationPolicy(excluded_fields=_RUNTIME | {field}),
    )


def _require_text(*values: str) -> None:
    if any(not value.strip() or value != value.strip() for value in values):
        raise ValueError("provider transport identity is invalid")
