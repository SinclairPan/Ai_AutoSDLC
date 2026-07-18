"""Controlled command execution receipts for Lean verification evidence."""

from __future__ import annotations

import re
import subprocess
import uuid
from dataclasses import replace as dataclass_replace
from pathlib import Path

from ai_sdlc.core.implementation_store import (
    implementation_artifacts,
    read_input,
    read_loop_run,
    repo_relative_path,
)
from ai_sdlc.core.lean_code_environment import (
    controlled_execution_environment,
    effective_command_argv,
    execution_toolchain,
    optional_file_digest,
    payload_digest,
    resolve_execution_adapter,
    safe_project_path,
)
from ai_sdlc.core.lean_code_execution_models import (
    LeanCommandExecutionReceipt,
    LeanExecutionOptions,
    LeanExecutionResult,
)
from ai_sdlc.core.lean_code_policy import stable_artifact_digest
from ai_sdlc.core.loop_artifacts import LoopArtifactStore
from ai_sdlc.core.loop_models import utc_now_iso
from ai_sdlc.core.source_snapshot import (
    SourceSnapshot,
    SourceSnapshotOptions,
    build_source_snapshot,
    revalidate_source_snapshot,
)
from ai_sdlc.core.source_snapshot_view import file_versions, materialized_source_view

_PURPOSES = {"targeted-verification", "regression-red", "regression-green"}
_REGRESSION_PURPOSES = {"regression-red", "regression-green"}
_SAFE_ID = re.compile(r"^[A-Za-z0-9._-]+$")


def run_lean_command(options: LeanExecutionOptions) -> LeanExecutionResult:
    """Execute argv without a shell and persist output plus a source-bound receipt."""

    root = options.root.resolve()
    issue = _options_issue(root, options)
    if issue:
        return _blocked(issue)
    try:
        snapshot = build_source_snapshot(
            SourceSnapshotOptions(
                root=root,
                source_kind=options.source_kind,
                base_ref=options.base_ref,
                head_ref=options.head_ref,
                patch_file=options.patch_file,
            )
        )
    except (OSError, ValueError) as exc:
        return _blocked(f"Lean execution source snapshot is unavailable: {exc}")
    freshness = revalidate_source_snapshot(root, snapshot)
    if not freshness.fresh:
        return _blocked(
            "Lean execution source snapshot changed before execution: "
            f"{freshness.reason}"
        )
    receipt_id = options.receipt_id or uuid.uuid4().hex
    run_dir = _run_dir(root, options.loop_id, receipt_id)
    if run_dir.exists():
        return _blocked(f"Lean execution receipt already exists: {receipt_id}")
    try:
        with materialized_source_view(root, snapshot) as execution_root:
            return _execute_and_persist(
                root,
                execution_root,
                options,
                snapshot,
                receipt_id,
                run_dir,
            )
    except subprocess.TimeoutExpired:
        return _blocked(f"Lean execution timed out after {options.timeout_seconds}s.")
    except (OSError, ValueError) as exc:
        return _blocked(f"Lean selected source execution could not start: {exc}")


def validate_execution_receipt(
    root: Path,
    reference: str,
    *,
    expected_digest: str = "",
    expected_purpose: str = "",
    expected_loop_id: str = "",
    current_diff_hash: str = "",
) -> tuple[LeanCommandExecutionReceipt | None, str]:
    """Re-read a receipt and every referenced byte artifact fail-closed."""

    try:
        path = safe_project_path(root, reference)
        raw = path.read_bytes()
        receipt = LeanCommandExecutionReceipt.model_validate_json(raw)
    except (OSError, ValueError) as exc:
        return None, f"execution receipt is unavailable or malformed: {exc}"
    loop_issue = _receipt_loop_issue(root, path, receipt, expected_loop_id)
    if loop_issue:
        return None, loop_issue
    issue = _receipt_binding_issue(
        root, receipt, raw, expected_digest, expected_purpose, current_diff_hash
    )
    return (None, issue) if issue else (receipt, "")


