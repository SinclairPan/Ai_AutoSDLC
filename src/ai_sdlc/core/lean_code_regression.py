"""Two-phase controlled RED/GREEN evidence capture for Lean Code."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from ai_sdlc.core.implementation_store import (
    implementation_artifacts,
    repo_relative_path,
)
from ai_sdlc.core.lean_code_execution import (
    LeanExecutionOptions,
    run_lean_command,
    validate_execution_receipt,
)
from ai_sdlc.core.lean_code_execution_models import (
    LeanCommandExecutionReceipt,
    LeanExecutionResult,
)
from ai_sdlc.core.lean_code_models import RegressionEvidence
from ai_sdlc.core.loop_artifacts import LoopArtifactStore
from ai_sdlc.core.loop_models import LoopArtifactModel

_SAFE_ID = re.compile(r"^[A-Za-z0-9._-]+$")


class LeanRegressionCaptureState(LoopArtifactModel):
    """RED receipt and invariant inputs held until the GREEN execution."""

    artifact_kind: str = "lean-regression-capture-state"
    loop_id: str
    test_id: str
    test_symbol: str = ""
    command_argv: list[str]
    cwd: str = "."
    test_source_ref: str
    failure_signature: str
    source_kind: str = "local-unstaged"
    base_ref: str = ""
    head_ref: str = "HEAD"
    patch_file: str = ""
    red_receipt_ref: str
    red_receipt_digest: str


class LeanRegressionResult(BaseModel):
    """CLI-facing state for a RED or GREEN capture."""

    model_config = ConfigDict(extra="forbid")

    status: str
    result: str
    blocker: str = ""
    next_action: str = ""
    receipt_path: str = ""
    evidence_path: str = ""


@dataclass(frozen=True)
class LeanRegressionOptions:
    """Inputs shared by both controlled regression phases."""

    root: Path
    loop_id: str
    phase: str
    test_id: str
    command_argv: tuple[str, ...]
    test_source_ref: str
    failure_signature: str
    test_symbol: str = ""
    cwd: str = "."
    source_kind: str = "local-unstaged"
    base_ref: str = ""
    head_ref: str = "HEAD"
    patch_file: str = ""


def capture_regression_phase(options: LeanRegressionOptions) -> LeanRegressionResult:
    """Run one phase and create final evidence only after the same test turns GREEN."""

    if options.phase not in {"red", "green"}:
        return _blocked("Regression phase must be red or green.")
    if not _SAFE_ID.fullmatch(options.test_id):
        return _blocked("Regression test id is unsafe.")
    return _capture_red(options) if options.phase == "red" else _capture_green(options)


def _capture_red(options: LeanRegressionOptions) -> LeanRegressionResult:
    state_path, evidence_path = _capture_paths(
        options.root.resolve(), options.loop_id, options.test_id
    )
    if state_path.exists() or evidence_path.exists():
        return _blocked("Regression capture already exists for this test id.")
    result = run_lean_command(_execution_options(options, "regression-red"))
    if result.status != "ready":
        return _from_execution(result)
    state = LeanRegressionCaptureState(
        loop_id=options.loop_id,
        test_id=options.test_id,
        test_symbol=options.test_symbol,
        command_argv=list(options.command_argv),
        cwd=options.cwd,
        test_source_ref=options.test_source_ref,
        failure_signature=options.failure_signature,
        source_kind=options.source_kind,
        base_ref=options.base_ref,
        head_ref=options.head_ref,
        patch_file=options.patch_file,
        red_receipt_ref=result.receipt_path,
        red_receipt_digest=result.receipt_digest,
    )
    LoopArtifactStore(options.root.resolve()).write_json_artifact(state_path, state)
    return LeanRegressionResult(
        status="ready",
        result="Regression RED phase captured by controlled execution.",
        next_action="Apply the minimal fix and capture the GREEN phase.",
        receipt_path=result.receipt_path,
    )


def _capture_green(options: LeanRegressionOptions) -> LeanRegressionResult:
    root = options.root.resolve()
    state_path, evidence_path = _capture_paths(root, options.loop_id, options.test_id)
    try:
        state = LeanRegressionCaptureState.model_validate_json(
            state_path.read_text("utf-8")
        )
    except (OSError, ValueError) as exc:
        return _blocked(f"Regression RED state is unavailable: {exc}")
    issue = _state_issue(state, options)
    if issue:
        return _blocked(issue)
    red, issue = validate_execution_receipt(
        root,
        state.red_receipt_ref,
        expected_digest=state.red_receipt_digest,
        expected_purpose="regression-red",
        expected_loop_id=state.loop_id,
    )
    if issue or red is None:
        return _blocked(f"Regression RED receipt is invalid: {issue}")
    result = run_lean_command(_execution_options(options, "regression-green"))
    if result.status != "ready":
        return _from_execution(result)
    green, issue = validate_execution_receipt(
        root,
        result.receipt_path,
        expected_digest=result.receipt_digest,
        expected_purpose="regression-green",
        expected_loop_id=options.loop_id,
    )
    if issue or green is None:
        return _blocked(f"Regression GREEN receipt is invalid: {issue}")
    evidence = _regression_evidence(state, red, green, result)
    LoopArtifactStore(root).write_json_artifact(evidence_path, evidence)
    return LeanRegressionResult(
        status="ready",
        result="Regression RED/GREEN evidence captured by controlled execution.",
        next_action="Pass the evidence path to implementation lean-check.",
        receipt_path=result.receipt_path,
        evidence_path=repo_relative_path(root, evidence_path),
    )


def _regression_evidence(
    state: LeanRegressionCaptureState,
    red: LeanCommandExecutionReceipt,
    green: LeanCommandExecutionReceipt,
    green_result: LeanExecutionResult,
) -> RegressionEvidence:
    return RegressionEvidence(
        test_id=state.test_id,
        test_symbol=state.test_symbol,
        command_argv=red.command_argv,
        cwd=state.cwd,
        red_source=red.source_snapshot_digest,
        red_diff_hash=red.diff_hash,
        red_exit_code=red.exit_code,
        green_source=green.source_snapshot_digest,
        green_diff_hash=green.diff_hash,
        green_exit_code=green.exit_code,
        failure_signature=state.failure_signature,
        red_output_ref=red.output_ref,
        red_output_digest=red.output_digest,
        green_output_ref=green.output_ref,
        green_output_digest=green.output_digest,
        test_source_ref=red.test_source_ref,
        test_source_digest=red.test_source_digest,
        red_receipt_ref=state.red_receipt_ref,
        red_receipt_digest=state.red_receipt_digest,
        green_receipt_ref=green_result.receipt_path,
        green_receipt_digest=green_result.receipt_digest,
        toolchain_fingerprint=red.toolchain_fingerprint,
        test_refs=[state.test_symbol or state.test_source_ref],
    )


def _execution_options(
    options: LeanRegressionOptions,
    purpose: str,
) -> LeanExecutionOptions:
    return LeanExecutionOptions(
        root=options.root,
        loop_id=options.loop_id,
        purpose=purpose,
        command_argv=options.command_argv,
        cwd=options.cwd,
        test_source_ref=options.test_source_ref,
        failure_signature=options.failure_signature,
        source_kind=options.source_kind,
        base_ref=options.base_ref,
        head_ref=options.head_ref,
        patch_file=options.patch_file,
    )


def _state_issue(
    state: LeanRegressionCaptureState,
    options: LeanRegressionOptions,
) -> str:
    if state.loop_id != options.loop_id or state.test_id != options.test_id:
        return "Regression RED state identity does not match."
    if state.command_argv != list(options.command_argv) or state.cwd != options.cwd:
        return "GREEN must execute the same argv and cwd as RED."
    if (
        state.test_source_ref != options.test_source_ref
        or state.failure_signature != options.failure_signature
        or state.test_symbol != options.test_symbol
    ):
        return "GREEN test identity must match the RED phase."
    if (
        state.source_kind,
        state.base_ref,
        state.head_ref,
        state.patch_file,
    ) != (
        options.source_kind,
        options.base_ref,
        options.head_ref,
        options.patch_file,
    ):
        return "GREEN source selection must match the RED phase."
    return ""


def _capture_paths(root: Path, loop_id: str, test_id: str) -> tuple[Path, Path]:
    directory = (
        implementation_artifacts(root, loop_id).loop_dir
        / "lean"
        / "regressions"
        / test_id
    )
    return directory / "capture-state.json", directory / "regression-evidence.json"


def _from_execution(result: LeanExecutionResult) -> LeanRegressionResult:
    return LeanRegressionResult(
        status=result.status,
        result=result.result,
        blocker=result.blocker,
        next_action=result.next_action,
        receipt_path=result.receipt_path,
    )


def _blocked(message: str) -> LeanRegressionResult:
    return LeanRegressionResult(
        status="blocked",
        result="Regression evidence was not accepted.",
        blocker=message,
        next_action="Correct the regression capture input and retry.",
    )


__all__ = [
    "LeanRegressionOptions",
    "LeanRegressionResult",
    "capture_regression_phase",
]
