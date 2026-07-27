"""CLI commands for local adversarial PR review."""

from __future__ import annotations

from pathlib import Path

import typer

from ai_sdlc.branch.git_client import GitClient, GitError
from ai_sdlc.cli.pr_review_rendering import (
    emit_pr_review_result as _emit_result,
)
from ai_sdlc.cli.stage_review_guidance import execute_stage_close_for_cli
from ai_sdlc.core.pr_review_provider import MockReviewerFixture, ProviderRunStatus
from ai_sdlc.core.pr_review_service import (
    PRReviewAttestResult,
    PRReviewCommandStatus,
    PRReviewStartOptions,
    attest_pr_review,
    close_pr_review,
    doctor_pr_review,
    fix_pr_review,
    parse_provider_command,
    rerun_pr_review,
    start_pr_review,
    status_pr_review,
)
from ai_sdlc.core.stage_review.artifacts import (
    ResourceLockUnavailableError,
    SharedStateIntegrityError,
    ShortFileLock,
    resolve_canonical_shared_state,
    resolve_repository_project_id,
)
from ai_sdlc.core.stage_review.ci_certificate import (
    CI_CERTIFICATE_BUNDLE_PATH,
    read_ci_certificate_bundle,
)
from ai_sdlc.core.stage_review.ci_certificate_export import (
    export_ci_certificate_bundle,
)
from ai_sdlc.utils.helpers import find_project_root

pr_review_app = typer.Typer(
    help="Run local adversarial PR review loops.",
    no_args_is_help=True,
)


@pr_review_app.command(name="doctor")
def pr_review_doctor(
    base_ref: str | None = typer.Option(
        None,
        "--base",
        help="Base branch or revision. Defaults to the repository default branch.",
    ),
    head_ref: str = typer.Option("HEAD", "--head", help="Head branch or revision."),
    diff_source: str = typer.Option(
        "local-git-range",
        "--diff-source",
        help="Review input source: local-git-range, patch, local-staged, local-unstaged, or scm-pr.",
    ),
    patch_file: str = typer.Option(
        "", "--patch-file", help="Patch file for patch diff source."
    ),
    source_id: str = typer.Option(
        "", "--source-id", help="External source id such as PR/MR id."
    ),
    source_provider: str = typer.Option(
        "",
        "--source-provider",
        help="External source provider such as github, gitlab, gitee, or custom.",
    ),
    provider_id: str = typer.Option(
        "",
        "--provider",
        help="Review provider: local-agent or mock-reviewer. Defaults to loop policy.",
    ),
    model_selector: str = typer.Option(
        "current",
        "--model",
        help="Model selector. Defaults to current.",
    ),
    current_model: str = typer.Option(
        "",
        "--current-model",
        help="Explicit current model for local CLI/agent environments.",
    ),
    provider_command: str = typer.Option(
        "",
        "--provider-command",
        help="Local reviewer command for local-agent.",
    ),
    code_egress: bool = typer.Option(
        False,
        "--code-egress/--no-code-egress",
        help="Whether the selected provider may send code to a remote model service.",
    ),
    confirm_code_egress: bool = typer.Option(
        False,
        "--confirm-code-egress",
        help="Confirm policy-gated remote code egress.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Print JSON output."),
) -> None:
    """Check local PR review readiness without writing review artifacts."""

    root = _project_root_or_exit(json_output=json_output)
    resolved_base = _resolve_base_ref(
        root,
        base_ref,
        diff_source=diff_source,
        json_output=json_output,
    )
    result = doctor_pr_review(
        root=root,
        base_ref=resolved_base,
        head_ref=head_ref,
        diff_source=diff_source,
        patch_file=patch_file,
        source_id=source_id,
        source_provider=source_provider,
        provider_id=provider_id,
        model_selector=model_selector,
        current_model=current_model,
        provider_command=parse_provider_command(provider_command),
        code_egress=code_egress,
        code_egress_confirmed=confirm_code_egress,
    )
    _emit_result(result.model_dump(mode="json"), json_output=json_output)
    raise typer.Exit(0 if result.status == PRReviewCommandStatus.READY else 1)


