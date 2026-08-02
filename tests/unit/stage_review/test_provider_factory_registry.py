from __future__ import annotations

import pytest

from ai_sdlc.core.stage_review.bindings import build_provider_binding_descriptor
from ai_sdlc.core.stage_review.codex_provider_execution import (
    build_codex_execution_identity,
    codex_reviewer_execution_route,
)
from ai_sdlc.core.stage_review.provider_execution_registry import (
    ProviderAdapterFactoryRegistry,
    ProviderExecutionUnavailableError,
    _build_reviewer_execution_registry,
)
from ai_sdlc.core.stage_review.provider_journal_models import (
    ProviderRecoveryCapabilities,
)
from ai_sdlc.core.stage_review.provider_route_models import ProviderExecutionRoute
from ai_sdlc.core.stage_review.provider_route_models import (
    _transport_profile_digest as transport_profile_digest,
)


def test_composition_rejects_unknown_provider_before_factory_or_broker() -> None:
    calls = 0
    factories = ProviderAdapterFactoryRegistry()

    def codex_factory(descriptor):
        nonlocal calls
        calls += 1
        raise AssertionError("Codex factory must not receive an external Provider")

    factories.register_reviewer(
        codex_reviewer_execution_route(),
        provider_id_prefixes=("provider.openai-codex.",),
        model_family_prefixes=("model.openai-codex.",),
        factory=codex_factory,
    )

    with pytest.raises(
        ProviderExecutionUnavailableError,
        match="adapter factory is not registered",
    ):
        _build_reviewer_execution_registry(
            (_descriptor(_external_route()),),
            factories.freeze(),
        )

    assert calls == 0


def test_codex_identity_rejects_non_codex_descriptor() -> None:
    descriptor = _descriptor(_external_route())

    with pytest.raises(ValueError, match="Codex execution route is incompatible"):
        build_codex_execution_identity("reviewer_binding", descriptor)


def test_provider_route_rejects_forged_transport_profile() -> None:
    payload = _external_route().model_dump(mode="json")
    payload["transport_profile_digest"] = "sha256:forged-transport-profile"

    with pytest.raises(ValueError, match="transport profile digest"):
        ProviderExecutionRoute.model_validate(payload)


def _descriptor(route: ProviderExecutionRoute):
    return build_provider_binding_descriptor(
        descriptor_id="descriptor.future-external",
        provider_id="provider.future-external",
        equivalence_class_id="provider.future-external",
        model_family="model.future-external.default",
        role_contract_digests=("sha256:role",),
        capability_ids=("capability.correctness",),
        provider_tags=("provider.remote",),
        tool_allowlist=(),
        recovery_capabilities=ProviderRecoveryCapabilities(
            idempotency_support=False,
            invocation_query_support=False,
            cost_metering_support=False,
        ),
        execution_route=route,
        isolation_backend="codex.permission-profile",
        network_enforcement=True,
        supported_independence_grade="model_diversity_proven",
        provider_policy_evidence_digest="sha256:provider-policy",
    )


def _external_route() -> ProviderExecutionRoute:
    return ProviderExecutionRoute(
        provider_adapter_id="adapter.future-external",
        provider_adapter_version="1.0.0",
        driver_factory_id="driver-factory.future-external",
        driver_factory_version="1.0.0",
        broker_id="broker.future-external",
        physical_provider_id="provider.future-external",
        physical_equivalence_class_id="provider.future-external",
        transport_contract_id="transport.future-external",
        transport_contract_version="1.0.0",
        transport_endpoint_id="ipc://future-external/provider",
        transport_workflow_ref="workflow:future-external",
        transport_profile_digest=transport_profile_digest(
            contract_id="transport.future-external",
            contract_version="1.0.0",
            endpoint_id="ipc://future-external/provider",
            workflow_ref="workflow:future-external",
        ),
    )
