from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from ai_sdlc.core.stage_review.artifacts import (
    SharedStateIntegrityError,
    atomic_write_json,
    read_json_object,
)
from ai_sdlc.core.stage_review.canonical import (
    CanonicalizationPolicy,
    canonical_digest,
)
from ai_sdlc.core.stage_review.optimization.commit_fencing import (
    OptimizationCommitLeaseHandle,
)
from ai_sdlc.core.stage_review.optimization.observations import (
    CommittedSessionBindingStore,
    OptimizationObservationStore,
)
from ai_sdlc.core.stage_review.optimization.pipeline_contracts import (
    PipelinePromotionAuthorization,
    PipelinePromotionPackage,
)
from ai_sdlc.core.stage_review.optimization.promotion import (
    AutoPromotionDecision,
    AutoPromotionEvidence,
    AutoPromotionGate,
    AutoPromotionPolicy,
)
from ai_sdlc.core.stage_review.optimization.snapshot_models import (
    SESSION_BINDING_OPERATION_DIGEST_EXTENSION,
    OptimizationSnapshot,
    SessionSnapshotBindingOperation,
    SnapshotControlEvent,
    SnapshotSelectionToken,
)
from ai_sdlc.core.stage_review.optimization.snapshots import SnapshotControlService
from ai_sdlc.core.stage_review.optimization.storage_models import (
    OptimizationStoragePolicy,
    OptimizationStorageRecord,
    StoragePressureError,
)
from ai_sdlc.core.stage_review.resource_models import ResourceAmounts
from ai_sdlc.core.stage_review.resources import ResourceGovernor


