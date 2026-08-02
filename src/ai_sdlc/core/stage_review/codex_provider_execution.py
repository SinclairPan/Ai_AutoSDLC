"""Codex Provider 的逻辑身份、物理出口与 Driver 合同。"""

from __future__ import annotations

from typing import Literal

from ai_sdlc.core.stage_review.binding_models import ProviderBindingDescriptor
from ai_sdlc.core.stage_review.provider_journal_models import (
    ProviderRecoveryCapabilities,
)
from ai_sdlc.core.stage_review.provider_route_models import ProviderExecutionRoute
from ai_sdlc.core.stage_review.provider_route_models import (
    _transport_profile_digest as transport_profile_digest,
)
from ai_sdlc.core.stage_review.provider_transport_models import (
    ProviderExecutionIdentity,
    _build_provider_execution_identity,
)
from ai_sdlc.core.stage_review.provider_transport_trust import (
    build_reviewer_execution_identity,
)


def build_codex_execution_identity(
    execution_scope: Literal["optimization_shadow", "reviewer_binding"],
    descriptor: ProviderBindingDescriptor | None,
) -> ProviderExecutionIdentity:
    if execution_scope == "reviewer_binding":
        if descriptor is None:
            raise ValueError("reviewer provider descriptor is required")
        return _reviewer_identity(descriptor)
    else:
        if descriptor is not None:
            raise ValueError("shadow provider cannot claim reviewer descriptor")
        return _shadow_identity()


def codex_reviewer_execution_route() -> ProviderExecutionRoute:
    return ProviderExecutionRoute(
        provider_adapter_id="adapter.openai-codex",
        provider_adapter_version="1.0.0",
        driver_factory_id="driver-factory.remote-review",
        driver_factory_version="1.0.0",
        broker_id="broker.codex-review",
        physical_provider_id="provider.openai-codex",
        physical_equivalence_class_id="provider.openai-codex",
        transport_contract_id="transport.codex-review",
        transport_contract_version="1.0.0",
        transport_endpoint_id="ipc://codex-review/provider",
        transport_workflow_ref="workflow:stage-review",
        transport_profile_digest=transport_profile_digest(
            contract_id="transport.codex-review",
            contract_version="1.0.0",
            endpoint_id="ipc://codex-review/provider",
            workflow_ref="workflow:stage-review",
        ),
    )


def _reviewer_identity(
    descriptor: ProviderBindingDescriptor,
) -> ProviderExecutionIdentity:
    route = codex_reviewer_execution_route()
    compatible = (
        descriptor.execution_route == route,
        descriptor.provider_id.startswith("provider.openai-codex."),
        descriptor.equivalence_class_id == route.physical_equivalence_class_id,
        descriptor.model_family.startswith("model.openai-codex."),
    )
    if not all(compatible):
        raise ValueError("Codex execution route is incompatible")
    return build_reviewer_execution_identity(descriptor)


def _shadow_identity() -> ProviderExecutionIdentity:
    return _build_provider_execution_identity(
        execution_scope="optimization_shadow",
        provider_id="provider.openai-codex.optimization-shadow",
        provider_descriptor_digest="sha256:optimization-shadow-provider",
        equivalence_class_id="provider.openai-codex",
        model_family="model.openai-codex.default",
        capability_ids=(),
        recovery_capabilities=ProviderRecoveryCapabilities(
            idempotency_support=False,
            invocation_query_support=False,
            cost_metering_support=False,
        ),
        provider_adapter_id="adapter.openai-codex",
        provider_adapter_version="1.0.0",
        driver_factory_id="driver-factory.codex-optimization-shadow",
        driver_factory_version="1.0.0",
        broker_id="broker.codex-review",
        physical_provider_id="provider.openai-codex",
        physical_equivalence_class_id="provider.openai-codex",
    )


__all__ = ["build_codex_execution_identity", "codex_reviewer_execution_route"]