@pr_review_app.command(name="start")
def pr_review_start(
    base_ref: str | None = typer.Option(
        None,
        "--base",
        help="Base branch or revision. Defaults to the repository default branch.",
    ),
    head_ref: str = typer.Option("HEAD", "--head", help="Head branch or revision."),
    diff_source: str = typer.Option(
        "local-git-range",
        "--diff-source",
        help="Review input source: local-git-range, patch, local-staged, local-unstaged, or scm-pr.",
    ),
    patch_file: str = typer.Option(
        "", "--patch-file", help="Patch file for patch diff source."
    ),
    source_id: str = typer.Option(
        "", "--source-id", help="External source id such as PR/MR id."
    ),
    source_provider: str = typer.Option(
        "",
        "--source-provider",
        help="External source provider such as github, gitlab, gitee, or custom.",
    ),
    provider_id: str = typer.Option(
        "",
        "--provider",
        help="Review provider: local-agent or mock-reviewer. Defaults to loop policy.",
    ),
    model_selector: str = typer.Option(
        "current",
        "--model",
        help="Model selector. Defaults to current.",
    ),
    current_model: str = typer.Option(
        "",
        "--current-model",
        help="Explicit current model for local CLI/agent environments.",
    ),
    provider_command: str = typer.Option(
        "",
        "--provider-command",
        help="Local reviewer command for local-agent.",
    ),
    mock_fixture: MockReviewerFixture = typer.Option(
        MockReviewerFixture.CLEAN,
        "--mock-fixture",
        help="Mock reviewer fixture.",
    ),
    code_egress: bool = typer.Option(
        False,
        "--code-egress/--no-code-egress",
        help="Whether the selected provider may send code to a remote model service.",
    ),
    confirm_code_egress: bool = typer.Option(
        False,
        "--confirm-code-egress",
        help="Confirm policy-gated remote code egress.",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Preview without writing review artifacts or invoking a provider.",
    ),
    review_id: str = typer.Option("", "--review-id", help="Explicit review id."),
    json_output: bool = typer.Option(False, "--json", help="Print JSON output."),
) -> None:
    """Start or preview a local adversarial PR review."""

    root = _project_root_or_exit(json_output=json_output)
    resolved_base = _resolve_base_ref(
        root,
        base_ref,
        diff_source=diff_source,
        json_output=json_output,
    )
    result = start_pr_review(
        PRReviewStartOptions(
            root=root,
            base_ref=resolved_base,
            head_ref=head_ref,
            diff_source=diff_source,
            patch_file=patch_file,
            source_id=source_id,
            source_provider=source_provider,
            provider_id=provider_id,
            model_selector=model_selector,
            current_model=current_model,
            provider_command=parse_provider_command(provider_command),
            code_egress=code_egress,
            code_egress_confirmed=confirm_code_egress,
            dry_run=dry_run,
            review_id=review_id,
            mock_fixture=mock_fixture,
        )
    )
    _emit_result(result.model_dump(mode="json"), json_output=json_output)
    if result.provider_status == ProviderRunStatus.CHANGES_REQUIRED:
        raise typer.Exit(10)
    raise typer.Exit(
        0
        if result.status
        in {PRReviewCommandStatus.DRY_RUN, PRReviewCommandStatus.STARTED}
        else 1
    )


@pr_review_app.command(name="status")
def pr_review_status(
    json_output: bool = typer.Option(False, "--json", help="Print JSON output."),
) -> None:
    """Show the current local PR review state."""

    root = _project_root_or_exit(json_output=json_output)
    result = status_pr_review(root)
    _emit_result(result.model_dump(mode="json"), json_output=json_output)
    raise typer.Exit(0 if result.status != PRReviewCommandStatus.BLOCKED else 1)


@pr_review_app.command(name="fix")
def pr_review_fix(
    max_rounds: int = typer.Option(2, "--max-rounds", help="Maximum fix rounds."),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Preview fix plan metadata without writing fix artifacts.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Print JSON output."),
) -> None:
    """Create a fix plan for unresolved BLOCKER/REQUIRED findings."""

    root = _project_root_or_exit(json_output=json_output)
    result = fix_pr_review(root, max_rounds=max_rounds, dry_run=dry_run)
    _emit_result(result.model_dump(mode="json"), json_output=json_output)
    raise typer.Exit(0 if result.status == PRReviewCommandStatus.READY else 1)


