from __future__ import annotations

import json
from pathlib import Path

import pytest

from ai_sdlc.core.stage_review.artifacts import SharedStateIntegrityError
from ai_sdlc.core.stage_review.optimization.observations import (
    CommittedSessionBindingStore,
    OptimizationObservationStore,
)
from ai_sdlc.core.stage_review.optimization.promotion import (
    AutoPromotionDecision,
    AutoPromotionEvidence,
    AutoPromotionGate,
    AutoPromotionPolicy,
)
from ai_sdlc.core.stage_review.optimization.snapshot_models import (
    OptimizationSnapshot,
    SessionSnapshotBindingOperation,
    SnapshotSelectionToken,
)
from ai_sdlc.core.stage_review.optimization.snapshots import SnapshotControlService
from ai_sdlc.core.stage_review.resource_models import ResourceAmounts
from ai_sdlc.core.stage_review.resources import ResourceGovernor


def test_auto_promotion_requires_quality_non_regression_and_significance() -> None:
    gate = AutoPromotionGate(_promotion_policy())
    accepted = gate.evaluate(_promotion_evidence(), decision_id="decision.accepted")
    quality_regression = gate.evaluate(
        _promotion_evidence().model_copy(update={"late_critical_delta": 0.01}),
        decision_id="decision.regression",
    )
    budget_regression = gate.evaluate(
        _promotion_evidence().model_copy(
            update={"hard_budget_exhausted_delta": 0.01}
        ),
        decision_id="decision.budget-regression",
    )
    insignificant = gate.evaluate(
        _promotion_evidence().model_copy(update={"quality_confidence_lower": 0}),
        decision_id="decision.insignificant",
    )

    assert accepted.approved
    assert not quality_regression.approved
    assert "late_critical_non_regression" in quality_regression.failed_guards
    assert "hard_budget_exhausted_non_regression" in (
        budget_regression.failed_guards
    )
    assert not insignificant.approved
    assert accepted == gate.evaluate(
        _promotion_evidence(), decision_id="decision.accepted"
    )


def test_packaged_baseline_change_preserves_existing_project_baseline(
    tmp_path: Path,
) -> None:
    original = _snapshot("baseline-original", is_baseline=True)
    first = _service(tmp_path, original)
    replacement = _snapshot("baseline-replacement", is_baseline=True)

    reopened = _service(tmp_path, replacement)

    assert first.resolve_snapshot().active_snapshot_digest == original.snapshot_digest
    assert reopened.resolve_snapshot().active_snapshot_digest == original.snapshot_digest
    assert reopened.store.snapshot(replacement.snapshot_digest) is None


def test_snapshot_digest_uses_a_windows_safe_physical_filename(
    tmp_path: Path,
) -> None:
    baseline = _snapshot("portable-filename", is_baseline=True)
    service = _service(tmp_path, baseline)

    paths = tuple((service.store.root / "snapshots").glob("*.json"))

    assert [path.name for path in paths] == [
        f"{baseline.snapshot_digest.removeprefix('sha256:')}.json"
    ]
    assert service.store.snapshot(baseline.snapshot_digest) == baseline


def test_promotion_only_affects_sessions_bound_after_control_event(
    tmp_path: Path,
) -> None:
    baseline = _snapshot("baseline", is_baseline=True)
    service = _service(tmp_path, baseline)
    before = service.resolve_snapshot()
    first_binding = service.bind_session(_binding("session.before", before), before)
    assert first_binding.commit_fencing_epoch > 0
    assert first_binding.commit_claim_digest
    challenger = _snapshot(
        "challenger",
        parent_snapshot_digest=baseline.snapshot_digest,
        stable_fallback_digest=baseline.snapshot_digest,
    )
    service.register_snapshot(challenger)
    decision = AutoPromotionGate(_promotion_policy()).evaluate(
        _promotion_evidence(
            baseline_digest=baseline.snapshot_digest,
            challenger_digest=challenger.snapshot_digest,
            candidate_digest=challenger.candidate_digest,
        ),
        decision_id="decision.promote",
    )
    service.promote(
        challenger.snapshot_digest,
        decision=decision,
        operation_id="operation.promote",
    )

    with pytest.raises(SharedStateIntegrityError, match="head"):
        service.bind_session(_binding("session.stale", before), before)
    after = service.resolve_snapshot()
    second_binding = service.bind_session(_binding("session.after", after), after)

    assert first_binding.target_snapshot_digest == baseline.snapshot_digest
    assert second_binding.target_snapshot_digest == challenger.snapshot_digest
    assert after.active_snapshot_digest == challenger.snapshot_digest
    assert service.events()[-1].event_kind == "session_binding"


