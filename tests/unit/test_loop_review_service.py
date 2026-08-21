"""Tests for the bounded Loop-native review outcome contract."""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from ai_sdlc.cli.loop_review_cmd import resolve_review_input
from ai_sdlc.core.loop_review_models import LoopReviewOutcome, ReviewStatusOverlay
from ai_sdlc.core.loop_review_service import (
    LoopReviewPreparation,
    LoopReviewServiceError,
    RecordLoopReviewOptions,
    prepare_loop_review,
    record_loop_review,
    validate_prepared_outcome_for_close,
)
from ai_sdlc.core.review_kernel import ReviewExecution, ReviewFinding


@dataclass(frozen=True)
class LoopFixture:
    root: Path
    loop_id: str
    loop_dir: Path

    def resolve_input(self, round_number: int):
        return resolve_review_input(
            self.root,
            loop_type="requirement",
            loop_id=self.loop_id,
            review_round_number=round_number,
        )

    def prepare(self) -> LoopReviewPreparation:
        return prepare_loop_review(
            self.root,
            loop_type="requirement",
            loop_id=self.loop_id,
            loop_dir=self.loop_dir,
            input_resolver=self.resolve_input,
        )


@pytest.fixture
def loop_fixture(tmp_path: Path) -> LoopFixture:
    loop_id = "requirement-review-contract"
    loop_dir = tmp_path / ".ai-sdlc" / "loops" / "requirement" / loop_id
    loop_dir.mkdir(parents=True)
    (loop_dir / "loop-run.json").write_text(
        json.dumps(
            {
                "loop_id": loop_id,
                "loop_type": "requirement",
                "current_round": 1,
            }
        ),
        encoding="utf-8",
    )
    pointer = loop_dir.parent / "current-requirement.json"
    pointer.write_text(
        json.dumps(
            {
                "loop_id": loop_id,
                "loop_run_path": (
                    f".ai-sdlc/loops/requirement/{loop_id}/loop-run.json"
                ),
            }
        ),
        encoding="utf-8",
    )
    (loop_dir / "requirement-intake.json").write_text("{}", encoding="utf-8")
    (loop_dir / "requirement-brief.md").write_text(
        "Security permission requirement.\n",
        encoding="utf-8",
    )
    (loop_dir / "clarification-questions.md").write_text(
        "No open questions.\n",
        encoding="utf-8",
    )
    (loop_dir / "acceptance-checklist.md").write_text(
        "- Permission is verified.\n",
        encoding="utf-8",
    )
    return LoopFixture(root=tmp_path, loop_id=loop_id, loop_dir=loop_dir)


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


def _result_paths(
    fixture: LoopFixture,
    prepared: LoopReviewPreparation,
    *,
    severity: str | None = None,
    failed: bool = False,
    roles: list[str] | None = None,
    name_prefix: str = "expert",
) -> tuple[Path, ...]:
    selected = roles or prepared.review_input.expert_roles
    paths: list[Path] = []
    for index, role in enumerate(selected):
        result_path = fixture.root / (
            f"{name_prefix}-{prepared.review_input.round_number}-{index}.json"
        )
        if failed:
            execution = ReviewExecution(
                status="failed",
                roles=[role],
                role_reasons={role: prepared.review_input.expert_reasons[role]},
                failure_kind="reviewer-exited",
                failure_reason="The independent reviewer exited nonzero.",
            )
        else:
            findings = []
            if severity is not None:
                findings.append(
                    ReviewFinding(
                        severity=severity,
                        role=role,
                        location="requirement-brief.md:1",
                        summary="The requirement still has an actionable gap.",
                        recommendation="Revise the requirement before Close.",
                    )
                )
            execution = ReviewExecution(
                status="completed",
                roles=[role],
                role_reasons={role: prepared.review_input.expert_reasons[role]},
                findings=findings,
            )
        result_path.write_text(execution.model_dump_json(), encoding="utf-8")
        paths.append(result_path)
    return tuple(paths)


def _record(
    fixture: LoopFixture,
    prepared: LoopReviewPreparation,
    result_paths: tuple[Path, ...],
):
    return record_loop_review(
        RecordLoopReviewOptions(
            root=fixture.root,
            loop_type="requirement",
            loop_id=fixture.loop_id,
            expected_digest=prepared.review_input.input_digest,
            result_paths=result_paths,
        ),
        loop_dir=fixture.loop_dir,
        input_resolver=fixture.resolve_input,
    )


