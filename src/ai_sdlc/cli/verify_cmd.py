"""Verify subcommands for read-only governance checks."""

from __future__ import annotations

import json

import typer
from rich.console import Console

from ai_sdlc.core.verify_constraints import (
    ConstraintProfile,
    build_constraint_report,
)
from ai_sdlc.utils.helpers import find_project_root

verify_app = typer.Typer(
    help=(
        "Read-only verification. Complements `ai-sdlc doctor` (environment/PATH); "
        "this command checks governance files, checkpoint vs specs tree, and "
        "repo-local framework backlog structure when present."
    ),
)
console = Console()


@verify_app.command(
    "constraints",
    help=(
        "Read-only: required governance files and checkpoint/specs consistency; "
        "when tasks.md exists under feature.spec_dir, task-level acceptance must "
        "match gate decompose (SC-014). Self-development checks are enabled only "
        "with --profile self-development. Does not write project state. "
        "Exit 0 if no BLOCKERs, else 1."
    ),
)
def verify_constraints(
    as_json: bool = typer.Option(
        False,
        "--json",
        help="Machine-readable report on stdout.",
    ),
    profile: ConstraintProfile = typer.Option(
        ConstraintProfile.PROJECT,
        "--profile",
        help="Verification scope: project or self-development.",
        case_sensitive=False,
    ),
) -> None:
    """Validate the constitution, checkpoint spec_dir, and task acceptance criteria."""
    root = find_project_root()
    if root is None:
        msg = "Not inside an AI-SDLC project (.ai-sdlc/ not found)."
        if as_json:
            typer.echo(
                json.dumps(
                    {"ok": False, "error": msg, "blockers": [], "root": None}, indent=2
                )
            )
        else:
            console.print(f"[red]{msg}[/red]")
        raise typer.Exit(code=1)

    report = build_constraint_report(root, profile=profile)
    effective_blockers = list(report.blockers)
    advisories: list[str] = []

    if as_json:
        typer.echo(
            json.dumps(
                {
                    "ok": len(effective_blockers) == 0,
                    "profile": report.profile.value,
                    "blockers": effective_blockers,
                    "advisories": advisories,
                    "root": str(root),
                    "verification_gate": {
                        "name": report.gate_name,
                        "source_name": report.source_name,
                        "profile": report.profile.value,
                        "sources": [report.source_name],
                        "check_objects": list(report.check_objects),
                        "coverage_gaps": list(report.coverage_gaps),
                        "release_gate": report.release_gate,
                    },
                    "decision": "block" if effective_blockers else "allow",
                },
                indent=2,
            )
        )
    else:
        if effective_blockers:
            console.print("[bold red]Constraint violations[/bold red]")
            for b in _string_list(effective_blockers):
                console.print(f"  {b}")
        else:
            console.print("[green]verify constraints: no BLOCKERs.[/green]")
            for advisory in _string_list(advisories):
                console.print(f"[yellow]{advisory}[/yellow]")
            if report.release_gate is not None:
                verdict = str(report.release_gate.get("overall_verdict", "UNKNOWN"))
                console.print(f"[cyan]release gate: {verdict}[/cyan]")
    raise typer.Exit(code=1 if effective_blockers else 0)


def _render_frontend_contract_summary(summary: object) -> None:
    _render_frontend_summary("frontend contract verification", summary)


def _render_frontend_gate_summary(summary: object) -> None:
    _render_frontend_summary("frontend gate verification", summary)


def _render_frontend_summary(label: str, summary: object) -> None:
    if not isinstance(summary, dict):
        return

    verdict = str(summary.get("gate_verdict", "UNKNOWN")).strip() or "UNKNOWN"
    coverage_gaps = _string_list(summary.get("coverage_gaps", ()))
    blockers = _string_list(summary.get("blockers", ()))
    details: list[str] = []
    diagnostic_summary = _frontend_diagnostic_summary(summary)
    if diagnostic_summary:
        details.append(diagnostic_summary)
    if coverage_gaps:
        details.append("coverage gaps: " + ", ".join(coverage_gaps[:3]))
    elif verdict != "PASS" and blockers:
        details.append("blockers: " + "; ".join(blockers[:1]))
    suffix = f" ({'; '.join(details)})" if details else ""
    style = "green" if verdict == "PASS" else "yellow"
    console.print(f"[{style}]{label}: {verdict}{suffix}[/{style}]")
    typer.echo(f"{label}: {verdict}{suffix}")


def _string_list(value: object) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    items: list[str] = []
    for item in value:
        text = str(item).strip()
        if text and text not in items:
            items.append(text)
    return items


def _frontend_diagnostic_summary(summary: dict[str, object]) -> str:
    diagnostic = _frontend_diagnostic_payload(summary)
    if not diagnostic:
        return ""

    status = str(diagnostic.get("diagnostic_status", "")).strip()
    if not status:
        return ""

    projection = diagnostic.get("policy_projection", {})
    if not isinstance(projection, dict):
        projection = {}

    projection_fields: list[str] = []
    coverage_effect = str(projection.get("coverage_effect", "")).strip()
    if coverage_effect:
        projection_fields.append(f"coverage={coverage_effect}")
    report_family_member = str(projection.get("report_family_member", "")).strip()
    if report_family_member:
        projection_fields.append(f"report={report_family_member}")
    blocker_class = str(projection.get("blocker_class", "")).strip()
    if blocker_class:
        projection_fields.append(f"blocker={blocker_class}")

    if not projection_fields:
        return f"diagnostic: {status}"
    return f"diagnostic: {status}; projection: {', '.join(projection_fields)}"


def _frontend_diagnostic_payload(summary: dict[str, object]) -> dict[str, object]:
    diagnostic = summary.get("diagnostic")
    if isinstance(diagnostic, dict):
        return diagnostic

    upstream = summary.get("upstream_contract_verification")
    if not isinstance(upstream, dict):
        return {}

    diagnostic = upstream.get("diagnostic")
    if isinstance(diagnostic, dict):
        return diagnostic
    return {}