def test_snapshot_control_recovers_from_segment_and_fences_followup_writer(
    tmp_path: Path,
) -> None:
    baseline = _snapshot("baseline", is_baseline=True)
    service = _service(tmp_path, baseline)
    token = service.resolve_snapshot()
    first = service.bind_session(_binding("session.first", token), token)
    prepared = service.store.storage._prepare_compaction("snapshot-control")
    assert prepared is not None

    with service.resources.storage_bundle(
        bundle_class="reclamation",
        bundle_bytes=prepared.required_bundle_bytes,
        net_reclaim_bytes=prepared.net_reclaim_bytes,
        policy=service.storage_policy,
        operation_id="compactor.snapshot-control.bundle",
    ) as bundle, service.store.commit_leases.acquire(
        owner_id="compactor.snapshot-control",
        scope="compaction",
        expected_head=first.event_digest,
    ) as lease:
        service.store.storage._commit_compaction(
            prepared,
            lease=lease,
            resource_bundle=bundle,
        )

    assert service.events() == (first,)
    next_token = service.resolve_snapshot()
    second = service.bind_session(_binding("session.second", next_token), next_token)
    assert second.sequence == 2
    assert second.previous_event_digest == first.event_digest
    assert second.commit_fencing_epoch > first.commit_fencing_epoch


def test_session_binding_rebases_stale_head_when_active_snapshot_is_unchanged(
    tmp_path: Path,
) -> None:
    baseline = _snapshot("baseline", is_baseline=True)
    service = _service(tmp_path, baseline)
    shared_token = service.resolve_snapshot()

    first = service.bind_session(_binding("session.first", shared_token), shared_token)
    second = service.bind_session(_binding("session.second", shared_token), shared_token)

    assert (first.sequence, second.sequence) == (1, 2)
    assert second.previous_event_digest == first.event_digest
    assert second.target_snapshot_digest == baseline.snapshot_digest


def test_same_session_binding_retry_does_not_create_second_control_event(
    tmp_path: Path,
) -> None:
    baseline = _snapshot("baseline", is_baseline=True)
    service = _service(tmp_path, baseline)
    token = service.resolve_snapshot()
    operation = _binding("session.same", token)

    first = service.bind_session(operation, token)
    second = service.bind_session(operation, token)

    assert second == first
    assert service.events() == (first,)


def test_visible_revocation_is_recovered_before_new_session_and_rolls_back(
    tmp_path: Path,
) -> None:
    baseline = _snapshot("baseline", is_baseline=True)
    challenger = _snapshot(
        "challenger",
        parent_snapshot_digest=baseline.snapshot_digest,
        stable_fallback_digest=baseline.snapshot_digest,
    )
    service = _service(tmp_path, baseline)
    service.register_snapshot(challenger)
    decision = AutoPromotionGate(_promotion_policy()).evaluate(
        _promotion_evidence(
            baseline_digest=baseline.snapshot_digest,
            challenger_digest=challenger.snapshot_digest,
            candidate_digest=challenger.candidate_digest,
        ),
        decision_id="decision.promote",
    )
    service.promote(
        challenger.snapshot_digest,
        decision=decision,
        operation_id="operation.promote",
    )
    service.request_revocation(
        challenger.snapshot_digest,
        reason="false_certificate",
        operation_id="operation.revoke",
    )
    pointer_path = service.store.root / "active-pointer.json"
    pointer_path.write_text(
        json.dumps({"active_snapshot_digest": challenger.snapshot_digest}),
        encoding="utf-8",
    )

    resolved = service.resolve_snapshot()
    binding = service.bind_session(_binding("session.safe", resolved), resolved)

    assert resolved.active_snapshot_digest == baseline.snapshot_digest
    assert challenger.snapshot_digest in resolved.revoked_snapshot_digests
    assert binding.target_snapshot_digest == baseline.snapshot_digest
    assert [event.event_kind for event in service.events()][-3:-1] == [
        "revocation",
        "rollback",
    ]


def test_stability_event_changes_fallback_but_revocation_remains_monotonic(
    tmp_path: Path,
) -> None:
    baseline = _snapshot("baseline", is_baseline=True)
    first = _snapshot(
        "first",
        parent_snapshot_digest=baseline.snapshot_digest,
        stable_fallback_digest=baseline.snapshot_digest,
    )
    second = _snapshot(
        "second",
        parent_snapshot_digest=first.snapshot_digest,
        stable_fallback_digest=first.snapshot_digest,
    )
    service = _service(tmp_path, baseline)
    service.register_snapshot(first)
    service.register_snapshot(second)
    service.promote(
        first.snapshot_digest,
        decision=_decision(
            baseline.snapshot_digest,
            first.snapshot_digest,
            first.candidate_digest,
            "one",
        ),
        operation_id="operation.promote-one",
    )
    service.mark_stable(first.snapshot_digest, operation_id="operation.stable-one")
    service.promote(
        second.snapshot_digest,
        decision=_decision(
            first.snapshot_digest,
            second.snapshot_digest,
            second.candidate_digest,
            "two",
        ),
        operation_id="operation.promote-two",
    )
    service.revoke_and_rollback(
        second.snapshot_digest,
        reason="critical_detection_regression",
        operation_id="operation.revoke-two",
    )

    resolved = service.resolve_snapshot()
    assert resolved.active_snapshot_digest == first.snapshot_digest
    assert resolved.stable_fallback_digest == first.snapshot_digest
    assert second.snapshot_digest in resolved.revoked_snapshot_digests


