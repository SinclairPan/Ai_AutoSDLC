from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

from ai_sdlc.core.stage_review import isolation_execution
from ai_sdlc.core.stage_review.activation_policy_store import current_activation_policy
from ai_sdlc.core.stage_review.binding_invocations import ReviewerInvocationCoordinator
from ai_sdlc.core.stage_review.bindings import (
    ReviewerBindingService,
    build_binding_authority_snapshot,
    build_isolation_execution_evidence,
)
from ai_sdlc.core.stage_review.candidate import candidate_binding_digest
from ai_sdlc.core.stage_review.canonical_stage_review_executor import (
    CanonicalStageReviewExecutor,
)
from ai_sdlc.core.stage_review.canonical_stage_review_support import execution_scope
from ai_sdlc.core.stage_review.codex_trusted_releases import (
    _trusted_published_codex_release_digests,
)
from ai_sdlc.core.stage_review.isolation_launcher import (
    IsolationProcessResult,
    ReviewerIsolationLauncher,
)
from ai_sdlc.core.stage_review.optimization.observations import (
    OptimizationObservationStore,
)
from ai_sdlc.core.stage_review.provider_execution_registry import (
    ProviderExecutionAdapterRegistry,
)
from ai_sdlc.core.stage_review.provider_journal import ProviderInvocationJournal
from ai_sdlc.core.stage_review.provider_usage_models import metered_provider_usage
from ai_sdlc.core.stage_review.remote_review_driver_factory import (
    RemoteReviewDriverFactory,
)
from ai_sdlc.core.stage_review.resource_models import ResourceAmounts
from ai_sdlc.core.stage_review.shadow_plan_reservation import (
    _hold_shadow_panel_plan as hold_shadow_panel_plan,
)
from ai_sdlc.core.stage_review.shadow_planner import (
    _build_shadow_panel_proposal as build_shadow_panel_proposal,
)
from ai_sdlc.core.stage_review.stage_review_execution import StageReviewExecutionRequest
from tests.integration.canonical_executor_fixtures import (
    allocations,
    candidate,
    descriptors,
    runtime_paths,
    transport,
)
from tests.unit.stage_review.test_bindings import (
    SequenceHostProbe,
    StaticAuthorityResolver,
    StaticRuntimeBroker,
)
from tests.unit.stage_review.test_isolation_execution import (
    _host,
    _manifest,
)
from tests.unit.stage_review.test_resources import _now


@dataclass
class _Clock:
    value: object

    def __call__(self):
        return self.value


@dataclass(frozen=True)
class _ExecutorRig:
    executor: CanonicalStageReviewExecutor
    request: StageReviewExecutionRequest
    broker: _RemoteBroker


class _EvidenceAdapter:
    def prepare(self, operation_id, allocations, host_snapshot, visibility_barrier_id):
        return tuple(
            build_isolation_execution_evidence(
                operation_id=operation_id,
                allocation=item,
                host_snapshot=host_snapshot,
                visibility_barrier_id=visibility_barrier_id,
                isolation_grade="enforced",
                isolation_backend="codex.permission-profile",
                candidate_snapshot_isolated=True,
                candidate_write_enforced=True,
                peer_outputs_hidden=True,
                disposable_home=True,
                disposable_config=True,
                disposable_credentials=True,
                output_isolated=True,
                user_home_protected=True,
                global_config_protected=True,
                network_policy_enforced=True,
                sentinel_environment_disposable=True,
                evidence_bundle_digest=f"sha256:evidence.{item.allocation_id}",
            )
            for item in allocations
        )


