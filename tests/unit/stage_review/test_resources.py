from __future__ import annotations

import json
import multiprocessing
import subprocess
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ai_sdlc.core.stage_review import artifacts
from ai_sdlc.core.stage_review.artifacts import (
    SharedStateIntegrityError,
    atomic_write_json,
    create_json_exclusive,
    read_json_object,
    resolve_canonical_shared_state,
)
from ai_sdlc.core.stage_review.canonical import CanonicalizationPolicy, canonical_digest
from ai_sdlc.core.stage_review.finding_models import FindingScope
from ai_sdlc.core.stage_review.panel import build_budget_policy
from ai_sdlc.core.stage_review.panel_digests import panel_proposal_digest
from ai_sdlc.core.stage_review.panel_finalization import _build_reviewer_panel_plan
from ai_sdlc.core.stage_review.panel_models import ReviewerBudgetPolicy
from ai_sdlc.core.stage_review.panel_plan_models import (
    CapabilityCoverageProof,
    FrozenQuorumPolicy,
    PanelResourceRequirement,
    ReviewerDifference,
    ReviewerPanelProposal,
    ReviewerSlot,
)
from ai_sdlc.core.stage_review.resource_builders import (
    build_resource_event,
    soft_limits,
    stable_id,
)
from ai_sdlc.core.stage_review.resource_digests import (
    budget_envelope_digest,
    reservation_digest,
    resource_config_digest,
    resource_event_digest,
    resource_operation_effect_digest,
)
from ai_sdlc.core.stage_review.resource_grant_models import (
    BudgetGrant,
    BudgetGrantResourceError,
)
from ai_sdlc.core.stage_review.resource_grants import (
    apply_budget_grant as _apply_budget_grant,
)
from ai_sdlc.core.stage_review.resource_grants import (
    build_budget_grant,
)
from ai_sdlc.core.stage_review.resource_grants import (
    reconcile_budget_grant as _reconcile_budget_grant,
)
from ai_sdlc.core.stage_review.resource_ledger_models import (
    ResourceLedgerEvent,
    ResourceReservation,
)
from ai_sdlc.core.stage_review.resource_models import (
    BudgetEnvelope,
    ResourceAmounts,
    ResourcePool,
)
from ai_sdlc.core.stage_review.resource_projection_builder import update_reservation
from ai_sdlc.core.stage_review.resource_session_grants import (
    ResourceBudgetGrantCoordinator,
)
from ai_sdlc.core.stage_review.resources import (
    ResourceGovernor,
    build_budget_envelope,
)
from ai_sdlc.core.stage_review.session_artifact_models import ArtifactRef
from ai_sdlc.core.stage_review.session_budget_approval_models import (
    BudgetGrantApproval,
    BudgetGrantApprovalState,
)
from ai_sdlc.core.stage_review.session_budget_grant_authority_store import (
    BoundBudgetGrantRequestAuthority,
    BudgetGrantApplyStatus,
)
from ai_sdlc.core.stage_review.session_budget_grant_request import (
    BudgetGrantRequestProof,
)
from ai_sdlc.core.stage_review.session_contracts import (
    BudgetGrantRequestCommand,
    SessionIntegrityError,
)
from ai_sdlc.core.stage_review.session_models import (
    SessionEvent,
    SessionOperation,
    SessionProjectionData,
    StageReviewSession,
)
from ai_sdlc.core.stage_review.session_store import SessionEventStore

_OWNER = "owner.test-process"
_REQUESTED_EVENT_DIGEST = "sha256:budget-grant-requested-event"


class _RequestAuthority:
    def __init__(
        self,
        *proofs: BudgetGrantRequestProof,
        authority_id: str = "budget-grant-request-authority.resource-tests",
    ) -> None:
        self.authority_id = authority_id
        self.project_id = "project.shared"
        self.shared_state_binding_id = ""
        self._proofs = {proof.request_operation.command_id: proof for proof in proofs}
        self.apply_status: BudgetGrantApplyStatus = "pending"
        self.after_apply_status_read: Callable[[], None] | None = None
        self.generation = 1
        self.active = True

    def add(self, proof: BudgetGrantRequestProof) -> None:
        self._proofs[proof.request_operation.command_id] = proof

    def verify_budget_grant_request(self, proof: BudgetGrantRequestProof) -> None:
        persisted = self._proofs.get(proof.request_operation.command_id)
        if persisted != proof:
            raise SessionIntegrityError("budget grant request is not authoritative")

    def approval_state(
        self,
        proof: BudgetGrantRequestProof,
    ) -> BudgetGrantApprovalState:
        return BudgetGrantApprovalState(
            authority_id=self.authority_id,
            approval_digest=proof.approval.approval_digest,
            generation=self.generation,
            active=self.active,
        )

    def budget_grant_apply_status(
        self,
        proof: BudgetGrantRequestProof,
        apply_command_id: str,
    ) -> BudgetGrantApplyStatus:
        self.verify_budget_grant_request(proof)
        del apply_command_id
        status = self.apply_status
        if self.after_apply_status_read is not None:
            self.after_apply_status_read()
        return status


class _ApprovalResolverIdentity:
    def __init__(
        self,
        authority_id: str = "budget-grant-approval-authority.identity-test",
    ) -> None:
        self.authority_id = authority_id

    def resolve(self, approval_digest: str) -> BudgetGrantApproval | None:
        del approval_digest
        return None

    def approval_state(self, approval_digest: str) -> BudgetGrantApprovalState | None:
        del approval_digest
        return None

    def hold_session_apply(
        self,
        expected: BudgetGrantApprovalState,
        *,
        decision_digest: str,
        command_id: str,
    ) -> AbstractContextManager[None]:
        del expected, decision_digest, command_id
        raise AssertionError("identity-only resolver must not execute")


def test_budget_envelope_uses_versioned_hard_and_eighty_percent_soft() -> None:
    envelope = _envelope()

    assert envelope.hard_limits.provider_calls == 8
    assert envelope.soft_limits.provider_calls == 6.4
    assert envelope.soft_limits.tokens == 8000
    assert envelope.soft_limits.active_wall_clock == 80
    assert envelope.admission_requirement == envelope.hard_limits


def test_admission_replays_budget_policy_instead_of_trusting_envelope(
    tmp_path: Path,
) -> None:
    original = _envelope()
    reduced = original.hard_limits.model_copy(update={"tokens": 9000})
    payload = original.model_dump(mode="json")
    payload.update(
        {
            "hard_limits": reduced,
            "soft_limits": soft_limits(reduced),
            "admission_requirement": reduced,
            "envelope_digest": "",
        }
    )
    draft = BudgetEnvelope.model_construct(**payload)
    payload["envelope_digest"] = budget_envelope_digest(draft)
    tampered = BudgetEnvelope.model_validate(payload)

    result = _governor(tmp_path).reserve_admission(
        tampered,
        budget_policy=_policy(),
        lease_owner=_OWNER,
        operation_id="operation.tampered-envelope",
        lease_seconds=60,
        now=_now(),
    )

    assert result.result_code == "invalid_reservation"


def test_budget_envelope_rejects_nonstandard_soft_limits() -> None:
    envelope = _envelope()
    payload = envelope.model_dump(mode="json")
    payload["hard_limits"] = envelope.hard_limits
    payload["admission_requirement"] = envelope.admission_requirement
    payload["soft_limits"] = envelope.soft_limits.model_copy(update={"tokens": 7999})
    draft = BudgetEnvelope.model_construct(**payload)
    payload["envelope_digest"] = budget_envelope_digest(draft)

    with pytest.raises(ValueError, match="eighty percent"):
        BudgetEnvelope.model_validate(payload)


def test_admission_to_final_releases_unused_capacity(tmp_path: Path) -> None:
    governor = _governor(tmp_path, capacity=_capacity(multiplier=2))
    envelope = _envelope()

    admission = governor.reserve_admission(
        envelope,
        budget_policy=_policy(),
        lease_owner=_OWNER,
        operation_id="operation.admission",
        lease_seconds=60,
        now=_now(),
    )
    assert admission.result_code == "reserved"
    assert admission.reservation is not None
    assert admission.reservation.reserved == envelope.hard_limits
    assert admission.reservation.lease_owner == _OWNER
    assert admission.reservation.idempotency_key.startswith("admission.")

    final = governor.finalize_reservation(
        admission.reservation.reservation_id,
        proposal=_proposal(),
        lease_owner=_OWNER,
        expected_fencing_token=admission.reservation.fencing_token,
        operation_id="operation.finalize",
        now=_now(),
    )

    assert final.result_code == "finalized"
    assert final.reservation is not None
    assert final.reservation.reserved.slots == 2
    assert final.reservation.reserved.tokens == 2000
    assert final.reservation.reserved.provider_retries == 2
    assert final.reservation.provider_scope_ids == (
        "provider.role.delivery",
        "provider.role.evolution",
    )
    assert governor.snapshot().reserved.tokens == 2000


def test_offline_optimization_finalizes_without_panel_proposal(
    tmp_path: Path,
) -> None:
    governor = _governor(tmp_path, offline_capacity=_capacity())
    envelope = _envelope(
        session_id="optimization-epoch.001",
        pool="offline_optimization",
    )
    admission = governor.reserve_admission(
        envelope,
        budget_policy=_policy(),
        lease_owner="optimization-controller.test",
        operation_id="operation.optimization-admission",
        lease_seconds=60,
        now=_now(),
    )
    assert admission.reservation is not None

    final = governor.finalize_offline_reservation(
        admission.reservation.reservation_id,
        lease_owner="optimization-controller.test",
        expected_fencing_token=admission.reservation.fencing_token,
        operation_id="operation.optimization-finalize",
        now=_now(),
    )
    repeated = governor.finalize_offline_reservation(
        admission.reservation.reservation_id,
        lease_owner="optimization-controller.test",
        expected_fencing_token=admission.reservation.fencing_token,
        operation_id="operation.optimization-finalize",
        now=_now(),
    )

    assert final.result_code == "finalized"
    assert final.reservation is not None
    assert final.reservation.state == "final"
    assert final.reservation.pool == "offline_optimization"
    assert final.reservation.proposal_digest == ""
    assert final.reservation.reserved == envelope.hard_limits
    assert repeated.reservation == final.reservation


def test_lease_renewal_rotates_fencing_and_rejects_old_writer(tmp_path: Path) -> None:
    governor = _governor(tmp_path)
    admission = governor.reserve_admission(
        _envelope(),
        budget_policy=_policy(),
        lease_owner=_OWNER,
        operation_id="operation.admission",
        lease_seconds=60,
        now=_now(),
    )
    assert admission.reservation is not None
    old_token = admission.reservation.fencing_token

    renewed = governor.renew_reservation(
        admission.reservation.reservation_id,
        lease_owner=_OWNER,
        lease_seconds=120,
        expected_fencing_token=old_token,
        operation_id="operation.renew",
        now=_now() + timedelta(seconds=30),
    )
    assert renewed.result_code == "renewed"
    assert renewed.reservation is not None
    assert renewed.reservation.fencing_token > old_token

    late = governor.finalize_reservation(
        admission.reservation.reservation_id,
        proposal=_proposal(),
        lease_owner=_OWNER,
        expected_fencing_token=old_token,
        operation_id="operation.late-finalize",
        now=_now() + timedelta(seconds=31),
    )
    assert late.result_code == "stale_fencing"


