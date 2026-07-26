from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from tests.unit.stage_review.optimization.test_snapshots import (
    _binding,
    _promotion_evidence,
    _promotion_package,
    _promotion_policy,
    _register_promotion,
    _service,
    _snapshot,
)

from ai_sdlc.core.stage_review.artifacts import SharedStateIntegrityError
from ai_sdlc.core.stage_review.optimization.promotion import (
    AutoPromotionDecision,
    AutoPromotionGate,
)
from ai_sdlc.core.stage_review.resource_storage_bundles import (
    ResourceStorageBundleLedger,
)


def test_barrier_serializes_promotion_revocation_and_sixteen_bindings(
    tmp_path: Path,
) -> None:
    baseline = _snapshot("baseline", is_baseline=True)
    active = _snapshot(
        "active",
        parent_snapshot_digest=baseline.snapshot_digest,
        stable_fallback_digest=baseline.snapshot_digest,
    )
    challenger = _snapshot(
        "challenger",
        parent_snapshot_digest=active.snapshot_digest,
        stable_fallback_digest=active.snapshot_digest,
    )
    service = _service(tmp_path, baseline)
    service.register_snapshot(active)
    active_package = _promotion_package(
        active,
        _decision(
            baseline.snapshot_digest,
            active.snapshot_digest,
            active.candidate_digest,
            "activate",
            shadow_result_digest=active.shadow_result_digest,
            evaluation_report_digests=active.evaluation_report_digests,
        ),
    )
    active_authorization = _register_promotion(service, active_package)
    service._promote_committed_package(
        active.snapshot_digest,
        promotion_package_digest=active_package.package_digest,
        promotion_authorization_digest=active_authorization,
        operation_id="operation.activate",
    )
    service.mark_stable(active.snapshot_digest, operation_id="operation.stable")
    service.register_snapshot(challenger)
    challenger_package = _promotion_package(
        challenger,
        _decision(
            active.snapshot_digest,
            challenger.snapshot_digest,
            challenger.candidate_digest,
            "concurrent",
            shadow_result_digest=challenger.shadow_result_digest,
            evaluation_report_digests=challenger.evaluation_report_digests,
        ),
    )
    challenger_authorization = _register_promotion(service, challenger_package)
    token = service.resolve_snapshot()
    barrier = threading.Barrier(18)

    def bind(index: int) -> object:
        barrier.wait()
        try:
            return service.bind_session(
                _binding(f"session.concurrent-{index}", token), token
            )
        except SharedStateIntegrityError as exc:
            assert _is_expected_contention_result(str(exc))
            return str(exc)

    def promote() -> object:
        barrier.wait()
        try:
            return service._promote_committed_package(
                challenger.snapshot_digest,
                promotion_package_digest=challenger_package.package_digest,
                promotion_authorization_digest=challenger_authorization,
                operation_id="operation.concurrent-promotion",
            )
        except SharedStateIntegrityError as exc:
            assert _is_expected_contention_result(str(exc))
            return str(exc)

    def revoke() -> object:
        barrier.wait()
        try:
            return service.revoke_and_rollback(
                active.snapshot_digest,
                reason="concurrent-safety-test",
                operation_id="operation.concurrent-revocation",
            )
        except SharedStateIntegrityError as exc:
            assert _is_expected_contention_result(str(exc))
            return str(exc)

    with ThreadPoolExecutor(max_workers=18) as pool:
        futures = [pool.submit(bind, index) for index in range(16)]
        futures.extend((pool.submit(promote), pool.submit(revoke)))
        results = tuple(future.result(timeout=5) for future in futures)

    assert len(results) == 18
    if isinstance(results[-1], str):
        # safety_pending 是有界竞争的公开结果；竞争结束后必须用同一操作身份重试。
        service.revoke_and_rollback(
            active.snapshot_digest,
            reason="concurrent-safety-test",
            operation_id="operation.concurrent-revocation",
        )
    final = service.resolve_snapshot()
    events = service.events()
    assert active.snapshot_digest in final.revoked_snapshot_digests
    assert tuple(event.sequence for event in events) == tuple(
        range(1, len(events) + 1)
    )
    assert all(
        event.previous_event_digest == events[index - 1].event_digest
        for index, event in enumerate(events[1:], start=1)
    )
    revocation_sequence = next(
        event.sequence
        for event in events
        if event.event_kind == "revocation"
        and event.revoked_snapshot_digest == active.snapshot_digest
    )
    assert not any(
        event.event_kind == "session_binding"
        and event.sequence > revocation_sequence
        and event.target_snapshot_digest == active.snapshot_digest
        for event in events
    )
    bundle_events = ResourceStorageBundleLedger(service.resources._store).events()
    reserved = {
        event.bundle_id for event in bundle_events if event.event_kind == "reserved"
    }
    released = {
        event.bundle_id for event in bundle_events if event.event_kind == "released"
    }
    assert reserved == released


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


def _is_expected_contention_result(message: str) -> bool:
    return any(
        code in message
        for code in (
            "snapshot_control_busy",
            "snapshot_control_safety_pending",
            "snapshot selection is stale",
        )
    )
