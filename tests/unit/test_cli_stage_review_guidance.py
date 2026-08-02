from __future__ import annotations

from pathlib import Path

import pytest
import typer

from ai_sdlc.cli.stage_review_guidance import (
    _stage_close_failure_payload,
    execute_stage_close_for_cli,
)
from ai_sdlc.core.stage_review.stage_review_execution import (
    StageCloseGateUnavailableError,
)


def test_isolation_failure_has_one_stable_user_action(tmp_path: Path) -> None:
    payload = _stage_close_failure_payload(tmp_path, "review-isolation-unproven")

    assert payload["status"] == "needs_user"
    assert payload["blocker"] == "reviewer_independence_unproven"
    assert payload["request_id"]
    assert "ai-sdlc doctor" in str(payload["next_action"])


def test_cli_guard_emits_result_and_exits_without_running_a_fallback(
    tmp_path: Path,
) -> None:
    emitted: list[dict[str, object]] = []

    def action() -> object:
        raise StageCloseGateUnavailableError("review-provider-unavailable")

    def emit(payload: dict[str, object], *, json_output: bool) -> None:
        assert json_output is True
        emitted.append(payload)

    with pytest.raises(typer.Exit) as raised:
        execute_stage_close_for_cli(
            tmp_path,
            action,
            json_output=True,
            emit=emit,
        )

    assert raised.value.exit_code == 2
    assert emitted[0]["status"] == "needs_user"
    assert emitted[0]["reason_code"] == "review-provider-unavailable"