@pr_review_app.command(name="rerun")
def pr_review_rerun(
    provider_command: str = typer.Option(
        "",
        "--provider-command",
        help="Local reviewer command for local-agent.",
    ),
    mock_fixture: MockReviewerFixture = typer.Option(
        MockReviewerFixture.CLEAN,
        "--mock-fixture",
        help="Mock reviewer fixture.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Print JSON output."),
) -> None:
    """Regenerate review pack and rerun the local review provider."""

    root = _project_root_or_exit(json_output=json_output)
    result = rerun_pr_review(
        root,
        provider_command=parse_provider_command(provider_command),
        mock_fixture=mock_fixture,
    )
    _emit_result(result.model_dump(mode="json"), json_output=json_output)
    if result.provider_status == ProviderRunStatus.CHANGES_REQUIRED:
        raise typer.Exit(10)
    raise typer.Exit(0 if result.status == PRReviewCommandStatus.STARTED else 1)


@pr_review_app.command(name="close")
def pr_review_close(
    require_no_blockers: bool = typer.Option(
        False,
        "--require-no-blockers",
        help="Allow risk_accepted when REQUIRED findings remain but no BLOCKERs.",
    ),
    evidence: list[str] = typer.Option(
        [],
        "--evidence",
        help="Verification evidence line to include in final report.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Print JSON output."),
) -> None:
    """Close the local PR review with a final verdict."""

    root = _project_root_or_exit(json_output=json_output)
    result = execute_stage_close_for_cli(
        root,
        lambda: close_pr_review(
            root,
            require_no_blockers=require_no_blockers,
            verification_evidence=evidence,
        ),
        json_output=json_output,
        emit=_emit_result,
    )
    _emit_result(result.model_dump(mode="json"), json_output=json_output)
    raise typer.Exit(0 if result.status == PRReviewCommandStatus.CLOSED else 1)


@pr_review_app.command(name="attest")
def pr_review_attest(
    json_output: bool = typer.Option(False, "--json", help="Print JSON output."),
) -> None:
    """Write a CI-readable attestation for the current closed local review."""

    root = _project_root_or_exit(json_output=json_output)
    try:
        shared = resolve_canonical_shared_state(
            root,
            resolve_repository_project_id(root),
        )
    except (OSError, ValueError, SharedStateIntegrityError) as exc:
        result = PRReviewAttestResult(
            status=PRReviewCommandStatus.BLOCKED,
            blocker=f"PR review attest shared lock state is unavailable: {exc}",
            next_action=(
                "Run ai-sdlc doctor, repair Git/shared stage-review state, "
                "then rerun pr-review attest."
            ),
        )
    else:
        try:
            with ShortFileLock(
                shared / "locks" / "pr-review-attest.lock",
                timeout_seconds=5,
            ):
                result = execute_stage_close_for_cli(
                    root,
                    lambda: attest_pr_review(root),
                    json_output=json_output,
                    emit=_emit_result,
                )
                result = _export_pr_review_attestation_bundle(root, result)
        except ResourceLockUnavailableError as exc:
            result = PRReviewAttestResult(
                status=PRReviewCommandStatus.BLOCKED,
                blocker=f"Another pr-review attest operation is active: {exc}",
                next_action=(
                    "Wait for that operation to finish, "
                    "then rerun pr-review attest."
                ),
            )
    _emit_result(result.model_dump(mode="json"), json_output=json_output)
    raise typer.Exit(0 if result.status == PRReviewCommandStatus.READY else 1)


