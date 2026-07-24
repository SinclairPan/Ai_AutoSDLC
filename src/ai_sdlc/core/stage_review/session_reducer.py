"""从不可变 SessionEvent 链重建 StageReviewSession 投影。"""

from __future__ import annotations

from ai_sdlc.core.stage_review.finding_models import FindingScope
from ai_sdlc.core.stage_review.session_close_validation import (
    CLOSE_EVENT_KINDS,
)
from ai_sdlc.core.stage_review.session_close_validation import (
    _validate_close_event as validate_close_event,
)
from ai_sdlc.core.stage_review.session_close_validation import (
    _validate_close_lineage as validate_close_lineage,
)
from ai_sdlc.core.stage_review.session_contracts import SessionIntegrityError
from ai_sdlc.core.stage_review.session_models import (
    SessionEvent,
    SessionProjectionData,
    StageReviewSession,
)
from ai_sdlc.core.stage_review.session_replan_validation import (
    _validate_replan_delta,
)

_POINTER_CHANGE_EVENTS = frozenset(
    {
        "candidate_updated",
        "risk_fact_enriched",
        "panel_plan_frozen",
        "reviewer_bindings_validated",
        "new_cohort_activated",
    }
)
_RESOURCE_CHANGE_EVENTS = frozenset(
    {
        "review_pass_committed",
        "panel_plan_frozen",
        "reviewer_bindings_validated",
    }
)
_BUDGET_EVENTS = frozenset(
    {
        "budget_grant_requested",
        "budget_grant_applied",
        "budget_grant_reconciled",
        "budget_grant_failed",
    }
)


def reduce_session_events(
    scope: FindingScope,
    events: tuple[SessionEvent, ...],
) -> StageReviewSession | None:
    previous: SessionEvent | None = None
    commands: dict[str, str] = {}
    for sequence, event in enumerate(events, start=1):
        _validate_chain(scope, event, previous, sequence, commands)
        _validate_transition(previous, event)
        previous = event
    if previous is None:
        return None
    return StageReviewSession(
        revision=previous.sequence,
        head_event_id=previous.event_id,
        head_event_digest=previous.event_digest,
        projection=previous.projection_after,
    )


def _validate_chain(
    scope: FindingScope,
    event: SessionEvent,
    previous: SessionEvent | None,
    sequence: int,
    commands: dict[str, str],
) -> None:
    if event.scope != scope or event.sequence != sequence:
        raise SessionIntegrityError("session event scope or sequence mismatch")
    expected_previous = (
        (previous.event_id, previous.event_digest) if previous is not None else ("", "")
    )
    if (event.previous_event_id, event.previous_event_digest) != expected_previous:
        raise SessionIntegrityError("session event chain fork")
    known = commands.setdefault(event.command_id, event.command_digest)
    if known != event.command_digest:
        raise SessionIntegrityError("session command idempotency fork")


def _validate_transition(
    previous_event: SessionEvent | None,
    event: SessionEvent,
) -> None:
    before = previous_event.projection_after if previous_event is not None else None
    after = event.projection_after
    if before is None:
        if event.event_kind != "session_started" or event.sequence != 1:
            raise SessionIntegrityError("session must start with session_started")
        valid_initial_state = after.state in {
            "collecting_initial_reviews",
            "replanning",
        }
        if not valid_initial_state or len(after.cohort_refs) != 1:
            raise SessionIntegrityError("session initial projection is incomplete")
        return
    _validate_invariants(before, after, event)
    _validate_event_delta(before, after, event)


def _validate_invariants(
    before: SessionProjectionData,
    after: SessionProjectionData,
    event: SessionEvent,
) -> None:
    immutable = (
        "scope",
        "policy_digest",
        "optimization_snapshot_digest",
        "risk_profile_lineage_id",
        "resource_reservation_id",
    )
    if any(getattr(before, name) != getattr(after, name) for name in immutable):
        raise SessionIntegrityError("session immutable lineage changed")
    if not before.resource_usage.fits_within(after.resource_usage):
        raise SessionIntegrityError("session resource usage moved backwards")
    if (
        before.resource_usage != after.resource_usage
        and event.event_kind not in _RESOURCE_CHANGE_EVENTS
    ):
        raise SessionIntegrityError("session resource usage changed without charge")
    ledger_events = {"initial_reviews_sealed", "cohort_reviews_sealed"}
    if (
        before.finding_ledger_digest != after.finding_ledger_digest
        and event.event_kind not in ledger_events
    ):
        raise SessionIntegrityError(
            "session finding ledger changed outside a review seal"
        )
    _validate_append_only(before, after)
    _validate_active_pointers(before, after, event)
    _validate_budget_lineage(before, after, event)
    validate_close_lineage(before, after, event)