def test_owner_release_reconciles_and_frees_capacity_idempotently(
    tmp_path: Path,
) -> None:
    governor = _governor(tmp_path)
    final = _final_reservation(governor)

    released = governor.release_reservation(
        final.reservation_id,
        lease_owner=_OWNER,
        expected_fencing_token=final.fencing_token,
        operation_id="operation.release",
        now=_now(),
    )
    repeated = governor.release_reservation(
        final.reservation_id,
        lease_owner=_OWNER,
        expected_fencing_token=final.fencing_token,
        operation_id="operation.release",
        now=_now(),
    )

    assert released.result_code == "released"
    assert released.reconciliation is not None
    assert repeated.reservation == released.reservation
    assert governor.snapshot().reserved == ResourceAmounts()


def test_two_processes_cannot_overbook_shared_capacity(tmp_path: Path) -> None:
    context = multiprocessing.get_context("spawn")
    queue = context.Queue()
    processes = [
        context.Process(
            target=_reserve_worker,
            args=(str(tmp_path), f"session.{index}", queue),
        )
        for index in range(2)
    ]

    for process in processes:
        process.start()
    results = [queue.get(timeout=20) for _ in processes]
    for process in processes:
        process.join(timeout=20)
        assert process.exitcode == 0

    assert sorted(results) == ["capacity_exhausted", "reserved"]


def test_committed_event_recovers_when_projection_write_crashes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    governor = _governor(tmp_path)
    original = governor._store.materialize_projection

    def crash_once(*args: object, **kwargs: object) -> None:
        monkeypatch.setattr(governor._store, "materialize_projection", original)
        raise RuntimeError("injected projection crash")

    monkeypatch.setattr(governor._store, "materialize_projection", crash_once)
    with pytest.raises(RuntimeError, match="projection crash"):
        governor.reserve_admission(
            _envelope(),
            budget_policy=_policy(),
            lease_owner=_OWNER,
            operation_id="operation.crash",
            lease_seconds=60,
            now=_now(),
        )

    recovered = _governor(tmp_path).reserve_admission(
        _envelope(),
        budget_policy=_policy(),
        lease_owner=_OWNER,
        operation_id="operation.crash",
        lease_seconds=60,
        now=_now(),
    )

    assert recovered.result_code == "reserved"
    assert recovered.reservation is not None
    assert _governor(tmp_path).snapshot().reserved == _envelope().hard_limits


def test_dead_lock_owner_is_reclaimed_before_reservation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    governor = _governor(tmp_path)
    governor._store.lock_path.parent.mkdir(parents=True, exist_ok=True)
    governor._store.lock_path.write_text(
        '{"pid":999999,"started_at":0}\n', encoding="utf-8"
    )
    monkeypatch.setattr(
        "ai_sdlc.core.stage_review.artifacts._pid_is_active", lambda _: False
    )

    result = governor.reserve_admission(
        _envelope(),
        budget_policy=_policy(),
        lease_owner=_OWNER,
        operation_id="operation.after-dead-owner",
        lease_seconds=60,
        now=_now(),
    )

    assert result.result_code == "reserved"


def test_operation_id_cannot_be_reused_for_a_different_effect(tmp_path: Path) -> None:
    governor = _governor(tmp_path, capacity=_capacity(multiplier=2))
    first = governor.reserve_admission(
        _envelope(),
        budget_policy=_policy(),
        lease_owner=_OWNER,
        operation_id="operation.same",
        lease_seconds=60,
        now=_now(),
    )
    conflicting = governor.reserve_admission(
        _envelope(session_id="session.conflicting"),
        budget_policy=_policy(),
        lease_owner=_OWNER,
        operation_id="operation.same",
        lease_seconds=60,
        now=_now(),
    )

    assert first.result_code == "reserved"
    assert conflicting.result_code == "state_corrupt"
    assert governor.snapshot().reservation_count == 1


def test_same_session_and_envelope_have_one_semantic_admission(
    tmp_path: Path,
) -> None:
    governor = _governor(tmp_path, capacity=_capacity(multiplier=2))
    first = governor.reserve_admission(
        _envelope(),
        budget_policy=_policy(),
        lease_owner=_OWNER,
        operation_id="command.first",
        lease_seconds=60,
        now=_now(),
    )
    repeated = governor.reserve_admission(
        _envelope(),
        budget_policy=_policy(),
        lease_owner=_OWNER,
        operation_id="command.second",
        lease_seconds=60,
        now=_now(),
    )

    assert first.result_code == "reserved"
    assert repeated.result_code == "reserved"
    assert repeated.reservation is not None
    assert first.reservation is not None
    assert repeated.reservation.reservation_id == first.reservation.reservation_id
    assert governor.snapshot().reserved == _envelope().hard_limits

    reused_command = governor.reserve_admission(
        _envelope(session_id="session.other"),
        budget_policy=_policy(),
        lease_owner=_OWNER,
        operation_id="command.second",
        lease_seconds=60,
        now=_now(),
    )
    assert reused_command.result_code == "state_corrupt"
    assert governor.snapshot().reservation_count == 1


def test_idempotent_result_exposes_original_target_and_current_projection(
    tmp_path: Path,
) -> None:
    governor = _governor(tmp_path)
    envelope = _envelope()
    admission = governor.reserve_admission(
        envelope,
        budget_policy=_policy(),
        lease_owner=_OWNER,
        operation_id="operation.admission",
        lease_seconds=60,
        now=_now(),
    )
    assert admission.reservation is not None
    final = governor.finalize_reservation(
        admission.reservation.reservation_id,
        proposal=_proposal(),
        lease_owner=_OWNER,
        expected_fencing_token=admission.reservation.fencing_token,
        operation_id="operation.finalize",
        now=_now(),
    )
    repeated = governor.reserve_admission(
        envelope,
        budget_policy=_policy(),
        lease_owner=_OWNER,
        operation_id="operation.admission",
        lease_seconds=60,
        now=_now(),
    )

    assert final.reservation is not None
    assert repeated.reservation == final.reservation
    assert repeated.operation_reservation == admission.reservation


def test_expiry_reclaims_capacity_and_rejects_stale_fencing(tmp_path: Path) -> None:
    governor = _governor(tmp_path)
    admission = governor.reserve_admission(
        _envelope(),
        budget_policy=_policy(),
        lease_owner=_OWNER,
        operation_id="operation.expiring",
        lease_seconds=1,
        now=_now(),
    )
    assert admission.reservation is not None
    expired_token = admission.reservation.fencing_token

    replacement = governor.reserve_admission(
        _envelope(session_id="session.replacement"),
        budget_policy=_policy(),
        lease_owner=_OWNER,
        operation_id="operation.replacement",
        lease_seconds=60,
        now=_now() + timedelta(seconds=2),
    )
    late = governor.finalize_reservation(
        admission.reservation.reservation_id,
        proposal=_proposal(),
        lease_owner=_OWNER,
        expected_fencing_token=expired_token,
        operation_id="operation.late",
        now=_now() + timedelta(seconds=2),
    )

    assert replacement.result_code == "reserved"
    assert late.result_code == "stale_fencing"
    with governor._store.locked():
        state = governor._store.load_state()
    assert any(
        item.reservation_id == admission.reservation.reservation_id
        for item in state.reconciliations.values()
    )


def test_final_reservation_rejects_requirement_above_admission(tmp_path: Path) -> None:
    governor = _governor(tmp_path)
    admission = governor.reserve_admission(
        _envelope(),
        budget_policy=_policy(),
        lease_owner=_OWNER,
        operation_id="operation.admission",
        lease_seconds=60,
        now=_now(),
    )
    assert admission.reservation is not None
    excessive = _requirement().model_copy(update={"total_tokens": 10001})

    result = governor.finalize_reservation(
        admission.reservation.reservation_id,
        proposal=_proposal(requirement=excessive),
        lease_owner=_OWNER,
        expected_fencing_token=admission.reservation.fencing_token,
        operation_id="operation.finalize",
        now=_now(),
    )

    assert result.result_code == "requirement_exceeds_admission"
    assert (
        governor.provider_call_authorized(
            admission.reservation.reservation_id,
            invocation_id="invocation.not-final",
            anticipated_usage=_provider_anticipated(),
            lease_owner=_OWNER,
            expected_fencing_token=admission.reservation.fencing_token,
            operation_id="operation.not-final-authorization",
            now=_now(),
        ).result_code
        == "not_final"
    )


def test_final_reservation_rejects_proposal_from_other_budget_envelope(
    tmp_path: Path,
) -> None:
    governor = _governor(tmp_path)
    admission = governor.reserve_admission(
        _envelope(),
        budget_policy=_policy(),
        lease_owner=_OWNER,
        operation_id="operation.admission",
        lease_seconds=60,
        now=_now(),
    )
    assert admission.reservation is not None
    other = _envelope(session_id="session.other")

    result = governor.finalize_reservation(
        admission.reservation.reservation_id,
        proposal=_proposal(envelope_digest=other.envelope_digest),
        lease_owner=_OWNER,
        expected_fencing_token=admission.reservation.fencing_token,
        operation_id="operation.wrong-proposal",
        now=_now(),
    )

    assert result.result_code == "invalid_reservation"
    assert governor.get_reservation(admission.reservation.reservation_id).state == (
        "admission"
    )


def test_formal_panel_plan_can_only_freeze_after_matching_final_reservation(
    tmp_path: Path,
) -> None:
    governor = _governor(tmp_path)
    proposal = _proposal()
    admission = governor.reserve_admission(
        _envelope(),
        budget_policy=_policy(),
        lease_owner=_OWNER,
        operation_id="operation.admission",
        lease_seconds=60,
        now=_now(),
    )
    assert admission.reservation is not None
    with pytest.raises(ValueError, match="FinalReservation"):
        _build_reviewer_panel_plan(proposal, admission.reservation)

    finalized = governor.finalize_reservation(
        admission.reservation.reservation_id,
        proposal=proposal,
        lease_owner=_OWNER,
        expected_fencing_token=admission.reservation.fencing_token,
        operation_id="operation.finalize",
        now=_now(),
    )
    assert finalized.reservation is not None
    plan = _build_reviewer_panel_plan(proposal, finalized.reservation)

    assert plan.proposal == proposal
    assert plan.final_reservation_digest == finalized.reservation.reservation_digest
    with pytest.raises(ValueError, match="reviewer proposal"):
        _build_reviewer_panel_plan(
            _proposal(envelope_digest=_envelope(session_id="other").envelope_digest),
            finalized.reservation,
        )