def _execute_and_persist(
    root: Path,
    execution_root: Path,
    options: LeanExecutionOptions,
    snapshot: SourceSnapshot,
    receipt_id: str,
    run_dir: Path,
) -> LeanExecutionResult:
    run_cwd = safe_project_path(execution_root, options.cwd)
    test_candidate = execution_root / options.test_source_ref
    if test_candidate.is_symlink():
        raise ValueError(
            "test source must be a regular file in the selected source view"
        )
    test_source = safe_project_path(execution_root, options.test_source_ref)
    if not test_source.is_file():
        raise ValueError("test source is unavailable in the selected source view")
    adapter = resolve_execution_adapter(
        execution_root,
        options.command_argv,
        options.test_source_ref,
    )
    if not adapter:
        raise ValueError(
            "supported runner command target must resolve inside the selected source view"
        )
    command_argv = effective_command_argv(adapter, options.command_argv)
    started_at = utc_now_iso()
    completed = subprocess.run(
        list(command_argv),
        cwd=run_cwd,
        env=controlled_execution_environment(adapter),
        capture_output=True,
        check=False,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=options.timeout_seconds,
    )
    finished_at = utc_now_iso()
    output = _combined_output(completed.stdout, completed.stderr)
    accepted, issue = _outcome(options, completed.returncode, output)
    receipt, receipt_path, output_path = _write_receipt(
        root,
        dataclass_replace(options, command_argv=command_argv),
        snapshot,
        receipt_id,
        run_dir,
        completed.returncode,
        output,
        started_at,
        finished_at,
        accepted,
        adapter,
        optional_file_digest(execution_root, options.test_source_ref),
    )
    return LeanExecutionResult(
        status="ready" if accepted else "blocked",
        result="Lean command execution captured.",
        blocker=issue,
        next_action=_next_action(options.purpose, accepted),
        receipt_path=repo_relative_path(root, receipt_path),
        receipt_digest=payload_digest(receipt_path.read_bytes()),
        output_path=repo_relative_path(root, output_path),
        exit_code=receipt.exit_code,
        command_argv=receipt.command_argv,
    )


def _write_receipt(
    root: Path,
    options: LeanExecutionOptions,
    snapshot: SourceSnapshot,
    receipt_id: str,
    run_dir: Path,
    exit_code: int,
    output: str,
    started_at: str,
    finished_at: str,
    accepted: bool,
    adapter: str,
    test_source_digest: str,
) -> tuple[LeanCommandExecutionReceipt, Path, Path]:
    store = LoopArtifactStore(root)
    toolchain = execution_toolchain(root, options.command_argv[0])
    output_path = run_dir / "output.txt"
    snapshot_path = run_dir / "source-snapshot.json"
    receipt_path = run_dir / "receipt.json"
    store.write_markdown_artifact(output_path, output)
    store.write_json_artifact(snapshot_path, snapshot)
    receipt = LeanCommandExecutionReceipt(
        receipt_id=receipt_id,
        loop_id=options.loop_id,
        purpose=options.purpose,
        command_argv=list(options.command_argv),
        cwd=options.cwd,
        source_snapshot_ref=repo_relative_path(root, snapshot_path),
        source_snapshot_digest=stable_artifact_digest(snapshot),
        diff_hash=snapshot.diff_hash,
        exit_code=exit_code,
        output_ref=repo_relative_path(root, output_path),
        output_digest=payload_digest(output_path.read_bytes()),
        test_source_ref=options.test_source_ref,
        test_source_digest=test_source_digest,
        failure_signature=options.failure_signature,
        runner_adapter=adapter,
        toolchain_fingerprint=toolchain[0],
        toolchain_executable=toolchain[1],
        toolchain_executable_digest=toolchain[2],
        environment_fingerprint=toolchain[3],
        started_at=started_at,
        finished_at=finished_at,
        accepted=accepted,
    )
    store.write_json_artifact(receipt_path, receipt)
    return receipt, receipt_path, output_path


def _receipt_binding_issue(
    root: Path,
    receipt: LeanCommandExecutionReceipt,
    raw: bytes,
    expected_digest: str,
    expected_purpose: str,
    current_diff_hash: str,
) -> str:
    if expected_digest and payload_digest(raw) != expected_digest:
        return "execution receipt digest is stale"
    if receipt.created_by != "ai-sdlc" or receipt.purpose not in _PURPOSES:
        return "execution receipt provenance is invalid"
    if expected_purpose and receipt.purpose != expected_purpose:
        return "execution receipt purpose does not match"
    if not receipt.accepted or not receipt.command_argv:
        return "execution receipt did not record an accepted command"
    try:
        snapshot_path = safe_project_path(root, receipt.source_snapshot_ref)
        snapshot = SourceSnapshot.model_validate_json(snapshot_path.read_text("utf-8"))
        output_path = safe_project_path(root, receipt.output_ref)
        output = output_path.read_text("utf-8")
    except (OSError, ValueError) as exc:
        return f"execution receipt artifact is unavailable: {exc}"
    return _receipt_content_issue(root, receipt, snapshot, output, current_diff_hash)


def _receipt_loop_issue(
    root: Path,
    path: Path,
    receipt: LeanCommandExecutionReceipt,
    expected_loop_id: str,
) -> str:
    if not expected_loop_id:
        return ""
    if receipt.loop_id != expected_loop_id:
        return "execution receipt loop identity does not match"
    expected = _run_dir(root, expected_loop_id, receipt.receipt_id) / "receipt.json"
    if path != expected.resolve():
        return "execution receipt path does not match its loop identity"
    return ""


