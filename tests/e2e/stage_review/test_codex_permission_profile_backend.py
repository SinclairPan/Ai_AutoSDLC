from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from tests.unit.stage_review.test_bindings import (
    StaticRuntimeBroker,
    _service,
)
from tests.unit.stage_review.test_bindings import (
    _context as _binding_context,
)
from tests.unit.stage_review.test_isolation_runtime_layout import (
    _candidate_authority,
)
from tests.unit.stage_review.test_provider_journal import _actual_usage
from tests.unit.stage_review.test_resources import (
    _OWNER,
    _now,
    _policy,
    _provider_anticipated,
)

from ai_sdlc.core.stage_review.binding_builders import (
    build_binding_attempt_request,
    build_binding_authority_snapshot,
    build_isolation_execution_evidence,
    build_provider_binding_descriptor,
    build_runtime_allocation,
)
from ai_sdlc.core.stage_review.binding_invocations import (
    ReviewerInvocationCoordinator,
)
from ai_sdlc.core.stage_review.binding_models import (
    IsolationExecutionEvidence,
    IsolationGrade,
)
from ai_sdlc.core.stage_review.candidate import candidate_binding_digest
from ai_sdlc.core.stage_review.canonical import (
    CanonicalizationPolicy,
    canonical_digest,
)
from ai_sdlc.core.stage_review.certificate_receipt_store import (
    FilesystemReviewReceiptArtifactStore,
)
from ai_sdlc.core.stage_review.codex_isolation_host_probe import (
    CodexIsolationHostProbe,
    resolve_codex_native_executable,
)
from ai_sdlc.core.stage_review.codex_provider_authority import (
    _codex_provider_descriptors,
)
from ai_sdlc.core.stage_review.codex_trusted_releases import (
    _trusted_published_codex_release as trusted_published_codex_release,
)
from ai_sdlc.core.stage_review.codex_trusted_releases import (
    _trusted_published_codex_release_digest,
)
from ai_sdlc.core.stage_review.isolation_execution import (
    IsolationBoundaryResult,
    IsolationNativeDenial,
    TrustedIsolationBackendRegistry,
)
from ai_sdlc.core.stage_review.isolation_launcher import (
    IsolatedProviderCommand,
    IsolationLaunchContext,
    ReviewerIsolationLauncher,
)
from ai_sdlc.core.stage_review.isolation_runtime_layout import (
    FilesystemAllocationPathResolver,
)
from ai_sdlc.core.stage_review.provider_journal import (
    ProviderInvocationJournal,
    ProviderQueryResult,
    build_provider_submission,
)
from ai_sdlc.core.stage_review.provider_journal_models import (
    ProviderInvocationRequest,
    ProviderRecoveryCapabilities,
    ProviderSubmission,
    provider_execution_evidence_root_digest,
)
from ai_sdlc.core.stage_review.provider_transport import (
    ControlledEndpointBroker,
    TrustedProviderTransport,
)
from ai_sdlc.core.stage_review.provider_transport_models import (
    ProviderTransportEnvelope,
    ProviderTransportExchangeResult,
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

_ExpectedMode = Literal[
    "ordinary-fail-closed",
    "required-enforced",
    "required-unavailable",
    "detected-only",
]


def test_codex_permission_profile_probe_is_real_or_fails_closed(
    tmp_path: Path,
) -> None:

    now = datetime.now(UTC)
    mode = _expected_mode()
    host = _actual_host_snapshot()
    context, canaries = _resolved_context(tmp_path, host)
    before = _canary_payloads(canaries)
    if mode == "ordinary-fail-closed":
        assert "isolation.codex.permission-profile" not in host.capability_ids
        assert host.backend_release_manifest_digest == ""
        assert host.backend_runtime_identity_digest == ""
        assert _canary_payloads(canaries) == before
        return
    if mode == "detected-only":
        context = replace(context, adapter_grade="detected_only")

    manifest = _actual_backend().probe(context, now)
    grade = TrustedIsolationBackendRegistry.default().derive_grade(
        manifest,
        host,
        adapter_grade=context.adapter_grade,
        now=now,
    )

    assert _canary_payloads(canaries) == before
    expected_grade: IsolationGrade = {
        "required-enforced": "enforced",
        "required-unavailable": "unproven",
        "detected-only": "detected_only",
    }[mode]
    assert grade == expected_grade, (
        json.dumps(manifest.model_dump(mode="json"))
        + "\n"
        + _actual_probe_diagnostic(context)
    )
    if mode == "required-enforced":
        assert manifest.boundary_results
        assert manifest.os_native_denials


def test_installed_codex_sandbox_help_uses_plural_profile_option() -> None:
    completed = subprocess.run(
        (_actual_codex_path(), "sandbox", "--help"),
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )

    assert completed.returncode == 0
    assert "--permissions-profile <NAME>" in completed.stdout
    assert "--permission-profile <NAME>" not in completed.stdout


def test_actual_host_probe_obeys_version_and_platform_floor() -> None:
    snapshot = _actual_host_snapshot()
    capability = "isolation.codex.permission-profile"

    if _expected_mode() != "ordinary-fail-closed":
        assert capability in snapshot.capability_ids
    else:
        assert capability not in snapshot.capability_ids


def test_final_coordinator_path_executes_isolated_or_refuses_before_command(
    tmp_path: Path,
) -> None:

    source, candidate, snapshot = _candidate_authority(tmp_path)
    candidate_digest = candidate_binding_digest(candidate)
    context = _binding_context(tmp_path)
    mode = _expected_mode()
    host_probe = _actual_host_probe()
    initial_host = host_probe.probe()
    release_digest = _trusted_published_codex_release_digest()
    authority = build_binding_authority_snapshot(
        plan=context.plan,
        risk_level="low",
        enforcement_mode="enforce",
        provider_descriptors=_codex_descriptors(
            context.plan,
            release_digest,
        ),
        attestor_id="ai-sdlc.codex-runtime",
        attestor_version="1.0.0",
        attestation_evidence_digest=release_digest,
    )
    allocations = _binding_allocations(context.plan, authority, candidate_digest)
    evidence_adapter = _CodexEvidenceAdapter(mode)
    service = _service(
        context,
        authority,
        host_probe,
        StaticRuntimeBroker(allocations),
        isolation_adapter=evidence_adapter,
    )
    bound = service.bind(
        context.plan,
        request=_binding_request(context, candidate_digest),
        budget_policy=_policy(),
        lease_owner=_OWNER,
        now=_now(),
    )
    assert host_probe.probe().snapshot_digest == initial_host.snapshot_digest
    if mode == "ordinary-fail-closed":
        assert all(
            item.isolation_grade == "unproven"
            for item in evidence_adapter.prepared_evidence
        )
    else:
        expected_grade: IsolationGrade = {
            "required-enforced": "enforced",
            "required-unavailable": "unproven",
            "detected-only": "detected_only",
        }[mode]
        assert evidence_adapter.prepared_evidence
        assert all(
            item.isolation_grade == expected_grade
            for item in evidence_adapter.prepared_evidence
        )
    if mode != "required-enforced":
        assert bound.result_code in {"actor_unavailable", "independence_unproven"}
        assert bound.binding_set is None
        if mode == "ordinary-fail-closed" and bound.result_code == "actor_unavailable":
            assert set(bound.reason_ids) & {
                "binding.host-agent-execution-unavailable",
                "binding.required-actor-unavailable",
            }
        else:
            expected_reason = {
                "ordinary-fail-closed": "binding.runtime-isolation-unproven",
                "required-unavailable": "binding.runtime-isolation-unproven",
                "detected-only": "binding.required-isolation-not-enforced",
            }[mode]
            assert expected_reason in bound.reason_ids
        if bound.result_code == "actor_unavailable":
            assert not evidence_adapter.prepared_evidence
            assert not bound.diagnostic_evidence_digests
        else:
            assert bound.diagnostic_evidence_digests
        assert not tuple(tmp_path.rglob("provider-invocation-journal"))
        assert not tuple(tmp_path.rglob("isolation-execution/consumed/*.json"))
        assert not tuple(tmp_path.rglob("certificates/*.json"))
        return
    assert bound.binding_set is not None, bound.reason_ids
    resolver = _coordinator_resolver(tmp_path, allocations, source, candidate, snapshot)
    launcher = ReviewerIsolationLauncher(
        tmp_path,
        registry=TrustedIsolationBackendRegistry.default(),
        backend=_actual_backend(),
    )
    journal = ProviderInvocationJournal(
        tmp_path,
        project_id=context.final.project_id,
        resource_governor=context.governor,
    )
    coordinator = ReviewerInvocationCoordinator(
        service,
        journal,
        isolation_launcher=launcher,
        allocation_path_resolver=resolver,
    )
    current = context.governor.get_reservation(context.final.reservation_id)
    request_payload = {
        "candidate_digest": candidate_digest,
        "review_contract": "candidate-read-only.v1",
    }
    prepared = coordinator.prepare(
        binding_set_id=bound.binding_set.binding_set_id,
        slot_id=bound.binding_set.bindings[0].slot_id,
        cohort_id="cohort.current",
        candidate_manifest_digest=candidate_digest,
        expected_pass_head_digest="sha256:pass-head",
        owner_scope_id=current.provider_scope_ids[0],
        request_digest=provider_payload_digest(request_payload),
        expected_reservation_digest=current.reservation_digest,
        anticipated_usage=_provider_anticipated(),
        command_id="command.e2e-isolated-review",
        idempotency_key="e2e-isolated-review",
        lease_owner=_OWNER,
        now=_now(),
    )
    assert prepared.invocation is not None
    request = prepared.invocation.request
    selected = next(
        item
        for item in allocations
        if item.slot_id == bound.binding_set.bindings[0].slot_id
    )
    layout = resolver.resolve(
        selected,
        peer_allocations=tuple(item for item in allocations if item != selected),
        assignment_digest=request.assignment_digest,
    )
    transport, exchange = _controlled_review_exchange(
        tmp_path,
        request,
        request_payload,
    )
    response_path = Path(layout.output_root) / "controlled-response.json"
    response_path.write_text(
        json.dumps(exchange.response, sort_keys=True),
        encoding="utf-8",
    )
    driver = _E2ECommandDriver(
        request,
        candidate_path=Path(layout.candidate_root) / "candidate.txt",
        response_path=response_path,
        exchange=exchange,
    )

    result = coordinator.resume(
        prepared.invocation.invocation_id,
        driver=driver,
        validator=_validate_e2e_submission,
        lease_owner=_OWNER,
        now=_now(),
    )

    receipts = launcher.receipts()
    assert driver.raw_invoke_count == 0
    assert driver.raw_query_count == 0
    assert receipts
    assert result.result_code == "committed"
    assert result.invocation is not None
    assert driver.command_count == 1
    assert receipts[-1].command_started is True
    _assert_enforced_boundaries(
        receipts[-1].boundary_results,
        receipts[-1].os_native_denials,
    )
    egress = exchange.receipt
    assert transport.remote_provider_available is False
    assert egress.remote_provider_exercised is False
    assert egress.transport_contract_attested is True
    assert result.invocation.isolation_receipt_digests == (receipts[-1].receipt_digest,)
    assert result.invocation.egress_receipt_digests == (egress.receipt_digest,)
    assert result.invocation.execution_evidence_root_digest == (
        provider_execution_evidence_root_digest(
            result.invocation.isolation_receipt_digests,
            result.invocation.egress_receipt_digests,
        )
    )
    artifact_store = FilesystemReviewReceiptArtifactStore(
        tmp_path,
        project_id=request.project_id,
    )
    assert artifact_store.resolve_invocation(request.invocation_id) == (
        result.invocation
    )
    assert (
        artifact_store.resolve_isolation_receipt(receipts[-1].receipt_digest)
        == receipts[-1]
    )
    assert artifact_store.resolve_egress_receipt(egress.receipt_digest) == egress
    assert artifact_store.resolve_response(egress.response_digest) == exchange.response


def test_detected_only_persists_pollution_then_cleans_without_provider_command(
    tmp_path: Path,
) -> None:
    mode = _expected_mode()
    now = datetime.now(UTC)
    host = _actual_host_snapshot()
    driver = _DetectedOnlyForbiddenDriver()
    if mode == "required-unavailable":
        context, canaries = _resolved_context(tmp_path, host)
        before = _canary_payloads(canaries)
        launcher = ReviewerIsolationLauncher(
            tmp_path / "required-unavailable-launcher",
            registry=TrustedIsolationBackendRegistry.default(),
            backend=_actual_backend(),
        )

        wrapped = launcher.prepare_driver(driver, context=context, now=now)

        assert wrapped is None
        assert driver.command_count == driver.raw_invoke_count == 0
        assert _canary_payloads(canaries) == before
        assert launcher.receipts()[-1].reason_id == "isolation.backend-unproven"
        return
    if mode != "detected-only":
        capability = "isolation.codex.permission-profile"
        assert (capability in host.capability_ids) == (
            mode in {"required-enforced", "required-unavailable"}
        )
        assert driver.command_count == driver.raw_invoke_count == 0
        assert not tuple(tmp_path.rglob("isolation-execution/consumed/*.json"))
        return
    context, canaries = _resolved_context(tmp_path, host)
    context = replace(context, adapter_grade="detected_only")
    before = _canary_payloads(canaries)
    launcher_root = tmp_path / "detected-only-launcher"
    launcher = ReviewerIsolationLauncher(
        launcher_root,
        registry=TrustedIsolationBackendRegistry.default(),
        backend=_actual_backend(),
    )

    wrapped = launcher.prepare_driver(driver, context=context, now=now)

    assert wrapped is None
    assert driver.command_count == driver.raw_invoke_count == 0
    assert _canary_payloads(canaries) == before
    evidence = launcher.detected_only_evidence()
    assert tuple(item.stage for item in evidence) == ("polluted", "cleaned")
    assert evidence[-1].cleanup_succeeded is True
    assert not Path(evidence[-1].sentinel_root).exists()
    assert not (launcher_root / "isolation-execution" / "consumed").exists()
    assert launcher.receipts()[-1].reason_id == "isolation.backend-detected-only"
    assert "isolation.codex.permission-profile" in host.capability_ids


def _resolved_context(tmp_path: Path, host):
    protected_home = tmp_path / "protected-home"
    protected_home.mkdir()
    global_config = protected_home / ".gitconfig"
    global_config.write_text("global-canary", encoding="utf-8")
    (protected_home / "home-canary.txt").write_text("home", encoding="utf-8")
    resolver = FilesystemAllocationPathResolver(
        tmp_path / "trusted-layouts",
        protected_home_root=protected_home,
        protected_config_roots=(global_config,),
    )
    source, candidate, snapshot = _candidate_authority(tmp_path)
    candidate_digest = candidate_binding_digest(candidate)
    allocation = _allocation("one", candidate_digest)
    peer = _allocation("peer", candidate_digest)
    resolver.materialize_candidate_snapshot(
        allocation,
        source,
        candidate=candidate,
        source_snapshot=snapshot,
    )
    resolver.provision_runtime(allocation)
    resolver.provision_runtime(peer)
    layout = resolver.resolve(
        allocation,
        peer_allocations=(peer,),
        assignment_digest="sha256:assignment.e2e",
    )
    peer_canary = Path(layout.peer_output_roots[0]) / "peer-canary.txt"
    peer_canary.write_text("peer", encoding="utf-8")
    context = IsolationLaunchContext.from_layout(
        layout,
        host_snapshot=host,
        adapter_grade="enforced",
    )
    canaries = (
        Path(layout.candidate_root) / "candidate.txt",
        peer_canary,
        protected_home / "home-canary.txt",
        global_config,
    )
    return context, canaries


def _allocation(suffix: str, candidate_digest: str):
    descriptor = build_provider_binding_descriptor(
        descriptor_id=f"descriptor.{suffix}",
        provider_id="provider.codex",
        equivalence_class_id="class.codex",
        model_family="gpt-5",
        role_contract_digests=("sha256:role",),
        capability_ids=("agent_execution",),
        provider_tags=(),
        tool_allowlist=(),
        recovery_capabilities=ProviderRecoveryCapabilities(
            idempotency_support=True,
            invocation_query_support=True,
            cost_metering_support=True,
        ),
        isolation_backend="codex.permission-profile",
        network_enforcement=True,
        supported_independence_grade="model_diversity_proven",
        provider_policy_evidence_digest="sha256:policy",
    )
    return build_runtime_allocation(
        allocation_id=f"allocation.{suffix}",
        slot_id=f"slot.{suffix}",
        actor_id=f"actor.{suffix}",
        session_id=f"session.{suffix}",
        provider_descriptor=descriptor,
        candidate_manifest_digest=candidate_digest,
        candidate_snapshot_id="snapshot.e2e",
        working_directory_id=f"opaque-cwd.{suffix}",
        disposable_home_id=f"opaque-home.{suffix}",
        disposable_config_id=f"opaque-config.{suffix}",
        disposable_credential_view_id=f"opaque-credential.{suffix}",
        output_directory_id=f"opaque-output.{suffix}",
        allocation_operation_id=f"operation.{suffix}",
    )


def _canary_payloads(paths: tuple[Path, ...]) -> tuple[str, ...]:
    return tuple(path.read_text(encoding="utf-8") for path in paths)


def _actual_codex_path() -> str:
    discovered = shutil.which("codex")
    assert discovered is not None
    return str(resolve_codex_native_executable(Path(discovered)).resolve(strict=True))


def _expected_mode() -> _ExpectedMode:
    required = os.getenv("AI_SDLC_REQUIRE_ENFORCED_ISOLATION") == "1"
    detected = os.getenv("AI_SDLC_EXPECT_DETECTED_ONLY") == "1"
    unavailable = os.getenv("AI_SDLC_EXPECT_REQUIRED_UNAVAILABLE") == "1"
    if sum((detected, unavailable)) > 1:
        raise ValueError("isolation expectation modes are mutually exclusive")
    if (detected or unavailable) and not required:
        raise ValueError("special isolation expectation requires the trusted capability")
    if unavailable:
        return "required-unavailable"
    if detected:
        return "detected-only"
    if required:
        return "required-enforced"
    return "ordinary-fail-closed"


def _actual_host_snapshot():
    return _actual_host_probe().probe()


def _actual_host_probe() -> CodexIsolationHostProbe:
    executable = Path(_actual_codex_path())
    return CodexIsolationHostProbe(
        str(executable),
        release_manifest=_measured_trusted_release(),
    )


def _actual_backend():
    from ai_sdlc.core.stage_review.codex_isolation_backend import (
        CodexPermissionProfileBackend,
    )

    return CodexPermissionProfileBackend(
        _actual_codex_path(),
        release_manifest=_measured_trusted_release(),
    )


def _actual_probe_diagnostic(context: IsolationLaunchContext) -> str:
    from ai_sdlc.core.stage_review.codex_isolation_boundary import (
        run_boundary_probe,
        write_profile,
    )
    from ai_sdlc.core.stage_review.codex_isolation_policy import (
        _calibration_context,
        _cleanup_calibration_root,
        _seed_calibration_targets,
        _with_backend_runtime,
    )

    executable = Path(_actual_codex_path())
    probe = _calibration_context(_with_backend_runtime(context, executable))
    probe_root = Path(probe.normalized_run_root).parent
    try:
        _seed_calibration_targets(probe)
        config_root, disposable_home = write_profile(probe)
        run = run_boundary_probe(
            str(executable),
            probe,
            config_root,
            disposable_home,
        )
        return json.dumps(
            {
                "return_code": run.return_code,
                "stdout_tail": run.stdout[-800:],
                "stderr_tail": run.stderr[-800:],
            }
        )
    except BaseException as exc:
        return f"{type(exc).__name__}: {exc}"
    finally:
        _cleanup_calibration_root(probe_root)


def _measured_trusted_release():
    evidence_path = os.getenv("AI_SDLC_CODEX_NPM_ATTESTATIONS", "")
    if not evidence_path:
        return None
    registry = json.loads(Path(evidence_path).read_text(encoding="utf-8"))
    return trusted_published_codex_release(registry_attestations=registry)


def _binding_allocations(plan, authority, candidate_digest: str):
    by_role = {
        item.role_contract_digests[0]: item
        for item in authority.provider_descriptors
    }
    values = []
    for index, slot in enumerate(plan.proposal.required_slots, start=1):
        values.append(
            build_runtime_allocation(
                allocation_id=f"runtime-allocation.{index}",
                slot_id=slot.slot_id,
                actor_id=f"actor.{index}",
                session_id=f"provider-session.{index}",
                provider_descriptor=by_role[slot.role_contract_digest],
                candidate_manifest_digest=candidate_digest,
                candidate_snapshot_id=f"candidate-snapshot.{index}",
                working_directory_id=f"opaque-cwd.{index}",
                disposable_home_id=f"opaque-home.{index}",
                disposable_config_id=f"opaque-config.{index}",
                disposable_credential_view_id=f"opaque-credential.{index}",
                output_directory_id=f"opaque-output.{index}",
                allocation_operation_id=f"allocation-operation.{index}",
            )
        )
    return tuple(values)


def _codex_descriptors(plan, release_digest: str):
    return tuple(
        descriptor
        for slot in plan.proposal.required_slots
        for descriptor in _codex_provider_descriptors(slot, release_digest)
    )


def _binding_request(context, candidate_digest: str):
    return build_binding_attempt_request(
        plan=context.plan,
        final_reservation=context.final,
        candidate_manifest_digest=candidate_digest,
        input_packet_digest="sha256:input-packet",
        visibility_barrier_id="visibility-barrier.initial",
        attempt_index=1,
        previous_binding_set_digest="",
        expected_cohort_id="",
        expected_pass_head_digest="",
        rebind_reason="initial_binding",
        availability_attestation=None,
    )


def _coordinator_resolver(root, allocations, source, candidate, snapshot):
    protected_home = root / "coordinator-protected-home"
    protected_home.mkdir()
    config = protected_home / ".gitconfig"
    config.write_text("protected", encoding="utf-8")
    resolver = FilesystemAllocationPathResolver(
        root / "coordinator-layouts",
        protected_home_root=protected_home,
        protected_config_roots=(config,),
    )
    for allocation in allocations:
        resolver.materialize_candidate_snapshot(
            allocation,
            source,
            candidate=candidate,
            source_snapshot=snapshot,
        )
        resolver.provision_runtime(allocation)
    return resolver


def _controlled_review_exchange(
    root: Path,
    request: ProviderInvocationRequest,
    payload: dict[str, object],
) -> tuple[TrustedProviderTransport, ProviderTransportExchangeResult]:
    authority = build_transport_authority(
        contract_id="transport.t601.e2e",
        contract_version="1",
        endpoint_id="ipc://t601/e2e-reviewer",
        workflow_ref="workflow:t601-required-isolation",
        evidence_digest="sha256:t601-e2e-transport-attestation",
    )
    contract = build_transport_contract(
        contract_id=authority.contract_id,
        contract_version=authority.contract_version,
        endpoint_id=authority.endpoint_id,
        authority=authority,
        execution_identity=_build_provider_execution_identity(
            execution_scope=request.authorization_scope or "generic",
            provider_id=request.provider_id,
            provider_descriptor_digest="sha256:t601-e2e-descriptor",
            equivalence_class_id="provider.t601-e2e",
            model_family="model.t601-e2e",
            capability_ids=(),
            recovery_capabilities=request.capabilities,
            provider_adapter_id="adapter.t601-e2e",
            provider_adapter_version="1.0.0",
            driver_factory_id="driver-factory.t601-e2e",
            driver_factory_version="1.0.0",
            broker_id="broker.t601-e2e",
            physical_provider_id="provider.t601-e2e",
            physical_equivalence_class_id="provider.t601-e2e",
        ),
    )
    observed: list[ProviderTransportEnvelope] = []

    def endpoint(envelope: ProviderTransportEnvelope) -> dict[str, object]:
        observed.append(envelope)
        candidate_matches = (
            envelope.payload.get("candidate_digest") == request.candidate_digest
        )
        return {
            "candidate_digest": request.candidate_digest,
            "request_digest": envelope.request_digest,
            "verdict": "accept" if candidate_matches else "reject",
        }

    broker = ControlledEndpointBroker(
        contract,
        {contract.endpoint_id: endpoint},
        authority=authority,
    )
    transport = TrustedProviderTransport(
        root,
        contract,
        project_id=request.project_id,
        broker=broker,
        authority=authority,
    )
    envelope = ProviderTransportEnvelope(
        invocation_id=request.invocation_id,
        assignment_digest=request.assignment_digest,
        provider_id=request.provider_id,
        execution_identity_digest=contract.execution_identity.identity_digest,
        request_digest=request.request_digest,
        turn_index=1,
        idempotency_key=request.idempotency_key,
        credential_view_digest="sha256:e2e-controlled-credential-view",
        backend_epoch="t601-e2e-broker.epoch",
        active_wall_clock_limit=request.anticipated_usage.active_wall_clock,
        payload=payload,
    )
    assert request.request_digest == provider_payload_digest(payload)
    exchange = transport.exchange(envelope)
    assert observed == [envelope]
    return transport, exchange


def _validate_e2e_submission(submission: ProviderSubmission) -> str:
    payload = submission.output_payload
    required = ("candidate_sha256", "decision", "response_digest")
    if (
        tuple(sorted(payload)) != tuple(sorted(required))
        or payload.get("decision") not in {"PASS", "CHANGES_REQUIRED"}
        or any(not str(payload.get(key, "")).strip() for key in required)
        or not submission.egress_receipt_digests
    ):
        raise ValueError("isolated reviewer submission is incomplete")
    return canonical_digest(payload, CanonicalizationPolicy())


def _assert_enforced_boundaries(
    boundaries: tuple[IsolationBoundaryResult, ...],
    denials: tuple[IsolationNativeDenial, ...],
) -> None:
    by_action = {item.action: item for item in boundaries}
    denied_actions = (
        "candidate-read-only",
        "peer-output-denied",
        "real-home-denied",
        "global-config-denied",
        "symlink-boundary-denied",
        "child-process-contained",
        "network-denied",
    )
    for action in denied_actions:
        item = by_action[action]
        assert item.expected == item.observed == "denied"
        assert item.blocked_before_side_effect is True
        assert item.before_digest == item.after_digest
    network = by_action["network-denied"]
    for token in ("ipv4-direct-ip", "ipv6-direct-ip", "localhost"):
        assert token in network.target_kind
    output = by_action["output-write-allowed"]
    assert output.expected == output.observed == "allowed"
    assert denials
    assert {item.operation for item in denials}.issubset(set(by_action))


_REVIEWER_PROGRAM = r"""
import hashlib
import json
import sys
from pathlib import Path

candidate_path = Path(sys.argv[1])
response_path = Path(sys.argv[2])
expected_response_digest = sys.argv[3]
result_path = Path(sys.argv[4])
candidate_payload = candidate_path.read_bytes()
response = json.loads(response_path.read_text(encoding="utf-8"))
canonical = json.dumps(
    response,
    ensure_ascii=False,
    sort_keys=True,
    separators=(",", ":"),
).encode("utf-8")
response_digest = "sha256:" + hashlib.sha256(canonical).hexdigest()
if response_digest != expected_response_digest:
    raise SystemExit("controlled response digest mismatch")
decision = "PASS" if response.get("verdict") == "accept" else "CHANGES_REQUIRED"
result = {
    "candidate_sha256": hashlib.sha256(candidate_payload).hexdigest(),
    "decision": decision,
    "response_digest": response_digest,
}
result_path.write_text(json.dumps(result, sort_keys=True), encoding="utf-8")
print(json.dumps(result, sort_keys=True))
"""


class _E2ECommandDriver:
    def __init__(
        self,
        request: ProviderInvocationRequest,
        *,
        candidate_path: Path,
        response_path: Path,
        exchange: ProviderTransportExchangeResult,
    ) -> None:
        self.provider_id = request.provider_id
        self.capabilities = request.capabilities
        self.raw_invoke_count = 0
        self.raw_query_count = 0
        self.command_count = 0
        self._candidate_path = candidate_path
        self._response_path = response_path
        self._result_path = response_path.with_name("review-result.json")
        self._candidate_sha256 = hashlib.sha256(candidate_path.read_bytes()).hexdigest()
        self._response = exchange.response
        self._egress_receipt = exchange.receipt

    def invoke(self, request):
        self.raw_invoke_count += 1
        raise AssertionError("raw provider invoke must not execute")

    def query(self, request):
        self.raw_query_count += 1
        raise AssertionError("raw provider query must not execute")

    def build_isolated_command(self, request, permit, command_kind):
        self.command_count += 1
        return IsolatedProviderCommand(
            argv=(
                str(Path(sys.executable).resolve()),
                "-c",
                _REVIEWER_PROGRAM,
                str(self._candidate_path),
                str(self._response_path),
                self._egress_receipt.response_digest,
                str(self._result_path),
            ),
            stdin_text="",
            command_kind=command_kind,
        )

    def decode_isolated_result(self, request, command_kind, result):
        if command_kind == "query":
            return ProviderQueryResult(query_status="not_found")
        if result.return_code != 0:
            raise ValueError(f"isolated reviewer failed: {result.stderr[-240:]}")
        try:
            payload = json.loads(result.stdout.strip().splitlines()[-1])
            persisted = json.loads(self._result_path.read_text(encoding="utf-8"))
        except (IndexError, OSError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError("isolated reviewer output is invalid") from exc
        expected_decision = (
            "PASS" if self._response.get("verdict") == "accept" else "CHANGES_REQUIRED"
        )
        if (
            payload != persisted
            or payload.get("decision") != expected_decision
            or payload.get("candidate_sha256") != self._candidate_sha256
            or payload.get("response_digest") != self._egress_receipt.response_digest
        ):
            raise ValueError("isolated reviewer output lineage diverged")
        return build_provider_submission(
            request,
            provider_call_id=self._egress_receipt.receipt_id,
            output_payload=payload,
            accounted_usage=metered_provider_usage(_actual_usage()),
            egress_receipt_digests=(self._egress_receipt.receipt_digest,),
        )


class _CodexEvidenceAdapter:
    def __init__(self, mode: _ExpectedMode) -> None:
        self._mode = mode
        self.prepared_evidence: tuple[IsolationExecutionEvidence, ...] = ()

    def prepare(
        self,
        operation_id,
        allocations,
        host_snapshot,
        visibility_barrier_id,
    ):
        grade: IsolationGrade = {
            "ordinary-fail-closed": "unproven",
            "required-enforced": "enforced",
            "required-unavailable": "unproven",
            "detected-only": "detected_only",
        }[self._mode]
        disposable = grade != "unproven"
        enforced = grade == "enforced"
        self.prepared_evidence = tuple(
            build_isolation_execution_evidence(
                operation_id=operation_id,
                allocation=allocation,
                host_snapshot=host_snapshot,
                visibility_barrier_id=visibility_barrier_id,
                isolation_grade=grade,
                isolation_backend="codex.permission-profile",
                candidate_snapshot_isolated=disposable,
                candidate_write_enforced=enforced,
                peer_outputs_hidden=disposable,
                disposable_home=disposable,
                disposable_config=disposable,
                disposable_credentials=disposable,
                output_isolated=disposable,
                user_home_protected=enforced,
                global_config_protected=enforced,
                network_policy_enforced=enforced,
                sentinel_environment_disposable=disposable,
                evidence_bundle_digest=f"sha256:e2e.{allocation.allocation_id}",
            )
            for allocation in allocations
        )
        return self.prepared_evidence


class _DetectedOnlyForbiddenDriver:
    provider_id = "provider.codex"
    capabilities = ProviderRecoveryCapabilities(
        idempotency_support=True,
        invocation_query_support=True,
        cost_metering_support=True,
    )

    def __init__(self) -> None:
        self.raw_invoke_count = 0
        self.command_count = 0

    def invoke(self, request):
        self.raw_invoke_count += 1
        raise AssertionError("detected_only cannot invoke a provider")

    def query(self, request):
        raise AssertionError("detected_only cannot query a provider")

    def build_isolated_command(self, request, permit, command_kind):
        self.command_count += 1
        raise AssertionError("detected_only cannot build an untrusted command")
