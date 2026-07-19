"""Prepare and invoke a source-bound Lean evidence command."""

from __future__ import annotations

import subprocess
from pathlib import Path

from ai_sdlc.core.lean_code_environment import (
    controlled_execution_environment,
    effective_command_argv,
    optional_file_digest,
    resolve_execution_adapter,
    safe_project_path,
)
from ai_sdlc.core.lean_code_execution_models import (
    LeanExecutionOptions,
)
from ai_sdlc.core.loop_models import utc_now_iso
from ai_sdlc.core.source_snapshot import SourceSnapshot


def _prepare_execution(
    execution_root: Path,
    options: LeanExecutionOptions,
    snapshot: SourceSnapshot,
) -> tuple[Path, SourceSnapshot, str, tuple[str, ...], str]:
    run_cwd = safe_project_path(execution_root, options.cwd)
    test_candidate = execution_root / options.test_source_ref
    if test_candidate.is_symlink():
        raise ValueError(
            "test source must be a regular file in the selected source view"
        )
    test_source = safe_project_path(execution_root, options.test_source_ref)
    if not test_source.is_file():
        raise ValueError("test source is unavailable in the selected source view")
    test_source_digest = optional_file_digest(
        execution_root,
        options.test_source_ref,
    )
    snapshot = snapshot.model_copy(
        update={
            "file_digests": {
                **snapshot.file_digests,
                options.test_source_ref: test_source_digest,
            }
        }
    )
    adapter = resolve_execution_adapter(
        execution_root,
        options.command_argv,
        options.test_source_ref,
    )
    if not adapter:
        raise ValueError(
            "supported runner command target must resolve inside the selected source view"
        )
    command_argv = effective_command_argv(adapter, options.command_argv, execution_root)
    adapter = resolve_execution_adapter(
        execution_root,
        command_argv,
        options.test_source_ref,
    )
    if not adapter:
        raise ValueError("canonical runner no longer consumes the selected test source")
    return run_cwd, snapshot, adapter, command_argv, test_source_digest


def _invoke_execution(
    command_argv: tuple[str, ...],
    run_cwd: Path,
    adapter: str,
    execution_root: Path,
    root: Path,
    timeout_seconds: int,
) -> tuple[subprocess.CompletedProcess[str], str, str]:
    started_at = utc_now_iso()
    completed = subprocess.run(
        list(command_argv),
        cwd=run_cwd,
        env=controlled_execution_environment(adapter, execution_root, root),
        capture_output=True,
        check=False,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout_seconds,
    )
    return completed, started_at, utc_now_iso()


__all__: list[str] = []
