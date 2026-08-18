"""Bounded review outcomes stored directly beside existing Loop artifacts."""

from __future__ import annotations

import json
import os
import re
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from ai_sdlc.core.loop_models import utc_now_iso
from ai_sdlc.core.loop_review_models import LoopReviewOutcome, ReviewStatusOverlay
from ai_sdlc.core.review_kernel import (
    LoopReviewType,
    ReviewExecution,
    ReviewInput,
    merge_expert_findings,
)
from ai_sdlc.core.stable_file_read import read_stable_text

ReviewInputResolver = Callable[[int], ReviewInput]
_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class LoopReviewServiceError(ValueError):
    """A bounded review transition cannot proceed."""

    def __init__(
        self,
        reason: str,
        *,
        detail: str = "",
        expected_digest: str = "",
        actual_digest: str = "",
    ) -> None:
        super().__init__(reason)
        self.reason = reason
        self.detail = detail
        self.expected_digest = expected_digest
        self.actual_digest = actual_digest

    def payload(self) -> dict[str, object]:
        payload: dict[str, object] = {"status": "blocked", "reason": self.reason}
        if self.detail:
            payload["detail"] = self.detail
        if self.expected_digest:
            payload["expected_digest"] = self.expected_digest
        if self.actual_digest:
            payload["actual_digest"] = self.actual_digest
        return payload


@dataclass(frozen=True)
class LoopReviewPreparation:
    """Current input plus derived review display state."""

    review_input: ReviewInput
    overlay: ReviewStatusOverlay
    current_outcome: LoopReviewOutcome | None
    outcome_path: Path

    @property
    def status(self) -> str:
        return self.overlay.status

    @property
    def reason(self) -> str:
        return self.overlay.reason

    @property
    def next_action(self) -> str:
        return self.overlay.next_action


@dataclass(frozen=True)
class RecordLoopReviewOptions:
    """Inputs required to record one bounded review outcome."""

    root: Path
    loop_type: LoopReviewType
    loop_id: str
    expected_digest: str
    result_paths: tuple[Path, ...]


def outcome_path(loop_dir: Path, round_number: int) -> Path:
    """Return one of the only two allowed outcome paths."""

    if round_number not in {1, 2}:
        raise LoopReviewServiceError("review-round-limit")
    return loop_dir / f"review-outcome-round-{round_number}.json"


def prepare_loop_review(
    root: Path,
    *,
    loop_type: LoopReviewType,
    loop_id: str,
    loop_dir: Path,
    input_resolver: ReviewInputResolver,
) -> LoopReviewPreparation:
    """Derive the current review round without writing Loop state."""

    _validate_identity(root, loop_type, loop_id, loop_dir)
    first_path = outcome_path(loop_dir, 1)
    second_path = outcome_path(loop_dir, 2)
    first = _read_outcome(root, first_path, loop_type, loop_id, 1)
    second = _read_outcome(root, second_path, loop_type, loop_id, 2)
    if second is not None and first is None:
        raise LoopReviewServiceError("review-outcome-sequence-invalid")

    first_input = input_resolver(1)
    _require_input_identity(first_input, loop_type, loop_id, 1)
    if first is None:
        return _preparation(
            first_input,
            None,
            first_path,
            status="review_missing",
            reason="review-result-missing",
            next_action="Run the selected independent experts and record round 1.",
        )

    if first.status == "failed":
        if first.input_digest != first_input.input_digest:
            return _drifted_preparation(first_input, first, first_path)
        return _preparation(
            first_input,
            first,
            first_path,
            status="failed",
            reason="review-execution-failed",
            next_action="Retry the same independent expert round on unchanged input.",
        )

    first_actionable = has_actionable_findings(first)
    if not first_actionable:
        if first.input_digest != first_input.input_digest:
            return _drifted_preparation(first_input, first, first_path)
        if second is not None:
            raise LoopReviewServiceError("review-outcome-sequence-invalid")
        return _preparation(
            first_input,
            first,
            first_path,
            status="passed",
            reason="review-passed",
            next_action="Close the unchanged Loop result.",
        )

    if first.input_digest == first_input.input_digest:
        if second is not None:
            raise LoopReviewServiceError("review-outcome-sequence-invalid")
        return _preparation(
            first_input,
            first,
            first_path,
            status="needs_fix",
            reason="review-findings-actionable",
            next_action="Resolve the actionable findings before round 2.",
        )

    second_input = input_resolver(2)
    _require_input_identity(second_input, loop_type, loop_id, 2)
    if second is None:
        return _preparation(
            second_input,
            None,
            second_path,
            status="review_missing",
            reason="review-result-missing",
            next_action="Run the selected independent experts and record round 2.",
        )
    if second.input_digest != second_input.input_digest:
        return _drifted_preparation(second_input, second, second_path)
    if second.status == "failed":
        return _preparation(
            second_input,
            second,
            second_path,
            status="failed",
            reason="review-execution-failed",
            next_action="Retry the same independent expert round on unchanged input.",
        )
    if has_actionable_findings(second):
        return _preparation(
            second_input,
            second,
            second_path,
            status="needs_user",
            reason="review-round-limit",
            next_action="Inspect the unresolved findings; a third review round is forbidden.",
        )
    return _preparation(
        second_input,
        second,
        second_path,
        status="passed",
        reason="review-passed",
        next_action="Close the unchanged Loop result.",
    )


