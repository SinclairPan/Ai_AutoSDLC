from __future__ import annotations

from pathlib import Path

import pytest

from ai_sdlc.core.stage_review.provider_journal_models import (
    ProviderRecoveryCapabilities,
)
from ai_sdlc.core.stage_review.provider_transport_models import (
    _build_provider_execution_identity,
)


def test_controlled_ipc_transport_persists_permit_and_attested_receipt(
    tmp_path: Path,
) -> None:
    from ai_sdlc.core.stage_review.certificate_receipt_store import (
        FilesystemReviewReceiptArtifactStore,
    )
    from ai_sdlc.core.stage_review.provider_transport import (
        ControlledEndpointBroker,
        TrustedProviderTransport,
    )
    from ai_sdlc.core.stage_review.provider_transport_models import (
        ProviderTransportEnvelope,
        provider_payload_digest,
    )
    from ai_sdlc.core.stage_review.provider_transport_models import (
        _build_transport_authority as build_transport_authority,
    )
    from ai_sdlc.core.stage_review.provider_transport_models import (
        _build_transport_contract as build_transport_contract,
    )

    authority = build_transport_authority(
        contract_id="transport.t601.local",
        contract_version="1",
        endpoint_id="ipc://t601/provider",
        workflow_ref="workflow:test",
        evidence_digest="sha256:transport-attestation",
    )
    contract = build_transport_contract(
        contract_id="transport.t601.local",
        contract_version="1",
        endpoint_id="ipc://t601/provider",
        authority=authority,
        execution_identity=_identity("provider.remote"),
    )
    calls = []
    broker = ControlledEndpointBroker(
        contract,
        {
            contract.endpoint_id: lambda envelope: (
                calls.append(envelope) or {"status": "contract-ok"}
            )
        },
        authority=authority,
    )
    transport = TrustedProviderTransport(
        tmp_path,
        contract,
        project_id="project.transport",
        broker=broker,
        authority=authority,
    )
    payload = {"prompt": "review"}
    envelope = ProviderTransportEnvelope(
        invocation_id="provider-invocation.123",
        assignment_digest="reviewer-assignment:sha256:123",
        provider_id="provider.remote",
        execution_identity_digest=_identity("provider.remote").identity_digest,
        request_digest=provider_payload_digest(payload),
        turn_index=1,
        idempotency_key="turn-1",
        credential_view_digest="sha256:credential-view",
        backend_epoch="backend-epoch-1",
        active_wall_clock_limit=30,
        payload=payload,
    )

    result = transport.exchange(envelope)

    assert result.response == {"status": "contract-ok"}
    assert calls == [envelope]
    receipt = result.receipt
    assert transport.receipts() == (receipt,)
    assert receipt.transport_contract_attested is True
    assert receipt.remote_provider_exercised is False
    assert receipt.endpoint_id == "ipc://t601/provider"
    assert receipt.transport_authority_digest == authority.authority_digest
    assert receipt.execution_identity.physical_provider_id == "provider.remote"
    assert receipt.execution_identity.provider_adapter_id == "adapter.provider.remote"
    assert receipt.execution_identity.driver_factory_id == "driver-factory.test"
    assert receipt.execution_identity.broker_id == "broker.test"
    assert transport.permits()[0].permit_digest == receipt.permit_digest
    artifact_store = FilesystemReviewReceiptArtifactStore(
        tmp_path,
        project_id="project.transport",
    )
    assert artifact_store.resolve_egress_receipt(receipt.receipt_digest) == receipt
    assert (
        artifact_store.resolve_egress_permit(receipt.permit_digest)
        == (transport.permits()[0])
    )
    assert artifact_store.resolve_response(receipt.response_digest) == result.response


