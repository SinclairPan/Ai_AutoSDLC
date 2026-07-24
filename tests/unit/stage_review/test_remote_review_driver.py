from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from tests.unit.stage_review.test_isolation_execution import _host, _manifest
from tests.unit.stage_review.test_isolation_launcher import _context
from tests.unit.stage_review.test_provider_journal import (
    _actual_usage,
    _journal_setup,
)
from tests.unit.stage_review.test_resources import _now

from ai_sdlc.core.stage_review.provider_journal import (
    build_provider_invocation_request,
)
from ai_sdlc.core.stage_review.provider_transport_models import (
    ProviderTransportEnvelope,
    _build_provider_execution_identity,
    provider_payload_digest,
)
from ai_sdlc.core.stage_review.provider_transport_models import (
    _build_transport_authority as build_transport_authority,
)
from ai_sdlc.core.stage_review.provider_transport_models import (
    _build_transport_contract as build_transport_contract,
)
from ai_sdlc.core.stage_review.provider_usage_models import metered_provider_usage

_OWNER = "owner.test-process"


@dataclass
class _ExecutingBackend:
    manifest: object

    def probe(self, context, now):
        return self.manifest

    def execute(self, command, permit):
        from ai_sdlc.core.stage_review.isolation_launcher import (
            IsolationProcessResult,
        )

        run = subprocess.run(
            command.argv,
            input=command.stdin_text,
            capture_output=True,
            check=False,
            text=True,
        )
        return IsolationProcessResult(
            return_code=run.returncode,
            stdout=run.stdout,
            stderr=run.stderr,
            process_id=202,
            parent_process_id=self.manifest.parent_process_id,
            boundary_results=self.manifest.boundary_results,
            os_native_denials=self.manifest.os_native_denials,
            before_digest="sha256:protected",
            after_digest="sha256:protected",
            cleanup_succeeded=True,
        )


class _RemoteBroker:
    def __init__(self, response, *, on_exchange, remote: bool = True) -> None:
        self.response = response
        self.on_exchange = on_exchange
        self.remote_provider_exercised = remote
        self.calls = 0

    def exchange(self, permit, envelope):
        self.calls += 1
        self.on_exchange(envelope)
        return self.response


@dataclass
class _ReviewRig:
    journal: object
    governor: object
    request: object
    broker: _RemoteBroker
    transport: object
    driver: object


def test_remote_review_runs_only_after_dispatch_and_binds_both_receipts(
    tmp_path: Path,
) -> None:
    rig = _review_rig(tmp_path, _valid_response())
    prepared = rig.journal.prepare(rig.request, lease_owner=_OWNER, now=_now())

    assert prepared.result_code == "prepared"
    assert rig.broker.calls == 0
    result = rig.journal.resume(
        rig.request.invocation_id,
        driver=rig.driver,
        validator=lambda submission: "sha256:review-output-valid",
        lease_owner=_OWNER,
        now=_now(),
    )

    assert result.result_code == "committed"
    assert rig.broker.calls == 1
    assert result.submission is not None
    assert result.submission.output_payload["verdict"] == "passed"
    assert len(result.submission.egress_receipt_digests) == 1
    assert len(result.submission.isolation_receipt_digests) == 1
    assert rig.transport.receipts()[0].remote_provider_exercised is True


def test_remote_review_rejects_malformed_provider_output(tmp_path: Path) -> None:
    response = _valid_response()
    response["review"] = {
        "verdict": "passed",
        "coverage": {"reviewed_area_ids": ["capability.security"]},
        "findings": [],
        "evidence_digests": [],
    }
    rig = _review_rig(tmp_path, response)
    rig.journal.prepare(rig.request, lease_owner=_OWNER, now=_now())

    result = rig.journal.resume(
        rig.request.invocation_id,
        driver=rig.driver,
        validator=lambda submission: "sha256:must-not-run",
        lease_owner=_OWNER,
        now=_now(),
    )

    assert result.result_code == "needs_user"
    assert rig.broker.calls == 1
    invocation = rig.journal.get(rig.request.invocation_id)
    assert invocation.state == "refused"
    assert len(invocation.egress_receipt_digests) == 1
    assert len(invocation.isolation_receipt_digests) == 1
    assert invocation.execution_evidence_root_digest
    assert not rig.journal.submission_path(rig.request.invocation_id).exists()
    reservation = rig.governor.get_reservation(rig.request.reservation_id)
    assert reservation.usage == _actual_usage().model_copy(update={"review_passes": 1})
    assert not reservation.authorized_pending.any_positive()
    assert not reservation.provider_permits

    replay = rig.journal.resume(
        rig.request.invocation_id,
        driver=rig.driver,
        validator=lambda submission: "sha256:must-not-run",
        lease_owner=_OWNER,
        now=_now(),
    )

    assert replay.result_code == "needs_user"
    assert rig.broker.calls == 1


def test_remote_review_never_calls_non_remote_broker(tmp_path: Path) -> None:
    rig = _review_rig(tmp_path, _valid_response(), remote=False)
    rig.journal.prepare(rig.request, lease_owner=_OWNER, now=_now())

    result = rig.journal.resume(
        rig.request.invocation_id,
        driver=rig.driver,
        validator=lambda submission: "sha256:must-not-run",
        lease_owner=_OWNER,
        now=_now(),
    )

    assert result.result_code == "needs_user"
    assert rig.broker.calls == 0
    assert rig.transport.receipts() == ()