def _prepare_round_two(fixture: LoopFixture) -> LoopReviewPreparation:
    first = fixture.prepare()
    first_record = _record(
        fixture,
        first,
        _result_paths(fixture, first, severity="important"),
    )
    assert first_record.status == "needs_fix"
    with (fixture.loop_dir / "requirement-brief.md").open(
        "a", encoding="utf-8"
    ) as stream:
        stream.write("Repair after round one.\n")
    second = fixture.prepare()
    assert second.review_input.round_number == 2
    assert second.status == "review_missing"
    return second


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


def test_matching_digest_without_outcome_cannot_close(
    loop_fixture: LoopFixture,
) -> None:
    prepared = loop_fixture.prepare()

    assert prepared.status == "review_missing"
    with pytest.raises(LoopReviewServiceError, match="review-result-missing"):
        validate_prepared_outcome_for_close(
            prepared,
            expected_digest=prepared.review_input.input_digest,
        )


def test_failed_outcome_cannot_close(loop_fixture: LoopFixture) -> None:
    prepared = loop_fixture.prepare()
    _record(
        loop_fixture,
        prepared,
        _result_paths(loop_fixture, prepared, failed=True),
    )
    current = loop_fixture.prepare()

    with pytest.raises(LoopReviewServiceError, match="review-execution-failed"):
        validate_prepared_outcome_for_close(
            current,
            expected_digest=current.review_input.input_digest,
        )


def test_actionable_outcome_cannot_close(loop_fixture: LoopFixture) -> None:
    prepared = loop_fixture.prepare()
    _record(
        loop_fixture,
        prepared,
        _result_paths(loop_fixture, prepared, severity="important"),
    )
    current = loop_fixture.prepare()

    with pytest.raises(LoopReviewServiceError, match="review-findings-actionable"):
        validate_prepared_outcome_for_close(
            current,
            expected_digest=current.review_input.input_digest,
        )


def test_stale_completed_outcome_cannot_close(loop_fixture: LoopFixture) -> None:
    prepared = loop_fixture.prepare()
    _record(loop_fixture, prepared, _result_paths(loop_fixture, prepared))
    with (loop_fixture.loop_dir / "requirement-brief.md").open(
        "a", encoding="utf-8"
    ) as stream:
        stream.write("Unreviewed change.\n")
    current = loop_fixture.prepare()

    with pytest.raises(LoopReviewServiceError, match="review-input-drift"):
        validate_prepared_outcome_for_close(
            current,
            expected_digest=prepared.review_input.input_digest,
        )


def test_wrong_role_outcome_cannot_close(loop_fixture: LoopFixture) -> None:
    prepared = loop_fixture.prepare()
    outcome = LoopReviewOutcome(
        loop_id=loop_fixture.loop_id,
        loop_type="requirement",
        round_number=1,
        input_digest=prepared.review_input.input_digest,
        status="completed",
        expert_roles=[prepared.review_input.expert_roles[0]],
        findings=[],
        recorded_at="2026-08-17T00:00:00Z",
    )
    (loop_fixture.loop_dir / "review-outcome-round-1.json").write_text(
        outcome.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )
    current = loop_fixture.prepare()

    with pytest.raises(LoopReviewServiceError, match="expert-role-mismatch"):
        validate_prepared_outcome_for_close(
            current,
            expected_digest=current.review_input.input_digest,
        )


def test_record_rejects_missing_selected_expert(loop_fixture: LoopFixture) -> None:
    prepared = loop_fixture.prepare()
    assert len(prepared.review_input.expert_roles) == 2
    paths = _result_paths(
        loop_fixture,
        prepared,
        roles=[prepared.review_input.expert_roles[0]],
    )

    with pytest.raises(LoopReviewServiceError, match="expert-role-mismatch"):
        _record(loop_fixture, prepared, paths)


def test_completed_clean_round_one_cannot_be_recorded_again(
    loop_fixture: LoopFixture,
) -> None:
    prepared = loop_fixture.prepare()
    paths = _result_paths(loop_fixture, prepared)

    first = _record(loop_fixture, prepared, paths)
    assert first.status == "passed"
    with pytest.raises(LoopReviewServiceError, match="review-already-completed"):
        _record(loop_fixture, prepared, paths)


