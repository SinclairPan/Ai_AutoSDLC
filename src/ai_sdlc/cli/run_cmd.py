"""Read-only ``ai-sdlc run`` routing for the five delivery Loops."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console

from ai_sdlc.cli.loop_cmd import get_review_aware_loop_status
from ai_sdlc.core.loop_router import (
    LoopRouteResult,
    LoopRouteStatus,
    route_five_loops,
)
from ai_sdlc.utils.helpers import find_project_root

console = Console()


def run_command(
    mode: str = typer.Option("auto", help="Legacy mode; only auto is supported."),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Read the same five-Loop route without executing legacy stages.",
    ),
    acknowledge_execute_batch: bool = typer.Option(
        False,
        "--acknowledge-execute-batch",
        help="Legacy option retained only to report its Loop migration.",
    ),
    yes: bool = typer.Option(
        False,
        "--yes",
        help="Legacy acknowledgement confirmation; does not enable execution.",
    ),
) -> None:
    """Show Result, Next and Blockers from the current five-Loop truth."""

    del dry_run, yes
    root = find_project_root()
    if root is None:
        _render(
            LoopRouteResult(
                status=LoopRouteStatus.BLOCKED,
                result="Project is not initialized.",
                next_action="Run ai-sdlc init .",
                blockers=[".ai-sdlc is missing."],
            )
        )
        raise typer.Exit(code=1)

    migration_blocker = _legacy_option_blocker(
        mode=mode,
        acknowledge_execute_batch=acknowledge_execute_batch,
    )
    if migration_blocker:
        _render(
            LoopRouteResult(
                status=LoopRouteStatus.NEEDS_USER,
                result="Legacy pipeline execution is retired from ai-sdlc run.",
                next_action="Continue through the current explicit Loop command.",
                blockers=[migration_blocker],
            )
        )
        raise typer.Exit(code=1)

    result = route_five_loops(root, status_loader=_review_aware_status)
    _render(result)
    if result.status == LoopRouteStatus.BLOCKED:
        raise typer.Exit(code=1)


def _review_aware_status(root: Path, loop_type: str):
    return get_review_aware_loop_status(root, loop_type)


def _legacy_option_blocker(*, mode: str, acknowledge_execute_batch: bool) -> str:
    normalized_mode = mode.strip().lower()
    if acknowledge_execute_batch:
        return (
            "--acknowledge-execute-batch belonged to the retired seven-stage runner; "
            "record progress in the current Implementation Loop instead."
        )
    if normalized_mode != "auto":
        return (
            f"--mode {mode} belonged to the retired seven-stage runner; "
            "five-Loop routing is read-only and does not prompt between stages."
        )
    return ""


def _render(result: LoopRouteResult) -> None:
    console.print(f"[bold]当前结果 / Result:[/bold] {result.result}")
    if result.current_loop is not None:
        console.print(
            "[bold]Loop:[/bold] "
            f"{result.current_loop.loop_type} / {result.current_loop.loop_id} "
            f"({result.current_loop.status})"
        )
    console.print(f"[bold]下一步 / Next:[/bold] {result.next_action or 'None'}")
    console.print("[bold]阻断项 / Blockers:[/bold]")
    if result.blockers:
        for blocker in result.blockers:
            console.print(f"- {blocker}", markup=False)
    else:
        console.print("- None")


__all__ = ["run_command"]