def record_loop_review(
    options: RecordLoopReviewOptions,
    *,
    loop_dir: Path,
    input_resolver: ReviewInputResolver,
) -> ReviewStatusOverlay:
    """Validate independent result files and record exactly one current outcome."""

    prepared = prepare_loop_review(
        options.root,
        loop_type=options.loop_type,
        loop_id=options.loop_id,
        loop_dir=loop_dir,
        input_resolver=input_resolver,
    )
    expected = options.expected_digest.strip().lower()
    if expected != prepared.review_input.input_digest:
        raise LoopReviewServiceError(
            "review-input-drift",
            expected_digest=expected,
            actual_digest=prepared.review_input.input_digest,
        )
    if prepared.status not in {"review_missing", "failed"}:
        if prepared.reason == "review-input-drift":
            raise LoopReviewServiceError("review-input-drift")
        if prepared.review_input.round_number == 2:
            raise LoopReviewServiceError("review-round-limit")
        if prepared.status == "needs_fix":
            raise LoopReviewServiceError("review-input-unchanged")
        raise LoopReviewServiceError("review-already-completed")

    executions = _read_executions(options.root, options.result_paths)
    roles = [execution.roles[0] for execution in executions]
    if len(roles) != len(set(roles)) or set(roles) != set(
        prepared.review_input.expert_roles
    ):
        raise LoopReviewServiceError("expert-role-mismatch")
    for execution in executions:
        role = execution.roles[0]
        if execution.role_reasons[role] != prepared.review_input.expert_reasons[role]:
            raise LoopReviewServiceError("expert-role-mismatch")

    merged = merge_expert_findings(executions)
    outcome = LoopReviewOutcome(
        loop_id=options.loop_id,
        loop_type=options.loop_type,
        round_number=prepared.review_input.round_number,
        input_digest=prepared.review_input.input_digest,
        status=merged.status,
        expert_roles=prepared.review_input.expert_roles,
        findings=merged.findings,
        failure_kind=merged.failure_kind,
        failure_reason=merged.failure_reason,
        recorded_at=utc_now_iso(),
    )
    _write_outcome(options.root, prepared.outcome_path, outcome)
    return _overlay_for_outcome(outcome)


def validate_prepared_outcome_for_close(
    prepared: LoopReviewPreparation,
    *,
    expected_digest: str,
) -> ReviewInput:
    """Require a current clean/advisory completed outcome before Close."""

    expected = expected_digest.strip().lower()
    if expected != prepared.review_input.input_digest:
        raise LoopReviewServiceError(
            "review-input-drift",
            expected_digest=expected,
            actual_digest=prepared.review_input.input_digest,
        )
    if prepared.current_outcome is None:
        raise LoopReviewServiceError("review-result-missing")
    if prepared.current_outcome.input_digest != prepared.review_input.input_digest:
        raise LoopReviewServiceError("review-input-drift")
    if prepared.status != "passed":
        raise LoopReviewServiceError(prepared.reason)
    return prepared.review_input


def has_actionable_findings(outcome: LoopReviewOutcome) -> bool:
    """Return whether an outcome contains a blocker or important finding."""

    return any(
        finding.severity in {"blocker", "important"} for finding in outcome.findings
    )


def _read_executions(root: Path, paths: tuple[Path, ...]) -> list[ReviewExecution]:
    if not paths or len(paths) > 2:
        raise LoopReviewServiceError("expert-role-mismatch")
    executions: list[ReviewExecution] = []
    for path in paths:
        try:
            execution = ReviewExecution.model_validate_json(
                read_stable_text(root, path, encoding="utf-8")
            )
        except (OSError, UnicodeError, ValidationError) as exc:
            raise LoopReviewServiceError(
                "review-result-invalid", detail=str(exc)
            ) from exc
        if len(execution.roles) != 1:
            raise LoopReviewServiceError("expert-role-mismatch")
        executions.append(execution)
    return executions