def test_final_reservation_binds_exact_proposal_request_lineage(
    tmp_path: Path,
) -> None:
    governor = _governor(tmp_path)
    proposal = _proposal(request_digest="sha256:request.one")
    final = _final_reservation(governor, proposal=proposal)
    changed_request = _proposal(request_digest="sha256:request.two")

    assert changed_request.proposal_digest == proposal.proposal_digest
    with pytest.raises(ValueError, match="lineage"):
        _build_reviewer_panel_plan(changed_request, final)


def test_budget_grant_expansion_is_cas_and_idempotent(tmp_path: Path) -> None:
    governor = _governor(tmp_path, capacity=_capacity(multiplier=2))
    final = _final_reservation(governor)
    increment = ResourceAmounts(tokens=500, provider_calls=1)
    grant = build_budget_grant(
        project_id=final.project_id,
        work_item_id=final.work_item_id,
        stage_review_session_id=final.stage_review_session_id,
        final_reservation_id=final.reservation_id,
        expected_budget_revision=final.budget_revision,
        increment=increment,
        requested_event_digest=_REQUESTED_EVENT_DIGEST,
    )

    expanded = _apply_budget_grant(
        governor._store,
        grant,
        lease_owner=_OWNER,
        expected_reservation_revision=final.revision,
        expected_reservation_digest=final.reservation_digest,
        expected_fencing_token=final.fencing_token,
        now=_now(),
    )
    repeated = _apply_budget_grant(
        governor._store,
        grant,
        lease_owner=_OWNER,
        expected_reservation_revision=final.revision,
        expected_reservation_digest=final.reservation_digest,
        expected_fencing_token=final.fencing_token,
        now=_now(),
    )

    assert expanded.result_code == "expanded"
    assert repeated.reservation == expanded.reservation
    assert expanded.reservation is not None
    assert expanded.reservation.reserved.tokens == 2500
    conflicting_grant = build_budget_grant(
        project_id=final.project_id,
        work_item_id=final.work_item_id,
        stage_review_session_id=final.stage_review_session_id,
        final_reservation_id=final.reservation_id,
        expected_budget_revision=final.budget_revision,
        increment=ResourceAmounts(tokens=501, provider_calls=1),
        requested_event_digest=_REQUESTED_EVENT_DIGEST,
    )
    conflict = _apply_budget_grant(
        governor._store,
        conflicting_grant,
        lease_owner=_OWNER,
        expected_reservation_revision=final.revision,
        expected_reservation_digest=final.reservation_digest,
        expected_fencing_token=expanded.reservation.fencing_token,
        now=_now(),
    )
    assert conflict.result_code == "cas_conflict"


def test_session_budget_grant_coordinator_returns_exact_resource_proof(
    tmp_path: Path,
) -> None:
    authority = _RequestAuthority()
    governor = _governor(
        tmp_path,
        capacity=_capacity(multiplier=2),
        budget_grant_authority=authority,
    )
    final = _final_reservation(governor)
    session = _session_for_reservation(final)
    proof, grant = _session_budget_request(
        session,
        final,
        ResourceAmounts(tokens=500, provider_calls=1),
    )
    authority.add(proof)
    coordinator = ResourceBudgetGrantCoordinator(governor, now=_now())

    applied = coordinator.apply(grant, session, proof)
    repeated = coordinator.apply(grant, session, proof)

    assert repeated == applied
    assert applied.reservation.budget_revision == 1
    assert applied.reservation.reserved.tokens == final.reserved.tokens + 500
    assert applied.previous_reservation_digest == final.reservation_digest
    assert applied.resource_operation_digest
    assert applied.resource_event_digest
    assert grant.grant_id in applied.reservation.budget_grant_ids


def test_session_budget_grant_coordinator_recovers_pending_resource_operation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    authority = _RequestAuthority()
    governor = _governor(
        tmp_path,
        capacity=_capacity(multiplier=2),
        budget_grant_authority=authority,
    )
    final = _final_reservation(governor)
    session = _session_for_reservation(final)
    proof, grant = _session_budget_request(
        session,
        final,
        ResourceAmounts(tokens=500),
    )
    authority.add(proof)
    coordinator = ResourceBudgetGrantCoordinator(governor, now=_now())
    original = governor._store.append_event
    monkeypatch.setattr(
        governor._store,
        "append_event",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("resource grant event exit")
        ),
    )
    with pytest.raises(RuntimeError, match="resource grant event exit"):
        coordinator.apply(grant, session, proof)
    monkeypatch.setattr(governor._store, "append_event", original)

    recovered = coordinator.apply(grant, session, proof)

    assert recovered.reservation.budget_revision == 1
    assert governor.get_reservation(final.reservation_id) == recovered.reservation


def test_session_budget_grant_rejects_request_outside_approval_authority(
    tmp_path: Path,
) -> None:
    authority = _RequestAuthority()
    governor = _governor(
        tmp_path,
        capacity=_capacity(multiplier=2),
        budget_grant_authority=authority,
    )
    final = _final_reservation(governor)
    session = _session_for_reservation(final)
    proof, _ = _session_budget_request(
        session,
        final,
        ResourceAmounts(tokens=500),
    )
    divergent = build_budget_grant(
        project_id=final.project_id,
        work_item_id=final.work_item_id,
        stage_review_session_id=final.stage_review_session_id,
        final_reservation_id=final.reservation_id,
        expected_budget_revision=final.budget_revision,
        increment=ResourceAmounts(tokens=501),
        requested_event_digest=proof.requested_event.event_digest,
    )
    authority.add(proof)
    coordinator = ResourceBudgetGrantCoordinator(governor, now=_now())

    with pytest.raises(BudgetGrantResourceError, match="invalid_input"):
        coordinator.apply(divergent, session, proof)

    assert governor.get_reservation(final.reservation_id) == final


def test_reconciled_resource_grant_cannot_be_verified_as_current(
    tmp_path: Path,
) -> None:
    authority = _RequestAuthority()
    governor = _governor(
        tmp_path,
        capacity=_capacity(multiplier=2),
        budget_grant_authority=authority,
    )
    final = _final_reservation(governor)
    session = _session_for_reservation(final)
    proof, grant = _session_budget_request(
        session,
        final,
        ResourceAmounts(tokens=500),
    )
    authority.add(proof)
    coordinator = ResourceBudgetGrantCoordinator(governor, now=_now())
    application = coordinator.apply(grant, session, proof)
    decision = coordinator.decide(application, proof, "reconcile")

    reconciled = coordinator.reconcile(
        application,
        decision,
        proof,
        _session_apply_command_id(grant),
    )
    repeated = coordinator.reconcile(application, decision, proof, "")

    assert repeated == reconciled
    assert grant.grant_id in (
        reconciled.resource_operation.target_event.reservation.reconciled_budget_grant_ids
    )
    with pytest.raises(BudgetGrantResourceError, match="grant_not_current"):
        coordinator.verify(application, session, proof)


def test_resource_gateway_rejects_self_attested_budget_grant_proof(
    tmp_path: Path,
) -> None:
    governor = _governor(tmp_path, capacity=_capacity(multiplier=2))
    final = _final_reservation(governor)
    session = _session_for_reservation(final)
    proof, grant = _session_budget_request(
        session,
        final,
        ResourceAmounts(tokens=500),
    )

    with pytest.raises(BudgetGrantResourceError, match="state_corrupt"):
        governor.apply_session_budget_grant(
            grant,
            proof,
            now=_now(),
        )

    assert governor.get_reservation(final.reservation_id) == final


def test_resource_governor_rejects_authority_identity_drift(
    tmp_path: Path,
) -> None:
    original = ResourceGovernor(
        tmp_path,
        project_id="project.shared",
        foreground_capacity=_capacity(),
        budget_grant_approval_resolver=_ApprovalResolverIdentity("authority.original"),
    )
    _final_reservation(original)
    with pytest.raises(SharedStateIntegrityError, match="authority binding changed"):
        ResourceGovernor(
            tmp_path,
            project_id="project.shared",
            foreground_capacity=_capacity(),
            budget_grant_approval_resolver=_ApprovalResolverIdentity(
                "authority.divergent"
            ),
        )


def test_resource_governor_reads_pre_authority_config_without_rewriting_it(
    tmp_path: Path,
) -> None:
    governor = _governor(tmp_path)
    governor.snapshot()
    config_path = governor._store.config_path
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    payload.pop("budget_grant_authority_id", None)
    payload["config_digest"] = resource_config_digest(payload)
    original = json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n"
    config_path.write_text(original, encoding="utf-8")

    reopened = _governor(tmp_path)

    assert reopened.snapshot().revision == 0
    assert config_path.read_text(encoding="utf-8") == original


def test_bound_authority_identity_includes_canonical_session_store(
    tmp_path: Path,
) -> None:
    primary_root = tmp_path / "primary"
    divergent_root = tmp_path / "divergent"
    resolver = _ApprovalResolverIdentity()
    primary_store = SessionEventStore(primary_root, project_id="project.shared")
    divergent_store = SessionEventStore(divergent_root, project_id="project.shared")
    primary = BoundBudgetGrantRequestAuthority(primary_store, resolver)
    divergent = BoundBudgetGrantRequestAuthority(divergent_store, resolver)

    governor = ResourceGovernor(
        primary_root,
        project_id="project.shared",
        foreground_capacity=_capacity(),
        budget_grant_approval_resolver=resolver,
    )

    assert primary.authority_id != divergent.authority_id
    assert isinstance(
        governor._budget_grant_authority, BoundBudgetGrantRequestAuthority
    )
    assert governor._budget_grant_authority.authority_id == primary.authority_id
    authority_path = governor._store.root / "budget-grant-authority.json"
    authority_payload = json.loads(authority_path.read_text(encoding="utf-8"))
    shared_payload = json.loads(
        (governor._store.shared_root / "shared-state-binding.json").read_text(
            encoding="utf-8"
        )
    )
    assert authority_payload["schema_version"] == "1"
    assert authority_payload["binding_digest"].startswith("sha256:")
    assert shared_payload["schema_version"] == "1"
    assert shared_payload["binding_digest"].startswith("sha256:")
    with pytest.raises(TypeError, match="budget_grant_authority"):
        ResourceGovernor(
            primary_root,
            project_id="project.shared",
            foreground_capacity=_capacity(),
            budget_grant_authority=divergent,  # type: ignore[call-arg]
        )


def test_budget_grant_decision_is_first_writer_wins(
    tmp_path: Path,
) -> None:
    authority = _RequestAuthority()
    governor = _governor(
        tmp_path,
        capacity=_capacity(multiplier=2),
        budget_grant_authority=authority,
    )
    final = _final_reservation(governor)
    session = _session_for_reservation(final)
    proof, grant = _session_budget_request(
        session,
        final,
        ResourceAmounts(tokens=500),
    )
    authority.add(proof)
    coordinator = ResourceBudgetGrantCoordinator(governor, now=_now())
    application = coordinator.apply(grant, session, proof)

    first = coordinator.decide(application, proof, "session_apply")
    competing = coordinator.decide(application, proof, "reconcile")

    assert competing == first
    assert first.decision_kind == "session_apply"


