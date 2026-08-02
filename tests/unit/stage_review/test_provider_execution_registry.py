from __future__ import annotations

from pathlib import Path

import pytest

from ai_sdlc.core.stage_review import provider_execution_registry
from ai_sdlc.core.stage_review.binding_models import ReviewerRuntimeAllocation
from ai_sdlc.core.stage_review.binding_result_models import ReviewerDispatchAssignment
from ai_sdlc.core.stage_review.bindings import build_provider_binding_descriptor
from ai_sdlc.core.stage_review.codex_provider_execution import (
    codex_reviewer_execution_route,
)
from ai_sdlc.core.stage_review.provider_execution_registry import (
    ProviderExecutionAdapterRegistry,
    ProviderExecutionUnavailableError,
)
from ai_sdlc.core.stage_review.provider_journal_models import (
    ProviderInvocationRequest,
    ProviderRecoveryCapabilities,
)
from ai_sdlc.core.stage_review.provider_transport import TrustedProviderTransport
from ai_sdlc.core.stage_review.provider_transport_models import (
    _build_provider_execution_identity,
)
from ai_sdlc.core.stage_review.provider_transport_models import (
    _build_transport_authority as build_transport_authority,
)
from ai_sdlc.core.stage_review.provider_transport_models import (
    _build_transport_contract as build_transport_contract,
)


class _Broker:
    remote_provider_exercised = True

    def __init__(self) -> None:
        self.calls = 0

    def exchange(self, permit, envelope):
        self.calls += 1
        return {"status": "must-not-run"}


def test_registry_rejects_unregistered_non_codex_before_broker(tmp_path: Path) -> None:
    descriptor = _descriptor()
    broker = _Broker()
    registry = ProviderExecutionAdapterRegistry()
    registry.register_reviewer(
        descriptor,
        _transport(tmp_path, descriptor, broker),
    )
    frozen = registry.freeze()
    request = ProviderInvocationRequest.model_construct(
        provider_id="provider.unregistered.non-codex",
        authorization_scope="reviewer_binding",
    )

    with pytest.raises(
        ProviderExecutionUnavailableError,
        match="not registered",
    ):
        frozen.resolve_reviewer(request, object(), object())  # type: ignore[arg-type]

    assert broker.calls == 0


def test_registry_resolves_complete_reviewer_binding(tmp_path: Path) -> None:
    descriptor = _descriptor()
    broker = _Broker()
    transport = _transport(tmp_path, descriptor, broker)
    registry = ProviderExecutionAdapterRegistry()
    registry.register_reviewer(descriptor, transport)
    frozen = registry.freeze()
    assignment = ReviewerDispatchAssignment.model_construct(
        assignment_digest="reviewer-assignment:sha256:registered",
        provider_id=descriptor.provider_id,
        provider_descriptor_digest=descriptor.descriptor_digest,
        provider_execution_identity_digest=(
            transport.contract.execution_identity.identity_digest
        ),
        physical_provider_id=descriptor.execution_route.physical_provider_id,
        physical_equivalence_class_id=(
            descriptor.execution_route.physical_equivalence_class_id
        ),
        transport_profile_digest=descriptor.execution_route.transport_profile_digest,
        transport_contract_digest=transport.contract.contract_digest,
        transport_authority_digest=transport.contract.authority_artifact_digest,
        model_family=descriptor.model_family,
        recovery_capabilities=descriptor.recovery_capabilities,
    )
    allocation = ReviewerRuntimeAllocation.model_construct(
        provider_id=descriptor.provider_id,
        provider_descriptor_digest=descriptor.descriptor_digest,
        equivalence_class_id=descriptor.equivalence_class_id,
        physical_provider_id=descriptor.execution_route.physical_provider_id,
        physical_equivalence_class_id=(
            descriptor.execution_route.physical_equivalence_class_id
        ),
        model_family=descriptor.model_family,
    )
    request = ProviderInvocationRequest.model_construct(
        assignment_digest=assignment.assignment_digest,
        authorization_scope="reviewer_binding",
        provider_id=descriptor.provider_id,
        capabilities=descriptor.recovery_capabilities,
    )

    resolved = frozen.resolve_reviewer(request, assignment, allocation)

    assert resolved.transport is transport
    assert resolved.identity.physical_provider_id == "provider.openai-codex"
    assert frozen.registry_digest.startswith("sha256:")
    assert broker.calls == 0


