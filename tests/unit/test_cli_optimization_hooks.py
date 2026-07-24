from __future__ import annotations

from pathlib import Path

import pytest

from ai_sdlc.cli.optimization_hooks import (
    _maintain_optimization_after_run,
    foreground_optimization_scope,
)
from ai_sdlc.core.stage_review.artifacts import resolve_repository_project_id
from ai_sdlc.core.stage_review.optimization.foreground import (
    _foreground_execution_requested as foreground_execution_requested,
)


def test_foreground_scope_preempts_maintenance_only_during_real_run(
    tmp_path: Path,
) -> None:
    project_id = resolve_repository_project_id(tmp_path)

    with foreground_optimization_scope(tmp_path, dry_run=False):
        assert foreground_execution_requested(tmp_path, project_id=project_id)

    assert not foreground_execution_requested(tmp_path, project_id=project_id)


def test_dry_run_hook_is_read_only(tmp_path: Path) -> None:
    with foreground_optimization_scope(tmp_path, dry_run=True):
        pass

    assert _maintain_optimization_after_run(tmp_path, dry_run=True) is None
    assert not (tmp_path / ".ai-sdlc").exists()


def test_maintenance_failure_never_replaces_foreground_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(_root: Path) -> object:
        raise RuntimeError("offline maintenance failed")

    monkeypatch.setattr(
        "ai_sdlc.cli.optimization_hooks.run_bounded_optimization_maintenance",
        fail,
    )

    assert _maintain_optimization_after_run(tmp_path, dry_run=False) is None
