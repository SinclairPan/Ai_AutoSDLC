"""Tests for the bounded Loop-native review outcome contract."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from ai_sdlc.core.loop_review_models import LoopReviewOutcome, ReviewStatusOverlay
from ai_sdlc.core.review_kernel import ReviewFinding


def _finding(*, role: str = "correctness-and-regression") -> ReviewFinding:
    return ReviewFinding(
        severity="important",
        role=role,
        location="implementation-report.md:10",
        summary="A required regression remains unresolved.",
        recommendation="Add a focused regression before Close.",
    )


def _outcome_payload() -> dict[str, object]:
    return {
        "loop_id": "implementation-1",
        "loop_type": "implementation",
        "round_number": 1,
        "input_digest": "a" * 64,
        "status": "completed",
        "expert_roles": ["correctness-and-regression"],
        "findings": [],
        "failure_kind": "",
        "failure_reason": "",
        "recorded_at": "2026-08-18T00:00:00Z",
    }


def test_loop_review_outcome_accepts_minimal_completed_shape() -> None:
    outcome = LoopReviewOutcome.model_validate(_outcome_payload())

    assert outcome.round_number == 1
    assert outcome.status == "completed"
    assert outcome.expert_roles == ["correctness-and-regression"]


@pytest.mark.parametrize("round_number", [0, 3])
def test_loop_review_outcome_allows_only_two_rounds(round_number: int) -> None:
    payload = _outcome_payload()
    payload["round_number"] = round_number

    with pytest.raises(ValidationError):
        LoopReviewOutcome.model_validate(payload)


def test_loop_review_outcome_rejects_third_expert() -> None:
    payload = _outcome_payload()
    payload["expert_roles"] = ["one", "two", "three"]

    with pytest.raises(ValidationError):
        LoopReviewOutcome.model_validate(payload)


def test_loop_review_outcome_rejects_finding_from_unselected_role() -> None:
    payload = _outcome_payload()
    payload["findings"] = [_finding(role="security-and-permissions")]

    with pytest.raises(ValidationError):
        LoopReviewOutcome.model_validate(payload)


@pytest.mark.parametrize(
    "field",
    [
        "authorization",
        "certificate",
        "session",
        "quorum",
        "score",
        "policy_digest",
        "approved_by",
    ],
)
def test_loop_review_outcome_rejects_credential_fields(field: str) -> None:
    payload = _outcome_payload()
    payload[field] = "forbidden"

    with pytest.raises(ValidationError):
        LoopReviewOutcome.model_validate(payload)


def test_loop_review_outcome_requires_failure_details_for_failed_status() -> None:
    payload = _outcome_payload()
    payload["status"] = "failed"

    with pytest.raises(ValidationError):
        LoopReviewOutcome.model_validate(payload)


def test_loop_review_outcome_rejects_failure_details_when_completed() -> None:
    payload = _outcome_payload()
    payload["failure_kind"] = "provider-failed"
    payload["failure_reason"] = "reviewer exited nonzero"

    with pytest.raises(ValidationError):
        LoopReviewOutcome.model_validate(payload)


def test_review_status_overlay_is_display_state_not_close_credential() -> None:
    overlay = ReviewStatusOverlay(
        status="needs_fix",
        reason="review-findings-actionable",
        next_action="Resolve the recorded findings and prepare round 2.",
        round_number=1,
    )

    assert set(overlay.model_dump()) == {
        "status",
        "reason",
        "next_action",
        "round_number",
    }
