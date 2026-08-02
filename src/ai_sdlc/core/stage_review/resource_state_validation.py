"""Resource 投影的容量聚合与池级不变量。"""

from __future__ import annotations

from ai_sdlc.core.stage_review.artifacts import SharedStateIntegrityError
from ai_sdlc.core.stage_review.resource_ledger_models import ResourceReservation
from ai_sdlc.core.stage_review.resource_models import (
    ResourceAmounts,
    ResourceGovernorConfig,
)


def _aggregate_reserved(
    reservations: tuple[ResourceReservation, ...],
) -> ResourceAmounts:
    total = ResourceAmounts()
    for reservation in reservations:
        if reservation.state in {"admission", "final"}:
            total = total + reservation.reserved
        elif reservation.authorized_pending.any_positive():
            total = total + reservation.authorized_pending
    return total


def _verify_pool_capacity(
    config: ResourceGovernorConfig,
    reservations: dict[str, ResourceReservation],
) -> None:
    for pool, capacity in (
        ("foreground", config.foreground_capacity),
        ("offline_optimization", config.offline_optimization_capacity),
    ):
        selected = tuple(item for item in reservations.values() if item.pool == pool)
        if not _aggregate_reserved(selected).fits_within(capacity):
            raise SharedStateIntegrityError(
                f"resource ledger exceeds {pool} configured capacity"
            )