def _export_pr_review_attestation_bundle(
    root: Path,
    result: PRReviewAttestResult,
) -> PRReviewAttestResult:
    if result.status == PRReviewCommandStatus.READY:
        has_session = bool(result.stage_review_session_id)
        has_certificate = bool(result.stage_close_certificate_id)
        if has_session != has_certificate:
            result = result.model_copy(
                update={
                    "status": PRReviewCommandStatus.BLOCKED,
                    "blocker": "Stage close certificate identity is incomplete.",
                    "next_action": "Rerun pr-review attest for the current review.",
                }
            )
            bundle_path = None
        elif not has_certificate:
            bundle_path = None
            cleanup_blocker = _clear_stale_ci_certificate_bundle(root)
            if cleanup_blocker:
                result = result.model_copy(
                    update={
                        "status": PRReviewCommandStatus.BLOCKED,
                        "blocker": cleanup_blocker,
                        "next_action": (
                            f"Remove {CI_CERTIFICATE_BUNDLE_PATH} and rerun "
                            "pr-review attest."
                        ),
                    }
                )
            else:
                result = result.model_copy(
                    update={
                        "next_action": (
                            "Attestation is ready; the current Shadow policy "
                            "does not require a CI certificate bundle, and CI "
                            "must not call any model."
                        )
                    }
                )
        else:
            try:
                bundle_path = export_ci_certificate_bundle(
                    root,
                    close_kind="local-pr-review-attest",
                    stage_instance_id=result.review_id,
                    review_session_id=result.stage_review_session_id,
                    certificate_id=result.stage_close_certificate_id,
                )
            except (OSError, ValueError) as exc:
                cleanup_blocker = _clear_stale_ci_certificate_bundle(
                    root,
                    preserve_review_session_id=result.stage_review_session_id,
                    preserve_certificate_id=result.stage_close_certificate_id,
                )
                result = result.model_copy(
                    update={
                        "status": PRReviewCommandStatus.BLOCKED,
                        "blocker": (
                            f"CI certificate bundle export failed: {exc}"
                            + (f"; {cleanup_blocker}" if cleanup_blocker else "")
                        ),
                        "next_action": (
                            "Rerun the local PR review with "
                            "`--diff-source local-git-range` before attestation."
                        ),
                    }
                )
                bundle_path = None
            if bundle_path is None and result.status == PRReviewCommandStatus.READY:
                cleanup_blocker = _clear_stale_ci_certificate_bundle(
                    root,
                    preserve_review_session_id=result.stage_review_session_id,
                    preserve_certificate_id=result.stage_close_certificate_id,
                )
                result = result.model_copy(
                    update={
                        "status": PRReviewCommandStatus.BLOCKED,
                        "blocker": (
                            "exact certificate proof did not produce a CI bundle"
                            + (f"; {cleanup_blocker}" if cleanup_blocker else "")
                        ),
                        "next_action": "Rerun pr-review attest for the current review.",
                    }
                )
        if bundle_path is not None:
            result = result.model_copy(
                update={
                    "ci_certificate_bundle_path": str(bundle_path),
                    "next_action": (
                        f"Stage only {CI_CERTIFICATE_BUNDLE_PATH} with "
                        f"`git add -- {CI_CERTIFICATE_BUNDLE_PATH}`, commit it, "
                        "then push the reviewed branch; CI verifies this bundle "
                        "and must not call any model."
                    ),
                }
            )
    if result.status != PRReviewCommandStatus.READY:
        cleanup_blocker = _clear_stale_ci_certificate_bundle(root)
        if cleanup_blocker:
            result = result.model_copy(
                update={
                    "blocker": (
                        f"{result.blocker}; {cleanup_blocker}"
                        if result.blocker
                        else cleanup_blocker
                    ),
                    "next_action": (
                        f"Remove {CI_CERTIFICATE_BUNDLE_PATH} and rerun "
                        "pr-review attest."
                    ),
                }
            )
    return result


def _clear_stale_ci_certificate_bundle(
    root: Path,
    *,
    preserve_review_session_id: str = "",
    preserve_certificate_id: str = "",
) -> str:
    path = root / CI_CERTIFICATE_BUNDLE_PATH
    try:
        if (
            path.is_file()
            and preserve_review_session_id
            and preserve_certificate_id
        ):
            try:
                current = read_ci_certificate_bundle(path)
            except (OSError, ValueError):
                current = None
            if current is not None and (
                current.certificate.scope.session_id == preserve_review_session_id
                and current.certificate.certificate_id == preserve_certificate_id
            ):
                return ""
        path.unlink(missing_ok=True)
    except (OSError, ValueError) as exc:
        return f"Unable to clear stale CI certificate bundle: {exc}"
    return ""


def _project_root_or_exit(*, json_output: bool = False) -> Path:
    root = find_project_root()
    if root is None:
        _emit_result(
            {
                "status": PRReviewCommandStatus.BLOCKED,
                "blocker": "Project is not initialized; .ai-sdlc is missing.",
                "next_action": "run ai-sdlc init .",
            },
            json_output=json_output,
        )
        raise typer.Exit(1)
    return root


def _resolve_base_ref(
    root: Path,
    base_ref: str | None,
    *,
    diff_source: str = "local-git-range",
    json_output: bool = False,
) -> str:
    if base_ref and base_ref.strip():
        return base_ref.strip()
    if diff_source.strip() != "local-git-range":
        return ""
    try:
        return GitClient(root).default_branch_name()
    except GitError as exc:
        _emit_result(
            {
                "status": PRReviewCommandStatus.BLOCKED,
                "blocker": str(exc),
                "next_action": "pass --base <branch> explicitly.",
            },
            json_output=json_output,
        )
        raise typer.Exit(1) from exc


__all__ = ["pr_review_app"]