def test_registry_rejects_transport_outside_descriptor_trust_profile(
    tmp_path: Path,
) -> None:
    descriptor = _descriptor()
    broker = _Broker()
    registry = ProviderExecutionAdapterRegistry()

    with pytest.raises(
        ProviderExecutionUnavailableError,
        match="registration diverged",
    ):
        registry.register_reviewer(
            descriptor,
            _transport(
                tmp_path,
                descriptor,
                broker,
                authority_evidence_digest="sha256:untrusted-authority",
            ),
        )

    assert broker.calls == 0


def test_registry_matches_semantic_contract_not_runtime_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    descriptor = _descriptor()
    transport = _transport(tmp_path, descriptor, _Broker())
    reconstructed = transport.contract.model_copy(
        update={"created_at": "2099-01-01T00:00:00Z"}
    )
    monkeypatch.setattr(
        provider_execution_registry,
        "_reviewer_transport_contract",
        lambda _descriptor: reconstructed,
    )
    registry = ProviderExecutionAdapterRegistry()

    registry.register_reviewer(descriptor, transport)

    assert registry.freeze().registry_digest.startswith("sha256:")


def _descriptor():
    recovery = ProviderRecoveryCapabilities(
        idempotency_support=False,
        invocation_query_support=False,
        cost_metering_support=False,
    )
    return build_provider_binding_descriptor(
        descriptor_id="descriptor.codex.registered",
        provider_id="provider.openai-codex.registered",
        equivalence_class_id="provider.openai-codex",
        model_family="model.openai-codex.default",
        role_contract_digests=("sha256:role",),
        capability_ids=("capability.correctness",),
        provider_tags=("provider.remote",),
        tool_allowlist=(),
        recovery_capabilities=recovery,
        execution_route=codex_reviewer_execution_route(),
        isolation_backend="codex.permission-profile",
        network_enforcement=True,
        supported_independence_grade="session_independent",
        provider_policy_evidence_digest="sha256:provider-policy",
    )


def _transport(
    root: Path,
    descriptor,
    broker: _Broker,
    *,
    authority_evidence_digest: str = "sha256:provider-policy",
) -> TrustedProviderTransport:
    authority = build_transport_authority(
        contract_id="transport.codex-review",
        contract_version="1.0.0",
        endpoint_id=descriptor.execution_route.transport_endpoint_id,
        workflow_ref=descriptor.execution_route.transport_workflow_ref,
        evidence_digest=authority_evidence_digest,
    )
    identity = _build_provider_execution_identity(
        execution_scope="reviewer_binding",
        provider_id=descriptor.provider_id,
        provider_descriptor_digest=descriptor.descriptor_digest,
        equivalence_class_id=descriptor.equivalence_class_id,
        model_family=descriptor.model_family,
        capability_ids=descriptor.capability_ids,
        recovery_capabilities=descriptor.recovery_capabilities,
        provider_adapter_id="adapter.openai-codex",
        provider_adapter_version="1.0.0",
        driver_factory_id="driver-factory.remote-review",
        driver_factory_version="1.0.0",
        broker_id="broker.codex-review",
        physical_provider_id="provider.openai-codex",
        physical_equivalence_class_id="provider.openai-codex",
    )
    contract = build_transport_contract(
        contract_id=authority.contract_id,
        contract_version=authority.contract_version,
        endpoint_id=authority.endpoint_id,
        authority=authority,
        execution_identity=identity,
    )
    return TrustedProviderTransport(
        root,
        contract,
        project_id="project.provider-registry",
        broker=broker,
        authority=authority,
    )
