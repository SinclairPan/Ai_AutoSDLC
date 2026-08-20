"""Internal trusted commands for the sealed v2 benefit evaluator."""

from __future__ import annotations

import json
from typing import Annotated

import typer

from ai_sdlc.benefit_sealed_materializer import (
    FINAL_LOCK_ID,
    MaterializationError,
    default_policy,
    fingerprint_tree,
    materialize_sealed_bundle,
)

benefit_evidence_app = typer.Typer(
    help="Trusted v2 benefit-evidence sealing operations.",
    no_args_is_help=True,
)


@benefit_evidence_app.command("fingerprint-old-root")
def fingerprint_old_root_command() -> None:
    """Print only the legacy root inode and canonical tree digest."""
    try:
        fingerprint = fingerprint_tree(default_policy().legacy_root)
    except MaterializationError as error:
        typer.echo(json.dumps({"status": "no-go", "code": error.code}), err=True)
        raise typer.Exit(code=1) from error
    except Exception:
        typer.echo(json.dumps({"status": "no-go", "code": "internal-error"}), err=True)
        raise typer.Exit(code=1) from None
    typer.echo(
        json.dumps(
            {"inode": fingerprint.inode, "tree_sha256": fingerprint.sha256},
            sort_keys=True,
            separators=(",", ":"),
        )
    )


@benefit_evidence_app.command("materialize-sealed")
def materialize_sealed_command(
    sealed_source_fd: Annotated[
        int,
        typer.Option("--sealed-source-fd", help="Already-open protected source FD."),
    ] = -1,
    expected_source_sha256: Annotated[
        str,
        typer.Option("--expected-source-sha256", help="Frozen source bundle SHA256."),
    ] = "",
    expected_head: Annotated[
        str,
        typer.Option("--expected-head", help="Exact clean repository HEAD."),
    ] = "",
    lock_id: Annotated[
        str,
        typer.Option("--lock-id", help="Fixed target lock identifier."),
    ] = "",
    expected_old_root_tree_sha256: Annotated[
        str,
        typer.Option(
            "--expected-old-root-tree-sha256",
            help="Canonical legacy-root tree SHA256 from fingerprint-old-root.",
        ),
    ] = "",
) -> None:
    """Compile, validate and exclusively publish the final sealed root."""
    if sealed_source_fd < 0:
        typer.echo(json.dumps({"status": "no-go", "code": "source-selector"}), err=True)
        raise typer.Exit(code=1)
    if lock_id != FINAL_LOCK_ID:
        typer.echo(json.dumps({"status": "no-go", "code": "target-lock"}), err=True)
        raise typer.Exit(code=1)
    try:
        result = materialize_sealed_bundle(
            source_fd=sealed_source_fd,
            expected_source_sha256=expected_source_sha256,
            expected_head=expected_head,
            lock_id=lock_id,
            expected_old_root_tree_sha256=expected_old_root_tree_sha256,
        )
    except MaterializationError as error:
        typer.echo(json.dumps({"status": "no-go", "code": error.code}), err=True)
        raise typer.Exit(code=1) from error
    except Exception:
        typer.echo(json.dumps({"status": "no-go", "code": "internal-error"}), err=True)
        raise typer.Exit(code=1) from None
    typer.echo(
        json.dumps(
            {
                "status": "materialized",
                "count": len(result.file_sha256),
                "receipt_sha256": result.file_sha256["isolation-attestation.json"],
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )
