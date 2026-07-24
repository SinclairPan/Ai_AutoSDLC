"""Activation 策略的显式兼容回滚命令。"""

from __future__ import annotations

from pathlib import Path

import typer

from ai_sdlc.core.stage_review.activation_rollback import (
    export_v1_rollback_bundle,
    recover_v2_rollback_backup,
    restore_v1_rollback_bundle,
)

activation_app = typer.Typer(
    help="Inspect and recover the protected stage-gate activation state.",
    no_args_is_help=True,
)


@activation_app.command("rollback-export")
def rollback_export(
    root: Path = typer.Argument(Path("."), exists=True, file_okay=False),
    output: Path = typer.Option(..., "--output"),
) -> None:
    """导出验签后的 Phase 1 → v1 兼容回滚包，不改变运行状态。"""

    try:
        target = export_v1_rollback_bundle(root, output)
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc
    typer.echo(f"Rollback bundle: {target}")


@activation_app.command("rollback-restore")
def rollback_restore(
    root: Path = typer.Argument(Path("."), exists=True, file_okay=False),
    bundle: Path = typer.Option(..., "--bundle", exists=True, dir_okay=False),
    execute: bool = typer.Option(False, "--execute"),
    yes: bool = typer.Option(False, "--yes"),
) -> None:
    """备份 v2 状态并恢复只读 v1 兼容验证视图；必须显式执行和确认。"""

    if not execute:
        typer.echo(
            "Dry run: validates only on --execute; no activation state was changed."
        )
        return
    if not yes:
        typer.echo("rollback restore requires --yes", err=True)
        raise typer.Exit(code=2)
    try:
        backup = restore_v1_rollback_bundle(root, bundle)
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc
    typer.echo(
        "Read-only V1 compatibility view restored. "
        "Do not run Stage Close or create activation state before recovery. "
        f"Backup: {backup}"
    )


@activation_app.command("rollback-recover")
def rollback_recover(
    root: Path = typer.Argument(Path("."), exists=True, file_okay=False),
    backup_id: str = typer.Option(..., "--backup-id"),
    execute: bool = typer.Option(False, "--execute"),
    yes: bool = typer.Option(False, "--yes"),
) -> None:
    """从 v1 视图或中断的回滚操作恢复严格 v2 状态。"""

    if not execute:
        typer.echo(
            "Dry run: validates only on --execute; no activation state was changed."
        )
        return
    if not yes:
        typer.echo("rollback recover requires --yes", err=True)
        raise typer.Exit(code=2)
    try:
        backup = recover_v2_rollback_backup(root, backup_id)
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc
    typer.echo(f"V2 activation state recovered from backup: {backup}")


__all__ = ["activation_app"]
