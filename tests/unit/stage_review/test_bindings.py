from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path

import pytest
from tests.unit.stage_review.test_resources import (
    _OWNER,
    _final_reservation,
    _governor,
    _now,
    _policy,
    _proposal,
)

from ai_sdlc.core.stage_review import (
    binding_lineage,
    provider_authority_registry,
)
from ai_sdlc.core.stage_review.artifacts import SharedStateIntegrityError
from ai_sdlc.core.stage_review.binding_store import BindingArtifactStore
from ai_sdlc.core.stage_review.bindings import (
    BindingAuthoritySnapshot,
    HostCapabilitySnapshot,
    IsolationGrade,
    ProviderAvailabilityAttestation,
    ProviderBindingDescriptor,
    ReviewerBindingService,
    ReviewerRuntimeAllocation,
    build_binding_attempt_request,
    build_binding_authority_snapshot,
    build_host_capability_snapshot,
    build_isolation_execution_evidence,
    build_provider_availability_attestation,
    build_provider_binding_descriptor,
    build_runtime_allocation,
)
from ai_sdlc.core.stage_review.panel_finalization import (
    _build_reviewer_panel_plan,
)
from ai_sdlc.core.stage_review.panel_plan_models import ReviewerPanelPlan
from ai_sdlc.core.stage_review.provider_journal import ProviderRecoveryCapabilities
from ai_sdlc.core.stage_review.resource_builders import utc_iso
from ai_sdlc.core.stage_review.resource_ledger_models import ResourceReservation
from ai_sdlc.core.stage_review.resources import ResourceGovernor

pytestmark = pytest.mark.usefixtures("allow_synthetic_binding_authority")


@dataclass
class StaticAuthorityResolver:
    snapshot: BindingAuthoritySnapshot
    calls: int = 0

    def resolve(self, plan: ReviewerPanelPlan) -> BindingAuthoritySnapshot:
        self.calls += 1
        return self.snapshot


@dataclass
class StaticAvailabilityResolver:
    attestation: ProviderAvailabilityAttestation | None = None
    calls: int = 0

    def resolve(
        self,
        plan: ReviewerPanelPlan,
        previous_binding_set_digest: str,
    ) -> ProviderAvailabilityAttestation | None:
        del plan, previous_binding_set_digest
        self.calls += 1
        return self.attestation


@dataclass
class SequenceHostProbe:
    snapshots: list[HostCapabilitySnapshot]
    calls: int = 0
    previous_snapshot_digests: list[str] = field(default_factory=list)

    def probe(self, previous_snapshot_digest: str = "") -> HostCapabilitySnapshot:
        self.previous_snapshot_digests.append(previous_snapshot_digest)
        index = min(self.calls, len(self.snapshots) - 1)
        self.calls += 1
        return self.snapshots[index]


@dataclass
class StaticRuntimeBroker:
    allocations: tuple[ReviewerRuntimeAllocation, ...]
    calls: int = 0

    def allocate(
        self,
        operation_id: str,
        plan: ReviewerPanelPlan,
        authority: BindingAuthoritySnapshot,
    ) -> tuple[ReviewerRuntimeAllocation, ...]:
        del operation_id, plan, authority
        self.calls += 1
        return self.allocations


@dataclass
class FakeIsolationAdapter:
    grade: IsolationGrade = "enforced"
    calls: int = 0

    def prepare(
        self,
        operation_id: str,
        allocations: tuple[ReviewerRuntimeAllocation, ...],
        host_snapshot: HostCapabilitySnapshot,
        visibility_barrier_id: str,
    ) -> tuple[object, ...]:
        self.calls += 1
        return tuple(
            build_isolation_execution_evidence(
                operation_id=operation_id,
                allocation=allocation,
                host_snapshot=host_snapshot,
                visibility_barrier_id=visibility_barrier_id,
                isolation_grade=self.grade,
                isolation_backend="sandbox.test",
                candidate_snapshot_isolated=True,
                candidate_write_enforced=self.grade == "enforced",
                peer_outputs_hidden=True,
                disposable_home=True,
                disposable_config=True,
                disposable_credentials=True,
                output_isolated=True,
                user_home_protected=self.grade == "enforced",
                global_config_protected=self.grade == "enforced",
                network_policy_enforced=self.grade == "enforced",
                sentinel_environment_disposable=True,
                evidence_bundle_digest=(
                    f"sha256:isolation.{allocation.allocation_id}.{self.calls}"
                ),
            )
            for allocation in allocations
        )


