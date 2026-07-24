"""从受信 Provider 描述符确定性派生传输 authority 与 contract。"""

from __future__ import annotations

from ai_sdlc.core.stage_review.binding_models import ProviderBindingDescriptor
from ai_sdlc.core.stage_review.provider_transport_models import (
    ProviderExecutionIdentity,
    TrustedProviderTransportAuthority,
    TrustedProviderTransportContract,
    _build_provider_execution_identity,
    _build_transport_authority,
    _build_transport_contract,
)


def build_reviewer_execution_identity(
    descriptor: ProviderBindingDescriptor,
) -> ProviderExecutionIdentity:
    route = descriptor.execution_route
    return _build_provider_execution_identity(
        execution_scope="reviewer_binding",
        provider_id=descriptor.provider_id,
        provider_descriptor_digest=descriptor.descriptor_digest,
        equivalence_class_id=descriptor.equivalence_class_id,
        model_family=descriptor.model_family,
        capability_ids=descriptor.capability_ids,
        recovery_capabilities=descriptor.recovery_capabilities,
        provider_adapter_id=route.provider_adapter_id,
        provider_adapter_version=route.provider_adapter_version,
        driver_factory_id=route.driver_factory_id,
        driver_factory_version=route.driver_factory_version,
        broker_id=route.broker_id,
        physical_provider_id=route.physical_provider_id,
        physical_equivalence_class_id=route.physical_equivalence_class_id,
    )


def _reviewer_transport_authority(
    descriptor: ProviderBindingDescriptor,
) -> TrustedProviderTransportAuthority:
    route = descriptor.execution_route
    return _build_transport_authority(
        contract_id=route.transport_contract_id,
        contract_version=route.transport_contract_version,
        endpoint_id=route.transport_endpoint_id,
        workflow_ref=route.transport_workflow_ref,
        evidence_digest=descriptor.provider_policy_evidence_digest,
    )


def _reviewer_transport_contract(
    descriptor: ProviderBindingDescriptor,
) -> TrustedProviderTransportContract:
    authority = _reviewer_transport_authority(descriptor)
    return _build_transport_contract(
        contract_id=authority.contract_id,
        contract_version=authority.contract_version,
        endpoint_id=authority.endpoint_id,
        authority=authority,
        execution_identity=build_reviewer_execution_identity(descriptor),
    )


__all__ = ["build_reviewer_execution_identity"]