def test_session_apply_decision_reconciles_after_approval_generation_changes(
    tmp_path: Path,
) -> None:
    authority = _RequestAuthority()
    governor = _governor(
        tmp_path,
        capacity=_capacity(multiplier=2),
        budget_grant_authority=authority,
    )
    final = _final_reservation(governor)
    session = _session_for_reservation(final)
    proof, grant = _session_budget_request(
        session,
        final,
        ResourceAmounts(tokens=500),
    )
    authority.add(proof)
    coordinator = ResourceBudgetGrantCoordinator(governor, now=_now())
    application = coordinator.apply(grant, session, proof)
    decision = coordinator.decide(application, proof, "session_apply")
    authority.generation += 1
    authority.active = False
    authority.apply_status = "superseded"

    reconciled = coordinator.reconcile(
        application,
        decision,
        proof,
        _session_apply_command_id(grant),
    )

    released = reconciled.resource_operation.target_event.reservation
    assert grant.grant_id in released.reconciled_budget_grant_ids
    assert released.reserved == final.reserved


def test_committed_session_apply_cannot_be_reconciled_after_approval_changes(
    tmp_path: Path,
) -> None:
    authority = _RequestAuthority()
    governor = _governor(
        tmp_path,
        capacity=_capacity(multiplier=2),
        budget_grant_authority=authority,
    )
    final = _final_reservation(governor)
    session = _session_for_reservation(final)
    proof, grant = _session_budget_request(
        session,
        final,
        ResourceAmounts(tokens=500),
    )
    authority.add(proof)
    coordinator = ResourceBudgetGrantCoordinator(governor, now=_now())
    application = coordinator.apply(grant, session, proof)
    decision = coordinator.decide(application, proof, "session_apply")
    authority.apply_status = "committed"
    authority.generation += 1
    authority.active = False

    with pytest.raises(BudgetGrantResourceError, match="invalid_input"):
        coordinator.reconcile(application, decision, proof, "")
    with pytest.raises(BudgetGrantResourceError, match="decision_conflict"):
        coordinator.reconcile(
            application,
            decision,
            proof,
            _session_apply_command_id(grant),
        )

    assert governor.get_reservation(final.reservation_id) == application.reservation


def test_pending_session_apply_cannot_release_if_commit_wins_after_status_read(
    tmp_path: Path,
) -> None:
    authority = _RequestAuthority()
    governor = _governor(
        tmp_path,
        capacity=_capacity(multiplier=2),
        budget_grant_authority=authority,
    )
    final = _final_reservation(governor)
    session = _session_for_reservation(final)
    proof, grant = _session_budget_request(
        session,
        final,
        ResourceAmounts(tokens=500),
    )
    authority.add(proof)
    coordinator = ResourceBudgetGrantCoordinator(governor, now=_now())
    application = coordinator.apply(grant, session, proof)
    decision = coordinator.decide(application, proof, "session_apply")
    authority.generation += 1
    authority.active = False
    authority.after_apply_status_read = lambda: setattr(
        authority,
        "apply_status",
        "committed",
    )

    with pytest.raises(BudgetGrantResourceError, match="decision_conflict"):
        coordinator.reconcile(
            application,
            decision,
            proof,
            _session_apply_command_id(grant),
        )

    assert authority.apply_status == "committed"
    assert governor.get_reservation(final.reservation_id) == application.reservation


def test_session_apply_commit_guard_rejects_expired_reservation_without_prior_command(
    tmp_path: Path,
) -> None:
    authority = _RequestAuthority()
    governor = _governor(
        tmp_path,
        capacity=_capacity(multiplier=2),
        budget_grant_authority=authority,
    )
    final = _final_reservation(governor)
    session = _session_for_reservation(final)
    proof, grant = _session_budget_request(
        session,
        final,
        ResourceAmounts(tokens=500),
    )
    authority.add(proof)
    coordinator = ResourceBudgetGrantCoordinator(governor, now=_now())
    application = coordinator.apply(grant, session, proof)
    decision = coordinator.decide(application, proof, "session_apply")
    late = ResourceBudgetGrantCoordinator(
        governor,
        now=_now() + timedelta(seconds=61),
    )

    with (
        pytest.raises(BudgetGrantResourceError, match="grant_not_current"),
        late.hold_apply_commit(application, decision, session, proof),
    ):
        pytest.fail("expired budget grant must not enter Session commit")

    assert governor.get_reservation(final.reservation_id).state == "expired"


def test_session_apply_decision_reconciles_after_resource_snapshot_changes(
    tmp_path: Path,
) -> None:
    authority = _RequestAuthority()
    governor = _governor(
        tmp_path,
        capacity=_capacity(multiplier=2),
        budget_grant_authority=authority,
    )
    final = _final_reservation(governor)
    session = _session_for_reservation(final)
    proof, grant = _session_budget_request(
        session,
        final,
        ResourceAmounts(tokens=500),
    )
    authority.add(proof)
    coordinator = ResourceBudgetGrantCoordinator(governor, now=_now())
    application = coordinator.apply(grant, session, proof)
    decision = coordinator.decide(application, proof, "session_apply")
    renewed = governor.renew_reservation(
        final.reservation_id,
        lease_owner=_OWNER,
        lease_seconds=60,
        expected_fencing_token=application.reservation.fencing_token,
        operation_id="operation.renew.after-session-apply-decision",
        now=_now() + timedelta(seconds=1),
    )
    assert renewed.reservation is not None
    authority.apply_status = "superseded"

    reconciled = coordinator.reconcile(
        application,
        decision,
        proof,
        _session_apply_command_id(grant),
    )

    released = reconciled.resource_operation.target_event.reservation
    assert released.reserved == final.reserved
    assert grant.grant_id in released.reconciled_budget_grant_ids


def test_budget_grant_reconciliation_rebases_after_legal_renewal(
    tmp_path: Path,
) -> None:
    authority = _RequestAuthority()
    governor = _governor(
        tmp_path,
        capacity=_capacity(multiplier=2),
        budget_grant_authority=authority,
    )
    final = _final_reservation(governor)
    session = _session_for_reservation(final)
    proof, grant = _session_budget_request(
        session,
        final,
        ResourceAmounts(tokens=500),
    )
    authority.add(proof)
    coordinator = ResourceBudgetGrantCoordinator(governor, now=_now())
    application = coordinator.apply(grant, session, proof)
    decision = coordinator.decide(application, proof, "reconcile")
    renewed = governor.renew_reservation(
        final.reservation_id,
        lease_owner=_OWNER,
        lease_seconds=60,
        expected_fencing_token=application.reservation.fencing_token,
        operation_id="operation.renew.after-budget-grant",
        now=_now() + timedelta(seconds=1),
    )
    assert renewed.reservation is not None

    reconciled = coordinator.reconcile(application, decision, proof, "")
    released = reconciled.resource_operation.target_event.reservation

    assert decision.resource_reservation == application.reservation
    assert renewed.reservation.revision > decision.resource_reservation_revision
    assert released.reserved == final.reserved
    assert released.hard_limits == final.hard_limits
    assert grant.grant_id in released.reconciled_budget_grant_ids


def test_budget_grant_reconciliation_releases_only_its_increment(
    tmp_path: Path,
) -> None:
    governor = _governor(tmp_path, capacity=_capacity(multiplier=2))
    final = _final_reservation(governor)
    grant = build_budget_grant(
        project_id=final.project_id,
        work_item_id=final.work_item_id,
        stage_review_session_id=final.stage_review_session_id,
        final_reservation_id=final.reservation_id,
        expected_budget_revision=final.budget_revision,
        increment=ResourceAmounts(tokens=500, provider_calls=1),
        requested_event_digest=_REQUESTED_EVENT_DIGEST,
    )
    applied = _apply_budget_grant(
        governor._store,
        grant,
        lease_owner=_OWNER,
        expected_reservation_revision=final.revision,
        expected_reservation_digest=final.reservation_digest,
        expected_fencing_token=final.fencing_token,
        now=_now(),
    )
    assert applied.reservation is not None

    reconciled = _reconcile_budget_grant(
        governor._store,
        grant,
        lease_owner=_OWNER,
        expected_reservation_revision=applied.reservation.revision,
        expected_reservation_digest=applied.reservation.reservation_digest,
        expected_fencing_token=applied.reservation.fencing_token,
        now=_now(),
    )
    repeated = _reconcile_budget_grant(
        governor._store,
        grant,
        lease_owner=_OWNER,
        expected_reservation_revision=applied.reservation.revision,
        expected_reservation_digest=applied.reservation.reservation_digest,
        expected_fencing_token=applied.reservation.fencing_token,
        now=_now(),
    )

    assert reconciled.result_code == "reconciled"
    assert reconciled.reservation is not None
    assert reconciled.reservation.reserved == final.reserved
    assert repeated.reservation == reconciled.reservation
    assert not hasattr(governor, "expand_final_reservation")
    assert not hasattr(governor, "apply_budget_grant")
    assert not hasattr(governor, "reconcile_budget_grant")
    assert grant.requested_event_digest == _REQUESTED_EVENT_DIGEST


def test_budget_grant_operation_recovers_without_duplicate_expansion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    governor = _governor(tmp_path, capacity=_capacity(multiplier=2))
    final = _final_reservation(governor)
    grant = build_budget_grant(
        project_id=final.project_id,
        work_item_id=final.work_item_id,
        stage_review_session_id=final.stage_review_session_id,
        final_reservation_id=final.reservation_id,
        expected_budget_revision=final.budget_revision,
        increment=ResourceAmounts(tokens=500),
        requested_event_digest=_REQUESTED_EVENT_DIGEST,
    )
    original_append = governor._store.append_event

    def crash_before_event(*args: object, **kwargs: object) -> object:
        raise RuntimeError("injected grant event crash")

    monkeypatch.setattr(governor._store, "append_event", crash_before_event)
    with pytest.raises(RuntimeError, match="grant event crash"):
        _apply_budget_grant(
            governor._store,
            grant,
            lease_owner=_OWNER,
            expected_reservation_revision=final.revision,
            expected_reservation_digest=final.reservation_digest,
            expected_fencing_token=final.fencing_token,
            now=_now(),
        )
    monkeypatch.setattr(governor._store, "append_event", original_append)

    recovered = _apply_budget_grant(
        governor._store,
        grant,
        lease_owner=_OWNER,
        expected_reservation_revision=final.revision,
        expected_reservation_digest=final.reservation_digest,
        expected_fencing_token=final.fencing_token,
        now=_now(),
    )

    assert recovered.reservation is not None
    assert recovered.reservation.reserved.tokens == final.reserved.tokens + 500
    assert len(tuple(governor._store.grant_operations_dir.glob("*.json"))) == 1