class _ExecutingBackend:
    def __init__(self) -> None:
        self.current = None

    def probe(self, context, now):
        seed = _manifest(
            isolation_execution,
            host_snapshot_digest=context.host_snapshot.snapshot_digest,
            release_manifest_digest=context.release_manifest_digest,
        )
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
        values.update(
            allocation_digest=context.allocation_digest,
            assignment_digest=context.assignment_digest,
            candidate_digest=context.candidate_digest,
            layout_digest=context.layout_digest,
        )
        self.current = isolation_execution.build_isolation_evidence_manifest(**values)
        return self.current

    def execute(self, command, permit):
        assert self.current is not None
        completed = subprocess.run(
            command.argv,
            input=command.stdin_text,
            capture_output=True,
            check=False,
            text=True,
        )
        return IsolationProcessResult(
            return_code=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
            process_id=202,
            parent_process_id=self.current.parent_process_id,
            boundary_results=self.current.boundary_results,
            os_native_denials=self.current.os_native_denials,
            before_digest="sha256:protected",
            after_digest="sha256:protected",
            cleanup_succeeded=True,
        )


class _RemoteBroker:
    remote_provider_exercised = True

    def __init__(self) -> None:
        self.calls = 0

    def exchange(self, permit, envelope):
        self.calls += 1
        packet = envelope.payload["packet"]
        assert isinstance(packet, dict)
        capabilities = tuple(sorted(packet["capability_ids"]))
        return {
            "provider_call_id": f"provider-call.{self.calls}",
            "review": {
                "verdict": "passed",
                "coverage": {"reviewed_area_ids": capabilities},
                "findings": [],
                "evidence_digests": [f"sha256:review.{self.calls}"],
            },
            "accounted_usage": metered_provider_usage(
                ResourceAmounts(
                    provider_calls=1,
                    review_passes=1,
                    tokens=100,
                    cost=0.1,
                    active_wall_clock=1,
                )
            ).model_dump(mode="json"),
        }


def test_canonical_executor_authorizes_and_replays_without_provider_recall(
    tmp_path: Path,
) -> None:
    rig = _executor_rig(tmp_path, transport_available=True)

    first = rig.executor.execute(rig.request)
    repeated = rig.executor.execute(rig.request)

    assert first.status == "completed", first
    assert repeated.status == "completed", repeated
    assert first == repeated
    assert rig.broker.calls == 2
    observations = OptimizationObservationStore(
        tmp_path,
        project_id=rig.request.candidate.project_id,
    ).read_session(rig.request.candidate.review_session_id)
    assert tuple(item.observation_kind for item in observations) == ("created",)


def test_canonical_executor_fails_closed_when_remote_transport_is_missing(
    tmp_path: Path,
) -> None:
    rig = _executor_rig(tmp_path, transport_available=False)

    outcome = rig.executor.execute(rig.request)

    assert outcome.status == "needs_user"
    assert outcome.reason_code == "review-provider-unavailable"
    assert rig.broker.calls == 0
    observations = OptimizationObservationStore(
        tmp_path,
        project_id=rig.request.candidate.project_id,
    ).read_session(rig.request.candidate.review_session_id)
    assert tuple(item.observation_kind for item in observations) == (
        "created",
        "needs_user",
    )


