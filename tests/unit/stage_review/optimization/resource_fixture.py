from __future__ import annotations

from tests.unit.stage_review.test_resources import _policy

from ai_sdlc.core.stage_review.optimization.maintenance_window import (
    _optimization_resource_session_id as optimization_resource_session_id,
)
from ai_sdlc.core.stage_review.resource_builders import build_budget_envelope
from ai_sdlc.core.stage_review.resource_ledger_models import ResourceReservation
from ai_sdlc.core.stage_review.resources import ResourceGovernor


def offline_reservation(
    governor: ResourceGovernor,
    epoch_id: str,
    *,
    fencing_epoch: int = 1,
) -> ResourceReservation:
    envelope = build_budget_envelope(
        project_id="project.shared",
        work_item_id="offline-optimization",
        stage_review_session_id=optimization_resource_session_id(
            epoch_id, fencing_epoch
        ),
        risk_level="low",
        budget_policy=_policy(),
        pool="offline_optimization",
    )
    admission = governor.reserve_admission(
        envelope,
        budget_policy=_policy(),
        lease_owner=f"optimization-test.worker-{fencing_epoch}",
        operation_id=f"optimization-test.admission-{fencing_epoch}",
        lease_seconds=360,
    )
    assert admission.reservation is not None
    final = governor.finalize_offline_reservation(
        admission.reservation.reservation_id,
        lease_owner=admission.reservation.lease_owner,
        expected_fencing_token=admission.reservation.fencing_token,
        operation_id=f"optimization-test.finalization-{fencing_epoch}",
    )
    assert final.reservation is not None
    return final.reservation