def _validate_append_only(
    before: SessionProjectionData,
    after: SessionProjectionData,
) -> None:
    for name in (
        "cohort_refs",
        "pass_refs",
        "initial_seal_refs",
        "progress_records",
        "role_replan_counts",
    ):
        prior = getattr(before, name)
        current = getattr(after, name)
        if name != "role_replan_counts" and current[: len(prior)] != prior:
            raise SessionIntegrityError(f"session append-only lineage changed: {name}")
    for name in (
        "sealed_cohort_ids",
        "superseded_cohort_ids",
        "invalidated_pass_ids",
        "revoked_plan_digests",
        "budget_grant_ids",
        "budget_grant_digests",
        "reconciled_budget_grant_ids",
        "reconciled_budget_grant_digests",
    ):
        if not set(getattr(before, name)) <= set(getattr(after, name)):
            raise SessionIntegrityError(
                f"session monotonic set moved backwards: {name}"
            )


def _validate_active_pointers(
    before: SessionProjectionData,
    after: SessionProjectionData,
    event: SessionEvent,
) -> None:
    pointers = (
        before.active_candidate_digest != after.active_candidate_digest,
        before.active_risk_profile_digest != after.active_risk_profile_digest,
        before.active_plan_digest != after.active_plan_digest,
        before.active_binding_set_digest != after.active_binding_set_digest,
        before.active_cohort_id != after.active_cohort_id,
    )
    if any(pointers) and event.event_kind not in _POINTER_CHANGE_EVENTS:
        raise SessionIntegrityError(
            "session active pointer changed without lineage event"
        )


def _validate_budget_lineage(
    before: SessionProjectionData,
    after: SessionProjectionData,
    event: SessionEvent,
) -> None:
    protected = (
        "budget_revision",
        "budget_grant_ids",
        "budget_grant_digests",
        "reconciled_budget_grant_ids",
        "reconciled_budget_grant_digests",
        "last_budget_grant_operation_id",
        "budget_grant_operation_effect_digest",
        "resource_fencing_epoch",
    )
    changed = any(getattr(before, name) != getattr(after, name) for name in protected)
    transition_events = {"budget_grant_applied", "budget_grant_reconciled"}
    if changed and event.event_kind not in transition_events:
        raise SessionIntegrityError("session budget lineage changed outside grant close")
    pending_changed = (
        before.pending_budget_grant_command_id
        != after.pending_budget_grant_command_id
    )
    if pending_changed and event.event_kind not in {
        "budget_grant_requested",
        "budget_grant_applied",
        "budget_grant_reconciled",
        "budget_grant_failed",
    }:
        raise SessionIntegrityError("session pending budget grant changed unexpectedly")
    resume_changed = before.budget_resume_state != after.budget_resume_state
    resume_events = {
        "review_pass_committed",
        "initial_reviews_sealed",
        "cohort_reviews_sealed",
        "new_cohort_activated",
        "budget_grant_applied",
        "budget_grant_reconciled",
        "budget_grant_failed",
    }
    if resume_changed and event.event_kind not in resume_events:
        raise SessionIntegrityError("session budget resume state changed unexpectedly")


def _validate_event_delta(
    before: SessionProjectionData,
    after: SessionProjectionData,
    event: SessionEvent,
) -> None:
    kind = event.event_kind
    if kind in CLOSE_EVENT_KINDS:
        validate_close_event(before, after, event)
        return
    if kind in _BUDGET_EVENTS:
        _validate_budget_event(before, after, event)
        return
    if kind == "review_pass_committed":
        _require_growth(before.pass_refs, after.pass_refs, 1, "review pass")
    elif kind in {"initial_reviews_sealed", "cohort_reviews_sealed"}:
        if len(after.sealed_cohort_ids) != len(before.sealed_cohort_ids) + 1:
            raise SessionIntegrityError("session cohort seal delta is invalid")
    elif kind == "progress_recorded":
        _require_growth(before.progress_records, after.progress_records, 1, "progress")
    elif kind == "cohort_superseded":
        if len(after.superseded_cohort_ids) != len(before.superseded_cohort_ids) + 1:
            raise SessionIntegrityError("session cohort supersession delta is invalid")
    elif kind == "old_passes_invalidated":
        if not set(_pass_refs_for(before, before.active_cohort_id)) <= set(
            after.invalidated_pass_ids
        ):
            raise SessionIntegrityError("session old cohort passes remain valid")
    elif kind == "panel_plan_frozen":
        if before.active_plan_digest == after.active_plan_digest:
            raise SessionIntegrityError("session replan did not freeze a new plan")
        _validate_replan_delta(before, after)
    elif kind == "reviewer_bindings_validated":
        if before.active_binding_set_digest == after.active_binding_set_digest:
            raise SessionIntegrityError("session binding event did not change binding")
    elif kind == "new_cohort_activated":
        _require_growth(before.cohort_refs, after.cohort_refs, 1, "cohort")
        if before.active_cohort_id == after.active_cohort_id:
            raise SessionIntegrityError("session cohort activation did not advance")
    elif kind == "macro_rebaseline_requested":
        if after.macro_rebaseline_request is None:
            raise SessionIntegrityError("session macro request is missing")
    elif kind == "reviewer_plan_revoked":
        if after.state != "blocked" or not set(after.revoked_plan_digests) - set(
            before.revoked_plan_digests
        ):
            raise SessionIntegrityError("session plan revocation is ineffective")
    elif kind == "user_decision_required" and after.state != "needs_user":
        raise SessionIntegrityError("session user decision event is not blocking")