def test_concurrent_completed_round_one_has_exactly_one_winner(
    loop_fixture: LoopFixture,
) -> None:
    prepared = loop_fixture.prepare()
    clean_paths = _result_paths(
        loop_fixture,
        prepared,
        name_prefix="clean-expert",
    )
    actionable_paths = _result_paths(
        loop_fixture,
        prepared,
        severity="important",
        name_prefix="actionable-expert",
    )
    start = threading.Barrier(2)
    replace_guard = threading.Lock()
    first_replace = threading.Event()
    second_replace = threading.Event()
    replacement_count = 0
    successes: list[tuple[str, bytes]] = []
    errors: list[str] = []
    result_guard = threading.Lock()
    outcome = loop_fixture.loop_dir / "review-outcome-round-1.json"
    real_replace = __import__("os").replace

    def synchronized_replace(source: Path, destination: Path) -> None:
        nonlocal replacement_count
        with replace_guard:
            replacement_count += 1
            ordinal = replacement_count
        if ordinal == 1:
            first_replace.set()
            second_replace.wait(timeout=0.5)
        elif ordinal == 2:
            second_replace.set()
        real_replace(source, destination)

    def record(name: str, paths: tuple[Path, ...]) -> None:
        start.wait()
        try:
            _record(loop_fixture, prepared, paths)
        except LoopReviewServiceError as exc:
            with result_guard:
                errors.append(exc.reason)
            return
        with result_guard:
            successes.append((name, outcome.read_bytes()))

    with patch(
        "ai_sdlc.core.loop_review_service.os.replace",
        side_effect=synchronized_replace,
    ):
        clean = threading.Thread(target=record, args=("clean", clean_paths))
        actionable = threading.Thread(
            target=record,
            args=("actionable", actionable_paths),
        )
        clean.start()
        actionable.start()
        clean.join(timeout=5)
        actionable.join(timeout=5)

    assert not clean.is_alive()
    assert not actionable.is_alive()
    assert first_replace.is_set()
    assert replacement_count == 1
    assert len(successes) == 1
    assert len(errors) == 1
    assert errors[0] in {
        "review-already-completed",
        "review-outcome-lock-unavailable",
    }
    assert outcome.read_bytes() == successes[0][1]


@pytest.mark.parametrize("severity", [None, "advisory", "important"])
def test_completed_round_two_cannot_be_recorded_again(
    loop_fixture: LoopFixture,
    severity: str | None,
) -> None:
    prepared = _prepare_round_two(loop_fixture)
    paths = _result_paths(loop_fixture, prepared, severity=severity)

    completed = _record(loop_fixture, prepared, paths)
    assert completed.status in {"passed", "needs_user"}
    with pytest.raises(LoopReviewServiceError, match="review-round-limit"):
        _record(loop_fixture, prepared, paths)


@pytest.mark.parametrize("round_number", [1, 2])
def test_failed_round_can_retry_only_same_digest(
    loop_fixture: LoopFixture,
    round_number: int,
) -> None:
    prepared = (
        loop_fixture.prepare()
        if round_number == 1
        else _prepare_round_two(loop_fixture)
    )
    failed = _record(
        loop_fixture,
        prepared,
        _result_paths(loop_fixture, prepared, failed=True),
    )
    assert failed.status == "failed"

    retry = _record(loop_fixture, prepared, _result_paths(loop_fixture, prepared))
    assert retry.status == "passed"


@pytest.mark.parametrize("round_number", [1, 2])
def test_failed_round_stale_retry_preserves_failed_outcome(
    loop_fixture: LoopFixture,
    round_number: int,
) -> None:
    prepared = (
        loop_fixture.prepare()
        if round_number == 1
        else _prepare_round_two(loop_fixture)
    )
    failed = _record(
        loop_fixture,
        prepared,
        _result_paths(loop_fixture, prepared, failed=True),
    )
    outcome_path = loop_fixture.loop_dir / f"review-outcome-round-{round_number}.json"
    failed_bytes = outcome_path.read_bytes()
    assert failed.status == "failed"

    with (loop_fixture.loop_dir / "requirement-brief.md").open(
        "a", encoding="utf-8"
    ) as stream:
        stream.write("Changed after failed review.\n")
    stale = loop_fixture.prepare()
    with pytest.raises(LoopReviewServiceError, match="review-input-drift"):
        _record(loop_fixture, stale, _result_paths(loop_fixture, stale))
    assert outcome_path.read_bytes() == failed_bytes