def test_pending_budget_grant_recovers_before_unrelated_accounting(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    governor = _governor(tmp_path, capacity=_capacity(multiplier=2))
    final = _final_reservation(governor)
    grant = build_budget_grant(
        project_id=final.project_id,
        work_item_id=final.work_item_id,
        stage_review_session_id=final.stage_review_session_id,
        final_reservation_id=final.reservation_id,
        expected_budget_revision=final.budget_revision,
        increment=ResourceAmounts(tokens=500),
        requested_event_digest=_REQUESTED_EVENT_DIGEST,
    )
    original_append = governor._store.append_event
    monkeypatch.setattr(
        governor._store,
        "append_event",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("grant crash")),
    )
    with pytest.raises(RuntimeError, match="grant crash"):
        _apply_budget_grant(
            governor._store,
            grant,
            lease_owner=_OWNER,
            expected_reservation_revision=final.revision,
            expected_reservation_digest=final.reservation_digest,
            expected_fencing_token=final.fencing_token,
            now=_now(),
        )
    monkeypatch.setattr(governor._store, "append_event", original_append)

    accounting = governor.record_usage(
        final.reservation_id,
        delta=ResourceAmounts(tokens=1),
        lease_owner=_OWNER,
        expected_fencing_token=final.fencing_token,
        operation_id="operation.after-pending-grant",
        now=_now(),
    )
    recovered = governor.get_reservation(final.reservation_id)
    repeated = _apply_budget_grant(
        governor._store,
        grant,
        lease_owner=_OWNER,
        expected_reservation_revision=final.revision,
        expected_reservation_digest=final.reservation_digest,
        expected_fencing_token=final.fencing_token,
        now=_now(),
    )

    assert accounting.result_code == "stale_fencing"
    assert recovered.reserved.tokens == final.reserved.tokens + 500
    assert repeated.result_code == "expanded"
    assert repeated.reservation == recovered


def test_pending_budget_grant_reconciliation_recovers_before_next_command(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    governor = _governor(tmp_path, capacity=_capacity(multiplier=2))
    final = _final_reservation(governor)
    grant = build_budget_grant(
        project_id=final.project_id,
        work_item_id=final.work_item_id,
        stage_review_session_id=final.stage_review_session_id,
        final_reservation_id=final.reservation_id,
        expected_budget_revision=final.budget_revision,
        increment=ResourceAmounts(tokens=500),
        requested_event_digest=_REQUESTED_EVENT_DIGEST,
    )
    applied = _apply_budget_grant(
        governor._store,
        grant,
        lease_owner=_OWNER,
        expected_reservation_revision=final.revision,
        expected_reservation_digest=final.reservation_digest,
        expected_fencing_token=final.fencing_token,
        now=_now(),
    )
    assert applied.reservation is not None
    original_append = governor._store.append_event
    monkeypatch.setattr(
        governor._store,
        "append_event",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("reconcile crash")),
    )
    with pytest.raises(RuntimeError, match="reconcile crash"):
        _reconcile_budget_grant(
            governor._store,
            grant,
            lease_owner=_OWNER,
            expected_reservation_revision=applied.reservation.revision,
            expected_reservation_digest=applied.reservation.reservation_digest,
            expected_fencing_token=applied.reservation.fencing_token,
            now=_now(),
        )
    monkeypatch.setattr(governor._store, "append_event", original_append)

    recovered = governor.get_reservation(final.reservation_id)
    repeated = _reconcile_budget_grant(
        governor._store,
        grant,
        lease_owner=_OWNER,
        expected_reservation_revision=applied.reservation.revision,
        expected_reservation_digest=applied.reservation.reservation_digest,
        expected_fencing_token=applied.reservation.fencing_token,
        now=_now(),
    )

    assert recovered.reserved == final.reserved
    assert repeated.result_code == "reconciled"
    assert repeated.reservation == recovered


def test_usage_is_monotonic_and_reports_soft_then_hard_limit(tmp_path: Path) -> None:
    governor = _governor(tmp_path)
    final = _final_reservation(governor)

    below = governor.record_usage(
        final.reservation_id,
        delta=ResourceAmounts(tokens=1500, active_wall_clock=10),
        lease_owner=_OWNER,
        expected_fencing_token=final.fencing_token,
        operation_id="operation.usage-1",
        now=_now(),
    )
    assert below.result_code == "recorded"
    assert below.pressure == "within"
    assert below.reservation is not None
    soft = governor.record_usage(
        final.reservation_id,
        delta=ResourceAmounts(tokens=200, active_wall_clock=1),
        lease_owner=_OWNER,
        expected_fencing_token=below.reservation.fencing_token,
        operation_id="operation.usage-2",
        now=_now(),
    )
    assert soft.pressure == "soft_limit_reached"
    assert soft.reservation is not None
    hard = governor.record_usage(
        final.reservation_id,
        delta=ResourceAmounts(tokens=300, active_wall_clock=1),
        lease_owner=_OWNER,
        expected_fencing_token=soft.reservation.fencing_token,
        operation_id="operation.usage-3",
        now=_now(),
    )
    assert hard.pressure == "hard_limit_reached"
    assert hard.reservation is not None
    assert hard.reservation.usage.tokens == 2000


def test_zero_usage_delta_is_rejected_without_writing_an_event(tmp_path: Path) -> None:
    governor = _governor(tmp_path)
    final = _final_reservation(governor)
    before = tuple(governor._store.events_dir.glob("*.json"))

    rejected = governor.record_usage(
        final.reservation_id,
        delta=ResourceAmounts(),
        lease_owner=_OWNER,
        expected_fencing_token=final.fencing_token,
        operation_id="operation.zero-usage",
        now=_now(),
    )

    assert rejected.result_code == "invalid_input"
    assert tuple(governor._store.events_dir.glob("*.json")) == before
    assert governor.snapshot().revision == 2


def test_event_is_fully_validated_before_exclusive_ledger_write(tmp_path: Path) -> None:
    governor = _governor(tmp_path)
    final = _final_reservation(governor)
    effect = resource_operation_effect_digest(
        "record_usage",
        {"reservation_id": final.reservation_id, "amounts": ResourceAmounts()},
    )
    invalid_target = update_reservation(
        final,
        operation_id="operation.invalid-preflight",
        operation_effect_digest=effect,
    )
    with governor._store.locked():
        state = governor._store.load_state()
        event = build_resource_event(
            sequence=state.head_sequence + 1,
            event_kind="usage_recorded",
            operation_id="operation.invalid-preflight",
            previous_event_digest=state.head_digest,
            previous_reservation_digest=final.reservation_digest,
            reservation=invalid_target,
            actual_usage=ResourceAmounts(),
        )
        before = tuple(governor._store.events_dir.glob("*.json"))
        with pytest.raises(SharedStateIntegrityError, match="actual usage"):
            governor._store.append_event(event)

    assert tuple(governor._store.events_dir.glob("*.json")) == before


def test_usage_above_final_reservation_is_rejected_without_mutation(
    tmp_path: Path,
) -> None:
    governor = _governor(tmp_path)
    final = _final_reservation(governor)

    rejected = governor.record_usage(
        final.reservation_id,
        delta=ResourceAmounts(tokens=2001),
        lease_owner=_OWNER,
        expected_fencing_token=final.fencing_token,
        operation_id="operation.excess",
        now=_now(),
    )

    assert rejected.result_code == "hard_limit_exceeded"
    assert governor.get_reservation(final.reservation_id).usage.tokens == 0


def test_provider_authorization_checks_all_anticipated_resource_dimensions(
    tmp_path: Path,
) -> None:
    governor = _governor(tmp_path)
    final = _final_reservation(governor)
    recorded = governor.record_usage(
        final.reservation_id,
        delta=ResourceAmounts(tokens=2000),
        lease_owner=_OWNER,
        expected_fencing_token=final.fencing_token,
        operation_id="operation.consume-tokens",
        now=_now(),
    )
    assert recorded.reservation is not None

    result = governor.provider_call_authorized(
        final.reservation_id,
        invocation_id="invocation.exhausted",
        anticipated_usage=_provider_anticipated(),
        lease_owner=_OWNER,
        expected_fencing_token=recorded.reservation.fencing_token,
        operation_id="operation.exhausted-authorization",
        now=_now(),
    )

    assert result.result_code == "hard_limit_exceeded"


def test_provider_authorization_is_pending_until_exactly_once_settlement(
    tmp_path: Path,
) -> None:
    governor = _governor(tmp_path)
    final = _final_reservation(governor)
    incomplete = governor.provider_call_authorized(
        final.reservation_id,
        invocation_id="invocation.incomplete",
        anticipated_usage=ResourceAmounts(provider_calls=1, tokens=100),
        lease_owner=_OWNER,
        expected_fencing_token=final.fencing_token,
        operation_id="operation.incomplete",
        now=_now(),
    )
    assert incomplete.result_code == "invalid_input"

    authorized = governor.provider_call_authorized(
        final.reservation_id,
        invocation_id="invocation.one",
        anticipated_usage=_provider_anticipated(),
        lease_owner=_OWNER,
        expected_fencing_token=final.fencing_token,
        operation_id="operation.authorize-one",
        now=_now(),
    )
    assert authorized.reservation is not None
    assert authorized.reservation.usage.provider_calls == 0
    assert authorized.reservation.authorized_pending.provider_calls == 1
    actual = ResourceAmounts(
        provider_calls=1,
        tokens=80,
        cost=0.8,
        active_wall_clock=8,
    )
    settled = governor.settle_provider_call(
        final.reservation_id,
        invocation_id="invocation.one",
        actual_usage=actual,
        lease_owner=_OWNER,
        expected_fencing_token=authorized.reservation.fencing_token,
        operation_id="operation.settle-one",
        now=_now(),
    )
    repeated = governor.settle_provider_call(
        final.reservation_id,
        invocation_id="invocation.one",
        actual_usage=actual,
        lease_owner=_OWNER,
        expected_fencing_token=authorized.reservation.fencing_token,
        operation_id="operation.settle-one",
        now=_now(),
    )

    assert settled.result_code == "settled"
    assert settled.reservation is not None
    assert settled.reservation.usage == actual
    assert not settled.reservation.authorized_pending.any_positive()
    assert repeated.reservation == settled.reservation


def test_provider_settlement_records_actual_overrun_without_losing_truth(
    tmp_path: Path,
) -> None:
    governor = _governor(tmp_path)
    final = _final_reservation(governor)
    authorized = governor.provider_call_authorized(
        final.reservation_id,
        invocation_id="invocation.overrun",
        anticipated_usage=_provider_anticipated(),
        lease_owner=_OWNER,
        expected_fencing_token=final.fencing_token,
        operation_id="operation.authorize-overrun",
        now=_now(),
    )
    assert authorized.reservation is not None
    actual = ResourceAmounts(
        provider_calls=1,
        tokens=80,
        cost=0.8,
        active_wall_clock=13,
    )

    settled = governor.settle_provider_call(
        final.reservation_id,
        invocation_id="invocation.overrun",
        actual_usage=actual,
        lease_owner=_OWNER,
        expected_fencing_token=authorized.reservation.fencing_token,
        operation_id="operation.settle-overrun",
        now=_now(),
    )

    assert settled.result_code == "settled"
    assert settled.reservation is not None
    assert settled.reservation.usage == actual
    assert settled.reservation.observed_overrun == ResourceAmounts(active_wall_clock=3)
    event = governor.get_operation_event("operation.settle-overrun")
    assert event is not None
    assert event.actual_usage == actual