def _review_rig(root: Path, response, *, remote: bool = True) -> _ReviewRig:
    from ai_sdlc.core.stage_review import isolation_execution, isolation_launcher
    from ai_sdlc.core.stage_review.remote_review_driver import RemoteReviewDriver
    from ai_sdlc.core.stage_review.reviewer_execution_gate import ReviewerExecutionGate

    journal, governor, base = _journal_setup(root)
    payload = {"prompt": "review the frozen candidate", "schema": "review.v1"}
    request = _reviewer_request(base, payload)
    host = _host(isolation_execution)
    context = _context(isolation_launcher, request, host, root)
    broker = _RemoteBroker(
        response,
        remote=remote,
        on_exchange=lambda envelope: _assert_dispatched(journal, request, envelope),
    )
    transport = _transport(root, base.project_id, broker, request)
    backend = _ExecutingBackend(
        _review_manifest(isolation_execution, host.snapshot_digest, request)
    )
    launcher = isolation_launcher.ReviewerIsolationLauncher(
        root,
        registry=isolation_execution.TrustedIsolationBackendRegistry.default(),
        backend=backend,
    )
    driver = RemoteReviewDriver(
        request,
        payload=payload,
        execution=_registered_execution(transport),
        output_root=Path(context.output_root),
        credential_view_digest="sha256:credential-view",
        layout_digest=context.layout_digest,
    )
    gate = ReviewerExecutionGate(
        authorize=lambda candidate, now: True,
        prepare_isolated_driver=lambda candidate, raw, now: launcher.prepare_driver(
            raw, context=context, now=now
        ),
        requires_reviewer_gate=lambda candidate: True,
    )
    journal.register_reviewer_driver_preparer(gate.prepare)
    return _ReviewRig(journal, governor, request, broker, transport, driver)


def _reviewer_request(base, payload):
    return build_provider_invocation_request(
        project_id=base.project_id,
        work_item_id=base.work_item_id,
        stage_review_session_id=base.stage_review_session_id,
        owner_scope_id=base.owner_scope_id,
        candidate_digest=base.candidate_digest,
        assignment_digest="reviewer-assignment:sha256:remote-review",
        authorization_scope="reviewer_binding",
        epoch_id=base.epoch_id,
        provider_id=base.provider_id,
        request_digest=provider_payload_digest(payload),
        reservation_id=base.reservation_id,
        expected_reservation_digest=base.expected_reservation_digest,
        expected_fencing_token=base.expected_fencing_token,
        anticipated_usage=base.anticipated_usage.model_copy(
            update={"review_passes": 1}
        ),
        capabilities=base.capabilities,
        command_id=base.command_id,
        idempotency_key=base.idempotency_key,
    )


def _transport(root: Path, project_id: str, broker, request):
    from ai_sdlc.core.stage_review.provider_transport import TrustedProviderTransport

    authority = build_transport_authority(
        contract_id="transport.remote-review",
        contract_version="1",
        endpoint_id="ipc://remote-review/provider",
        workflow_ref="workflow:remote-review",
        evidence_digest="sha256:remote-review-authority",
    )
    contract = build_transport_contract(
        contract_id=authority.contract_id,
        contract_version=authority.contract_version,
        endpoint_id=authority.endpoint_id,
        authority=authority,
        execution_identity=_build_provider_execution_identity(
            execution_scope="reviewer_binding",
            provider_id=request.provider_id,
            provider_descriptor_digest="sha256:remote-review-descriptor",
            equivalence_class_id="provider.test-remote",
            model_family="model.test-remote",
            capability_ids=(),
            recovery_capabilities=request.capabilities,
            provider_adapter_id="adapter.test-remote",
            provider_adapter_version="1.0.0",
            driver_factory_id="driver-factory.remote-review",
            driver_factory_version="1.0.0",
            broker_id="broker.test-remote",
            physical_provider_id="provider.test-remote",
            physical_equivalence_class_id="provider.test-remote",
        ),
    )
    return TrustedProviderTransport(
        root,
        contract,
        project_id=project_id,
        broker=broker,
        authority=authority,
    )


def _registered_execution(transport):
    from ai_sdlc.core.stage_review.provider_execution_registry import (
        RegisteredProviderExecution,
    )

    return RegisteredProviderExecution(
        identity=transport.contract.execution_identity,
        transport=transport,
    )


def _assert_dispatched(journal, request, envelope: ProviderTransportEnvelope) -> None:
    invocation = journal.get(request.invocation_id)
    assert invocation is not None and invocation.state == "dispatched"
    assert envelope.request_digest == request.request_digest


def _valid_response() -> dict[str, object]:
    return {
        "provider_call_id": "provider-call.remote-review",
        "review": {
            "verdict": "passed",
            "coverage": {"reviewed_area_ids": ["capability.security"]},
            "findings": [],
            "evidence_digests": ["sha256:review-evidence"],
        },
        "accounted_usage": metered_provider_usage(
            _actual_usage().model_copy(update={"review_passes": 1})
        ).model_dump(mode="json"),
    }


def _review_manifest(api, host_digest: str, request):
    seed = _manifest(api, host_snapshot_digest=host_digest)
    values = seed.model_dump(
        mode="json",
        exclude={
            "schema_version",
            "artifact_kind",
            "created_by",
            "created_at",
            "ai_sdlc_version",
            "extensions",
            "canonicalization_version",
            "compatibility_mode",
            "manifest_digest",
        },
    )
    values["assignment_digest"] = request.assignment_digest
    values["candidate_digest"] = request.candidate_digest
    return api.build_isolation_evidence_manifest(**values)