def test_bind_freezes_lineage_and_charges_attempt_exactly_once(tmp_path: Path) -> None:
    context = _context(tmp_path)
    authority = _authority(context.plan, suffixes=("a", "b"))
    host = _host_snapshot()
    allocations = _allocations(context.plan, authority, suffix="a", run="one")
    probe = SequenceHostProbe([host])
    broker = StaticRuntimeBroker(allocations)
    service = _service(context, authority, probe, broker)
    request = _request(context)

    first = service.bind(
        context.plan,
        request=request,
        budget_policy=_policy(),
        lease_owner=_OWNER,
        now=_now(),
    )
    repeated = service.bind(
        context.plan,
        request=request,
        budget_policy=_policy(),
        lease_owner=_OWNER,
        now=_now(),
    )

    assert first.result_code == repeated.result_code == "bound"
    assert first.binding_set == repeated.binding_set
    assert first.binding_set is not None
    assert first.binding_set.plan_digest == context.plan.plan_digest
    assert first.binding_set.final_reservation_digest == (
        context.final.reservation_digest
    )
    assert first.binding_set.authority_snapshot_digest == authority.snapshot_digest
    assert all(
        binding.eligible_for_enforce_quorum for binding in first.binding_set.bindings
    )
    assert context.governor.get_reservation(context.final.reservation_id).usage == (
        context.final.usage.model_copy(update={"binding_attempts": 1})
    )
    assert broker.calls == probe.calls == 1
    assert service.get_binding_set(first.binding_set.binding_set_id) == (
        first.binding_set
    )


def test_bind_rejects_unregistered_authority_before_any_side_effect(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        binding_lineage,
        "_validate_registered_provider_authority",
        provider_authority_registry._validate_registered_provider_authority,
    )
    context = _context(tmp_path)
    authority = _authority(context.plan)
    host_probe = SequenceHostProbe([_host_snapshot()])
    broker = StaticRuntimeBroker(_allocations(context.plan, authority))
    adapter = FakeIsolationAdapter()
    request = _request(context)
    service = _service(
        context,
        authority,
        host_probe,
        broker,
        isolation_adapter=adapter,
    )

    result = service.bind(
        context.plan,
        request=request,
        budget_policy=_policy(),
        lease_owner=_OWNER,
        now=_now(),
    )

    current = context.governor.get_reservation(context.final.reservation_id)
    assert result.result_code == "provider_policy_blocked"
    assert result.reason_ids == ("binding.authority-untrusted",)
    assert current.usage.binding_attempts == 0
    assert host_probe.calls == 0
    assert broker.calls == 0
    assert adapter.calls == 0
    store = BindingArtifactStore(
        tmp_path,
        project_id=context.final.project_id,
        lock_timeout_seconds=2,
    )
    assert store.get_operation(request.operation_id) is None


def test_authority_snapshot_is_deterministic_across_pool_order(tmp_path: Path) -> None:
    context = _context(tmp_path)
    descriptors = _descriptors(context.plan, suffixes=("a", "b"))

    left = _authority(context.plan, descriptors=descriptors)
    right = _authority(context.plan, descriptors=tuple(reversed(descriptors)))

    assert left == right
    assert left.snapshot_digest == right.snapshot_digest


