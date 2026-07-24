"""Stage Review 阻断对普通用户的稳定 Result/Next 映射。"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Protocol, TypeVar

import typer

from ai_sdlc.core.stage_review.artifacts import resolve_repository_project_id
from ai_sdlc.core.stage_review.resource_builders import stable_id
from ai_sdlc.core.stage_review.stage_review_execution import (
    StageCloseGateUnavailableError,
)

_RESULT = TypeVar("_RESULT")


class StageClosePayloadEmitter(Protocol):
    def __call__(
        self,
        payload: dict[str, object],
        *,
        json_output: bool,
    ) -> None: ...


def execute_stage_close_for_cli(
    root: Path,
    action: Callable[[], _RESULT],
    *,
    json_output: bool,
    emit: StageClosePayloadEmitter,
) -> _RESULT:
    try:
        return action()
    except StageCloseGateUnavailableError as exc:
        emit(
            _stage_close_failure_payload(root, str(exc)),
            json_output=json_output,
        )
        raise typer.Exit(code=2) from None


def _stage_close_failure_payload(root: Path, reason_code: str) -> dict[str, object]:
    reason = reason_code.strip() or "review-runtime-integrity-failure"
    status, stable_reason, next_action = _guidance(reason)
    request_id = stable_id(
        "stage-close-next-action",
        resolve_repository_project_id(root),
        reason,
    )
    return {
        "status": status,
        "result": "Stage close was not authorized.",
        "blocker": stable_reason,
        "reason_code": reason,
        "request_id": request_id,
        "next_action": next_action,
    }


def _guidance(reason: str) -> tuple[str, str, str]:
    if reason == "review-isolation-unproven":
        return (
            "needs_user",
            "reviewer_independence_unproven",
            "Run ai-sdlc doctor, restore enforced Codex reviewer isolation, "
            "then rerun the same close command.",
        )
    if reason in {"review-provider-unavailable", "review-binding-unavailable"}:
        return (
            "needs_user",
            "reviewer_actor_unavailable",
            "Restore an eligible reviewer provider, then rerun the same close command.",
        )
    if reason == "review-candidate-unavailable":
        return (
            "blocked",
            "reviewer_input_invalid",
            "Repair the stage input artifacts, then rerun ai-sdlc run.",
        )
    return (
        "blocked",
        "reviewer_runtime_integrity_failure",
        "Run ai-sdlc doctor, repair the reported review integrity failure, "
        "then rerun ai-sdlc run.",
    )


__all__ = ["execute_stage_close_for_cli"]