def test_only_committed_binding_event_enters_population_and_missing_files_recover(
    tmp_path: Path,
) -> None:
    baseline = _snapshot("baseline", is_baseline=True)
    service = _service(tmp_path, baseline)
    token = service.resolve_snapshot()
    orphan = _binding("session.orphan", token)
    service.store.persist_binding_operation(orphan)
    binding_store = CommittedSessionBindingStore(tmp_path, project_id="project.shared")
    observation_store = OptimizationObservationStore(
        tmp_path, project_id="project.shared"
    )

    assert (
        service.recover_session_population(
            binding_store=binding_store,
            observation_store=observation_store,
        )
        == ()
    )

    committed = _binding("session.committed", token)
    service.bind_session(committed, token)
    recovered = service.recover_session_population(
        binding_store=binding_store,
        observation_store=observation_store,
    )

    assert tuple(item.session_id for item in recovered) == ("session.committed",)
    assert (
        observation_store.read_session("session.committed")[0].observation_kind
        == "created"
    )
    assert not observation_store.read_session("session.orphan")


def _promotion_policy() -> AutoPromotionPolicy:
    return AutoPromotionPolicy(
        policy_version="1.0.0",
        minimum_holdout_sessions=10,
        minimum_shadow_sessions=10,
        minimum_shadow_days=14,
    )


def _promotion_evidence(
    *,
    baseline_digest: str = "sha256:baseline",
    challenger_digest: str = "sha256:challenger",
    candidate_digest: str = "sha256:candidate",
) -> AutoPromotionEvidence:
    return AutoPromotionEvidence(
        baseline_snapshot_digest=baseline_digest,
        challenger_snapshot_digest=challenger_digest,
        candidate_digest=candidate_digest,
        evaluation_report_digests=("sha256:evaluation",),
        invariant_results={"protocol": True, "isolation": True, "recovery": True},
        critical_detection_delta=0,
        late_critical_delta=0,
        reviewer_coverage_leak_delta=0,
        false_positive_delta=-0.01,
        reversal_delta=0,
        stage_reopen_delta=0,
        needs_user_delta=0,
        blocked_delta=0,
        timeout_delta=0,
        abandon_delta=0,
        hard_budget_exhausted_delta=0,
        unknown_or_censored_delta=0,
        quality_confidence_lower=0.01,
        holdout_session_count=10,
        shadow_session_count=10,
        shadow_observation_days=14,
        resources_within_constitution=True,
        duties_independent=True,
    )


def _decision(
    baseline_digest: str,
    challenger_digest: str,
    candidate_digest: str,
    suffix: str,
) -> AutoPromotionDecision:
    return AutoPromotionGate(_promotion_policy()).evaluate(
        _promotion_evidence(
            baseline_digest=baseline_digest,
            challenger_digest=challenger_digest,
            candidate_digest=candidate_digest,
        ),
        decision_id=f"decision.{suffix}",
    )


def _snapshot(
    suffix: str,
    *,
    is_baseline: bool = False,
    parent_snapshot_digest: str = "",
    stable_fallback_digest: str = "",
) -> OptimizationSnapshot:
    return OptimizationSnapshot(
        snapshot_id=f"optimization-snapshot.{suffix}",
        project_id="project.shared",
        parent_snapshot_digest=parent_snapshot_digest,
        stable_fallback_digest=stable_fallback_digest,
        candidate_digest="" if is_baseline else f"sha256:candidate-{suffix}",
        evaluation_report_digests=()
        if is_baseline
        else (f"sha256:evaluation-{suffix}",),
        policy_payload={"selection_policy": {"version": suffix}},
        created_at="2026-07-22T00:00:00+00:00",
        is_baseline=is_baseline,
    )


def _binding(
    session_id: str,
    token: SnapshotSelectionToken,
) -> SessionSnapshotBindingOperation:
    return SessionSnapshotBindingOperation(
        operation_id=f"binding.{session_id}",
        project_id="project.shared",
        session_id=session_id,
        initial_candidate_digest=f"sha256:{session_id}-candidate",
        stage_key="implementation",
        risk_level="medium",
        candidate_size_bucket="small",
        provider_ids=("provider.test",),
        created_at="2026-07-22T00:00:00+00:00",
        target_snapshot_digest=token.active_snapshot_digest,
        expected_head_sequence=token.head_sequence,
        expected_head_digest=token.head_digest,
        expected_pointer_revision=token.pointer_revision,
        expected_revocation_generation=token.revocation_generation,
    )


def _service(root: Path, baseline: OptimizationSnapshot) -> SnapshotControlService:
    governor = ResourceGovernor(
        root,
        project_id="project.shared",
        foreground_capacity=ResourceAmounts(),
        offline_optimization_capacity=ResourceAmounts(),
        lock_timeout_seconds=1,
    )
    return SnapshotControlService(
        root,
        project_id="project.shared",
        baseline_snapshot=baseline,
        resource_governor=governor,
    )