def _validate_budget_event(
    before: SessionProjectionData,
    after: SessionProjectionData,
    event: SessionEvent,
) -> None:
    if event.event_kind == "budget_grant_requested":
        if (
            before.state != "needs_user"
            or before.budget_resume_state is None
            or before.pending_budget_grant_command_id
            or after.pending_budget_grant_command_id != event.command_id
            or after.budget_revision != before.budget_revision
        ):
            raise SessionIntegrityError("session budget grant request is invalid")
    elif event.event_kind == "budget_grant_applied":
        _validate_budget_grant_applied(before, after)
    elif event.event_kind == "budget_grant_reconciled":
        _validate_budget_grant_reconciled(before, after)
    else:
        _validate_budget_grant_failed(before, after)


def _validate_budget_grant_applied(
    before: SessionProjectionData,
    after: SessionProjectionData,
) -> None:
    expected = (
        before.state == "needs_user",
        before.budget_resume_state is not None,
        bool(before.pending_budget_grant_command_id),
        after.state == before.budget_resume_state,
        after.budget_resume_state is None,
        not after.pending_budget_grant_command_id,
        after.budget_revision == before.budget_revision + 1,
        len(after.budget_grant_ids) == len(before.budget_grant_ids) + 1,
        len(after.budget_grant_digests) == len(before.budget_grant_digests) + 1,
        after.resource_fencing_epoch > before.resource_fencing_epoch,
        after.resource_usage == before.resource_usage,
        bool(after.last_budget_grant_operation_id),
        bool(after.budget_grant_operation_effect_digest),
    )
    if not all(expected):
        raise SessionIntegrityError("session budget grant apply delta is invalid")


def _validate_budget_grant_reconciled(
    before: SessionProjectionData,
    after: SessionProjectionData,
) -> None:
    expected = (
        before.state == "needs_user",
        before.budget_resume_state is not None,
        bool(before.pending_budget_grant_command_id),
        after.state == "needs_user",
        after.budget_resume_state == before.budget_resume_state,
        not after.pending_budget_grant_command_id,
        after.budget_revision == before.budget_revision + 1,
        len(after.reconciled_budget_grant_ids)
        == len(before.reconciled_budget_grant_ids) + 1,
        len(after.reconciled_budget_grant_digests)
        == len(before.reconciled_budget_grant_digests) + 1,
        after.budget_grant_ids == before.budget_grant_ids,
        after.budget_grant_digests == before.budget_grant_digests,
        after.resource_fencing_epoch > before.resource_fencing_epoch,
        before.resource_usage.fits_within(after.resource_usage),
        bool(after.last_budget_grant_operation_id),
        bool(after.budget_grant_operation_effect_digest),
    )
    if not all(expected):
        raise SessionIntegrityError("session budget grant reconcile delta is invalid")


def _validate_budget_grant_failed(
    before: SessionProjectionData,
    after: SessionProjectionData,
) -> None:
    state_valid = after.state == "needs_user" and (
        after.budget_resume_state == before.budget_resume_state
    )
    if after.state == "blocked":
        state_valid = after.budget_resume_state is None
    expected = (
        before.state == "needs_user",
        before.budget_resume_state is not None,
        bool(before.pending_budget_grant_command_id),
        not after.pending_budget_grant_command_id,
        bool(after.budget_grant_failure_code),
        state_valid,
        after.budget_revision == before.budget_revision,
        after.budget_grant_ids == before.budget_grant_ids,
        after.reconciled_budget_grant_ids == before.reconciled_budget_grant_ids,
        after.resource_fencing_epoch == before.resource_fencing_epoch,
        after.resource_usage == before.resource_usage,
    )
    if not all(expected):
        raise SessionIntegrityError("session budget grant failure delta is invalid")


def _require_growth(
    before: tuple[object, ...], after: tuple[object, ...], count: int, label: str
) -> None:
    if len(after) != len(before) + count:
        raise SessionIntegrityError(f"session {label} delta is invalid")


def _pass_refs_for(
    projection: SessionProjectionData,
    cohort_id: str,
) -> tuple[str, ...]:
    return tuple(
        item.pass_id for item in projection.pass_refs if item.cohort_id == cohort_id
    )
