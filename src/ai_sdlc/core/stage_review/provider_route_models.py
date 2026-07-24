"""Provider 描述符声明的不可变物理执行路由。"""

from __future__ import annotations

from typing import Self

from pydantic import BaseModel, ConfigDict, model_validator

from ai_sdlc.core.stage_review.canonical import CanonicalizationPolicy, canonical_digest


class ProviderExecutionRoute(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider_adapter_id: str
    provider_adapter_version: str
    driver_factory_id: str
    driver_factory_version: str
    broker_id: str
    physical_provider_id: str
    physical_equivalence_class_id: str
    transport_contract_id: str
    transport_contract_version: str
    transport_endpoint_id: str
    transport_workflow_ref: str
    transport_profile_digest: str

    @model_validator(mode="after")
    def _verify_route(self) -> Self:
        values = tuple(str(value) for value in self.model_dump().values())
        if any(not value.strip() or value != value.strip() for value in values):
            raise ValueError("provider execution route is incomplete")
        if self.transport_profile_digest != _transport_profile_digest(
            contract_id=self.transport_contract_id,
            contract_version=self.transport_contract_version,
            endpoint_id=self.transport_endpoint_id,
            workflow_ref=self.transport_workflow_ref,
        ):
            raise ValueError("provider transport profile digest is invalid")
        return self


def _transport_profile_digest(
    *,
    contract_id: str,
    contract_version: str,
    endpoint_id: str,
    workflow_ref: str,
) -> str:
    return canonical_digest(
        {
            "contract_id": contract_id,
            "contract_version": contract_version,
            "endpoint_id": endpoint_id,
            "workflow_ref": workflow_ref,
        },
        CanonicalizationPolicy(),
    )


def _unavailable_provider_execution_route(
    provider_id: str,
    equivalence_class_id: str,
) -> ProviderExecutionRoute:
    """为无 Adapter 的描述符冻结显式不可执行路由。"""
    return ProviderExecutionRoute(
        provider_adapter_id="adapter.unavailable",
        provider_adapter_version="1.0.0",
        driver_factory_id="driver-factory.unavailable",
        driver_factory_version="1.0.0",
        broker_id="broker.unavailable",
        physical_provider_id=provider_id,
        physical_equivalence_class_id=equivalence_class_id,
        transport_contract_id="transport.unavailable",
        transport_contract_version="1.0.0",
        transport_endpoint_id="ipc://unavailable/provider",
        transport_workflow_ref="workflow:unavailable",
        transport_profile_digest=_transport_profile_digest(
            contract_id="transport.unavailable",
            contract_version="1.0.0",
            endpoint_id="ipc://unavailable/provider",
            workflow_ref="workflow:unavailable",
        ),
    )


__all__ = ["ProviderExecutionRoute"]
