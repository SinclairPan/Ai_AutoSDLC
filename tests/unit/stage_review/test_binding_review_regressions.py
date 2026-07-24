from __future__ import annotations

import ast
import inspect
import textwrap
from pathlib import Path
from types import SimpleNamespace

import pytest
from tests.unit.stage_review.test_bindings import (
    FakeIsolationAdapter,
    SequenceHostProbe,
    StaticRuntimeBroker,
    _allocations,
    _authority,
    _availability,
    _context,
    _host_snapshot,
    _request,
    _service,
)
from tests.unit.stage_review.test_provider_journal import FakeProviderDriver, _validator
from tests.unit.stage_review.test_resources import (
    _OWNER,
    _now,
    _policy,
    _provider_anticipated,
)

from ai_sdlc.core.stage_review import binding_dispatch_store
from ai_sdlc.core.stage_review.artifacts import atomic_write_json
from ai_sdlc.core.stage_review.binding_builders import (
    build_binding_attempt_operation,
    build_isolation_execution_evidence,
    build_provider_binding_descriptor,
)
from ai_sdlc.core.stage_review.binding_digests import (
    binding_authority_digest,
    dispatch_assignment_digest,
    host_capability_digest,
    reviewer_binding_digest,
)
from ai_sdlc.core.stage_review.binding_invocations import (
    ReviewerInvocationCoordinator,
)
from ai_sdlc.core.stage_review.binding_models import (
    BindingAuthoritySnapshot,
    HostCapabilitySnapshot,
)
from ai_sdlc.core.stage_review.binding_result_models import ReviewerBinding
from ai_sdlc.core.stage_review.binding_store import BindingArtifactStore
from ai_sdlc.core.stage_review.binding_validation import (
    BindingRefusal,
    validate_evidence,
    validate_rebind,
)
from ai_sdlc.core.stage_review.bindings import BindingRetryableError
from ai_sdlc.core.stage_review.provider_journal import (
    ProviderInvocationJournal,
    ProviderRecoveryCapabilities,
)
from ai_sdlc.core.stage_review.provider_journal_builders import (
    build_provider_invocation_request,
)
from ai_sdlc.core.stage_review.provider_journal_models import (
    ProviderInvocation,
    ProviderInvocationEvent,
    ProviderInvocationRequest,
    request_artifact_digest,
)
from ai_sdlc.core.stage_review.provider_journal_reducer import (
    build_provider_event,
    rebuild_provider_invocation,
)
from ai_sdlc.core.stage_review.provider_transport_models import (
    _build_provider_execution_identity,
)

pytestmark = pytest.mark.usefixtures("allow_synthetic_binding_authority")


