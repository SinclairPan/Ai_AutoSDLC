"""根据新 Session 的独立终态自动稳定或撤销 Active Snapshot。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Literal

from ai_sdlc.core.stage_review.artifacts import SharedStateIntegrityError
from ai_sdlc.core.stage_review.optimization.observations import (
    TERMINAL_OBSERVATION_KINDS,
    OptimizationObservationStore,
    OptimizationSessionObservation,
)
from ai_sdlc.core.stage_review.optimization.snapshots import SnapshotControlService
from ai_sdlc.core.stage_review.resource_builders import parse_utc, stable_id

SnapshotMonitorResult = Literal[
    "no_change",
    "marked_stable",
    "revoked_and_rolled_back",
    "safety_pending",
]
_CRITICAL_REASONS = frozenset(
    {
        "false_certificate",
        "isolation_escape",
        "isolation_pollution",
        "duplicate_billing",
        "late_critical_finding",
        "reviewer_coverage_leak",
        "safety_rollback",
    }
)


def reconcile_active_snapshot(
    snapshots: SnapshotControlService,
    observations: OptimizationObservationStore,
    *,
    clock: Callable[[], str],
    minimum_stable_sessions: int = 10,
    minimum_stable_days: int = 14,
) -> SnapshotMonitorResult:
    token = snapshots.resolve_snapshot()
    if token.active_snapshot_digest == token.stable_fallback_digest:
        return "no_change"
    snapshot = snapshots.store.snapshot(token.active_snapshot_digest)
    if snapshot is None:
        raise SharedStateIntegrityError("active optimization snapshot is unavailable")
    eligible = _active_terminal_observations(
        observations.read_all(),
        snapshot.snapshot_digest,
        snapshot.created_at,
    )
    critical = next((item for item in eligible if _is_critical(item)), None)
    if critical is not None:
        return _revoke(snapshots, snapshot.snapshot_digest, critical)
    sessions = {item.session_id for item in eligible}
    elapsed = parse_utc(clock()) - parse_utc(snapshot.created_at)
    if len(sessions) < minimum_stable_sessions:
        return "no_change"
    if elapsed.total_seconds() < minimum_stable_days * 86400:
        return "no_change"
    event = snapshots.mark_stable(
        snapshot.snapshot_digest,
        operation_id=stable_id(
            "snapshot-auto-stability",
            snapshot.snapshot_digest,
        ),
    )
    return "marked_stable" if event is not None else "safety_pending"


def _active_terminal_observations(
    values: tuple[OptimizationSessionObservation, ...],
    snapshot_digest: str,
    created_at: str,
) -> tuple[OptimizationSessionObservation, ...]:
    boundary = parse_utc(created_at)
    return tuple(
        item
        for item in values
        if item.active_snapshot_digest == snapshot_digest
        and item.observation_kind in TERMINAL_OBSERVATION_KINDS
        and parse_utc(item.occurred_at) >= boundary
    )


def _is_critical(value: OptimizationSessionObservation) -> bool:
    return (
        value.observation_kind == "integrity_failure"
        or value.terminal_reason in _CRITICAL_REASONS
    )


def _revoke(
    snapshots: SnapshotControlService,
    snapshot_digest: str,
    observation: OptimizationSessionObservation,
) -> SnapshotMonitorResult:
    reason = observation.terminal_reason or observation.observation_kind
    snapshots.revoke_and_rollback(
        snapshot_digest,
        reason=reason,
        operation_id=stable_id(
            "snapshot-auto-revocation",
            snapshot_digest,
            observation.observation_digest,
        ),
    )
    return "revoked_and_rolled_back"


__all__ = ["SnapshotMonitorResult", "reconcile_active_snapshot"]