def _read_outcome(
    root: Path,
    path: Path,
    loop_type: LoopReviewType,
    loop_id: str,
    round_number: int,
) -> LoopReviewOutcome | None:
    if not path.exists():
        return None
    try:
        outcome = LoopReviewOutcome.model_validate_json(
            read_stable_text(root, path, encoding="utf-8")
        )
    except (OSError, UnicodeError, ValidationError) as exc:
        raise LoopReviewServiceError("review-outcome-invalid", detail=str(exc)) from exc
    if (
        outcome.loop_type != loop_type
        or outcome.loop_id != loop_id
        or outcome.round_number != round_number
    ):
        raise LoopReviewServiceError("review-outcome-identity-mismatch")
    return outcome


def _write_outcome(root: Path, path: Path, outcome: LoopReviewOutcome) -> None:
    existing = _read_outcome(
        root,
        path,
        outcome.loop_type,
        outcome.loop_id,
        outcome.round_number,
    )
    if existing is not None:
        if existing.status == "completed":
            raise LoopReviewServiceError(
                "review-round-limit"
                if outcome.round_number == 2
                else "review-already-completed"
            )
        if existing.input_digest != outcome.input_digest:
            raise LoopReviewServiceError("review-input-drift")

    encoded = (
        json.dumps(
            outcome.model_dump(mode="json"),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        latest = _read_outcome(
            root,
            path,
            outcome.loop_type,
            outcome.loop_id,
            outcome.round_number,
        )
        if latest is not None:
            if latest.status == "completed":
                raise LoopReviewServiceError(
                    "review-round-limit"
                    if outcome.round_number == 2
                    else "review-already-completed"
                )
            if latest.input_digest != outcome.input_digest:
                raise LoopReviewServiceError("review-input-drift")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _validate_identity(
    root: Path,
    loop_type: LoopReviewType,
    loop_id: str,
    loop_dir: Path,
) -> None:
    if _SAFE_IDENTIFIER.fullmatch(loop_id) is None:
        raise LoopReviewServiceError("review-loop-identity-invalid")
    resolved_root = root.resolve(strict=True)
    resolved_loop_dir = loop_dir.resolve(strict=True)
    try:
        resolved_loop_dir.relative_to(resolved_root)
    except ValueError as exc:
        raise LoopReviewServiceError("review-loop-directory-invalid") from exc
    if loop_type != "local-pr-review":
        expected = resolved_root / ".ai-sdlc" / "loops" / loop_type / loop_id
        if resolved_loop_dir != expected:
            raise LoopReviewServiceError("review-loop-directory-invalid")


def _require_input_identity(
    review_input: ReviewInput,
    loop_type: LoopReviewType,
    loop_id: str,
    round_number: int,
) -> None:
    if (
        review_input.loop_type != loop_type
        or review_input.loop_id != loop_id
        or review_input.round_number != round_number
    ):
        raise LoopReviewServiceError("review-input-identity-mismatch")


def _preparation(
    review_input: ReviewInput,
    outcome: LoopReviewOutcome | None,
    path: Path,
    *,
    status: str,
    reason: str,
    next_action: str,
) -> LoopReviewPreparation:
    return LoopReviewPreparation(
        review_input=review_input,
        overlay=ReviewStatusOverlay(
            status=status,
            reason=reason,
            next_action=next_action,
            round_number=review_input.round_number,
        ),
        current_outcome=outcome,
        outcome_path=path,
    )


def _drifted_preparation(
    review_input: ReviewInput,
    outcome: LoopReviewOutcome,
    path: Path,
) -> LoopReviewPreparation:
    return _preparation(
        review_input,
        outcome,
        path,
        status="needs_user",
        reason="review-input-drift",
        next_action="The reviewed input changed outside the allowed repair transition.",
    )


def _overlay_for_outcome(outcome: LoopReviewOutcome) -> ReviewStatusOverlay:
    if outcome.status == "failed":
        return ReviewStatusOverlay(
            status="failed",
            reason="review-execution-failed",
            next_action="Retry the same independent expert round on unchanged input.",
            round_number=outcome.round_number,
        )
    if has_actionable_findings(outcome):
        if outcome.round_number == 1:
            return ReviewStatusOverlay(
                status="needs_fix",
                reason="review-findings-actionable",
                next_action="Resolve the actionable findings before round 2.",
                round_number=1,
            )
        return ReviewStatusOverlay(
            status="needs_user",
            reason="review-round-limit",
            next_action="Inspect the unresolved findings; a third round is forbidden.",
            round_number=2,
        )
    return ReviewStatusOverlay(
        status="passed",
        reason="review-passed",
        next_action="Close the unchanged Loop result.",
        round_number=outcome.round_number,
    )


__all__ = [
    "LoopReviewPreparation",
    "LoopReviewServiceError",
    "RecordLoopReviewOptions",
    "ReviewInputResolver",
    "has_actionable_findings",
    "outcome_path",
    "prepare_loop_review",
    "record_loop_review",
    "validate_prepared_outcome_for_close",
]