def test_different_provider_same_model_is_only_session_independent(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    descriptors = tuple(
        build_provider_binding_descriptor(
            descriptor_id=f"descriptor.same-model.{slot.slot_id}",
            provider_id=f"provider.{slot.slot_id}",
            equivalence_class_id=f"equivalence.{slot.slot_id}",
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
            supported_independence_grade="model_diversity_proven",
            provider_policy_evidence_digest=f"sha256:policy.{slot.slot_id}",
        )
        for slot in context.plan.proposal.required_slots
    )
    authority = _authority(context.plan, descriptors=descriptors)
    allocations = tuple(
        _runtime_for_descriptor(slot.slot_id, descriptor, index)
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
    assert result.binding_set.independence_proofs[0].independence_grade == (
        "session_independent"
    )


def test_initial_actor_failure_can_retry_without_a_previous_binding_set(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    authority = _authority(context.plan)
    first = _service(
        context,
        authority,
        SequenceHostProbe([_host_snapshot()]),
        StaticRuntimeBroker(()),
    ).bind(
        context.plan,
        request=_request(context),
        budget_policy=_policy(),
        lease_owner=_OWNER,
        now=_now(),
    )
    assert first.result_code == "actor_unavailable"

    retry = _service(
        context,
        authority,
        SequenceHostProbe([_host_snapshot()]),
        StaticRuntimeBroker(_allocations(context.plan, authority, run="retry")),
    ).bind(
        context.plan,
        request=_request(
            context,
            attempt_index=2,
            rebind_reason="actor_unavailable_retry",
        ),
        budget_policy=_policy(),
        lease_owner=_OWNER,
        now=_now(),
    )

    assert retry.result_code == "bound"
    usage = context.governor.get_reservation(context.final.reservation_id).usage
    assert usage.binding_attempts == 2
    assert usage.provider_retries == 0


@pytest.mark.parametrize(
    ("reason", "expected_retries"),
    (
        ("actor_unavailable_retry", 0),
        ("session_creation_retry", 1),
    ),
)
def test_binding_retry_charge_is_derived_from_the_governed_reason(
    tmp_path: Path,
    reason: str,
    expected_retries: int,
) -> None:
    context = _context(tmp_path)

    request = _request(context, attempt_index=2, rebind_reason=reason)

    assert request.provider_retry_delta == expected_retries


def test_visibility_barrier_failure_cannot_be_declared_retryable(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)

    with pytest.raises(ValueError, match="visibility_barrier_retry"):
        _request(
            context,
            attempt_index=2,
            rebind_reason="visibility_barrier_retry",
        )


def test_transitioned_host_snapshot_can_be_reverified_for_dispatch(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    authority = _authority(context.plan, suffixes=("a", "b"))
    first_host = _host_snapshot()
    initial = _service(
        context,
        authority,
        SequenceHostProbe([first_host]),
        StaticRuntimeBroker(_allocations(context.plan, authority, suffix="a")),
    ).bind(
        context.plan,
        request=_request(context),
        budget_policy=_policy(),
        lease_owner=_OWNER,
        now=_now(),
    )
    assert initial.binding_set is not None
    attestation = _availability(
        context.plan,
        initial.binding_set.binding_set_digest,
        tuple(item.provider_id for item in initial.binding_set.bindings),
    )
    second_host = _host_snapshot(
        host_session_id="host.second",
        previous_snapshot_digest=first_host.snapshot_digest,
    )
    service = _service(
        context,
        authority,
        SequenceHostProbe([second_host, second_host]),
        StaticRuntimeBroker(_allocations(context.plan, authority, suffix="b")),
        availability_attestation=attestation,
    )
    rebound = service.bind(
        context.plan,
        request=_request(
            context,
            attempt_index=2,
            previous_binding_set_digest=initial.binding_set.binding_set_digest,
            expected_cohort_id="cohort.current",
            expected_pass_head_digest="sha256:pass-head",
            rebind_reason="provider_unavailable",
            availability_attestation=attestation,
        ),
        budget_policy=_policy(),
        lease_owner=_OWNER,
        now=_now(),
    )
    assert rebound.binding_set is not None

    dispatch = service.authorize_dispatch(
        binding_set_id=rebound.binding_set.binding_set_id,
        slot_id=rebound.binding_set.bindings[0].slot_id,
        cohort_id="cohort.current",
        candidate_manifest_digest="sha256:candidate",
        expected_pass_head_digest="sha256:pass-head",
        now=_now(),
    )

    assert dispatch.result_code == "bound"


def test_evidence_crash_window_reuses_the_persisted_bundle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _context(tmp_path)
    authority = _authority(context.plan)
    adapter = FakeIsolationAdapter()
    probe = SequenceHostProbe([_host_snapshot()])
    service = _service(
        context,
        authority,
        probe,
        StaticRuntimeBroker(_allocations(context.plan, authority)),
        isolation_adapter=adapter,
    )
    original = service._store.persist_binding_set

    monkeypatch.setattr(
        service._store,
        "persist_binding_set",
        lambda binding_set: (_ for _ in ()).throw(RuntimeError("crash after evidence")),
    )
    request = _request(context)
    with pytest.raises(RuntimeError, match="crash after evidence"):
        service.bind(
            context.plan,
            request=request,
            budget_policy=_policy(),
            lease_owner=_OWNER,
            now=_now(),
        )
    monkeypatch.setattr(service._store, "persist_binding_set", original)

    recovered = service.bind(
        context.plan,
        request=request,
        budget_policy=_policy(),
        lease_owner=_OWNER,
        now=_now(),
    )

    assert recovered.result_code == "bound"
    assert adapter.calls == 1
    assert probe.calls == 2


def test_evidence_recovery_rejects_a_changed_host_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _context(tmp_path)
    authority = _authority(context.plan)
    first_host = _host_snapshot()
    changed_host = _host_snapshot(
        host_session_id="host.changed-after-crash",
        previous_snapshot_digest=first_host.snapshot_digest,
    )
    adapter = FakeIsolationAdapter()
    service = _service(
        context,
        authority,
        SequenceHostProbe([first_host, changed_host]),
        StaticRuntimeBroker(_allocations(context.plan, authority)),
        isolation_adapter=adapter,
    )
    original = service._store.persist_binding_set
    monkeypatch.setattr(
        service._store,
        "persist_binding_set",
        lambda binding_set: (_ for _ in ()).throw(RuntimeError("crash after evidence")),
    )
    request = _request(context)
    with pytest.raises(RuntimeError, match="crash after evidence"):
        service.bind(
            context.plan,
            request=request,
            budget_policy=_policy(),
            lease_owner=_OWNER,
            now=_now(),
        )
    monkeypatch.setattr(service._store, "persist_binding_set", original)

    recovered = service.bind(
        context.plan,
        request=request,
        budget_policy=_policy(),
        lease_owner=_OWNER,
        now=_now(),
    )

    assert recovered.result_code == "provider_policy_blocked"
    assert recovered.reason_ids == ("binding.host-capability-changed",)
    assert adapter.calls == 1


def test_refusal_result_crash_window_reuses_the_persisted_bundle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _context(tmp_path)
    authority = _authority(context.plan, enforcement_mode="enforce")
    adapter = FakeIsolationAdapter("detected_only")
    probe = SequenceHostProbe([_host_snapshot()])
    service = _service(
        context,
        authority,
        probe,
        StaticRuntimeBroker(_allocations(context.plan, authority)),
        isolation_adapter=adapter,
    )
    original = service._store.persist_result
    monkeypatch.setattr(
        service._store,
        "persist_result",
        lambda result: (_ for _ in ()).throw(RuntimeError("crash before refusal")),
    )
    request = _request(context)
    with pytest.raises(RuntimeError, match="crash before refusal"):
        service.bind(
            context.plan,
            request=request,
            budget_policy=_policy(),
            lease_owner=_OWNER,
            now=_now(),
        )
    monkeypatch.setattr(service._store, "persist_result", original)

    recovered = service.bind(
        context.plan,
        request=request,
        budget_policy=_policy(),
        lease_owner=_OWNER,
        now=_now(),
    )

    assert recovered.result_code == "independence_unproven"
    assert adapter.calls == 1
    assert probe.calls == 2


def test_same_dispatch_slot_reuses_one_assignment_and_one_evidence(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    authority = _authority(context.plan)
    adapter = FakeIsolationAdapter()
    host = _host_snapshot()
    service = _service(
        context,
        authority,
        SequenceHostProbe([host]),
        StaticRuntimeBroker(_allocations(context.plan, authority)),
        isolation_adapter=adapter,
    )
    bound = service.bind(
        context.plan,
        request=_request(context),
        budget_policy=_policy(),
        lease_owner=_OWNER,
        now=_now(),
    )
    assert bound.binding_set is not None
    arguments = {
        "binding_set_id": bound.binding_set.binding_set_id,
        "slot_id": bound.binding_set.bindings[0].slot_id,
        "cohort_id": "cohort.current",
        "candidate_manifest_digest": "sha256:candidate",
        "expected_pass_head_digest": "sha256:pass-head",
        "now": _now(),
    }

    first = service.authorize_dispatch(**arguments)
    second = service.authorize_dispatch(**arguments)

    assert first.assignment is not None
    assert second.assignment == first.assignment
    assert adapter.calls == 2
    assert (
        len(tuple((service._store.root / "dispatch-assignments").glob("*.json"))) == 1
    )


def test_dispatch_slot_recovers_evidence_written_before_assignment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _context(tmp_path)
    authority = _authority(context.plan)
    adapter = FakeIsolationAdapter()
    service = _service(
        context,
        authority,
        SequenceHostProbe([_host_snapshot()]),
        StaticRuntimeBroker(_allocations(context.plan, authority)),
        isolation_adapter=adapter,
    )
    bound = service.bind(
        context.plan,
        request=_request(context),
        budget_policy=_policy(),
        lease_owner=_OWNER,
        now=_now(),
    )
    assert bound.binding_set is not None
    arguments = {
        "binding_set_id": bound.binding_set.binding_set_id,
        "slot_id": bound.binding_set.bindings[0].slot_id,
        "cohort_id": "cohort.current",
        "candidate_manifest_digest": "sha256:candidate",
        "expected_pass_head_digest": "sha256:pass-head",
        "now": _now(),
    }
    original = binding_dispatch_store.persist_model
    crashed = False

    def crash_after_evidence(path, *args):
        nonlocal crashed
        result = original(path, *args)
        if "dispatch-evidence" in path.parts and not crashed:
            crashed = True
            raise RuntimeError("crash after dispatch evidence")
        return result

    monkeypatch.setattr(binding_dispatch_store, "persist_model", crash_after_evidence)
    with pytest.raises(RuntimeError, match="crash after dispatch evidence"):
        service.authorize_dispatch(**arguments)
    monkeypatch.setattr(binding_dispatch_store, "persist_model", original)

    recovered = service.authorize_dispatch(**arguments)

    assert recovered.result_code == "bound"
    assert adapter.calls == 2
    assert len(tuple((service._store.root / "dispatch-evidence").glob("*.json"))) == 1


def test_dispatch_replay_rejects_a_self_consistent_forged_assignment(
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
    bound = service.bind(
        context.plan,
        request=_request(context),
        budget_policy=_policy(),
        lease_owner=_OWNER,
        now=_now(),
    )
    assert bound.binding_set is not None
    arguments = {
        "binding_set_id": bound.binding_set.binding_set_id,
        "slot_id": bound.binding_set.bindings[0].slot_id,
        "cohort_id": "cohort.current",
        "candidate_manifest_digest": "sha256:candidate",
        "expected_pass_head_digest": "sha256:pass-head",
        "now": _now(),
    }
    first = service.authorize_dispatch(**arguments)
    assert first.assignment is not None
    draft = first.assignment.model_copy(
        update={"provider_id": "provider.forged", "assignment_digest": ""}
    )
    forged = draft.model_copy(
        update={"assignment_digest": dispatch_assignment_digest(draft)}
    )
    path = service._store.root / "dispatch-assignments" / f"{forged.assignment_id}.json"
    atomic_write_json(path, forged.model_dump(mode="json"))

    replayed = service.authorize_dispatch(**arguments)

    assert replayed.result_code == "provider_policy_blocked"
    assert replayed.reason_ids == ("binding.dispatch-assignment-lineage-invalid",)


def test_dispatch_replay_ignores_nonsemantic_runtime_metadata(
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
    bound = service.bind(
        context.plan,
        request=_request(context),
        budget_policy=_policy(),
        lease_owner=_OWNER,
        now=_now(),
    )
    assert bound.binding_set is not None
    arguments = {
        "binding_set_id": bound.binding_set.binding_set_id,
        "slot_id": bound.binding_set.bindings[0].slot_id,
        "cohort_id": "cohort.current",
        "candidate_manifest_digest": "sha256:candidate",
        "expected_pass_head_digest": "sha256:pass-head",
        "now": _now(),
    }
    first = service.authorize_dispatch(**arguments)
    assert first.assignment is not None
    historical = first.assignment.model_copy(
        update={
            "created_at": "2000-01-01T00:00:00Z",
            "created_by": "ai-sdlc-legacy",
            "ai_sdlc_version": "1.0.0",
        }
    )
    path = (
        service._store.root
        / "dispatch-assignments"
        / f"{historical.assignment_id}.json"
    )
    atomic_write_json(path, historical.model_dump(mode="json"))

    replayed = service.authorize_dispatch(**arguments)

    assert replayed.result_code == "bound"
    assert replayed.assignment == historical


def test_legacy_provider_request_without_authorization_scope_is_readable(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    request = build_provider_invocation_request(
        project_id=context.final.project_id,
        work_item_id=context.final.work_item_id,
        stage_review_session_id=context.final.stage_review_session_id,
        owner_scope_id=context.final.provider_scope_ids[0],
        candidate_digest="sha256:candidate",
        assignment_digest="sha256:legacy-assignment",
        epoch_id="",
        provider_id="provider.legacy",
        request_digest="sha256:legacy-request",
        reservation_id=context.final.reservation_id,
        expected_reservation_digest=context.final.reservation_digest,
        expected_fencing_token=context.final.fencing_token,
        anticipated_usage=_provider_anticipated(),
        capabilities=ProviderRecoveryCapabilities(
            idempotency_support=True,
            invocation_query_support=True,
            cost_metering_support=True,
        ),
        command_id="command.legacy",
        idempotency_key="legacy",
    )
    legacy = request.model_dump(mode="json")
    legacy.pop("authorization_scope")
    legacy["request_artifact_digest"] = request_artifact_digest(legacy)

    loaded = ProviderInvocationRequest.model_validate(legacy)

    assert loaded.authorization_scope in {None, "generic"}

    event = build_provider_event(
        loaded,
        None,
        "prepared",
        context.final.reservation_digest,
        "",
        "",
        "",
        "",
        "",
    )
    legacy_event = event.model_dump(mode="json")
    legacy_event["request"].pop("authorization_scope")
    loaded_event = ProviderInvocationEvent.model_validate(legacy_event)
    projection = rebuild_provider_invocation((loaded_event,))
    legacy_projection = projection.model_dump(mode="json")
    legacy_projection["request"].pop("authorization_scope")

    loaded_projection = ProviderInvocation.model_validate(legacy_projection)

    assert loaded_projection.state == "prepared"


def test_host_snapshot_identity_cannot_escape_its_store_directory(
    tmp_path: Path,
) -> None:
    host = _host_snapshot()
    draft = host.model_copy(
        update={"snapshot_id": "../outside-host", "snapshot_digest": ""}
    )
    with pytest.raises(ValueError, match="snapshot identity"):
        HostCapabilitySnapshot.model_validate(
            draft.model_copy(
                update={"snapshot_digest": host_capability_digest(draft)}
            ).model_dump(mode="json")
        )

    assert not tuple(tmp_path.rglob("outside-host.json"))


def test_isolation_evidence_fork_compares_the_evidence_digest(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    authority = _authority(context.plan)
    request = _request(context)
    operation = build_binding_attempt_operation(request, authority)
    allocation = _allocations(context.plan, authority)[0]
    host = _host_snapshot()
    enforced = _evidence(operation.operation_id, allocation, host, "enforced", "one")
    detected = _evidence(
        operation.operation_id, allocation, host, "detected_only", "two"
    )
    store = BindingArtifactStore(
        tmp_path, project_id=context.final.project_id, lock_timeout_seconds=2
    )
    store.persist_evidence(operation.operation_id, (enforced,))

    with pytest.raises(Exception, match="immutable identity fork"):
        store.persist_evidence(operation.operation_id, (detected,))


def test_binding_set_without_result_recovers_without_reprobing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _context(tmp_path)
    authority = _authority(context.plan)
    original_host = _host_snapshot()
    service = _service(
        context,
        authority,
        SequenceHostProbe([original_host]),
        StaticRuntimeBroker(_allocations(context.plan, authority)),
    )
    original_persist = service._store.persist_result

    def crash_before_result(*args: object) -> None:
        del args
        raise RuntimeError("crash before result")

    monkeypatch.setattr(service._store, "persist_result", crash_before_result)
    request = _request(context)
    with pytest.raises(RuntimeError, match="crash before result"):
        service.bind(
            context.plan,
            request=request,
            budget_policy=_policy(),
            lease_owner=_OWNER,
            now=_now(),
        )
    monkeypatch.setattr(service._store, "persist_result", original_persist)
    changed = _host_snapshot(
        host_session_id="host.changed",
        previous_snapshot_digest=original_host.snapshot_digest,
    )
    recovery_probe = SequenceHostProbe([changed])
    recovered = _service(
        context,
        authority,
        recovery_probe,
        StaticRuntimeBroker(_allocations(context.plan, authority)),
    ).bind(
        context.plan,
        request=request,
        budget_policy=_policy(),
        lease_owner=_OWNER,
        now=_now(),
    )

    assert recovered.result_code == "bound"
    assert recovered.binding_set is not None
    assert recovered.binding_set.host_snapshot_digest == original_host.snapshot_digest
    assert recovery_probe.calls == 0


def test_request_cannot_self_attest_provider_unavailability(tmp_path: Path) -> None:
    context = _context(tmp_path)
    authority = _authority(context.plan, suffixes=("a", "b"))
    initial = _service(
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
    assert initial.binding_set is not None
    attestation = _availability(
        context.plan,
        initial.binding_set.binding_set_digest,
        tuple(item.provider_id for item in initial.binding_set.bindings),
    )
    broker = StaticRuntimeBroker(_allocations(context.plan, authority, suffix="b"))
    request = _request(
        context,
        attempt_index=2,
        previous_binding_set_digest=initial.binding_set.binding_set_digest,
        expected_cohort_id="cohort.current",
        expected_pass_head_digest="sha256:pass-head",
        rebind_reason="provider_unavailable",
        availability_attestation=attestation,
    )

    result = _service(
        context,
        authority,
        SequenceHostProbe([_host_snapshot()]),
        broker,
    ).bind(
        context.plan,
        request=request,
        budget_policy=_policy(),
        lease_owner=_OWNER,
        now=_now(),
    )

    assert result.result_code == "provider_policy_blocked"
    assert result.reason_ids == ("binding.provider-availability-untrusted",)
    assert broker.calls == 0
    usage = context.governor.get_reservation(context.final.reservation_id).usage
    assert usage.binding_attempts == 1


def test_rebind_host_change_requires_the_previous_snapshot_digest(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    authority = _authority(context.plan, suffixes=("a", "b"))
    original_host = _host_snapshot()
    initial = _service(
        context,
        authority,
        SequenceHostProbe([original_host]),
        StaticRuntimeBroker(_allocations(context.plan, authority, suffix="a")),
    ).bind(
        context.plan,
        request=_request(context),
        budget_policy=_policy(),
        lease_owner=_OWNER,
        now=_now(),
    )
    assert initial.binding_set is not None
    attestation = _availability(
        context.plan,
        initial.binding_set.binding_set_digest,
        tuple(item.provider_id for item in initial.binding_set.bindings),
    )
    changed_without_lineage = _host_snapshot(host_session_id="host.changed")
    probe = SequenceHostProbe([changed_without_lineage])
    broker = StaticRuntimeBroker(_allocations(context.plan, authority, suffix="b"))

    result = _service(
        context,
        authority,
        probe,
        broker,
        availability_attestation=attestation,
    ).bind(
        context.plan,
        request=_request(
            context,
            attempt_index=2,
            previous_binding_set_digest=initial.binding_set.binding_set_digest,
            expected_cohort_id="cohort.current",
            expected_pass_head_digest="sha256:pass-head",
            rebind_reason="provider_unavailable",
            availability_attestation=attestation,
        ),
        budget_policy=_policy(),
        lease_owner=_OWNER,
        now=_now(),
    )

    assert result.result_code == "provider_policy_blocked"
    assert result.reason_ids == ("binding.host-snapshot-lineage-invalid",)
    assert probe.previous_snapshot_digests == [original_host.snapshot_digest]
    assert broker.calls == 0


def test_resource_lock_contention_does_not_persist_a_binding_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _context(tmp_path)
    authority = _authority(context.plan)
    service = _service(
        context,
        authority,
        SequenceHostProbe([_host_snapshot()]),
        StaticRuntimeBroker(_allocations(context.plan, authority)),
    )
    request = _request(context)
    original = context.governor.record_usage
    monkeypatch.setattr(
        context.governor,
        "record_usage",
        lambda *args, **kwargs: SimpleNamespace(
            result_code="lock_unavailable", operation_reservation=None
        ),
    )

    with pytest.raises(BindingRetryableError, match="lock is unavailable"):
        service.bind(
            context.plan,
            request=request,
            budget_policy=_policy(),
            lease_owner=_OWNER,
            now=_now(),
        )
    assert service.get_attempt_result(request.operation_id) is None
    monkeypatch.setattr(context.governor, "record_usage", original)
    assert (
        service.bind(
            context.plan,
            request=request,
            budget_policy=_policy(),
            lease_owner=_OWNER,
            now=_now(),
        ).result_code
        == "bound"
    )


def test_enforce_optional_detected_only_is_rejected(tmp_path: Path) -> None:
    context = _context(tmp_path)
    authority = _authority(context.plan, enforcement_mode="enforce")
    operation = build_binding_attempt_operation(_request(context), authority)
    allocation = _allocations(context.plan, authority)[0]
    descriptor = authority.provider_descriptors[0]
    optional_slot = context.plan.proposal.required_slots[0].model_copy(
        update={"slot_kind": "optional"}
    )
    host = _host_snapshot()
    evidence = _evidence(
        operation.operation_id, allocation, host, "detected_only", "optional"
    )

    with pytest.raises(BindingRefusal) as refusal:
        validate_evidence(
            operation=operation,
            host_snapshot=host,
            pairs=((optional_slot, allocation, descriptor),),
            evidence=(evidence,),
        )

    assert refusal.value.reason_id == "binding.required-isolation-not-enforced"


def test_same_provider_cannot_change_equivalence_class_on_rebind(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    authority = _authority(context.plan)
    initial = _service(
        context,
        authority,
        SequenceHostProbe([_host_snapshot()]),
        StaticRuntimeBroker(_allocations(context.plan, authority)),
    ).bind(
        context.plan,
        request=_request(context),
        budget_policy=_policy(),
        lease_owner=_OWNER,
        now=_now(),
    )
    assert initial.binding_set is not None
    attestation = _availability(
        context.plan,
        initial.binding_set.binding_set_digest,
        tuple(item.provider_id for item in initial.binding_set.bindings),
    )
    request = _request(
        context,
        attempt_index=2,
        previous_binding_set_digest=initial.binding_set.binding_set_digest,
        expected_cohort_id="cohort.current",
        expected_pass_head_digest="sha256:pass-head",
        rebind_reason="provider_unavailable",
        availability_attestation=attestation,
    )
    operation = build_binding_attempt_operation(request, authority, attestation)
    old = initial.binding_set.bindings[0]
    identity = old.execution_identity
    changed_identity = _build_provider_execution_identity(
        execution_scope=identity.execution_scope,
        provider_id=identity.provider_id,
        provider_descriptor_digest=identity.provider_descriptor_digest,
        equivalence_class_id="equivalence.changed",
        model_family=identity.model_family,
        capability_ids=identity.capability_ids,
        recovery_capabilities=identity.recovery_capabilities,
        provider_adapter_id=identity.provider_adapter_id,
        provider_adapter_version=identity.provider_adapter_version,
        driver_factory_id=identity.driver_factory_id,
        driver_factory_version=identity.driver_factory_version,
        broker_id=identity.broker_id,
        physical_provider_id=identity.physical_provider_id,
        physical_equivalence_class_id=identity.physical_equivalence_class_id,
    )
    draft = old.model_copy(
        update={
            "equivalence_class_id": "equivalence.changed",
            "execution_identity": changed_identity,
            "binding_digest": "",
        }
    )
    changed = ReviewerBinding.model_validate(
        draft.model_copy(
            update={
                "binding_digest": reviewer_binding_digest(
                    draft.model_dump(exclude={"binding_digest"}, mode="json")
                )
            }
        ).model_dump(mode="json")
    )

    with pytest.raises(BindingRefusal) as refusal:
        validate_rebind(
            operation=operation,
            previous=initial.binding_set,
            bindings=(changed, *initial.binding_set.bindings[1:]),
        )

    assert refusal.value.reason_id == "binding.provider-not-equivalent"


def test_authority_snapshot_identity_is_content_derived(tmp_path: Path) -> None:
    context = _context(tmp_path)
    authority = _authority(context.plan)
    draft = authority.model_copy(
        update={"snapshot_id": "../outside-authority", "snapshot_digest": ""}
    )

    with pytest.raises(ValueError, match="snapshot identity"):
        BindingAuthoritySnapshot.model_validate(
            draft.model_copy(
                update={"snapshot_digest": binding_authority_digest(draft)}
            ).model_dump(mode="json")
        )


def test_reviewer_journal_resume_reauthorizes_host_before_provider_call(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    authority = _authority(context.plan)
    host = _host_snapshot()
    changed = _host_snapshot(
        host_session_id="host.changed",
        previous_snapshot_digest=host.snapshot_digest,
    )
    service = _service(
        context,
        authority,
        SequenceHostProbe([host, host, changed]),
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
    journal = ProviderInvocationJournal(
        tmp_path,
        project_id=context.final.project_id,
        resource_governor=context.governor,
    )
    coordinator = ReviewerInvocationCoordinator(service, journal)
    current = context.governor.get_reservation(context.final.reservation_id)
    prepared = coordinator.prepare(
        binding_set_id=bound.binding_set.binding_set_id,
        slot_id=bound.binding_set.bindings[0].slot_id,
        cohort_id="cohort.current",
        candidate_manifest_digest="sha256:candidate",
        expected_pass_head_digest="sha256:pass-head",
        owner_scope_id=current.provider_scope_ids[0],
        request_digest="sha256:review-request",
        expected_reservation_digest=current.reservation_digest,
        anticipated_usage=_provider_anticipated(),
        command_id="command.review",
        idempotency_key="reviewer-one",
        lease_owner=_OWNER,
        now=_now(),
    )
    assert prepared.result_code == "prepared"
    assert prepared.invocation is not None
    assignment = service.get_dispatch_assignment(
        prepared.invocation.request.assignment_digest
    )
    assert assignment is not None
    assert prepared.invocation.request.capabilities == assignment.recovery_capabilities
    driver = FakeProviderDriver(assignment.recovery_capabilities)
    driver.provider_id = assignment.provider_id

    blocked = coordinator.resume(
        prepared.invocation.invocation_id,
        driver=driver,
        validator=_validator,
        lease_owner=_OWNER,
        now=_now(),
    )

    assert blocked.result_code == "dispatch_unauthorized"
    assert driver.invoke_count == 0


def test_reviewer_resume_without_core_isolation_launcher_never_calls_raw_driver(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    authority = _authority(context.plan)
    host = _host_snapshot()
    service = _service(
        context,
        authority,
        SequenceHostProbe([host, host, host]),
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
    journal = ProviderInvocationJournal(
        tmp_path,
        project_id=context.final.project_id,
        resource_governor=context.governor,
    )
    coordinator = ReviewerInvocationCoordinator(service, journal)
    current = context.governor.get_reservation(context.final.reservation_id)
    prepared = coordinator.prepare(
        binding_set_id=bound.binding_set.binding_set_id,
        slot_id=bound.binding_set.bindings[0].slot_id,
        cohort_id="cohort.current",
        candidate_manifest_digest="sha256:candidate",
        expected_pass_head_digest="sha256:pass-head",
        owner_scope_id=current.provider_scope_ids[0],
        request_digest="sha256:review-request",
        expected_reservation_digest=current.reservation_digest,
        anticipated_usage=_provider_anticipated(),
        command_id="command.review.unisolated",
        idempotency_key="reviewer-unisolated",
        lease_owner=_OWNER,
        now=_now(),
    )
    assert prepared.invocation is not None
    assignment = service.get_dispatch_assignment(
        prepared.invocation.request.assignment_digest
    )
    assert assignment is not None
    driver = FakeProviderDriver(assignment.recovery_capabilities)
    driver.provider_id = assignment.provider_id

    blocked = coordinator.resume(
        prepared.invocation.invocation_id,
        driver=driver,
        validator=_validator,
        lease_owner=_OWNER,
        now=_now(),
    )

    assert blocked.result_code == "needs_user"
    assert driver.invoke_count == 0


def test_reviewer_journal_resume_cannot_bypass_registered_isolation_boundary(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    authority = _authority(context.plan)
    host = _host_snapshot()
    service = _service(
        context,
        authority,
        SequenceHostProbe([host, host, host]),
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
    journal = ProviderInvocationJournal(
        tmp_path,
        project_id=context.final.project_id,
        resource_governor=context.governor,
    )
    coordinator = ReviewerInvocationCoordinator(service, journal)
    current = context.governor.get_reservation(context.final.reservation_id)
    prepared = coordinator.prepare(
        binding_set_id=bound.binding_set.binding_set_id,
        slot_id=bound.binding_set.bindings[0].slot_id,
        cohort_id="cohort.current",
        candidate_manifest_digest="sha256:candidate",
        expected_pass_head_digest="sha256:pass-head",
        owner_scope_id=current.provider_scope_ids[0],
        request_digest="sha256:review-request",
        expected_reservation_digest=current.reservation_digest,
        anticipated_usage=_provider_anticipated(),
        command_id="command.review.journal-bypass",
        idempotency_key="reviewer-journal-bypass",
        lease_owner=_OWNER,
        now=_now(),
    )
    assert prepared.invocation is not None
    assignment = service.get_dispatch_assignment(
        prepared.invocation.request.assignment_digest
    )
    assert assignment is not None
    raw = FakeProviderDriver(assignment.recovery_capabilities)
    raw.provider_id = assignment.provider_id

    blocked = journal.resume(
        prepared.invocation.invocation_id,
        driver=raw,
        validator=_validator,
        lease_owner=_OWNER,
        now=_now(),
    )

    assert blocked.result_code == "needs_user"
    assert raw.invoke_count == 0


def test_reviewer_provider_cannot_bypass_gate_with_generic_request(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    authority = _authority(context.plan)
    host = _host_snapshot()
    service = _service(
        context,
        authority,
        SequenceHostProbe([host, host, host]),
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
    journal = ProviderInvocationJournal(
        tmp_path,
        project_id=context.final.project_id,
        resource_governor=context.governor,
    )
    ReviewerInvocationCoordinator(service, journal)
    dispatch = service.authorize_dispatch(
        binding_set_id=bound.binding_set.binding_set_id,
        slot_id=bound.binding_set.bindings[0].slot_id,
        cohort_id="cohort.current",
        candidate_manifest_digest="sha256:candidate",
        expected_pass_head_digest="sha256:pass-head",
        now=_now(),
    )
    assert dispatch.assignment is not None
    assignment = dispatch.assignment
    current = context.governor.get_reservation(context.final.reservation_id)
    request = build_provider_invocation_request(
        project_id=context.final.project_id,
        work_item_id=context.final.work_item_id,
        stage_review_session_id=context.final.stage_review_session_id,
        owner_scope_id=current.provider_scope_ids[0],
        candidate_digest="sha256:candidate",
        assignment_digest="sha256:forged-generic-assignment",
        epoch_id="",
        provider_id=assignment.provider_id,
        request_digest="sha256:forged-generic-request",
        reservation_id=context.final.reservation_id,
        expected_reservation_digest=current.reservation_digest,
        expected_fencing_token=current.fencing_token,
        anticipated_usage=_provider_anticipated(),
        capabilities=assignment.recovery_capabilities,
        command_id="command.review.forged-generic",
        idempotency_key="reviewer-forged-generic",
        authorization_scope="generic",
    )
    prepared = journal.prepare(request, lease_owner=_OWNER, now=_now())
    assert prepared.result_code == "prepared"
    assert prepared.invocation is not None
    raw = FakeProviderDriver(assignment.recovery_capabilities)
    raw.provider_id = assignment.provider_id

    blocked = journal.resume(
        prepared.invocation.invocation_id,
        driver=raw,
        validator=_validator,
        lease_owner=_OWNER,
        now=_now(),
    )

    assert blocked.result_code == "dispatch_unauthorized"
    assert raw.invoke_count == 0


def test_reviewer_resume_routes_final_call_through_core_isolation_launcher(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    authority = _authority(context.plan)
    host = _host_snapshot()
    service = _service(
        context,
        authority,
        SequenceHostProbe([host, host, host, host]),
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
    journal = ProviderInvocationJournal(
        tmp_path,
        project_id=context.final.project_id,
        resource_governor=context.governor,
    )
    launcher = _RecordingLauncher()
    resolver = _RecordingPathResolver(tmp_path)
    coordinator = ReviewerInvocationCoordinator(
        service,
        journal,
        isolation_launcher=launcher,
        allocation_path_resolver=resolver,
    )
    current = context.governor.get_reservation(context.final.reservation_id)
    prepared = coordinator.prepare(
        binding_set_id=bound.binding_set.binding_set_id,
        slot_id=bound.binding_set.bindings[0].slot_id,
        cohort_id="cohort.current",
        candidate_manifest_digest="sha256:candidate",
        expected_pass_head_digest="sha256:pass-head",
        owner_scope_id=current.provider_scope_ids[0],
        request_digest="sha256:review-request",
        expected_reservation_digest=current.reservation_digest,
        anticipated_usage=_provider_anticipated(),
        command_id="command.review.isolated",
        idempotency_key="reviewer-isolated",
        lease_owner=_OWNER,
        now=_now(),
    )
    assert prepared.invocation is not None
    assignment = service.get_dispatch_assignment(
        prepared.invocation.request.assignment_digest
    )
    assert assignment is not None
    driver = FakeProviderDriver(assignment.recovery_capabilities)
    driver.provider_id = assignment.provider_id

    result = coordinator.resume(
        prepared.invocation.invocation_id,
        driver=driver,
        validator=_validator,
        lease_owner=_OWNER,
        now=_now(),
    )

    assert result.result_code == "committed"
    assert launcher.calls == 1
    assert launcher.allocation_digest == bound.binding_set.bindings[0].allocation_digest
    assert resolver.calls == 1
    assert Path(launcher.normalized_run_root).is_absolute()
    assert launcher.normalized_run_root != "cwd.0"
    assert launcher.layout_digest.startswith("sha256:")
    assert result.submission is not None
    assert result.invocation is not None
    assert result.submission.isolation_receipt_digests == ("sha256:isolation-receipt",)
    assert (
        result.invocation.isolation_receipt_digests
        == result.submission.isolation_receipt_digests
    )


class _RecordingLauncher:
    def __init__(self) -> None:
        self.calls = 0
        self.allocation_digest = ""
        self.normalized_run_root = ""
        self.layout_digest = ""

    def prepare_driver(self, driver, *, context, now):
        self.calls += 1
        self.allocation_digest = context.allocation_digest
        self.normalized_run_root = context.normalized_run_root
        self.layout_digest = context.layout_digest
        return _ReceiptBindingDriver(driver)


class _ReceiptBindingDriver:
    def __init__(self, driver) -> None:
        self.provider_id = driver.provider_id
        self.capabilities = driver.capabilities
        self._driver = driver

    def invoke(self, request):
        from ai_sdlc.core.stage_review.provider_journal_builders import (
            _bind_submission_isolation_receipt as bind_submission_isolation_receipt,
        )

        return bind_submission_isolation_receipt(
            self._driver.invoke(request),
            "sha256:isolation-receipt",
        )

    def query(self, request):
        return self._driver.query(request)


class _RecordingPathResolver:
    def __init__(self, root: Path) -> None:
        from ai_sdlc.core.stage_review.isolation_layout_identity import (
            _runtime_layout_digest,
        )
        from ai_sdlc.core.stage_review.isolation_runtime_layout import (
            IsolationRuntimeLayout,
        )

        self.calls = 0
        self._root = root.resolve()
        self._layout_type = IsolationRuntimeLayout
        self._layout_digest = _runtime_layout_digest

    def resolve(self, allocation, *, peer_allocations, assignment_digest):
        self.calls += 1
        allocation_root = self._root / "trusted-layouts" / allocation.allocation_id
        values = {
            "allocation_digest": allocation.allocation_digest,
            "assignment_digest": assignment_digest,
            "candidate_digest": allocation.candidate_manifest_digest,
            "normalized_run_root": str(allocation_root / "run"),
            "candidate_root": str(self._root / "candidate"),
            "peer_output_roots": (),
            "disposable_home_root": str(allocation_root / "home"),
            "disposable_config_root": str(allocation_root / "config"),
            "disposable_credential_root": str(allocation_root / "credentials"),
            "output_root": str(allocation_root / "output"),
            "controller_config_root": str(self._root / "controller"),
            "protected_home_root": str(self._root / "protected-home"),
            "protected_config_roots": (
                str(self._root / "protected-home" / ".gitconfig"),
            ),
            "runtime_read_roots": (str(self._root),),
        }
        draft = self._layout_type.model_construct(**values, layout_digest="")
        return self._layout_type.model_validate(
            {**values, "layout_digest": self._layout_digest(draft)}
        )


def _runtime_for_descriptor(slot_id: str, descriptor: object, index: int):
    from ai_sdlc.core.stage_review.binding_builders import build_runtime_allocation

    return build_runtime_allocation(
        allocation_id=f"allocation.{index}",
        slot_id=slot_id,
        actor_id=f"actor.{index}",
        session_id=f"session.{index}",
        provider_descriptor=descriptor,  # type: ignore[arg-type]
        candidate_manifest_digest="sha256:candidate",
        candidate_snapshot_id=f"snapshot.{index}",
        working_directory_id=f"cwd.{index}",
        disposable_home_id=f"home.{index}",
        disposable_config_id=f"config.{index}",
        disposable_credential_view_id=f"credential.{index}",
        output_directory_id=f"output.{index}",
        allocation_operation_id=f"allocation-operation.{index}",
    )


def _evidence(operation_id, allocation, host, grade, suffix):
    enforced = grade == "enforced"
    return build_isolation_execution_evidence(
        operation_id=operation_id,
        allocation=allocation,
        host_snapshot=host,
        visibility_barrier_id="visibility-barrier.initial",
        isolation_grade=grade,
        isolation_backend="sandbox.test",
        candidate_snapshot_isolated=True,
        candidate_write_enforced=enforced,
        peer_outputs_hidden=True,
        disposable_home=True,
        disposable_config=True,
        disposable_credentials=True,
        output_isolated=True,
        user_home_protected=enforced,
        global_config_protected=enforced,
        network_policy_enforced=enforced,
        sentinel_environment_disposable=True,
        evidence_bundle_digest=f"sha256:evidence.{suffix}",
    )


def test_reviewer_invocation_resume_respects_lean_function_limit() -> None:
    source = textwrap.dedent(inspect.getsource(ReviewerInvocationCoordinator.resume))
    function = ast.parse(source).body[0]

    assert isinstance(function, ast.FunctionDef)
    assert function.end_lineno is not None
    assert function.end_lineno - function.lineno + 1 <= 50