def test_ledger_replay_rejects_incomplete_provider_actual_usage(tmp_path: Path) -> None:
    governor = _governor(tmp_path)
    final = _final_reservation(governor)
    authorized = governor.provider_call_authorized(
        final.reservation_id,
        invocation_id="invocation.incomplete-actual",
        anticipated_usage=_provider_anticipated(),
        lease_owner=_OWNER,
        expected_fencing_token=final.fencing_token,
        operation_id="operation.authorize-incomplete-actual",
        now=_now(),
    )
    assert authorized.reservation is not None
    permit = authorized.reservation.provider_permits[0]
    effect = resource_operation_effect_digest(
        "settle_provider_call",
        {"invocation_id": permit.invocation_id, "actual_usage": ResourceAmounts()},
    )
    invalid_target = update_reservation(
        authorized.reservation,
        operation_id="operation.incomplete-actual",
        operation_effect_digest=effect,
        authorized_pending=ResourceAmounts(),
        provider_permits=(),
    )
    with governor._store.locked():
        state = governor._store.load_state()
        event = build_resource_event(
            sequence=state.head_sequence + 1,
            event_kind="provider_call_settled",
            operation_id="operation.incomplete-actual",
            previous_event_digest=state.head_digest,
            previous_reservation_digest=authorized.reservation.reservation_digest,
            reservation=invalid_target,
            provider_permit=permit,
            actual_usage=ResourceAmounts(),
        )
        with pytest.raises(SharedStateIntegrityError, match="not complete"):
            governor._store.append_event(event)


def test_expiry_conservatively_settles_pending_provider_before_release(
    tmp_path: Path,
) -> None:
    governor = _governor(tmp_path)
    final = _final_reservation(governor)
    authorized = governor.provider_call_authorized(
        final.reservation_id,
        invocation_id="invocation.expiring",
        anticipated_usage=_provider_anticipated(),
        lease_owner=_OWNER,
        expected_fencing_token=final.fencing_token,
        operation_id="operation.authorize-expiring",
        now=_now(),
    )
    assert authorized.reservation is not None

    replacement = governor.reserve_admission(
        _envelope(session_id="session.after-expiry"),
        budget_policy=_policy(),
        lease_owner="owner.replacement",
        operation_id="operation.after-expiry",
        lease_seconds=60,
        now=_now() + timedelta(seconds=61),
    )
    expired = governor.get_reservation(final.reservation_id)

    assert replacement.result_code == "reserved"
    assert expired.state == "expired"
    assert not expired.authorized_pending.any_positive()
    assert not expired.provider_permits
    assert expired.usage == _provider_anticipated().model_copy(
        update={"parallelism": 0}
    )


def test_expired_provider_reconciliation_replaces_estimate_with_actual_once(
    tmp_path: Path,
) -> None:
    governor = _governor(tmp_path)
    final = _final_reservation(governor)
    invocation_id = "invocation.expired-actual"
    governor.provider_call_authorized(
        final.reservation_id,
        invocation_id=invocation_id,
        anticipated_usage=_provider_anticipated(),
        lease_owner=_OWNER,
        expected_fencing_token=final.fencing_token,
        operation_id="operation.authorize-expired-actual",
        now=_now(),
    )
    governor.reserve_admission(
        _envelope(session_id="session.expiry-trigger"),
        budget_policy=_policy(),
        lease_owner="owner.replacement",
        operation_id="operation.expiry-trigger",
        lease_seconds=60,
        now=_now() + timedelta(seconds=61),
    )
    actual = ResourceAmounts(
        provider_calls=1,
        tokens=80,
        cost=0.8,
        active_wall_clock=8,
    )
    reconciled = governor.reconcile_expired_provider_call(
        final.reservation_id,
        invocation_id=invocation_id,
        actual_usage=actual,
        lease_owner=_OWNER,
        expected_fencing_token=final.fencing_token,
        operation_id="operation.reconcile-expired-actual",
        now=_now() + timedelta(seconds=61),
    )
    repeated = governor.reconcile_expired_provider_call(
        final.reservation_id,
        invocation_id=invocation_id,
        actual_usage=actual,
        lease_owner=_OWNER,
        expected_fencing_token=final.fencing_token,
        operation_id="operation.reconcile-expired-actual",
        now=_now() + timedelta(seconds=61),
    )
    duplicate = governor.reconcile_expired_provider_call(
        final.reservation_id,
        invocation_id=invocation_id,
        actual_usage=actual,
        lease_owner=_OWNER,
        expected_fencing_token=final.fencing_token,
        operation_id="operation.reconcile-expired-actual-again",
        now=_now() + timedelta(seconds=61),
    )

    event = governor.get_operation_event("operation.reconcile-expired-actual")
    assert reconciled.result_code == repeated.result_code == "settled"
    assert reconciled.operation_reservation is not None
    assert reconciled.operation_reservation.usage == actual
    assert repeated.operation_reservation == reconciled.operation_reservation
    assert event is not None
    assert event.event_kind == "provider_call_reconciled"
    assert event.reconciled_event_digest
    assert duplicate.result_code == "state_corrupt"


def test_expired_provider_reconciliation_preserves_actual_overrun(
    tmp_path: Path,
) -> None:
    governor = _governor(tmp_path)
    final = _final_reservation(governor)
    invocation_id = "invocation.expired-overrun"
    governor.provider_call_authorized(
        final.reservation_id,
        invocation_id=invocation_id,
        anticipated_usage=_provider_anticipated(),
        lease_owner=_OWNER,
        expected_fencing_token=final.fencing_token,
        operation_id="operation.authorize-expired-overrun",
        now=_now(),
    )
    governor.reserve_admission(
        _envelope(session_id="session.expiry-overrun-trigger"),
        budget_policy=_policy(),
        lease_owner="owner.replacement",
        operation_id="operation.expiry-overrun-trigger",
        lease_seconds=60,
        now=_now() + timedelta(seconds=61),
    )
    actual = ResourceAmounts(
        provider_calls=1,
        tokens=80,
        cost=0.8,
        active_wall_clock=13,
    )

    reconciled = governor.reconcile_expired_provider_call(
        final.reservation_id,
        invocation_id=invocation_id,
        actual_usage=actual,
        lease_owner=_OWNER,
        expected_fencing_token=final.fencing_token,
        operation_id="operation.reconcile-expired-overrun",
        now=_now() + timedelta(seconds=61),
    )

    assert reconciled.result_code == "settled"
    assert reconciled.operation_reservation is not None
    assert reconciled.operation_reservation.usage == actual
    assert reconciled.operation_reservation.observed_overrun == ResourceAmounts(
        active_wall_clock=3
    )


def test_resource_event_reader_preserves_legacy_digest_without_reconciliation_field(
    tmp_path: Path,
) -> None:
    governor = _governor(tmp_path)
    _final_reservation(governor)
    path = governor._store.events_dir / "00000000000000000001.json"
    legacy_payload = json.loads(path.read_text(encoding="utf-8"))
    legacy_payload.pop("reconciled_event_digest")
    legacy_payload["event_digest"] = resource_event_digest(legacy_payload)

    event = ResourceLedgerEvent.model_validate(legacy_payload)

    assert event.reconciled_event_digest == ""
    assert event.event_digest == legacy_payload["event_digest"]


def test_resource_event_reader_accepts_legacy_reservation_without_overrun_field(
    tmp_path: Path,
) -> None:
    governor = _governor(tmp_path)
    _final_reservation(governor)
    path = governor._store.events_dir / "00000000000000000001.json"
    legacy_payload = json.loads(path.read_text(encoding="utf-8"))
    reservation = legacy_payload["reservation"]
    reservation.pop("observed_overrun")
    reservation["reservation_digest"] = reservation_digest(reservation)
    legacy_payload["target_reservation_digest"] = reservation["reservation_digest"]
    legacy_payload["event_digest"] = resource_event_digest(legacy_payload)

    event = ResourceLedgerEvent.model_validate(legacy_payload)

    assert event.reservation.observed_overrun == ResourceAmounts()
    assert event.event_digest == legacy_payload["event_digest"]


def test_all_resource_mutations_reject_a_fencing_token_thief(tmp_path: Path) -> None:
    governor = _governor(tmp_path, capacity=_capacity(multiplier=2))
    final = _final_reservation(governor)
    stolen_usage = governor.record_usage(
        final.reservation_id,
        delta=ResourceAmounts(tokens=1),
        lease_owner="owner.attacker",
        expected_fencing_token=final.fencing_token,
        operation_id="operation.stolen-usage",
        now=_now(),
    )
    stolen_call = governor.provider_call_authorized(
        final.reservation_id,
        invocation_id="invocation.stolen",
        anticipated_usage=_provider_anticipated(),
        lease_owner="owner.attacker",
        expected_fencing_token=final.fencing_token,
        operation_id="operation.stolen-provider",
        now=_now(),
    )
    grant = build_budget_grant(
        project_id=final.project_id,
        work_item_id=final.work_item_id,
        stage_review_session_id=final.stage_review_session_id,
        final_reservation_id=final.reservation_id,
        expected_budget_revision=final.budget_revision,
        increment=ResourceAmounts(tokens=1),
        requested_event_digest=_REQUESTED_EVENT_DIGEST,
    )
    stolen_grant = _apply_budget_grant(
        governor._store,
        grant,
        lease_owner="owner.attacker",
        expected_reservation_revision=final.revision,
        expected_reservation_digest=final.reservation_digest,
        expected_fencing_token=final.fencing_token,
        now=_now(),
    )

    assert stolen_usage.result_code == "invalid_reservation"
    assert stolen_call.result_code == "invalid_reservation"
    assert stolen_grant.result_code == "invalid_reservation"


def test_ledger_replay_rejects_self_consistent_immutable_lineage_forgery(
    tmp_path: Path,
) -> None:
    governor = _governor(tmp_path)
    final = _final_reservation(governor)
    path = governor._store.events_dir / "00000000000000000002.json"
    event_payload = json.loads(path.read_text(encoding="utf-8"))
    original_event = ResourceLedgerEvent.model_validate(event_payload)
    draft_reservation = final.model_copy(
        update={"project_id": "project.forged", "reservation_digest": ""}
    )
    forged_reservation = ResourceReservation.model_validate(
        draft_reservation.model_copy(
            update={"reservation_digest": reservation_digest(draft_reservation)}
        ).model_dump(mode="json")
    )
    draft_event = original_event.model_copy(
        update={
            "reservation": forged_reservation,
            "target_reservation_digest": forged_reservation.reservation_digest,
            "event_digest": "",
        }
    )
    forged_event = ResourceLedgerEvent.model_validate(
        draft_event.model_copy(
            update={"event_digest": resource_event_digest(draft_event)}
        ).model_dump(mode="json")
    )
    path.write_text(
        json.dumps(forged_event.model_dump(mode="json")),
        encoding="utf-8",
    )

    assert final.project_id == "project.shared"
    with pytest.raises(SharedStateIntegrityError, match="immutable lineage"):
        governor.snapshot()


