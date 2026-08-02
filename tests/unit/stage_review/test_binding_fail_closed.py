from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from threading import Event

import pytest
from tests.unit.stage_review.test_bindings import (
    FakeIsolationAdapter,
    SequenceHostProbe,
    StaticAuthorityResolver,
    StaticRuntimeBroker,
    _allocations,
    _authority,
    _availability,
    _context,
    _descriptors,
    _host_snapshot,
    _request,
    _service,
)
from tests.unit.stage_review.test_resources import _OWNER, _git, _now, _policy

from ai_sdlc.core.stage_review.binding_builders import build_runtime_allocation
from ai_sdlc.core.stage_review.binding_digests import binding_attempt_request_digest
from ai_sdlc.core.stage_review.binding_models import (
    BindingAttemptRequest,
    BindingAuthoritySnapshot,
    ReviewerRuntimeAllocation,
)
from ai_sdlc.core.stage_review.binding_store import BindingArtifactStore
from ai_sdlc.core.stage_review.bindings import (
    ReviewerBindingService,
    RuntimeSessionCreationError,
    VisibilityBarrierError,
    build_host_capability_snapshot,
    build_provider_binding_descriptor,
)
from ai_sdlc.core.stage_review.panel_plan_models import ReviewerPanelPlan
from ai_sdlc.core.stage_review.provider_journal import (
    ProviderRecoveryCapabilities,
    build_provider_invocation_request,
)
from ai_sdlc.core.stage_review.resource_models import ResourceAmounts

pytestmark = pytest.mark.usefixtures("allow_synthetic_binding_authority")


@dataclass
class RaisingRuntimeBroker:
    calls: int = 0

    def allocate(
        self,
        operation_id: str,
        plan: ReviewerPanelPlan,
        authority: BindingAuthoritySnapshot,
    ) -> tuple[ReviewerRuntimeAllocation, ...]:
        del operation_id, plan, authority
        self.calls += 1
        raise RuntimeSessionCreationError("provider session unavailable")


@dataclass
class RaisingIsolationAdapter:
    calls: int = 0

    def prepare(self, *args: object) -> tuple[object, ...]:
        del args
        self.calls += 1
        raise VisibilityBarrierError("barrier setup failed")


@dataclass
class CrashOnceRuntimeBroker:
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
        if self.calls == 1:
            raise RuntimeError("simulated process crash")
        return self.allocations


@dataclass
class BlockingRuntimeBroker:
    allocations: tuple[ReviewerRuntimeAllocation, ...]
    entered: Event
    release: Event
    calls: int = 0

    def allocate(
        self,
        operation_id: str,
        plan: ReviewerPanelPlan,
        authority: BindingAuthoritySnapshot,
    ) -> tuple[ReviewerRuntimeAllocation, ...]:
        del operation_id, plan, authority
        self.calls += 1
        self.entered.set()
        if not self.release.wait(timeout=2):
            raise RuntimeError("test did not release runtime allocation")
        return self.allocations


