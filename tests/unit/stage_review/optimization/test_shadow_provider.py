from __future__ import annotations

from pathlib import Path

import pytest

from ai_sdlc.core.stage_review.optimization.shadow_provider import (
    CodexOptimizationShadowDriver,
    shadow_provider_spec,
    validate_shadow_provider_output,
)
from ai_sdlc.core.stage_review.provider_journal import (
    build_provider_invocation_request,
)
from ai_sdlc.core.stage_review.provider_journal_driver import ProviderDriverRefused
from ai_sdlc.core.stage_review.provider_transport import TrustedProviderTransport
from ai_sdlc.core.stage_review.provider_transport_models import (
    ProviderEgressPermit,
    ProviderTransportEnvelope,
    _build_provider_execution_identity,
)
from ai_sdlc.core.stage_review.provider_transport_models import (
    _build_transport_authority as build_transport_authority,
)
from ai_sdlc.core.stage_review.provider_transport_models import (
    _build_transport_contract as build_transport_contract,
)
from ai_sdlc.core.stage_review.provider_usage_models import metered_provider_usage
from ai_sdlc.core.stage_review.resource_models import ResourceAmounts


class _RemoteShadowBroker:
    remote_provider_exercised = True

    def __init__(self) -> None:
        self.permit: ProviderEgressPermit | None = None
        self.envelope: ProviderTransportEnvelope | None = None

    def exchange(
        self,
        permit: ProviderEgressPermit,
        envelope: ProviderTransportEnvelope,
    ) -> dict[str, object]:
        self.permit = permit
        self.envelope = envelope
        return {
            "provider_call_id": "call.shadow-001",
            "review": {
                "verdict": "passed",
                "coverage": {
                    "reviewed_area_ids": ["capability.correctness"],
                    "uncovered_area_ids": [],
                    "evidence_gap_ids": [],
                },
                "findings": [],
                "evidence_digests": ["sha256:shadow-evidence"],
            },
            "accounted_usage": metered_provider_usage(_actual_usage()).model_dump(
                mode="json"
            ),
        }


def test_shadow_driver_uses_trusted_egress_and_binds_receipt(tmp_path: Path) -> None:
    payload: dict[str, object] = {
        "schema": "optimization-shadow-review-provider-request.v1",
        "instructions": "review frozen challenger",
    }
    spec = shadow_provider_spec(payload)
    request = _request(spec, payload)
    broker = _RemoteShadowBroker()
    transport = _transport(tmp_path, request.project_id, broker)

    submission = CodexOptimizationShadowDriver(
        payload=payload,
        executions=_executions(transport),
    ).invoke(request)

    assert submission.provider_call_id == "call.shadow-001"
    assert len(submission.egress_receipt_digests) == 1
    assert submission.execution_evidence_root_digest
    assert validate_shadow_provider_output(submission).startswith("sha256:")
    assert transport.receipts()[0].remote_provider_exercised is True
    assert broker.envelope is not None
    assert broker.permit is not None
    assert broker.envelope.active_wall_clock_limit == 150
    assert broker.permit.active_wall_clock_limit == 150


def test_shadow_invalid_output_preserves_usage_and_egress_outcome(
    tmp_path: Path,
) -> None:
    payload: dict[str, object] = {
        "schema": "optimization-shadow-review-provider-request.v1",
        "instructions": "review frozen challenger",
    }
    spec = shadow_provider_spec(payload)
    request = _request(spec, payload)
    broker = _RemoteShadowBroker()
    original = broker.exchange

    def invalid_exchange(permit, envelope):
        response = original(permit, envelope)
        response["review"]["evidence_digests"] = []
        return response

    broker.exchange = invalid_exchange  # type: ignore[method-assign]
    driver = CodexOptimizationShadowDriver(
        payload=payload,
        executions=_executions(_transport(tmp_path, request.project_id, broker)),
    )

    with pytest.raises(ProviderDriverRefused) as captured:
        driver.invoke(request)

    assert captured.value.accounted_usage is not None
    assert len(captured.value.outcome.egress_receipt_digests) == 1
    assert captured.value.outcome.execution_evidence_root_digest


def _request(spec, payload: dict[str, object]):
    return build_provider_invocation_request(
        project_id="project.shadow-provider",
        work_item_id="001-shadow",
        stage_review_session_id="session.shadow-provider",
        owner_scope_id="optimization-epoch.shadow-provider",
        candidate_digest="sha256:candidate",
        assignment_digest="sha256:assignment",
        authorization_scope="optimization_shadow",
        epoch_id="optimization-epoch.shadow-provider",
        provider_id=spec.provider_id,
        request_digest=spec.request_digest,
        reservation_id="reservation.shadow-provider",
        expected_reservation_digest="sha256:reservation",
        expected_fencing_token=1,
        anticipated_usage=spec.anticipated_usage,
        capabilities=spec.capabilities,
        command_id="command.shadow-provider",
        idempotency_key="idempotency.shadow-provider",
    )


def _transport(
    root: Path,
    project_id: str,
    broker: _RemoteShadowBroker,
) -> TrustedProviderTransport:
    spec = shadow_provider_spec({})
    authority = build_transport_authority(
        contract_id="transport.codex-shadow",
        contract_version="1.0.0",
        endpoint_id="ipc://codex-shadow",
        workflow_ref="workflow.optimization-shadow",
        evidence_digest="sha256:transport-authority",
    )
    contract = build_transport_contract(
        contract_id=authority.contract_id,
        contract_version=authority.contract_version,
        endpoint_id=authority.endpoint_id,
        authority=authority,
        execution_identity=_build_provider_execution_identity(
            execution_scope="optimization_shadow",
            provider_id=spec.provider_id,
            provider_descriptor_digest="sha256:optimization-shadow-provider",
            equivalence_class_id="provider.openai-codex",
            model_family="model.openai-codex.default",
            capability_ids=(),
            recovery_capabilities=spec.capabilities,
            provider_adapter_id="adapter.openai-codex",
            provider_adapter_version="1.0.0",
            driver_factory_id="driver-factory.codex-optimization-shadow",
            driver_factory_version="1.0.0",
            broker_id="broker.codex-shadow",
            physical_provider_id="provider.openai-codex",
            physical_equivalence_class_id="provider.openai-codex",
        ),
    )
    return TrustedProviderTransport(
        root,
        contract,
        project_id=project_id,
        broker=broker,
        authority=authority,
    )


def _executions(transport: TrustedProviderTransport):
    from ai_sdlc.core.stage_review.provider_execution_registry import (
        ProviderExecutionAdapterRegistry,
    )

    registry = ProviderExecutionAdapterRegistry()
    registry.register_shadow(transport)
    return registry.freeze()


def _actual_usage() -> ResourceAmounts:
    return ResourceAmounts(
        provider_calls=1,
        review_passes=1,
        tokens=1200,
        cost=0.1,
        active_wall_clock=3,
        parallelism=0,
    )