def test_auto_promotion_requires_quality_non_regression_and_significance() -> None:
    gate = AutoPromotionGate(_promotion_policy())
    accepted = gate.evaluate(_promotion_evidence(), decision_id="decision.accepted")
    quality_regression = gate.evaluate(
        _promotion_evidence_with(late_critical_delta=0.01),
        decision_id="decision.regression",
    )
    budget_regression = gate.evaluate(
        _promotion_evidence_with(hard_budget_exhausted_delta=0.01),
        decision_id="decision.budget-regression",
    )
    insignificant = gate.evaluate(
        _promotion_evidence_with(quality_confidence_lower=0),
        decision_id="decision.insignificant",
    )

    assert accepted.approved
    assert not quality_regression.approved
    assert "late_critical_non_regression" in quality_regression.failed_guards
    assert "hard_budget_exhausted_non_regression" in (budget_regression.failed_guards)
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
    assert (
        reopened.resolve_snapshot().active_snapshot_digest == original.snapshot_digest
    )
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
    assert first_binding.extensions["session_binding_operation_digest"]
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
            shadow_result_digest=challenger.shadow_result_digest,
            evaluation_report_digests=challenger.evaluation_report_digests,
        ),
        decision_id="decision.promote",
    )
    package = _promotion_package(challenger, decision)
    authorization_digest = _register_promotion(service, package)
    service._promote_committed_package(
        challenger.snapshot_digest,
        promotion_package_digest=package.package_digest,
        promotion_authorization_digest=authorization_digest,
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


def test_session_binding_rejects_same_domain_rehashed_tail_facts(
    tmp_path: Path,
) -> None:
    baseline = _snapshot("trusted-binding", is_baseline=True)
    service = _service(tmp_path, baseline)
    token = service.resolve_snapshot()
    operation = _binding("session.trusted", token)
    service.bind_session(operation, token)
    stored_operation = service.store.binding_operations()[0]

    operation_path = (
        service.store.root
        / "session-binding-operations"
        / f"{stored_operation.operation_id}.json"
    )
    operation_payload = read_json_object(operation_path)
    operation_payload.update(
        {
            "created_at": "2035-12-31T23:59:59Z",
            "operation_digest": "",
        }
    )
    rehashed_operation = SessionSnapshotBindingOperation.model_validate(
        operation_payload
    )
    atomic_write_json(operation_path, rehashed_operation.model_dump(mode="json"))

    record_path = next(
        (service.store.storage.loose_root / "snapshot-control").glob("*.json")
    )
    record_payload = read_json_object(record_path)
    event_payload = dict(record_payload["payload"])
    extensions = dict(event_payload["extensions"])
    extensions[SESSION_BINDING_OPERATION_DIGEST_EXTENSION] = (
        rehashed_operation.operation_digest
    )
    event_payload.update({"extensions": extensions, "event_digest": ""})
    rehashed_event = SnapshotControlEvent.model_validate(event_payload)
    record_payload.update(
        {
            "payload": rehashed_event.model_dump(mode="json"),
            "record_digest": "",
        }
    )
    rehashed_record = OptimizationStorageRecord.model_validate(record_payload)
    atomic_write_json(record_path, rehashed_record.model_dump(mode="json"))

    with pytest.raises(SharedStateIntegrityError, match="trust anchor"):
        _service(tmp_path, baseline).events()


def test_snapshot_control_rejects_valid_mac_prefix_rollback(
    tmp_path: Path,
) -> None:
    baseline = _snapshot("trusted-head", is_baseline=True)
    service = _service(tmp_path, baseline)
    token = service.resolve_snapshot()
    service.bind_session(_binding("session.first", token), token)
    second_token = service.resolve_snapshot()
    service.bind_session(_binding("session.second", second_token), second_token)
    records = sorted(
        (service.store.storage.loose_root / "snapshot-control").glob("*.json")
    )
    assert len(records) == 2
    records[-1].unlink()

    with pytest.raises(SharedStateIntegrityError, match="trusted head"):
        _service(tmp_path, baseline).events()


def test_snapshot_control_recovers_signed_tail_after_head_update_crash(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    baseline = _snapshot("head-recovery", is_baseline=True)
    service = _service(tmp_path, baseline)
    token = service.resolve_snapshot()
    original = service.store.trust_anchor._reconcile_head

    def crash_after_event_append(
        events: tuple[SnapshotControlEvent, ...],
        *,
        legacy_sequence: int,
    ) -> None:
        if events:
            raise RuntimeError("simulated crash before trusted head update")
        original(events, legacy_sequence=legacy_sequence)

    monkeypatch.setattr(
        service.store.trust_anchor,
        "_reconcile_head",
        crash_after_event_append,
    )
    with pytest.raises(RuntimeError, match="simulated crash"):
        service.bind_session(_binding("session.crash", token), token)

    reopened = _service(tmp_path, baseline)
    events = reopened.events()

    assert len(events) == 1
    assert events[0].session_id == "session.crash"
    assert reopened.store.trust_anchor.head_path.is_file()


def test_snapshot_control_recovers_epoch_after_head_commit_crash(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    baseline = _snapshot("epoch-recovery", is_baseline=True)
    service = _service(tmp_path, baseline)
    token = service.resolve_snapshot()
    original = service.store.trust_anchor.epoch_store._reconcile

    def crash_after_head_commit(commitment: dict[str, object]) -> None:
        if commitment["head_sequence"] == 0:
            original(commitment)
            return
        raise RuntimeError("simulated crash before trust epoch update")

    monkeypatch.setattr(
        service.store.trust_anchor.epoch_store,
        "_reconcile",
        crash_after_head_commit,
    )
    with pytest.raises(RuntimeError, match="simulated crash"):
        service.bind_session(_binding("session.epoch-crash", token), token)

    reopened = _service(tmp_path, baseline)

    assert reopened.events()[0].session_id == "session.epoch-crash"
    assert reopened.store.trust_anchor.epoch_store._read()["head_sequence"] == 1


def test_snapshot_control_rejects_old_valid_head_and_event_prefix_replay(
    tmp_path: Path,
) -> None:
    baseline = _snapshot("head-replay", is_baseline=True)
    service = _service(tmp_path, baseline)
    first_token = service.resolve_snapshot()
    service.bind_session(_binding("session.first", first_token), first_token)
    record_root = service.store.storage.loose_root / "snapshot-control"
    first_records = {path.name: path.read_bytes() for path in record_root.glob("*.json")}
    first_head = service.store.trust_anchor.head_path.read_bytes()
    second_token = service.resolve_snapshot()
    service.bind_session(_binding("session.second", second_token), second_token)

    for path in record_root.glob("*.json"):
        path.unlink()
    for name, payload in first_records.items():
        (record_root / name).write_bytes(payload)
    service.store.trust_anchor.head_path.write_bytes(first_head)

    with pytest.raises(SharedStateIntegrityError, match="trusted.*diverged"):
        _service(tmp_path, baseline).events()


def test_snapshot_control_rejects_missing_committed_epoch(
    tmp_path: Path,
) -> None:
    baseline = _snapshot("missing-epoch", is_baseline=True)
    service = _service(tmp_path, baseline)
    token = service.resolve_snapshot()
    service.bind_session(_binding("session.committed", token), token)
    epoch_path = (
        service.store.trust_anchor.epoch_store.root
        / "snapshot-control-epoch.json"
    )
    epoch_path.unlink()

    with pytest.raises(SharedStateIntegrityError, match="trust epoch.*missing"):
        _service(tmp_path, baseline).events()


def test_snapshot_control_rejects_epoch_head_and_event_prefix_replay(
    tmp_path: Path,
) -> None:
    baseline = _snapshot("epoch-replay", is_baseline=True)
    service = _service(tmp_path, baseline)
    first_token = service.resolve_snapshot()
    service.bind_session(_binding("session.first", first_token), first_token)
    record_root = service.store.storage.loose_root / "snapshot-control"
    first_records = {path.name: path.read_bytes() for path in record_root.glob("*.json")}
    first_head = service.store.trust_anchor.head_path.read_bytes()
    epoch_path = (
        service.store.trust_anchor.epoch_store.root
        / "snapshot-control-epoch.json"
    )
    first_epoch = epoch_path.read_bytes()
    second_token = service.resolve_snapshot()
    service.bind_session(_binding("session.second", second_token), second_token)

    for path in record_root.glob("*.json"):
        path.unlink()
    for name, payload in first_records.items():
        (record_root / name).write_bytes(payload)
    service.store.trust_anchor.head_path.write_bytes(first_head)
    epoch_path.write_bytes(first_epoch)

    with pytest.raises(SharedStateIntegrityError, match="trust epoch.*backwards"):
        _service(tmp_path, baseline).events()


def test_legacy_unsigned_binding_cannot_downgrade_committed_trust_epoch(
    tmp_path: Path,
) -> None:
    baseline = _snapshot("legacy-binding", is_baseline=True)
    service = _service(tmp_path, baseline)
    _convert_session_binding_to_legacy(service)
    trusted_root = (
        tmp_path
        / ".ai-sdlc"
        / "state"
        / "trusted"
        / "projects"
        / "project.shared"
        / "snapshot-control"
    )
    for path in trusted_root.iterdir():
        if path.is_file():
            path.unlink()

    with pytest.raises(SharedStateIntegrityError, match="trust anchor.*invalid"):
        _service(tmp_path, baseline)


def test_legacy_unsigned_binding_cannot_delete_every_mutable_anchor(
    tmp_path: Path,
) -> None:
    baseline = _snapshot("legacy-full-anchor-delete", is_baseline=True)
    service = _service(tmp_path, baseline)
    _convert_session_binding_to_legacy(service)
    epoch_path = (
        service.store.trust_anchor.epoch_store.root
        / "snapshot-control-epoch.json"
    )
    epoch_path.unlink()
    for path in service.store.trust_anchor.root.iterdir():
        if path.is_file():
            path.unlink()

    with pytest.raises(SharedStateIntegrityError, match="trust epoch.*missing"):
        _service(tmp_path, baseline)


def test_legacy_unsigned_binding_migrates_once_without_recovery_authority(
    tmp_path: Path,
) -> None:
    source = tmp_path / "legacy-source"
    source.mkdir()
    baseline = _snapshot("legacy-migration", is_baseline=True)
    source_service = _service(source, baseline)
    legacy_event = _convert_session_binding_to_legacy(source_service)
    migrated = tmp_path / "migrated-project"
    shutil.copytree(source, migrated, ignore=shutil.ignore_patterns("trusted"))

    reopened = _service(migrated, baseline)

    assert reopened.events() == (legacy_event,)
    assert reopened.session_binding_lineage("session.legacy") is None
    current = reopened.resolve_snapshot()
    with pytest.raises(
        SharedStateIntegrityError,
        match="session binding identity diverged",
    ):
        reopened.bind_session(_binding("session.legacy", current), current)


def test_snapshot_control_rejects_uncommitted_promotion_package(
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
    assert not hasattr(service, "promote")

    with pytest.raises(
        SharedStateIntegrityError,
        match="committed promotion package is unavailable",
    ):
        service._promote_committed_package(
            challenger.snapshot_digest,
            promotion_package_digest=f"sha256:{'0' * 64}",
            promotion_authorization_digest=f"sha256:{'1' * 64}",
            operation_id="operation.uncommitted-promotion",
        )

    assert service.resolve_snapshot().active_snapshot_digest == baseline.snapshot_digest


def test_snapshot_control_recovers_from_segment_and_fences_followup_writer(
    tmp_path: Path,
) -> None:
    baseline = _snapshot("baseline", is_baseline=True)
    service = _service(tmp_path, baseline)
    token = service.resolve_snapshot()
    first = service.bind_session(
        _binding(f"session.first.{'x' * 8192}", token),
        token,
    )
    prepared = service.store.storage._prepare_compaction("snapshot-control")
    assert prepared is not None

    with (
        service.resources.storage_bundle(
            bundle_class="reclamation",
            bundle_bytes=prepared.required_bundle_bytes,
            net_reclaim_bytes=prepared.net_reclaim_bytes,
            policy=service.storage_policy,
            operation_id="compactor.snapshot-control.bundle",
        ) as bundle,
        service.store.storage.acquire_planned_lease(
            prepared.lease_plan,
            write_class="reclamation",
            bundle_bytes=prepared.required_bundle_bytes,
            net_reclaim_bytes=prepared.net_reclaim_bytes,
            resource_bundle=bundle,
        ) as lease,
    ):
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
    second = service.bind_session(
        _binding("session.second", shared_token), shared_token
    )

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


def test_session_binding_retries_an_expired_commit_lease_without_duplicates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    baseline = _snapshot("baseline", is_baseline=True)
    service = _service(tmp_path, baseline)
    token = service.resolve_snapshot()
    operation = _binding("session.lease-retry", token)
    monotonic_values = iter((0.0, 2.001))
    service.retry.monotonic = lambda: next(monotonic_values)
    original = OptimizationCommitLeaseHandle.assert_current
    current_checks = 0

    def expire_once(
        current: OptimizationCommitLeaseHandle,
        *,
        now=None,
    ) -> None:
        nonlocal current_checks
        current_checks += 1
        if current_checks == 1:
            raise SharedStateIntegrityError("optimization commit lease expired")
        original(current, now=now)

    monkeypatch.setattr(OptimizationCommitLeaseHandle, "assert_current", expire_once)

    event = service.bind_session(operation, token)

    claims = service.store.commit_leases.claims()
    assert len(claims) == 2
    assert claims[1].fencing_epoch > claims[0].fencing_epoch
    assert current_checks >= 2
    assert event.event_kind == "session_binding"
    assert service.events() == (event,)
    binding_operations = service.store.binding_operations()
    assert len(binding_operations) == 1
    assert binding_operations[0].session_id == operation.session_id


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
            shadow_result_digest=challenger.shadow_result_digest,
            evaluation_report_digests=challenger.evaluation_report_digests,
        ),
        decision_id="decision.promote",
    )
    package = _promotion_package(challenger, decision)
    authorization_digest = _register_promotion(service, package)
    service._promote_committed_package(
        challenger.snapshot_digest,
        promotion_package_digest=package.package_digest,
        promotion_authorization_digest=authorization_digest,
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
    first_package = _promotion_package(
        first,
        _decision(
            baseline.snapshot_digest,
            first.snapshot_digest,
            first.candidate_digest,
            "one",
            shadow_result_digest=first.shadow_result_digest,
            evaluation_report_digests=first.evaluation_report_digests,
        ),
    )
    first_authorization = _register_promotion(service, first_package)
    service._promote_committed_package(
        first.snapshot_digest,
        promotion_package_digest=first_package.package_digest,
        promotion_authorization_digest=first_authorization,
        operation_id="operation.promote-one",
    )
    service.mark_stable(first.snapshot_digest, operation_id="operation.stable-one")
    second_package = _promotion_package(
        second,
        _decision(
            first.snapshot_digest,
            second.snapshot_digest,
            second.candidate_digest,
            "two",
            shadow_result_digest=second.shadow_result_digest,
            evaluation_report_digests=second.evaluation_report_digests,
        ),
    )
    second_authorization = _register_promotion(service, second_package)
    service._promote_committed_package(
        second.snapshot_digest,
        promotion_package_digest=second_package.package_digest,
        promotion_authorization_digest=second_authorization,
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
    with service._storage_bundle(
        "session_binding",
        orphan.operation_id,
    ) as bundle:
        service.store.persist_binding_operation(
            orphan,
            resource_bundle=bundle,
        )
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


def test_snapshot_registration_cannot_bypass_offline_storage_limit(
    tmp_path: Path,
) -> None:
    baseline = _snapshot("baseline", is_baseline=True)
    service = _service_with_policy(tmp_path, baseline)
    oversized_payload = _snapshot(
        "oversized",
        parent_snapshot_digest=baseline.snapshot_digest,
        stable_fallback_digest=baseline.snapshot_digest,
    ).model_dump(mode="json")
    oversized_payload["policy_payload"] = {
        "selection_policy": {
            "version": "oversized",
            "padding": "x" * 20_000,
        }
    }
    oversized_payload["snapshot_digest"] = ""
    oversized = OptimizationSnapshot.model_validate(oversized_payload)

    with pytest.raises(StoragePressureError, match="dedicated reserve"):
        service.register_snapshot(oversized)

    assert service.store.snapshot(oversized.snapshot_digest) is None
    assert _tree_bytes(service.store.storage.accounting_root) <= 12_000


def test_revocation_operation_consumes_safety_bundle_before_write(
    tmp_path: Path,
) -> None:
    baseline = _snapshot("baseline", is_baseline=True)
    service = _service_with_policy(tmp_path, baseline)
    challenger = _snapshot(
        "challenger",
        parent_snapshot_digest=baseline.snapshot_digest,
        stable_fallback_digest=baseline.snapshot_digest,
    )
    service.register_snapshot(challenger)
    before = _tree_bytes(service.store.storage.accounting_root)

    with pytest.raises(
        SharedStateIntegrityError,
        match="snapshot_control_safety_pending",
    ):
        service.request_revocation(
            challenger.snapshot_digest,
            reason="r" * 5_000,
            operation_id="operation.oversized-revocation",
        )

    assert service.store.revocation_operations() == ()
    assert _tree_bytes(service.store.storage.accounting_root) == before


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
    shadow_result_digest: str = "sha256:shadow-result",
    evaluation_report_digests: tuple[str, ...] = ("sha256:evaluation",),
) -> AutoPromotionEvidence:
    return AutoPromotionEvidence(
        baseline_snapshot_digest=baseline_digest,
        challenger_snapshot_digest=challenger_digest,
        candidate_digest=candidate_digest,
        evaluation_report_digests=evaluation_report_digests,
        shadow_result_digest=shadow_result_digest,
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


def _promotion_evidence_with(**updates: object) -> AutoPromotionEvidence:
    baseline = _promotion_evidence()
    return AutoPromotionEvidence.model_validate(
        {
            **baseline.model_dump(
                mode="json",
                exclude={"evidence_digest"},
            ),
            **updates,
        }
    )


def _decision(
    baseline_digest: str,
    challenger_digest: str,
    candidate_digest: str,
    suffix: str,
    *,
    shadow_result_digest: str,
    evaluation_report_digests: tuple[str, ...],
) -> AutoPromotionDecision:
    return AutoPromotionGate(_promotion_policy()).evaluate(
        _promotion_evidence(
            baseline_digest=baseline_digest,
            challenger_digest=challenger_digest,
            candidate_digest=candidate_digest,
            shadow_result_digest=shadow_result_digest,
            evaluation_report_digests=evaluation_report_digests,
        ),
        decision_id=f"decision.{suffix}",
    )


def _promotion_package(
    snapshot: OptimizationSnapshot,
    decision: AutoPromotionDecision,
) -> PipelinePromotionPackage:
    evidence = _promotion_evidence(
        baseline_digest=decision.baseline_snapshot_digest,
        challenger_digest=snapshot.snapshot_digest,
        candidate_digest=snapshot.candidate_digest,
        shadow_result_digest=snapshot.shadow_result_digest,
        evaluation_report_digests=snapshot.evaluation_report_digests,
    )
    assert decision.promotion_evidence_digest == evidence.evidence_digest
    return PipelinePromotionPackage(
        epoch_id="optimization-epoch.snapshot-control-test",
        constitution_digest="sha256:snapshot-control-constitution",
        decision=decision,
        evidence=evidence,
        snapshot=snapshot,
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
        shadow_result_digest="" if is_baseline else f"sha256:shadow-{suffix}",
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
        command_id=f"command.{session_id}",
        idempotency_key=f"idempotency.{session_id}",
        command_digest=f"sha256:command-{session_id}",
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


class _SnapshotPromotionAuthority:
    def __init__(self) -> None:
        self.receipts: dict[str, PipelinePromotionAuthorization] = {}

    def authorize(
        self,
        package: PipelinePromotionPackage,
    ) -> PipelinePromotionAuthorization:
        receipt = PipelinePromotionAuthorization(
            authorization_id=f"authorization.{package.snapshot.snapshot_id}",
            epoch_id=package.epoch_id,
            epoch_revision=1,
            epoch_digest="sha256:test-epoch",
            constitution_digest=package.constitution_digest,
            runtime_bundle_manifest_digest="sha256:test-runtime-bundle",
            epoch_fencing_epoch=1,
            epoch_claim_digest="sha256:test-epoch-claim",
            promotion_package_digest=package.package_digest,
            decision_digest=package.decision.decision_digest,
            promotion_evidence_digest=package.evidence.evidence_digest,
            snapshot_digest=package.snapshot.snapshot_digest,
            shadow_result_digest=package.snapshot.shadow_result_digest,
            evaluation_report_digests=package.snapshot.evaluation_report_digests,
        )
        self.receipts[package.package_digest] = receipt
        return receipt

    def promotion_authorization(
        self,
        package_digest: str,
    ) -> PipelinePromotionAuthorization | None:
        return self.receipts.get(package_digest)

    def verify_promotion_authorization(
        self,
        receipt: PipelinePromotionAuthorization,
        package: PipelinePromotionPackage,
    ) -> None:
        if self.receipts.get(package.package_digest) != receipt:
            raise SharedStateIntegrityError("promotion authorization lineage diverged")


def _register_promotion(
    service: SnapshotControlService,
    package: PipelinePromotionPackage,
) -> str:
    authority = service.promotion_authority
    assert isinstance(authority, _SnapshotPromotionAuthority)
    receipt = authority.authorize(package)
    service.store.register_promotion_package(
        package,
        authorization_digest=receipt.authorization_digest,
    )
    return receipt.authorization_digest


def _convert_session_binding_to_legacy(
    service: SnapshotControlService,
) -> SnapshotControlEvent:
    token = service.resolve_snapshot()
    operation = _binding("session.legacy", token)
    service.bind_session(operation, token)
    stored_operation = service.store.binding_operations()[0]
    operation_path = (
        service.store.root
        / "session-binding-operations"
        / f"{stored_operation.operation_id}.json"
    )
    legacy_operation = read_json_object(operation_path)
    for field in ("command_id", "idempotency_key", "command_digest"):
        legacy_operation.pop(field)
    legacy_operation.update(
        {
            "schema_version": "session-snapshot-binding-operation.v1",
            "operation_digest": "",
        }
    )
    legacy_operation["operation_digest"] = canonical_digest(
        {
            key: value
            for key, value in legacy_operation.items()
            if key != "operation_digest"
        },
        CanonicalizationPolicy(),
    )
    atomic_write_json(operation_path, legacy_operation)

    record_path = next(
        (service.store.storage.loose_root / "snapshot-control").glob("*.json")
    )
    record_payload = read_json_object(record_path)
    event_payload = dict(record_payload["payload"])
    extensions = dict(event_payload["extensions"])
    extensions.pop("snapshot_control_trust_mac")
    event_payload.update({"extensions": extensions, "event_digest": ""})
    legacy_event = SnapshotControlEvent.model_validate(event_payload)
    record_payload.update(
        {
            "payload": legacy_event.model_dump(mode="json"),
            "record_digest": "",
        }
    )
    legacy_record = OptimizationStorageRecord.model_validate(record_payload)
    atomic_write_json(record_path, legacy_record.model_dump(mode="json"))
    return legacy_event


def _service(root: Path, baseline: OptimizationSnapshot) -> SnapshotControlService:
    governor = ResourceGovernor(
        root,
        project_id="project.shared",
        foreground_capacity=ResourceAmounts(),
        offline_optimization_capacity=ResourceAmounts(),
        lock_timeout_seconds=1,
    )
    service = SnapshotControlService(
        root,
        project_id="project.shared",
        baseline_snapshot=baseline,
        resource_governor=governor,
        promotion_authority=_SnapshotPromotionAuthority(),
    )
    return service


def _service_with_policy(
    root: Path,
    baseline: OptimizationSnapshot,
) -> SnapshotControlService:
    governor = ResourceGovernor(
        root,
        project_id="project.shared",
        foreground_capacity=ResourceAmounts(),
        offline_optimization_capacity=ResourceAmounts(),
        lock_timeout_seconds=1,
    )
    service = SnapshotControlService(
        root,
        project_id="project.shared",
        baseline_snapshot=baseline,
        resource_governor=governor,
        promotion_authority=_SnapshotPromotionAuthority(),
        storage_policy=OptimizationStoragePolicy(
            maximum_total_bytes=12_000,
            minimum_free_bytes=0,
            minimum_free_ratio=0,
            critical_recovery_reserve_bytes=3_000,
            session_binding_reserve_bytes=2_000,
            maintenance_reclamation_reserve_bytes=3_000,
            safety_bundle_max_bytes=2_000,
        ),
    )
    return service


def _tree_bytes(root: Path) -> int:
    return sum(path.stat().st_size for path in root.rglob("*") if path.is_file())