def test_transport_rejects_unregistered_provider_alias_before_broker(
    tmp_path: Path,
) -> None:
    from ai_sdlc.core.stage_review.provider_transport import (
        TrustedEgressUnavailable,
        TrustedProviderTransport,
    )
    from ai_sdlc.core.stage_review.provider_transport_models import (
        ProviderTransportEnvelope,
        provider_payload_digest,
    )
    from ai_sdlc.core.stage_review.provider_transport_models import (
        _build_transport_authority as build_transport_authority,
    )
    from ai_sdlc.core.stage_review.provider_transport_models import (
        _build_transport_contract as build_transport_contract,
    )

    authority = build_transport_authority(
        contract_id="transport.codex-review",
        contract_version="1.0.0",
        endpoint_id="ipc://codex-review/provider",
        workflow_ref="workflow:stage-review",
        evidence_digest="sha256:transport-authority",
    )
    contract = build_transport_contract(
        contract_id=authority.contract_id,
        contract_version=authority.contract_version,
        endpoint_id=authority.endpoint_id,
        authority=authority,
        execution_identity=_identity("provider.openai-codex"),
    )
    calls: list[str] = []

    class Broker:
        remote_provider_exercised = True

        def exchange(self, permit, envelope):
            calls.append(envelope.provider_id)
            return {"status": "must-not-run"}

    transport = TrustedProviderTransport(
        tmp_path,
        contract,
        project_id="project.transport-alias",
        broker=Broker(),
        authority=authority,
    )
    payload = {"prompt": "review"}
    envelope = ProviderTransportEnvelope(
        invocation_id="provider-invocation.alias",
        assignment_digest="reviewer-assignment:sha256:alias",
        provider_id="provider.unregistered.non-codex",
        execution_identity_digest=_identity("provider.openai-codex").identity_digest,
        request_digest=provider_payload_digest(payload),
        turn_index=1,
        idempotency_key="turn-alias",
        credential_view_digest="sha256:credential-view",
        backend_epoch="backend-epoch-1",
        active_wall_clock_limit=30,
        payload=payload,
    )

    with pytest.raises(TrustedEgressUnavailable, match="provider execution scope"):
        transport.exchange(envelope)

    assert calls == []
    assert transport.permits() == ()


def test_transport_rejects_network_endpoint_and_missing_broker(tmp_path: Path) -> None:
    from ai_sdlc.core.stage_review.provider_transport import (
        TrustedEgressUnavailable,
        TrustedProviderTransport,
    )
    from ai_sdlc.core.stage_review.provider_transport_models import (
        ProviderTransportEnvelope,
        provider_payload_digest,
    )
    from ai_sdlc.core.stage_review.provider_transport_models import (
        _build_transport_authority as build_transport_authority,
    )
    from ai_sdlc.core.stage_review.provider_transport_models import (
        _build_transport_contract as build_transport_contract,
    )

    invalid_authority = build_transport_authority(
        contract_id="transport.network",
        contract_version="1",
        endpoint_id="https://provider.invalid",
        workflow_ref="workflow:test",
        evidence_digest="sha256:attestation",
    )
    with pytest.raises(ValueError, match="controlled IPC"):
        build_transport_contract(
            contract_id="transport.network",
            contract_version="1",
            endpoint_id="https://provider.invalid",
            authority=invalid_authority,
            execution_identity=_identity("provider.remote"),
        )
    authority = build_transport_authority(
        contract_id="transport.t601.local",
        contract_version="1",
        endpoint_id="ipc://t601/provider",
        workflow_ref="workflow:test",
        evidence_digest="sha256:transport-attestation",
    )
    contract = build_transport_contract(
        contract_id="transport.t601.local",
        contract_version="1",
        endpoint_id="ipc://t601/provider",
        authority=authority,
        execution_identity=_identity("provider.remote"),
    )
    transport = TrustedProviderTransport(
        tmp_path,
        contract,
        project_id="project.transport",
        broker=None,
        authority=authority,
    )

    with pytest.raises(TrustedEgressUnavailable):
        payload = {"prompt": "review"}
        transport.exchange(
            ProviderTransportEnvelope(
                invocation_id="provider-invocation.123",
                assignment_digest="reviewer-assignment:sha256:123",
                provider_id="provider.remote",
                execution_identity_digest=_identity("provider.remote").identity_digest,
                request_digest=provider_payload_digest(payload),
                turn_index=1,
                idempotency_key="turn-1",
                credential_view_digest="sha256:credential-view",
                backend_epoch="backend-epoch-1",
                active_wall_clock_limit=30,
                payload=payload,
            )
        )

    assert transport.permits() == transport.receipts() == ()


