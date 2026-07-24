from __future__ import annotations

import json
from pathlib import Path

from ai_sdlc.core.stage_review.artifacts import resolve_canonical_shared_state
from ai_sdlc.core.stage_review.optimization.foreground import (
    ForegroundExecutionLease,
)
from ai_sdlc.core.stage_review.optimization.foreground import (
    _foreground_execution_requested as foreground_execution_requested,
)


def test_foreground_lease_preempts_only_while_user_run_is_active(tmp_path: Path) -> None:
    assert not foreground_execution_requested(tmp_path, project_id="project.shared")

    with ForegroundExecutionLease(tmp_path, project_id="project.shared"):
        assert foreground_execution_requested(tmp_path, project_id="project.shared")

    assert not foreground_execution_requested(tmp_path, project_id="project.shared")


def test_dead_foreground_owner_is_removed_before_preemption(tmp_path: Path) -> None:
    shared = resolve_canonical_shared_state(tmp_path, "project.shared")
    path = (
        shared
        / "offline-optimization"
        / "foreground-requests"
        / "dead-owner.json"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"pid": 2_147_483_647}), encoding="utf-8")

    assert not foreground_execution_requested(tmp_path, project_id="project.shared")
    assert not path.exists()


def test_overlapping_user_runs_keep_preemption_until_last_exit(tmp_path: Path) -> None:
    first = ForegroundExecutionLease(tmp_path, project_id="project.shared")
    second = ForegroundExecutionLease(tmp_path, project_id="project.shared")

    with first:
        with second:
            assert foreground_execution_requested(
                tmp_path, project_id="project.shared"
            )
        assert foreground_execution_requested(tmp_path, project_id="project.shared")

    assert not foreground_execution_requested(tmp_path, project_id="project.shared")