def test_authority_snapshot_ignores_nested_descriptor_runtime_metadata(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    descriptors = _descriptors(context.plan)
    shifted = tuple(
        descriptor.model_copy(update={"created_at": "2030-01-01T00:00:00Z"})
        for descriptor in descriptors
    )

    first = _authority(context.plan, descriptors=descriptors)
    second = _authority(context.plan, descriptors=shifted)

    assert first.snapshot_digest == second.snapshot_digest


def test_enforce_required_rejects_detected_only_but_persists_diagnostic(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    authority = _authority(context.plan, enforcement_mode="enforce")
    host = _host_snapshot()
    service = _service(
        context,
        authority,
        SequenceHostProbe([host]),
        StaticRuntimeBroker(_allocations(context.plan, authority)),
        grade="detected_only",
    )

    result = service.bind(
        context.plan,
        request=_request(context),
        budget_policy=_policy(),
        lease_owner=_OWNER,
        now=_now(),
    )

    assert result.result_code == "independence_unproven"
    assert result.binding_set is None
    assert result.reason_ids == ("binding.required-isolation-not-enforced",)
    assert result.diagnostic_evidence_digests
    assert service.get_attempt_result(result.operation_id) == result


def test_shadow_detected_only_is_never_enforce_eligible(tmp_path: Path) -> None:
    context = _context(tmp_path)
    authority = _authority(context.plan, enforcement_mode="shadow")
    host = _host_snapshot()
    service = _service(
        context,
        authority,
        SequenceHostProbe([host]),
        StaticRuntimeBroker(_allocations(context.plan, authority)),
        grade="detected_only",
    )

    result = service.bind(
        context.plan,
        request=_request(context),
        budget_policy=_policy(),
        lease_owner=_OWNER,
        now=_now(),
    )

    assert result.result_code == "bound"
    assert result.binding_set is not None
    assert not any(
        binding.eligible_for_enforce_quorum for binding in result.binding_set.bindings
    )
    assert result.binding_set.execution_mode == "shadow_only"


def test_rebind_uses_frozen_equivalence_pool_and_records_session_directive(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    authority = _authority(context.plan, suffixes=("a", "b"))
    host = _host_snapshot()
    initial = _service(
        context,
        authority,
        SequenceHostProbe([host]),
        StaticRuntimeBroker(
            _allocations(context.plan, authority, suffix="a", run="initial")
        ),
    ).bind(
        context.plan,
        request=_request(context),
        budget_policy=_policy(),
        lease_owner=_OWNER,
        now=_now(),
    )
    assert initial.binding_set is not None
    availability = _availability(
        context.plan,
        initial.binding_set.binding_set_digest,
        tuple(binding.provider_id for binding in initial.binding_set.bindings),
    )
    service = _service(
        context,
        authority,
        SequenceHostProbe([host]),
        StaticRuntimeBroker(
            _allocations(context.plan, authority, suffix="b", run="rebind")
        ),
        availability_attestation=availability,
    )
    request = _request(
        context,
        attempt_index=2,
        previous_binding_set_digest=initial.binding_set.binding_set_digest,
        expected_cohort_id="cohort.current",
        expected_pass_head_digest="sha256:pass-head",
        rebind_reason="provider_unavailable",
        availability_attestation=availability,
    )

    result = service.bind(
        context.plan,
        request=request,
        budget_policy=_policy(),
        lease_owner=_OWNER,
        now=_now(),
    )

    assert result.result_code == "bound"
    assert result.binding_set is not None
    assert result.binding_set.previous_binding_set_digest == (
        initial.binding_set.binding_set_digest
    )
    assert result.rebind_directive is not None
    assert result.rebind_directive.expected_cohort_id == "cohort.current"
    assert result.rebind_directive.expected_pass_head_digest == "sha256:pass-head"
    assert not hasattr(result.rebind_directive, "next_cohort_id")
    usage = context.governor.get_reservation(context.final.reservation_id).usage
    assert usage.binding_attempts == 2
    assert usage.provider_retries == 1


def test_same_attempt_slot_rejects_a_concurrent_rebind_fork(tmp_path: Path) -> None:
    context = _context(tmp_path)
    authority = _authority(context.plan, suffixes=("a", "b"))
    host = _host_snapshot()
    initial_service = _service(
        context,
        authority,
        SequenceHostProbe([host]),
        StaticRuntimeBroker(_allocations(context.plan, authority, suffix="a")),
    )
    initial = initial_service.bind(
        context.plan,
        request=_request(context),
        budget_policy=_policy(),
        lease_owner=_OWNER,
        now=_now(),
    )
    assert initial.binding_set is not None
    availability = _availability(
        context.plan,
        initial.binding_set.binding_set_digest,
        tuple(binding.provider_id for binding in initial.binding_set.bindings),
    )
    first_request = _request(
        context,
        attempt_index=2,
        previous_binding_set_digest=initial.binding_set.binding_set_digest,
        expected_cohort_id="cohort.current",
        expected_pass_head_digest="sha256:pass.one",
        rebind_reason="provider_unavailable",
        availability_attestation=availability,
    )
    first_service = _service(
        context,
        authority,
        SequenceHostProbe([host]),
        StaticRuntimeBroker(
            _allocations(context.plan, authority, suffix="b", run="first")
        ),
        availability_attestation=availability,
    )
    assert (
        first_service.bind(
            context.plan,
            request=first_request,
            budget_policy=_policy(),
            lease_owner=_OWNER,
            now=_now(),
        ).result_code
        == "bound"
    )
    forked_request = _request(
        context,
        attempt_index=2,
        previous_binding_set_digest=initial.binding_set.binding_set_digest,
        expected_cohort_id="cohort.current",
        expected_pass_head_digest="sha256:pass.fork",
        rebind_reason="provider_unavailable",
        availability_attestation=availability,
    )

    with pytest.raises(SharedStateIntegrityError, match="attempt lineage fork"):
        first_service.bind(
            context.plan,
            request=forked_request,
            budget_policy=_policy(),
            lease_owner=_OWNER,
            now=_now(),
        )


def test_dispatch_reprobes_host_and_binds_t302_assignment(tmp_path: Path) -> None:
    context = _context(tmp_path)
    authority = _authority(context.plan)
    host = _host_snapshot()
    probe = SequenceHostProbe([host, host])
    service = _service(
        context,
        authority,
        probe,
        StaticRuntimeBroker(_allocations(context.plan, authority)),
    )
    bound = service.bind(
        context.plan,
        request=_request(context),
        budget_policy=_policy(),
        lease_owner=_OWNER,
        now=_now(),
    )
    assert bound.binding_set is not None
    binding = bound.binding_set.bindings[0]

    dispatch = service.authorize_dispatch(
        binding_set_id=bound.binding_set.binding_set_id,
        slot_id=binding.slot_id,
        cohort_id="cohort.current",
        candidate_manifest_digest="sha256:candidate",
        expected_pass_head_digest="sha256:pass-head",
        now=_now(),
    )

    assert dispatch.result_code == "bound"
    assert dispatch.assignment is not None
    assert dispatch.assignment.binding_set_digest == (
        bound.binding_set.binding_set_digest
    )
    assert dispatch.assignment.host_snapshot_digest == host.snapshot_digest
    assert dispatch.assignment.cohort_id == "cohort.current"
    assert dispatch.assignment.slot_id == binding.slot_id
    assert dispatch.assignment.candidate_manifest_digest == "sha256:candidate"
    assert dispatch.assignment.assignment_digest.startswith(
        "reviewer-assignment:sha256:"
    )
    assert probe.calls == 2


def test_dispatch_blocks_expired_or_changed_host_before_provider_call(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    authority = _authority(context.plan)
    current = _host_snapshot()
    changed = _host_snapshot(
        host_session_id="host-session.changed",
        previous_snapshot_digest=current.snapshot_digest,
    )
    service = _service(
        context,
        authority,
        SequenceHostProbe([current, changed]),
        StaticRuntimeBroker(_allocations(context.plan, authority)),
    )
    bound = service.bind(
        context.plan,
        request=_request(context),
        budget_policy=_policy(),
        lease_owner=_OWNER,
        now=_now(),
    )
    assert bound.binding_set is not None

    dispatch = service.authorize_dispatch(
        binding_set_id=bound.binding_set.binding_set_id,
        slot_id=bound.binding_set.bindings[0].slot_id,
        cohort_id="cohort.current",
        candidate_manifest_digest="sha256:candidate",
        expected_pass_head_digest="sha256:pass-head",
        now=_now(),
    )

    assert dispatch.result_code == "provider_policy_blocked"
    assert dispatch.assignment is None
    assert dispatch.reason_ids == ("binding.host-capability-changed",)
    assert dispatch.requires_rebind


@dataclass(frozen=True)
class _BindingContext:
    governor: ResourceGovernor
    plan: ReviewerPanelPlan
    final: ResourceReservation
    root: Path


def _context(tmp_path: Path) -> _BindingContext:
    governor = _governor(tmp_path)
    proposal = _proposal()
    final = _final_reservation(governor, proposal=proposal)
    return _BindingContext(
        governor=governor,
        plan=_build_reviewer_panel_plan(proposal, final),
        final=final,
        root=tmp_path,
    )


def _host_snapshot(
    *,
    host_session_id: str = "host-session.local",
    previous_snapshot_digest: str = "",
) -> HostCapabilitySnapshot:
    return build_host_capability_snapshot(
        host_adapter_id="host-adapter.test",
        host_adapter_version="1.0.0",
        host_session_id=host_session_id,
        capability_ids=(
            "agent_execution",
            "isolation.sandbox.test",
            "network_enforcement.sandbox.test",
        ),
        capability_source="trusted-probe",
        evidence_digest=f"sha256:host-evidence.{host_session_id}",
        previous_snapshot_digest=previous_snapshot_digest,
        authorization_transition="probe-confirmed",
        issued_at=utc_iso(_now()),
        expires_at=utc_iso(_now() + timedelta(minutes=5)),
    )


def _descriptors(
    plan: ReviewerPanelPlan,
    *,
    suffixes: tuple[str, ...] = ("a",),
) -> tuple[ProviderBindingDescriptor, ...]:
    return tuple(
        build_provider_binding_descriptor(
            descriptor_id=f"provider-descriptor.{slot.role_profile_id}.{suffix}",
            provider_id=f"provider-runtime.{slot.role_profile_id}.{suffix}",
            equivalence_class_id=f"provider-equivalence.{slot.role_profile_id}",
            model_family=f"model.{slot.role_profile_id}.{suffix}",
            role_contract_digests=(slot.role_contract_digest,),
            capability_ids=slot.capability_ids,
            provider_tags=slot.provider_constraints,
            tool_allowlist=slot.tool_permission_ids,
            recovery_capabilities=ProviderRecoveryCapabilities(
                idempotency_support=True,
                invocation_query_support=True,
                cost_metering_support=True,
            ),
            isolation_backend="sandbox.test",
            network_enforcement=True,
            supported_independence_grade="model_diversity_proven",
            provider_policy_evidence_digest=(
                f"sha256:provider-policy.{slot.role_profile_id}.{suffix}"
            ),
        )
        for slot in plan.proposal.required_slots
        for suffix in suffixes
    )


def _authority(
    plan: ReviewerPanelPlan,
    *,
    suffixes: tuple[str, ...] = ("a",),
    descriptors: tuple[ProviderBindingDescriptor, ...] | None = None,
    enforcement_mode: str = "enforce",
) -> BindingAuthoritySnapshot:
    return build_binding_authority_snapshot(
        plan=plan,
        risk_level="low",
        enforcement_mode=enforcement_mode,
        provider_descriptors=descriptors or _descriptors(plan, suffixes=suffixes),
        attestor_id="binding-authority.test",
        attestor_version="1.0.0",
        attestation_evidence_digest="sha256:binding-authority-evidence",
    )


def _allocations(
    plan: ReviewerPanelPlan,
    authority: BindingAuthoritySnapshot,
    *,
    suffix: str = "a",
    run: str = "one",
) -> tuple[ReviewerRuntimeAllocation, ...]:
    by_provider = {
        descriptor.provider_id: descriptor
        for descriptor in authority.provider_descriptors
    }
    values = []
    for index, slot in enumerate(plan.proposal.required_slots, start=1):
        provider_id = f"provider-runtime.{slot.role_profile_id}.{suffix}"
        values.append(
            build_runtime_allocation(
                allocation_id=f"runtime-allocation.{slot.role_profile_id}.{run}",
                slot_id=slot.slot_id,
                actor_id=f"actor.{slot.role_profile_id}.{run}",
                session_id=f"provider-session.{slot.role_profile_id}.{run}",
                provider_descriptor=by_provider[provider_id],
                candidate_manifest_digest="sha256:candidate",
                candidate_snapshot_id=f"candidate-snapshot.{index}.{run}",
                working_directory_id=f"cwd.{index}.{run}",
                disposable_home_id=f"home.{index}.{run}",
                disposable_config_id=f"config.{index}.{run}",
                disposable_credential_view_id=f"credential.{index}.{run}",
                output_directory_id=f"output.{index}.{run}",
                allocation_operation_id=f"allocation-operation.{index}.{run}",
            )
        )
    return tuple(values)


def _request(
    context: _BindingContext,
    *,
    attempt_index: int = 1,
    previous_binding_set_digest: str = "",
    expected_cohort_id: str = "",
    expected_pass_head_digest: str = "",
    rebind_reason: str = "initial_binding",
    availability_attestation: ProviderAvailabilityAttestation | None = None,
):
    return build_binding_attempt_request(
        plan=context.plan,
        final_reservation=context.final,
        candidate_manifest_digest="sha256:candidate",
        input_packet_digest="sha256:input-packet",
        visibility_barrier_id="visibility-barrier.initial",
        attempt_index=attempt_index,
        previous_binding_set_digest=previous_binding_set_digest,
        expected_cohort_id=expected_cohort_id,
        expected_pass_head_digest=expected_pass_head_digest,
        rebind_reason=rebind_reason,
        availability_attestation=availability_attestation,
    )


def _service(
    context: _BindingContext,
    authority: BindingAuthoritySnapshot,
    host_probe: SequenceHostProbe,
    broker: StaticRuntimeBroker,
    *,
    grade: IsolationGrade = "enforced",
    availability_attestation: ProviderAvailabilityAttestation | None = None,
    isolation_adapter: FakeIsolationAdapter | None = None,
) -> ReviewerBindingService:
    return ReviewerBindingService(
        context.root,
        project_id=context.final.project_id,
        resource_governor=context.governor,
        authority_resolver=StaticAuthorityResolver(authority),
        host_probe=host_probe,
        runtime_broker=broker,
        isolation_adapter=isolation_adapter or FakeIsolationAdapter(grade),
        availability_resolver=StaticAvailabilityResolver(availability_attestation),
    )


def _availability(
    plan: ReviewerPanelPlan,
    previous_binding_set_digest: str,
    unavailable_provider_ids: tuple[str, ...],
) -> ProviderAvailabilityAttestation:
    return build_provider_availability_attestation(
        plan_digest=plan.plan_digest,
        previous_binding_set_digest=previous_binding_set_digest,
        unavailable_provider_ids=unavailable_provider_ids,
        source_journal_event_digests=("sha256:provider-unavailable-event",),
        attestor_id="provider-availability.test",
        attestor_version="1.0.0",
        evidence_digest=(f"sha256:provider-availability.{previous_binding_set_digest}"),
        issued_at=utc_iso(_now()),
        expires_at=utc_iso(_now() + timedelta(minutes=5)),
    )