def test_canonical_executor_records_provider_timeout_before_propagating(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rig = _executor_rig(tmp_path, transport_available=True)

    def timed_out(*_args, **_kwargs):
        raise TimeoutError("provider timed out")

    monkeypatch.setattr(rig.broker, "exchange", timed_out)

    with pytest.raises(TimeoutError, match="provider timed out"):
        rig.executor.execute(rig.request)

    observations = OptimizationObservationStore(
        tmp_path,
        project_id=rig.request.candidate.project_id,
    ).read_session(rig.request.candidate.review_session_id)
    assert tuple(item.observation_kind for item in observations) == (
        "created",
        "timed_out",
    )


def test_canonical_executor_exposes_only_the_authorized_session_service(
    tmp_path: Path,
) -> None:
    authorized = []
    rig = _executor_rig(
        tmp_path,
        transport_available=True,
        on_authorized=authorized.append,
    )

    outcome = rig.executor.execute(rig.request)

    assert outcome.status == "completed"
    assert len(authorized) == 1
    assert authorized[0].get(execution_scope(rig.request)).state == "authorized"


def _executor_rig(
    root: Path,
    *,
    transport_available: bool,
    on_authorized=None,
    source_kind: str = "local-unstaged",
) -> _ExecutorRig:
    candidate_manifest, snapshot = candidate(root, source_kind=source_kind)
    planned = build_shadow_panel_proposal(
        candidate=candidate_manifest,
        activation_policy=current_activation_policy(root),
        enforcement_mode="enforce",
    )
    held = hold_shadow_panel_plan(root, planned)
    authority = build_binding_authority_snapshot(
        plan=held.plan,
        risk_level=planned.risk_profile.risk_level,
        enforcement_mode="enforce",
        provider_descriptors=descriptors(held.plan),
        attestor_id="ai-sdlc.codex-runtime",
        attestor_version="1.0.0",
        attestation_evidence_digest=_trusted_published_codex_release_digests()[0],
    )
    runtime_allocations = allocations(
        held.plan,
        authority,
        candidate_binding_digest(candidate_manifest),
    )
    bindings, paths, journal, invocations = _binding_runtime(
        root,
        candidate_manifest,
        snapshot,
        held,
        authority,
        runtime_allocations,
    )
    broker = _RemoteBroker()
    factory = _driver_factory(
        root,
        candidate_manifest,
        authority,
        broker,
        bindings,
        paths,
        transport_available=transport_available,
    )
    executor = _canonical_executor(
        root,
        bindings,
        authority,
        journal,
        invocations,
        factory,
        on_authorized,
    )
    request = _execution_request(candidate_manifest, snapshot, planned, held)
    return _ExecutorRig(executor, request, broker)


def _driver_factory(
    root, candidate_manifest, authority, broker, bindings, paths, *, transport_available
):
    executions = _executions(
        root,
        candidate_manifest.project_id,
        authority,
        broker if transport_available else None,
    )
    return RemoteReviewDriverFactory(
        bindings=bindings,
        allocation_path_resolver=paths,
        executions=executions,
    )


def _canonical_executor(
    root,
    bindings,
    authority,
    journal,
    invocations,
    factory,
    on_authorized,
):
    return CanonicalStageReviewExecutor(
        root,
        bindings=bindings,
        binding_authority=authority,
        journal=journal,
        invocations=invocations,
        drivers=factory,
        clock=_Clock(_now()),
        on_authorized=on_authorized,
    )


def _execution_request(candidate_manifest, snapshot, planned, held):
    return StageReviewExecutionRequest(
        candidate=candidate_manifest,
        source_snapshot=snapshot,
        proposal=planned,
        plan=held.plan,
        budget_policy=planned.budget_policy,
        governor=held.governor,
        lease_owner=held.lease_owner,
        mode="enforce",
    )


def _binding_runtime(root, candidate, snapshot, held, authority, allocations):
    host = _host(
        isolation_execution,
        release_manifest_digest=authority.attestation_evidence_digest,
    )
    bindings = ReviewerBindingService(
        root,
        project_id=candidate.project_id,
        resource_governor=held.governor,
        authority_resolver=StaticAuthorityResolver(authority),
        host_probe=SequenceHostProbe([host]),
        runtime_broker=StaticRuntimeBroker(allocations),
        isolation_adapter=_EvidenceAdapter(),
    )
    paths = runtime_paths(root, allocations, candidate, snapshot)
    journal = ProviderInvocationJournal(
        root,
        project_id=candidate.project_id,
        resource_governor=held.governor,
    )
    launcher = ReviewerIsolationLauncher(
        root,
        registry=isolation_execution.TrustedIsolationBackendRegistry.default(),
        backend=_ExecutingBackend(),
        project_id=candidate.project_id,
    )
    invocations = ReviewerInvocationCoordinator(
        bindings,
        journal,
        isolation_launcher=launcher,
        allocation_path_resolver=paths,
    )
    return bindings, paths, journal, invocations


def _executions(root, project_id, authority, broker):
    registry = ProviderExecutionAdapterRegistry()
    for descriptor in authority.provider_descriptors:
        provider_transport = transport(root, project_id, broker, descriptor)
        registry.register_reviewer(descriptor, provider_transport)
    return registry.freeze()