def test_attested_remote_broker_marks_real_provider_exercise(tmp_path: Path) -> None:
    from ai_sdlc.core.stage_review.provider_transport import TrustedProviderTransport
    from ai_sdlc.core.stage_review.provider_transport_models import (
        ProviderTransportEnvelope,
        provider_payload_digest,
    )
    from ai_sdlc.core.stage_review.provider_transport_models import (
        _build_transport_authority as build_transport_authority,
    )
    from ai_sdlc.core.stage_review.provider_transport_models import (
        _build_transport_contract as build_transport_contract,
    )

    authority = build_transport_authority(
        contract_id="transport.t602.remote",
        contract_version="1",
        endpoint_id="ipc://t602/remote-provider",
        workflow_ref="workflow:test-remote",
        evidence_digest="sha256:remote-transport-attestation",
    )
    contract = build_transport_contract(
        contract_id=authority.contract_id,
        contract_version=authority.contract_version,
        endpoint_id=authority.endpoint_id,
        authority=authority,
        execution_identity=_identity("provider.remote"),
    )
    broker = _RemoteBroker()
    transport = TrustedProviderTransport(
        tmp_path,
        contract,
        project_id="project.transport",
        broker=broker,
        authority=authority,
    )
    payload = {"prompt": "review"}
    envelope = ProviderTransportEnvelope(
        invocation_id="provider-invocation.remote",
        assignment_digest="sha256:" + "1" * 64,
        provider_id="provider.remote",
        execution_identity_digest=_identity("provider.remote").identity_digest,
        request_digest=provider_payload_digest(payload),
        turn_index=1,
        idempotency_key="remote-turn-1",
        credential_view_digest="sha256:" + "2" * 64,
        backend_epoch="remote-backend-1",
        active_wall_clock_limit=30,
        payload=payload,
    )

    result = transport.exchange(envelope)

    assert transport.remote_provider_available is True
    assert result.receipt.remote_provider_exercised is True
    assert broker.calls == 1


class _RemoteBroker:
    remote_provider_exercised = True

    def __init__(self) -> None:
        self.calls = 0

    def exchange(self, permit, envelope):
        del permit, envelope
        self.calls += 1
        return {"decision": "PASS"}


def test_execution_gate_blocks_trusted_egress_before_local_command() -> None:
    from ai_sdlc.core.stage_review.reviewer_execution_gate import (
        ReviewerExecutionGate,
    )

    class Driver:
        provider_id = "provider.remote"
        capabilities = object()
        command_count = 0

        def invoke(self, request):
            raise AssertionError("remote provider cannot use the local command path")

    gate = ReviewerExecutionGate(
        authorize=lambda request, now: True,
        prepare_isolated_driver=lambda request, driver, now: driver,
        requires_reviewer_gate=lambda request: True,
        trusted_egress_provider_ids=("provider.remote",),
        trusted_transport=None,
    )
    request = type("Request", (), {"provider_id": "provider.remote"})()
    driver = Driver()

    prepared, result = gate.prepare(request, driver, None)

    assert prepared is None
    assert result == "needs_user"
    assert driver.command_count == 0


def test_transport_envelope_rejects_payload_digest_replay() -> None:
    from ai_sdlc.core.stage_review.provider_transport_models import (
        ProviderTransportEnvelope,
        provider_payload_digest,
    )

    with pytest.raises(ValueError, match="request digest"):
        ProviderTransportEnvelope(
            invocation_id="provider-invocation.123",
            assignment_digest="reviewer-assignment:sha256:123",
            provider_id="provider.remote",
            execution_identity_digest=_identity("provider.remote").identity_digest,
            request_digest=provider_payload_digest({"prompt": "original"}),
            turn_index=1,
            idempotency_key="turn-1",
            credential_view_digest="sha256:credential-view",
            backend_epoch="backend-epoch-1",
            active_wall_clock_limit=30,
            payload={"prompt": "tampered"},
        )