def _receipt_content_issue(
    root: Path,
    receipt: LeanCommandExecutionReceipt,
    snapshot: SourceSnapshot,
    output: str,
    current_diff_hash: str,
) -> str:
    if stable_artifact_digest(snapshot) != receipt.source_snapshot_digest:
        return "execution source snapshot digest is stale"
    if snapshot.diff_hash != receipt.diff_hash:
        return "execution receipt diff does not match its source snapshot"
    if current_diff_hash and receipt.diff_hash != current_diff_hash:
        return "execution receipt does not match the current diff"
    if (
        payload_digest(safe_project_path(root, receipt.output_ref).read_bytes())
        != receipt.output_digest
    ):
        return "execution output digest is stale"
    try:
        current_test_digest = _snapshot_file_digest(
            root,
            snapshot,
            receipt.test_source_ref,
        )
    except (OSError, ValueError) as exc:
        return f"execution test source artifact is unavailable: {exc}"
    if current_test_digest != receipt.test_source_digest:
        return "execution test source digest is stale"
    if (
        resolve_execution_adapter(
            root,
            tuple(receipt.command_argv),
            receipt.test_source_ref,
        )
        != receipt.runner_adapter
        or not receipt.runner_adapter
    ):
        return "execution runner adapter is invalid or stale"
    if execution_toolchain(root, receipt.command_argv[0]) != (
        receipt.toolchain_fingerprint,
        receipt.toolchain_executable,
        receipt.toolchain_executable_digest,
        receipt.environment_fingerprint,
    ):
        return "execution toolchain or dependency environment is stale"
    accepted, issue = _outcome_from_receipt(receipt, output)
    return issue if not accepted else ""


def _snapshot_file_digest(
    root: Path,
    snapshot: SourceSnapshot,
    reference: str,
) -> str:
    if not reference:
        return ""
    _before, after = file_versions(root, snapshot, reference)
    return payload_digest(after)


def _options_issue(root: Path, options: LeanExecutionOptions) -> str:
    if options.purpose not in _PURPOSES:
        return f"Unsupported Lean execution purpose: {options.purpose}"
    if (
        options.purpose in _REGRESSION_PURPOSES
        and not options.failure_signature.startswith("assertion:")
    ):
        return "Regression failure signature must start with 'assertion:'."
    if not options.command_argv or any(not item for item in options.command_argv):
        return "Lean execution requires a non-empty argv."
    if options.receipt_id and not _SAFE_ID.fullmatch(options.receipt_id):
        return "Lean execution receipt id is unsafe."
    if not 1 <= options.timeout_seconds <= 1800:
        return "Lean execution timeout must be between 1 and 1800 seconds."
    if not options.test_source_ref:
        return "Lean execution requires a project-local test source."
    try:
        artifacts = implementation_artifacts(root, options.loop_id)
        loop_run = read_loop_run(artifacts.loop_run_path)
        impl_input = read_input(artifacts.input_path)
    except (OSError, ValueError) as exc:
        return f"Lean execution input is unavailable: {exc}"
    if (
        loop_run.input_digest
        and stable_artifact_digest(impl_input) != loop_run.input_digest
    ):
        return "Implementation input digest mismatch."
    if "lean-code" not in impl_input.quality_profiles:
        return "Lean Code profile is not enabled for this implementation loop."
    return ""


def _outcome(
    options: LeanExecutionOptions,
    exit_code: int,
    output: str,
) -> tuple[bool, str]:
    if options.purpose == "regression-red":
        if exit_code == 0:
            return False, "RED command unexpectedly passed."
        if not options.failure_signature or options.failure_signature not in output:
            return False, "RED output does not contain the target failure signature."
        return True, ""
    return (True, "") if exit_code == 0 else (False, "Verification command failed.")


def _outcome_from_receipt(
    receipt: LeanCommandExecutionReceipt,
    output: str,
) -> tuple[bool, str]:
    options = LeanExecutionOptions(
        root=Path(),
        loop_id=receipt.loop_id,
        purpose=receipt.purpose,
        command_argv=tuple(receipt.command_argv),
        failure_signature=receipt.failure_signature,
    )
    return _outcome(options, receipt.exit_code, output)


def _run_dir(root: Path, loop_id: str, receipt_id: str) -> Path:
    if not _SAFE_ID.fullmatch(receipt_id):
        raise ValueError("Lean execution receipt id is unsafe.")
    return (
        implementation_artifacts(root, loop_id).loop_dir
        / "lean"
        / "executions"
        / receipt_id
    )


def _combined_output(stdout: str, stderr: str) -> str:
    return stdout + (f"\n[stderr]\n{stderr}" if stderr else "")


def _next_action(purpose: str, accepted: bool) -> str:
    if not accepted:
        return "Inspect the captured output and rerun the command after correction."
    if purpose == "regression-red":
        return "Apply the minimal fix, then capture the GREEN phase with the same test."
    return "Attach the receipt path to Implementation progress evidence."


def _blocked(message: str) -> LeanExecutionResult:
    return LeanExecutionResult(
        status="blocked",
        result="Lean command execution was not accepted.",
        blocker=message,
        next_action="Correct the command input and retry.",
    )


__all__ = [
    "LeanCommandExecutionReceipt",
    "LeanExecutionOptions",
    "LeanExecutionResult",
    "run_lean_command",
    "validate_execution_receipt",
]