def test_ledger_replay_requires_budget_grant_operation_artifacts(
    tmp_path: Path,
) -> None:
    governor = _governor(tmp_path, capacity=_capacity(multiplier=2))
    final = _final_reservation(governor)
    grant = build_budget_grant(
        project_id=final.project_id,
        work_item_id=final.work_item_id,
        stage_review_session_id=final.stage_review_session_id,
        final_reservation_id=final.reservation_id,
        expected_budget_revision=final.budget_revision,
        increment=ResourceAmounts(tokens=500),
        requested_event_digest=_REQUESTED_EVENT_DIGEST,
    )
    expanded = _apply_budget_grant(
        governor._store,
        grant,
        lease_owner=_OWNER,
        expected_reservation_revision=final.revision,
        expected_reservation_digest=final.reservation_digest,
        expected_fencing_token=final.fencing_token,
        now=_now(),
    )
    assert expanded.result_code == "expanded"
    next(governor._store.grant_operations_dir.glob("*.json")).unlink()

    with pytest.raises(SharedStateIntegrityError, match="BudgetGrantOperation"):
        governor.snapshot()


def test_ledger_replay_checks_pool_capacity_after_every_event(tmp_path: Path) -> None:
    governor = _governor(tmp_path)
    admission = governor.reserve_admission(
        _envelope(),
        budget_policy=_policy(),
        lease_owner=_OWNER,
        operation_id="operation.capacity-ledger",
        lease_seconds=60,
        now=_now(),
    )
    assert admission.reservation is not None
    path = governor._store.events_dir / "00000000000000000001.json"
    original = ResourceLedgerEvent.model_validate_json(path.read_text(encoding="utf-8"))
    oversized = _capacity(multiplier=2)
    draft_reservation = original.reservation.model_copy(
        update={
            "reserved": oversized,
            "policy_hard_limits": oversized,
            "hard_limits": oversized,
            "soft_limits": soft_limits(oversized),
            "reservation_digest": "",
        }
    )
    forged_reservation = draft_reservation.model_copy(
        update={"reservation_digest": reservation_digest(draft_reservation)}
    )
    draft_event = original.model_copy(
        update={
            "reservation": forged_reservation,
            "target_reservation_digest": forged_reservation.reservation_digest,
            "event_digest": "",
        }
    )
    forged_event = draft_event.model_copy(
        update={"event_digest": resource_event_digest(draft_event)}
    )
    path.write_text(forged_event.model_dump_json(), encoding="utf-8")
    governor._store.projection_path.unlink()

    with pytest.raises(SharedStateIntegrityError, match="configured capacity"):
        governor.snapshot()


def test_ledger_replay_rejects_provider_settlement_without_exact_actual_delta(
    tmp_path: Path,
) -> None:
    governor = _governor(tmp_path)
    final = _final_reservation(governor)
    authorized = governor.provider_call_authorized(
        final.reservation_id,
        invocation_id="invocation.ledger",
        anticipated_usage=_provider_anticipated(),
        lease_owner=_OWNER,
        expected_fencing_token=final.fencing_token,
        operation_id="operation.authorize-ledger",
        now=_now(),
    )
    assert authorized.reservation is not None
    settled = governor.settle_provider_call(
        final.reservation_id,
        invocation_id="invocation.ledger",
        actual_usage=ResourceAmounts(
            provider_calls=1,
            tokens=80,
            cost=0.8,
            active_wall_clock=8,
        ),
        lease_owner=_OWNER,
        expected_fencing_token=authorized.reservation.fencing_token,
        operation_id="operation.settle-ledger",
        now=_now(),
    )
    assert settled.result_code == "settled"
    path = governor._store.events_dir / "00000000000000000004.json"
    original = ResourceLedgerEvent.model_validate_json(path.read_text(encoding="utf-8"))
    draft_reservation = original.reservation.model_copy(
        update={"usage": ResourceAmounts(), "reservation_digest": ""}
    )
    forged_reservation = draft_reservation.model_copy(
        update={"reservation_digest": reservation_digest(draft_reservation)}
    )
    draft_event = original.model_copy(
        update={
            "reservation": forged_reservation,
            "target_reservation_digest": forged_reservation.reservation_digest,
            "event_digest": "",
        }
    )
    forged_event = draft_event.model_copy(
        update={"event_digest": resource_event_digest(draft_event)}
    )
    path.write_text(forged_event.model_dump_json(), encoding="utf-8")

    with pytest.raises(SharedStateIntegrityError, match="actual usage"):
        governor.snapshot()


def test_panel_semantic_digest_excludes_runtime_and_reservation_binding(
    tmp_path: Path,
) -> None:
    governor = _governor(tmp_path)
    proposal = _proposal()
    final = _final_reservation(governor, proposal=proposal)
    runtime_variant = proposal.model_copy(update={"created_at": "2030-01-01T00:00:00Z"})

    first = _build_reviewer_panel_plan(proposal, final)
    second = _build_reviewer_panel_plan(runtime_variant, final)

    assert first.plan_digest == second.plan_digest
    assert first.finalization_digest == second.finalization_digest


def test_two_process_provider_authorization_cannot_exceed_final_budget(
    tmp_path: Path,
) -> None:
    governor = _governor(tmp_path)
    final = _final_reservation(governor)
    context = multiprocessing.get_context("spawn")
    queue = context.Queue()
    processes = [
        context.Process(
            target=_authorize_worker,
            args=(
                str(tmp_path),
                final.reservation_id,
                final.fencing_token,
                index,
                queue,
            ),
        )
        for index in range(2)
    ]

    for process in processes:
        process.start()
    results = [queue.get(timeout=20) for _ in processes]
    for process in processes:
        process.join(timeout=20)
        assert process.exitcode == 0

    assert sorted(results) == ["authorized", "hard_limit_exceeded"]
    current = governor.get_reservation(final.reservation_id)
    assert current.usage.provider_calls == 0
    assert current.authorized_pending.provider_calls == 1


def test_foreground_and_offline_optimization_use_separate_capacity_pools(
    tmp_path: Path,
) -> None:
    governor = _governor(tmp_path, offline_capacity=_capacity())
    foreground = governor.reserve_admission(
        _envelope(session_id="session.foreground"),
        budget_policy=_policy(),
        lease_owner=_OWNER,
        operation_id="operation.foreground",
        lease_seconds=60,
        now=_now(),
    )
    offline = governor.reserve_admission(
        _envelope(session_id="session.offline", pool="offline_optimization"),
        budget_policy=_policy(),
        lease_owner=_OWNER,
        operation_id="operation.offline",
        lease_seconds=60,
        now=_now(),
    )

    assert foreground.result_code == "reserved"
    assert offline.result_code == "reserved"


def test_reconciliation_releases_unused_capacity_once(tmp_path: Path) -> None:
    governor = _governor(tmp_path)
    final = _final_reservation(governor)

    reconciled = governor.reconcile(
        final.reservation_id,
        lease_owner=_OWNER,
        expected_fencing_token=final.fencing_token,
        operation_id="operation.reconcile",
        now=_now(),
    )
    repeated = governor.reconcile(
        final.reservation_id,
        lease_owner=_OWNER,
        expected_fencing_token=final.fencing_token,
        operation_id="operation.reconcile",
        now=_now(),
    )

    assert reconciled.result_code == "reconciled"
    assert repeated.reservation == reconciled.reservation
    assert governor.snapshot().reserved == ResourceAmounts()


@pytest.mark.parametrize(
    "error",
    [PermissionError("windows lock denied"), OSError("posix lock unavailable")],
)
def test_platform_lock_failures_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    error: OSError,
) -> None:
    governor = _governor(tmp_path)

    def fail_lock(*args: object, **kwargs: object) -> int:
        raise error

    monkeypatch.setattr(
        "ai_sdlc.core.stage_review.artifacts._open_exclusive", fail_lock
    )
    result = governor.reserve_admission(
        _envelope(),
        budget_policy=_policy(),
        lease_owner=_OWNER,
        operation_id="operation.lock-failure",
        lease_seconds=60,
        now=_now(),
    )

    assert result.result_code == "lock_unavailable"


def test_git_worktrees_share_project_state_root(tmp_path: Path) -> None:
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

    primary = resolve_canonical_shared_state(repository, "project.shared")
    secondary = resolve_canonical_shared_state(worktree, "project.shared")

    assert primary == secondary


def test_git_resolution_failure_does_not_fall_back_to_worktree_local_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    (repository / ".git").mkdir()

    def timeout(*args: object, **kwargs: object) -> object:
        raise subprocess.TimeoutExpired("git", 5)

    monkeypatch.setattr("subprocess.run", timeout)
    with pytest.raises(SharedStateIntegrityError, match="Git"):
        resolve_canonical_shared_state(repository, "project.shared")