def test_forged_frozen_reservation_is_blocked_before_runtime_calls(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    authority = _authority(context.plan)
    probe = SequenceHostProbe([_host_snapshot()])
    broker = StaticRuntimeBroker(_allocations(context.plan, authority))
    service = _service(context, authority, probe, broker)
    request = _replace_request(
        _request(context),
        final_reservation_digest="sha256:forged-reservation",
    )

    result = service.bind(
        context.plan,
        request=request,
        budget_policy=_policy(),
        lease_owner=_OWNER,
        now=_now(),
    )

    assert result.result_code == "provider_policy_blocked"
    assert result.reason_ids == ("binding.resource-ancestor-invalid",)
    assert probe.calls == broker.calls == 0


def test_runtime_candidate_outside_attested_provider_pool_is_blocked(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    authority = _authority(context.plan)
    allocations = list(_allocations(context.plan, authority))
    untrusted = _descriptors(context.plan, suffixes=("untrusted",))[0]
    original = allocations[0]
    allocations[0] = build_runtime_allocation(
        allocation_id="allocation.untrusted",
        slot_id=original.slot_id,
        actor_id=original.actor_id,
        session_id=original.session_id,
        provider_descriptor=untrusted,
        candidate_manifest_digest=original.candidate_manifest_digest,
        candidate_snapshot_id=original.candidate_snapshot_id,
        working_directory_id=original.working_directory_id,
        disposable_home_id=original.disposable_home_id,
        disposable_config_id=original.disposable_config_id,
        disposable_credential_view_id=original.disposable_credential_view_id,
        output_directory_id=original.output_directory_id,
        allocation_operation_id=original.allocation_operation_id,
    )
    service = _service(
        context,
        authority,
        SequenceHostProbe([_host_snapshot()]),
        StaticRuntimeBroker(tuple(allocations)),
    )

    result = service.bind(
        context.plan,
        request=_request(context),
        budget_policy=_policy(),
        lease_owner=_OWNER,
        now=_now(),
    )

    assert result.result_code == "provider_policy_blocked"
    assert result.reason_ids == ("binding.provider-policy-mismatch",)


def test_duplicate_runtime_identity_is_independence_unproven(tmp_path: Path) -> None:
    context = _context(tmp_path)
    authority = _authority(context.plan)
    allocations = list(_allocations(context.plan, authority))
    second = allocations[1]
    descriptor = next(
        item
        for item in authority.provider_descriptors
        if item.descriptor_digest == second.provider_descriptor_digest
    )
    allocations[1] = build_runtime_allocation(
        allocation_id=second.allocation_id,
        slot_id=second.slot_id,
        actor_id=allocations[0].actor_id,
        session_id=second.session_id,
        provider_descriptor=descriptor,
        candidate_manifest_digest=second.candidate_manifest_digest,
        candidate_snapshot_id=second.candidate_snapshot_id,
        working_directory_id=second.working_directory_id,
        disposable_home_id=second.disposable_home_id,
        disposable_config_id=second.disposable_config_id,
        disposable_credential_view_id=second.disposable_credential_view_id,
        output_directory_id=second.output_directory_id,
        allocation_operation_id=second.allocation_operation_id,
    )
    result = _service(
        context,
        authority,
        SequenceHostProbe([_host_snapshot()]),
        StaticRuntimeBroker(tuple(allocations)),
    ).bind(
        context.plan,
        request=_request(context),
        budget_policy=_policy(),
        lease_owner=_OWNER,
        now=_now(),
    )

    assert result.result_code == "independence_unproven"
    assert result.reason_ids == ("binding.runtime-independence-unproven",)
    assert result.binding_set is None


@pytest.mark.parametrize(
    ("failure", "expected_code", "expected_reason"),
    (
        ("actor", "actor_unavailable", "binding.required-actor-unavailable"),
        (
            "session",
            "session_creation_failed",
            "binding.session-creation-failed",
        ),
        (
            "barrier",
            "visibility_barrier_failed",
            "binding.visibility-barrier-failed",
        ),
    ),
)
def test_binding_failure_codes_are_exact_and_persisted(
    tmp_path: Path,
    failure: str,
    expected_code: str,
    expected_reason: str,
) -> None:
    context = _context(tmp_path)
    authority = _authority(context.plan)
    broker: object
    adapter: object = FakeIsolationAdapter()
    if failure == "actor":
        broker = StaticRuntimeBroker(())
    elif failure == "session":
        broker = RaisingRuntimeBroker()
    else:
        broker = StaticRuntimeBroker(_allocations(context.plan, authority))
        adapter = RaisingIsolationAdapter()
    service = ReviewerBindingService(
        context.root,
        project_id=context.final.project_id,
        resource_governor=context.governor,
        authority_resolver=StaticAuthorityResolver(authority),
        host_probe=SequenceHostProbe([_host_snapshot()]),
        runtime_broker=broker,  # type: ignore[arg-type]
        isolation_adapter=adapter,  # type: ignore[arg-type]
    )

    result = service.bind(
        context.plan,
        request=_request(context),
        budget_policy=_policy(),
        lease_owner=_OWNER,
        now=_now(),
    )

    assert result.result_code == expected_code
    assert result.reason_ids == (expected_reason,)
    assert service.get_attempt_result(result.operation_id) == result


def test_expired_or_environment_only_host_is_never_bound(tmp_path: Path) -> None:
    for index, snapshot in enumerate(
        (
            build_host_capability_snapshot(
                host_adapter_id="host.test",
                host_adapter_version="1.0.0",
                host_session_id="host.expired",
                capability_ids=("agent_execution",),
                capability_source="trusted-probe",
                evidence_digest="sha256:expired",
                previous_snapshot_digest="",
                authorization_transition="probe-confirmed",
                issued_at="2026-07-20T11:00:00Z",
                expires_at="2026-07-20T11:30:00Z",
            ),
            build_host_capability_snapshot(
                host_adapter_id="host.test",
                host_adapter_version="1.0.0",
                host_session_id="host.environment",
                capability_ids=("agent_execution",),
                capability_source="environment-only",
                evidence_digest="sha256:environment",
                previous_snapshot_digest="",
                authorization_transition="unverified",
                issued_at="2026-07-20T12:00:00Z",
                expires_at="2026-07-20T12:05:00Z",
            ),
        ),
        start=1,
    ):
        context = _context(tmp_path / str(index))
        authority = _authority(context.plan)
        broker = StaticRuntimeBroker(_allocations(context.plan, authority))
        result = _service(
            context,
            authority,
            SequenceHostProbe([snapshot]),
            broker,
        ).bind(
            context.plan,
            request=_request(context),
            budget_policy=_policy(),
            lease_owner=_OWNER,
            now=_now(),
        )
        assert result.result_code == "provider_policy_blocked"
        assert broker.calls == 0


def test_crash_after_charge_replays_without_double_charging(tmp_path: Path) -> None:
    context = _context(tmp_path)
    authority = _authority(context.plan)
    broker = CrashOnceRuntimeBroker(_allocations(context.plan, authority))
    service = ReviewerBindingService(
        context.root,
        project_id=context.final.project_id,
        resource_governor=context.governor,
        authority_resolver=StaticAuthorityResolver(authority),
        host_probe=SequenceHostProbe([_host_snapshot(), _host_snapshot()]),
        runtime_broker=broker,
        isolation_adapter=FakeIsolationAdapter(),
    )
    request = _request(context)

    with pytest.raises(RuntimeError, match="simulated process crash"):
        service.bind(
            context.plan,
            request=request,
            budget_policy=_policy(),
            lease_owner=_OWNER,
            now=_now(),
        )
    recovered = service.bind(
        context.plan,
        request=request,
        budget_policy=_policy(),
        lease_owner=_OWNER,
        now=_now(),
    )

    assert recovered.result_code == "bound"
    assert broker.calls == 2
    usage = context.governor.get_reservation(context.final.reservation_id).usage
    assert usage.binding_attempts == 1
    assert usage.provider_retries == 0


def test_rebind_rejects_a_changed_authority_before_budget_or_runtime(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    original = _authority(context.plan, suffixes=("a", "b"))
    initial = _service(
        context,
        original,
        SequenceHostProbe([_host_snapshot()]),
        StaticRuntimeBroker(_allocations(context.plan, original, suffix="a")),
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
        tuple(item.provider_id for item in initial.binding_set.bindings),
    )
    changed = _authority(context.plan, suffixes=("a", "b", "injected"))
    broker = StaticRuntimeBroker(_allocations(context.plan, changed, suffix="b"))
    request = _request(
        context,
        attempt_index=2,
        previous_binding_set_digest=initial.binding_set.binding_set_digest,
        expected_cohort_id="cohort.current",
        expected_pass_head_digest="sha256:pass-head",
        rebind_reason="provider_unavailable",
        availability_attestation=availability,
    )

    result = _service(
        context,
        changed,
        SequenceHostProbe([_host_snapshot()]),
        broker,
        availability_attestation=availability,
    ).bind(
        context.plan,
        request=request,
        budget_policy=_policy(),
        lease_owner=_OWNER,
        now=_now(),
    )

    assert result.result_code == "provider_policy_blocked"
    assert result.reason_ids == ("binding.provider-pool-changed",)
    assert broker.calls == 0
    usage = context.governor.get_reservation(context.final.reservation_id).usage
    assert usage.binding_attempts == 1


def test_invalid_binding_set_identity_has_no_filesystem_side_effect(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    authority = _authority(context.plan)
    service = _service(
        context,
        authority,
        SequenceHostProbe([_host_snapshot()]),
        StaticRuntimeBroker(_allocations(context.plan, authority)),
    )
    before = tuple(sorted(path.relative_to(tmp_path) for path in tmp_path.rglob("*")))

    with pytest.raises(ValueError, match="identity is invalid"):
        service.get_binding_set("../../outside")

    after = tuple(sorted(path.relative_to(tmp_path) for path in tmp_path.rglob("*")))
    assert after == before


def test_same_provider_and_model_only_proves_session_independence(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    descriptors = tuple(
        build_provider_binding_descriptor(
            descriptor_id=f"descriptor.shared.{slot.slot_id}",
            provider_id="provider.shared",
            equivalence_class_id="provider-equivalence.shared",
            model_family="model.shared",
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
            supported_independence_grade="session_independent",
            provider_policy_evidence_digest=f"sha256:policy.{slot.slot_id}",
        )
        for slot in context.plan.proposal.required_slots
    )
    authority = _authority(context.plan, descriptors=descriptors)
    allocations = tuple(
        build_runtime_allocation(
            allocation_id=f"allocation.{index}",
            slot_id=slot.slot_id,
            actor_id=f"actor.{index}",
            session_id=f"session.{index}",
            provider_descriptor=descriptor,
            candidate_manifest_digest="sha256:candidate",
            candidate_snapshot_id=f"snapshot.{index}",
            working_directory_id=f"cwd.{index}",
            disposable_home_id=f"home.{index}",
            disposable_config_id=f"config.{index}",
            disposable_credential_view_id=f"credential.{index}",
            output_directory_id=f"output.{index}",
            allocation_operation_id=f"allocation-operation.{index}",
        )
        for index, (slot, descriptor) in enumerate(
            zip(context.plan.proposal.required_slots, descriptors, strict=True),
            start=1,
        )
    )

    result = _service(
        context,
        authority,
        SequenceHostProbe([_host_snapshot()]),
        StaticRuntimeBroker(allocations),
    ).bind(
        context.plan,
        request=_request(context),
        budget_policy=_policy(),
        lease_owner=_OWNER,
        now=_now(),
    )

    assert result.binding_set is not None
    assert tuple(
        proof.independence_grade for proof in result.binding_set.independence_proofs
    ) == ("session_independent",)


def test_binding_and_provider_retry_hard_limits_stop_before_runtime(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    authority = _authority(context.plan, suffixes=("a", "b", "c", "d"))
    previous = _service(
        context,
        authority,
        SequenceHostProbe([_host_snapshot()]),
        StaticRuntimeBroker(_allocations(context.plan, authority, suffix="a")),
    ).bind(
        context.plan,
        request=_request(context),
        budget_policy=_policy(),
        lease_owner=_OWNER,
        now=_now(),
    )
    assert previous.binding_set is not None
    for attempt, suffix in ((2, "b"), (3, "c")):
        old = previous.binding_set
        availability = _availability(
            context.plan,
            old.binding_set_digest,
            tuple(item.provider_id for item in old.bindings),
        )
        previous = _service(
            context,
            authority,
            SequenceHostProbe([_host_snapshot()]),
            StaticRuntimeBroker(
                _allocations(context.plan, authority, suffix=suffix, run=suffix)
            ),
            availability_attestation=availability,
        ).bind(
            context.plan,
            request=_request(
                context,
                attempt_index=attempt,
                previous_binding_set_digest=old.binding_set_digest,
                expected_cohort_id="cohort.current",
                expected_pass_head_digest=f"sha256:pass.{attempt}",
                rebind_reason="provider_unavailable",
                availability_attestation=availability,
            ),
            budget_policy=_policy(),
            lease_owner=_OWNER,
            now=_now(),
        )
        assert previous.binding_set is not None
    old = previous.binding_set
    availability = _availability(
        context.plan,
        old.binding_set_digest,
        tuple(item.provider_id for item in old.bindings),
    )
    blocked_broker = StaticRuntimeBroker(
        _allocations(context.plan, authority, suffix="d", run="d")
    )

    blocked = _service(
        context,
        authority,
        SequenceHostProbe([_host_snapshot()]),
        blocked_broker,
        availability_attestation=availability,
    ).bind(
        context.plan,
        request=_request(
            context,
            attempt_index=4,
            previous_binding_set_digest=old.binding_set_digest,
            expected_cohort_id="cohort.current",
            expected_pass_head_digest="sha256:pass.4",
            rebind_reason="provider_unavailable",
            availability_attestation=availability,
        ),
        budget_policy=_policy(),
        lease_owner=_OWNER,
        now=_now(),
    )

    assert blocked.result_code == "provider_policy_blocked"
    assert blocked.reason_ids == ("binding.budget-exhausted",)
    assert blocked_broker.calls == 0
    usage = context.governor.get_reservation(context.final.reservation_id).usage
    assert usage.binding_attempts == 3
    assert usage.provider_retries == 2


def test_dispatch_assignment_is_the_t302_provider_journal_authority(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    authority = _authority(context.plan)
    host = _host_snapshot()
    service = _service(
        context,
        authority,
        SequenceHostProbe([host, host]),
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
    assert dispatch.assignment is not None

    invocation = build_provider_invocation_request(
        project_id=context.final.project_id,
        work_item_id=context.final.work_item_id,
        stage_review_session_id=context.final.stage_review_session_id,
        owner_scope_id="owner.reviewer",
        candidate_digest=dispatch.assignment.candidate_manifest_digest,
        assignment_digest=dispatch.assignment.assignment_digest,
        epoch_id="cohort.current",
        provider_id=dispatch.assignment.provider_id,
        request_digest="sha256:review-request",
        reservation_id=context.final.reservation_id,
        expected_reservation_digest=(
            context.governor.get_reservation(
                context.final.reservation_id
            ).reservation_digest
        ),
        expected_fencing_token=context.final.fencing_token,
        anticipated_usage=ResourceAmounts(
            provider_calls=1,
            tokens=1,
            cost=0.01,
            active_wall_clock=0.1,
            parallelism=1,
        ),
        capabilities=ProviderRecoveryCapabilities(
            idempotency_support=True,
            invocation_query_support=True,
            cost_metering_support=True,
        ),
        command_id="command.review",
        idempotency_key="reviewer-dispatch-one",
        authorization_scope="reviewer_binding",
    )

    assert invocation.assignment_digest == dispatch.assignment.assignment_digest
    assert invocation.provider_id == dispatch.assignment.provider_id


def test_same_attempt_concurrency_allocates_runtime_exactly_once(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    authority = _authority(context.plan)
    entered = Event()
    release = Event()
    broker = BlockingRuntimeBroker(
        _allocations(context.plan, authority), entered, release
    )
    service = ReviewerBindingService(
        context.root,
        project_id=context.final.project_id,
        resource_governor=context.governor,
        authority_resolver=StaticAuthorityResolver(authority),
        host_probe=SequenceHostProbe([_host_snapshot()]),
        runtime_broker=broker,
        isolation_adapter=FakeIsolationAdapter(),
        lock_timeout_seconds=2,
    )
    request = _request(context)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(
            service.bind,
            context.plan,
            request=request,
            budget_policy=_policy(),
            lease_owner=_OWNER,
            now=_now(),
        )
        assert entered.wait(timeout=2)
        second = pool.submit(
            service.bind,
            context.plan,
            request=request,
            budget_policy=_policy(),
            lease_owner=_OWNER,
            now=_now(),
        )
        release.set()
        first_result = first.result(timeout=2)
        second_result = second.result(timeout=2)

    assert first_result == second_result
    assert first_result.result_code == "bound"
    assert broker.calls == 1
    usage = context.governor.get_reservation(context.final.reservation_id).usage
    assert usage.binding_attempts == 1


def test_binding_store_uses_one_canonical_root_across_git_worktrees(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    worktree = tmp_path / "worktree"
    repository.mkdir()
    _git(repository, "init")
    _git(repository, "config", "user.email", "test@example.com")
    _git(repository, "config", "user.name", "Test")
    (repository / "README.md").write_text("demo\n", encoding="utf-8")
    _git(repository, "add", "README.md")
    _git(repository, "commit", "-m", "init")
    _git(repository, "worktree", "add", "-b", "feature/test", str(worktree))

    primary = BindingArtifactStore(
        repository, project_id="project.shared", lock_timeout_seconds=2
    )
    secondary = BindingArtifactStore(
        worktree, project_id="project.shared", lock_timeout_seconds=2
    )

    assert primary.root == secondary.root


def _replace_request(
    request: BindingAttemptRequest,
    **changes: object,
) -> BindingAttemptRequest:
    draft = request.model_copy(update={**changes, "request_digest": ""})
    return BindingAttemptRequest.model_validate(
        draft.model_copy(
            update={"request_digest": binding_attempt_request_digest(draft)}
        ).model_dump(mode="json")
    )