def test_transport_crash_leaves_consumed_permit_and_blocks_retry(
    tmp_path: Path,
) -> None:
    from ai_sdlc.core.stage_review.certificate_receipt_store import (
        FilesystemReviewReceiptArtifactStore,
        ReceiptArtifactError,
    )
    from ai_sdlc.core.stage_review.provider_transport import (
        ControlledEndpointBroker,
        TrustedEgressUnavailable,
        TrustedProviderTransport,
    )
    from ai_sdlc.core.stage_review.provider_transport_models import (
        ProviderTransportEnvelope,
        provider_payload_digest,
    )
    from ai_sdlc.core.stage_review.provider_transport_models import (
        _build_transport_authority as build_transport_authority,
    )
    from ai_sdlc.core.stage_review.provider_transport_models import (
        _build_transport_contract as build_transport_contract,
    )

    authority = build_transport_authority(
        contract_id="transport.t601.crash",
        contract_version="1",
        endpoint_id="ipc://t601/crash",
        workflow_ref="workflow:test",
        evidence_digest="sha256:transport-attestation",
    )
    contract = build_transport_contract(
        contract_id="transport.t601.crash",
        contract_version="1",
        endpoint_id="ipc://t601/crash",
        authority=authority,
        execution_identity=_identity("provider.remote"),
    )

    def crash(envelope):
        raise RuntimeError("broker crashed")

    broker = ControlledEndpointBroker(
        contract,
        {contract.endpoint_id: crash},
        authority=authority,
    )
    transport = TrustedProviderTransport(
        tmp_path,
        contract,
        project_id="project.transport",
        broker=broker,
        authority=authority,
    )
    payload = {"prompt": "review"}
    envelope = ProviderTransportEnvelope(
        invocation_id="provider-invocation.123",
        assignment_digest="reviewer-assignment:sha256:123",
        provider_id="provider.remote",
        execution_identity_digest=_identity("provider.remote").identity_digest,
        request_digest=provider_payload_digest(payload),
        turn_index=1,
        idempotency_key="turn-1",
        credential_view_digest="sha256:credential-view",
        backend_epoch="backend-epoch-1",
        active_wall_clock_limit=30,
        payload=payload,
    )
    with pytest.raises(TrustedEgressUnavailable, match="closed early|failed"):
        transport.exchange(envelope)
    with pytest.raises(TrustedEgressUnavailable, match="recovery"):
        transport.exchange(envelope)

    assert len(transport.permits()) == 1
    assert transport.receipts() == ()
    artifact_store = FilesystemReviewReceiptArtifactStore(
        tmp_path,
        project_id="project.transport",
    )
    permit = transport.permits()[0]
    assert artifact_store.resolve_egress_permit(permit.permit_digest) == permit
    with pytest.raises(ReceiptArtifactError):
        artifact_store.resolve_egress_receipt("sha256:unfinished")


def _identity(provider_id: str):
    return _build_provider_execution_identity(
        execution_scope="generic",
        provider_id=provider_id,
        provider_descriptor_digest=f"sha256:descriptor.{provider_id}",
        equivalence_class_id=f"class.{provider_id}",
        model_family=f"model.{provider_id}",
        capability_ids=(),
        recovery_capabilities=ProviderRecoveryCapabilities(
            idempotency_support=False,
            invocation_query_support=False,
            cost_metering_support=False,
        ),
        provider_adapter_id=f"adapter.{provider_id}",
        provider_adapter_version="1.0.0",
        driver_factory_id="driver-factory.test",
        driver_factory_version="1.0.0",
        broker_id="broker.test",
        physical_provider_id=provider_id,
        physical_equivalence_class_id=f"class.{provider_id}",
    )