def test_nonexistent_non_git_root_uses_local_shared_state_when_windows_rejects_cwd(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "not-created"

    def invalid_windows_cwd(*args: object, **kwargs: object) -> object:
        raise NotADirectoryError(267, "The directory name is invalid")

    monkeypatch.setattr("subprocess.run", invalid_windows_cwd)

    shared = resolve_canonical_shared_state(root, "project.shared")

    assert shared == (
        root.resolve()
        / ".ai-sdlc"
        / "state"
        / "shared"
        / "projects"
        / "project.shared"
    )


def test_atomic_artifact_temp_names_fit_windows_max_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target_name = "optimization-snapshot.json"
    component_size = 250 - len(str(tmp_path)) - len(target_name) - 2
    directory = tmp_path / ("x" * component_size)
    directory.mkdir()
    atomic_target = directory / target_name
    exclusive_target = directory / "candidate.json"
    assert len(str(atomic_target)) == 250
    original_open = Path.open

    def windows_guarded_open(
        path: Path,
        *args: object,
        **kwargs: object,
    ) -> object:
        if len(str(path)) >= 260:
            raise OSError(206, "The filename or extension is too long")
        return original_open(path, *args, **kwargs)

    original_exclusive = artifacts._open_exclusive

    def windows_guarded_exclusive(path: Path) -> int:
        if len(str(path)) >= 260:
            raise OSError(206, "The filename or extension is too long")
        return original_exclusive(path)

    monkeypatch.setattr(Path, "open", windows_guarded_open)
    monkeypatch.setattr(artifacts, "_open_exclusive", windows_guarded_exclusive)

    atomic_write_json(atomic_target, {"kind": "atomic"})
    created = create_json_exclusive(exclusive_target, {"kind": "exclusive"})

    assert created is True
    assert read_json_object(atomic_target) == {"kind": "atomic"}
    assert read_json_object(exclusive_target) == {"kind": "exclusive"}


def _reserve_worker(root: str, session_id: str, queue: object) -> None:
    governor = _governor(Path(root))
    result = governor.reserve_admission(
        _envelope(session_id=session_id),
        budget_policy=_policy(),
        lease_owner=f"owner.{session_id}",
        operation_id=f"operation.{session_id}",
        lease_seconds=60,
        now=_now(),
    )
    queue.put(result.result_code)  # type: ignore[attr-defined]


def _authorize_worker(
    root: str,
    reservation_id: str,
    fencing_token: int,
    index: int,
    queue: object,
) -> None:
    result = _governor(Path(root)).provider_call_authorized(
        reservation_id,
        invocation_id=f"invocation.concurrent.{index}",
        anticipated_usage=_provider_anticipated(tokens=1500),
        lease_owner=_OWNER,
        expected_fencing_token=fencing_token,
        operation_id=f"operation.authorize.{index}",
        now=_now(),
    )
    queue.put(result.result_code)  # type: ignore[attr-defined]


def _governor(
    root: Path,
    *,
    capacity: ResourceAmounts | None = None,
    offline_capacity: ResourceAmounts | None = None,
    budget_grant_authority: _RequestAuthority | None = None,
) -> ResourceGovernor:
    governor = ResourceGovernor(
        root,
        project_id="project.shared",
        foreground_capacity=capacity or _capacity(),
        offline_optimization_capacity=offline_capacity,
        lock_timeout_seconds=5,
    )
    if budget_grant_authority is not None:
        governor._budget_grant_authority = budget_grant_authority
    return governor


def _envelope(
    *,
    session_id: str = "session.one",
    pool: ResourcePool = "foreground",
) -> BudgetEnvelope:
    return build_budget_envelope(
        project_id="project.shared",
        work_item_id="work-item.one",
        stage_review_session_id=session_id,
        risk_level="low",
        budget_policy=_policy(),
        pool=pool,
    )


def _policy() -> ReviewerBudgetPolicy:
    return build_budget_policy(
        policy_id="budget.low",
        version="1.0.0",
        maximum_slots=2,
        hard_provider_calls=8,
        hard_review_passes=4,
        hard_tokens=10000,
        hard_cost=10,
        hard_wall_clock=100,
        hard_parallelism=2,
        hard_role_replans=1,
        hard_provider_retries=2,
        hard_binding_attempts=3,
        owner="ai-sdlc",
        review_date="2026-07-20",
    )


def _capacity(*, multiplier: int = 1) -> ResourceAmounts:
    hard = _envelope().hard_limits
    return hard.scaled(multiplier)


def _provider_anticipated(*, tokens: int = 100) -> ResourceAmounts:
    return ResourceAmounts(
        provider_calls=1,
        tokens=tokens,
        cost=1,
        active_wall_clock=10,
        parallelism=1,
    )


def _requirement() -> PanelResourceRequirement:
    return PanelResourceRequirement(
        required_slot_count=2,
        total_slot_count=2,
        required_provider_calls=2,
        total_provider_calls=2,
        required_review_passes=2,
        total_review_passes=2,
        required_tokens=2000,
        total_tokens=2000,
        required_cost=2,
        total_cost=2,
        required_wall_clock=20,
        total_wall_clock=20,
        parallelism=2,
    )


def _proposal(
    *,
    requirement: PanelResourceRequirement | None = None,
    envelope_digest: str | None = None,
    request_digest: str = "sha256:request",
) -> ReviewerPanelProposal:
    slots = (
        _reviewer_slot("slot.required.delivery", "role.delivery", "cap.a"),
        _reviewer_slot("slot.required.evolution", "role.evolution", "cap.b"),
    )
    draft = ReviewerPanelProposal.model_construct(
        request_digest=request_digest,
        planning_context_digest="sha256:context",
        solver_version="solver.v1",
        registry_digest="sha256:registry",
        role_catalog_digest="sha256:roles",
        selection_policy_digest="sha256:selection",
        quorum_policy_digest="sha256:quorum",
        budget_policy_digest=_policy().policy_digest,
        budget_envelope_digest=envelope_digest or _envelope().envelope_digest,
        optimization_snapshot_digest="sha256:optimization",
        required_slots=slots,
        optional_slots=(),
        advisory_slots=(),
        shadow_slots=(),
        coverage_proof=(
            CapabilityCoverageProof(
                capability_id="cap.a",
                required_slot_ids=(slots[0].slot_id,),
                minimum_required_slots=1,
                blocking_slot_ids=(slots[0].slot_id,),
            ),
            CapabilityCoverageProof(
                capability_id="cap.b",
                required_slot_ids=(slots[1].slot_id,),
                minimum_required_slots=1,
                blocking_slot_ids=(slots[1].slot_id,),
            ),
        ),
        difference_matrix=(
            ReviewerDifference(
                left_slot_id=slots[0].slot_id,
                right_slot_id=slots[1].slot_id,
                difference_dimensions=("capability", "prompt"),
            ),
        ),
        quorum=FrozenQuorumPolicy(
            required_slot_ids=tuple(item.slot_id for item in slots),
            required_capability_expressions=("cap.a>=1", "cap.b>=1"),
            minimum_pass_count=2,
            veto_authorities=("cap.a", "cap.b"),
            allowed_abstentions=(),
            source_policy_digest="sha256:quorum",
        ),
        resource_requirement=requirement or _requirement(),
        rejected_role_reasons=(),
        planning_explanations=("panel.minimum-required-set",),
        proposal_digest="",
    )
    payload = draft.model_dump(mode="json", warnings=False)
    payload["proposal_digest"] = panel_proposal_digest(draft)
    return ReviewerPanelProposal.model_validate(payload)


def _reviewer_slot(slot_id: str, role_id: str, capability: str) -> ReviewerSlot:
    return ReviewerSlot(
        slot_id=slot_id,
        slot_kind="required",
        role_profile_id=role_id,
        role_contract_digest=f"sha256:{role_id}",
        capability_ids=(capability,),
        blocking_authority=(capability,),
        primary_dimensions=(capability,),
        prompt_template_digest=f"sha256:prompt.{role_id}",
        provider_constraints=(f"provider.{role_id}",),
        tool_permission_ids=(f"tool.{role_id}",),
        evidence_source_ids=(f"evidence.{role_id}",),
        independence_key=f"sha256:independence.{role_id}",
        counts_for_quorum=True,
        allows_abstain=False,
        selection_reason_ids=("panel.selected.required",),
        estimated_provider_calls=1,
        estimated_review_passes=1,
        estimated_tokens=1000,
        estimated_cost=1,
        estimated_wall_clock=10,
    )


def _final_reservation(
    governor: ResourceGovernor,
    *,
    proposal: ReviewerPanelProposal | None = None,
) -> ResourceReservation:
    admission = governor.reserve_admission(
        _envelope(),
        budget_policy=_policy(),
        lease_owner=_OWNER,
        operation_id="operation.admission",
        lease_seconds=60,
        now=_now(),
    )
    assert admission.reservation is not None
    final = governor.finalize_reservation(
        admission.reservation.reservation_id,
        proposal=proposal or _proposal(),
        lease_owner=_OWNER,
        expected_fencing_token=admission.reservation.fencing_token,
        operation_id="operation.finalize",
        now=_now(),
    )
    assert final.reservation is not None
    return final.reservation


def _session_for_reservation(
    reservation: ResourceReservation,
) -> StageReviewSession:
    projection = SessionProjectionData(
        scope=FindingScope(
            project_id=reservation.project_id,
            work_item_id=reservation.work_item_id,
            stage_instance_id="implementation",
            session_id=reservation.stage_review_session_id,
        ),
        state="needs_user",
        policy_digest="sha256:policy",
        optimization_snapshot_digest="sha256:snapshot",
        risk_profile_lineage_id="risk-lineage.one",
        active_candidate_digest="sha256:candidate",
        active_risk_profile_digest="sha256:risk",
        active_plan_digest="sha256:plan",
        active_binding_set_digest="sha256:binding",
        active_cohort_id="cohort.one",
        active_cohort_initial_head_digest="sha256:head",
        resource_reservation_id=reservation.reservation_id,
        resource_reservation_digest=reservation.reservation_digest,
        resource_fencing_epoch=reservation.fencing_token,
        resource_usage=reservation.usage,
        budget_revision=reservation.budget_revision,
        budget_resume_state="collecting_initial_reviews",
        finding_ledger_digest="sha256:ledger",
        cohort_refs=(),
    )
    return StageReviewSession(
        revision=1,
        head_event_id="session-event.one",
        head_event_digest="sha256:session-event.one",
        projection=projection,
    )


def _session_budget_request(
    session: StageReviewSession,
    reservation: ResourceReservation,
    increment: ResourceAmounts,
) -> tuple[BudgetGrantRequestProof, BudgetGrant]:
    approval = BudgetGrantApproval(
        approval_id="budget-grant-approval.resource-test",
        scope=session.scope,
        final_reservation_id=reservation.reservation_id,
        final_reservation_digest=reservation.reservation_digest,
        final_reservation_revision=reservation.revision,
        final_fencing_token=reservation.fencing_token,
        expected_budget_revision=reservation.budget_revision,
        increment=increment,
        authority_id="user.test",
        approved_at="2026-07-20T12:00:00Z",
    )
    command = BudgetGrantRequestCommand(
        scope=session.scope,
        command_id="budget-grant-request.resource-test",
        idempotency_key="budget-grant-request-key.resource-test",
        expected_revision=session.revision,
        expected_budget_revision=reservation.budget_revision,
        increment=increment,
        approval_digest=approval.approval_digest,
    )
    command_digest = canonical_digest(command, CanonicalizationPolicy())
    operation = SessionOperation(
        scope=session.scope,
        command_type="BudgetGrantRequestCommand",
        command_payload=command.model_dump(mode="json"),
        command_id=command.command_id,
        idempotency_key=command.idempotency_key,
        command_digest=command_digest,
        expected_revision=session.revision,
        expected_event_kinds=("budget_grant_requested",),
        prepared_at="2026-07-20T12:00:00Z",
    )
    event = SessionEvent(
        scope=session.scope,
        sequence=session.revision + 1,
        event_id="session-event.budget-grant-resource-test",
        event_kind="budget_grant_requested",
        command_id=command.command_id,
        command_digest=command_digest,
        previous_event_id=session.head_event_id,
        previous_event_digest=session.head_event_digest,
        occurred_at="2026-07-20T12:00:00Z",
        projection_after=session.projection.model_copy(
            update={"pending_budget_grant_command_id": command.command_id}
        ),
        artifact_refs=(
            ArtifactRef(
                artifact_id=approval.approval_id,
                artifact_digest=approval.approval_digest,
            ),
        ),
    )
    proof = BudgetGrantRequestProof(
        approval=approval,
        request_operation=operation,
        requested_event=event,
    )
    grant = build_budget_grant(
        project_id=reservation.project_id,
        work_item_id=reservation.work_item_id,
        stage_review_session_id=reservation.stage_review_session_id,
        final_reservation_id=reservation.reservation_id,
        expected_budget_revision=reservation.budget_revision,
        increment=increment,
        requested_event_digest=event.event_digest,
    )
    return proof, grant


def _session_apply_command_id(grant: BudgetGrant) -> str:
    return stable_id("budget-grant-session-apply", grant.grant_id)


def _now() -> datetime:
    return datetime(2026, 7, 20, 12, tzinfo=UTC)


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
