"""Integrity checks for Lean Code regression and targeted-verification evidence."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from ai_sdlc.core.implementation_models import ImplementationInput
from ai_sdlc.core.implementation_store import implementation_artifacts, read_progress
from ai_sdlc.core.lean_code_execution import validate_execution_receipt
from ai_sdlc.core.lean_code_models import RegressionEvidence


def regression_evidence_issue(
    root: Path,
    evidence: RegressionEvidence,
    expected_loop_id: str = "",
) -> str:
    """Return why self-reported RED/GREEN fields lack verifiable local artifacts."""

    references = (
        (evidence.red_output_ref, evidence.red_output_digest, "RED output"),
        (evidence.green_output_ref, evidence.green_output_digest, "GREEN output"),
        (evidence.test_source_ref, evidence.test_source_digest, "test source"),
        (evidence.red_receipt_ref, evidence.red_receipt_digest, "RED receipt"),
        (evidence.green_receipt_ref, evidence.green_receipt_digest, "GREEN receipt"),
    )
    for reference, expected, label in references:
        if not reference or not expected:
            return f"{label} binding is missing"
        try:
            path = _safe_path(root, reference)
            actual = _digest(path.read_bytes())
        except (OSError, ValueError):
            return f"{label} is unavailable: {reference}"
        if actual != expected:
            return f"{label} digest is stale: {reference}"
    try:
        red_output = _safe_path(root, evidence.red_output_ref).read_text(
            "utf-8", errors="strict"
        )
    except (OSError, UnicodeDecodeError, ValueError):
        return "RED output is not readable UTF-8 evidence"
    if evidence.failure_signature not in red_output:
        return "RED output does not contain the target failure signature"
    if not evidence.command_argv or not evidence.test_refs:
        return "replay command or test reference is missing"
    return _regression_receipt_issue(root, evidence, expected_loop_id)


def _regression_receipt_issue(
    root: Path,
    evidence: RegressionEvidence,
    expected_loop_id: str,
) -> str:
    red, issue = validate_execution_receipt(
        root,
        evidence.red_receipt_ref,
        expected_digest=evidence.red_receipt_digest,
        expected_purpose="regression-red",
        expected_loop_id=expected_loop_id,
    )
    if issue or red is None:
        return f"RED execution receipt is invalid: {issue}"
    green, issue = validate_execution_receipt(
        root,
        evidence.green_receipt_ref,
        expected_digest=evidence.green_receipt_digest,
        expected_purpose="regression-green",
        expected_loop_id=expected_loop_id,
    )
    if issue or green is None:
        return f"GREEN execution receipt is invalid: {issue}"
    return _regression_field_issue(red, green, evidence)


def _regression_field_issue(red, green, evidence: RegressionEvidence) -> str:
    expected = (
        (red.command_argv, evidence.command_argv, "command argv"),
        (green.command_argv, evidence.command_argv, "GREEN command argv"),
        (red.cwd, evidence.cwd, "working directory"),
        (green.cwd, evidence.cwd, "GREEN working directory"),
        (red.diff_hash, evidence.red_diff_hash, "RED diff"),
        (green.diff_hash, evidence.green_diff_hash, "GREEN diff"),
        (red.exit_code, evidence.red_exit_code, "RED exit code"),
        (green.exit_code, evidence.green_exit_code, "GREEN exit code"),
        (red.source_snapshot_digest, evidence.red_source, "RED source"),
        (green.source_snapshot_digest, evidence.green_source, "GREEN source"),
        (red.output_ref, evidence.red_output_ref, "RED output"),
        (green.output_ref, evidence.green_output_ref, "GREEN output"),
        (red.test_source_digest, evidence.test_source_digest, "test source"),
        (red.test_source_ref, evidence.test_source_ref, "test source path"),
        (red.failure_signature, evidence.failure_signature, "failure signature"),
        (red.toolchain_fingerprint, evidence.toolchain_fingerprint, "toolchain"),
    )
    for actual, declared, label in expected:
        if actual != declared:
            return f"{label} does not match the controlled receipt"
    if red.test_source_digest != green.test_source_digest:
        return "test source changed between RED and GREEN"
    if red.toolchain_fingerprint != green.toolchain_fingerprint:
        return "toolchain changed between RED and GREEN"
    if red.source_snapshot_digest == green.source_snapshot_digest:
        return "RED and GREEN did not execute against different source identities"
    return ""


def verification_digest(references: tuple[str, ...]) -> str:
    """Hash stable targeted-verification references for round-to-round freshness."""

    payload = json.dumps(
        sorted(set(references)), ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    return _digest(payload)


def evidence_reference(root: Path, reference: str) -> str:
    """Bind an evidence path to bytes while preserving command-only references."""

    try:
        path = _safe_path(root, reference)
    except ValueError:
        return f"unavailable:{reference}"
    if path.is_file():
        return f"file:{reference}:{_digest(path.read_bytes())}"
    return f"command:{reference}"


def implementation_verification_refs(
    root: Path, impl_input: ImplementationInput, current_diff_hash: str = ""
) -> tuple[str, ...]:
    """Return only successful current-diff receipts, never command strings."""

    _refs, _digests, tokens = implementation_verification_artifacts(
        root, impl_input, current_diff_hash
    )
    return tokens


def implementation_verification_artifacts(
    root: Path, impl_input: ImplementationInput, current_diff_hash: str
) -> tuple[tuple[str, ...], dict[str, str], tuple[str, ...]]:
    """Validate runtime receipts and return paths, byte digests, and stable tokens."""

    path = implementation_artifacts(root, impl_input.loop_id).progress_path
    if not path.is_file():
        return (), {}, ()
    progress = read_progress(path)
    references: list[str] = []
    digests: dict[str, str] = {}
    for item in progress.tasks:
        for reference in item.evidence:
            if not _execution_reference(reference):
                continue
            path = _safe_path(root, reference)
            digest = _digest(path.read_bytes())
            receipt, issue = validate_execution_receipt(
                root,
                reference,
                expected_digest=digest,
                expected_purpose="targeted-verification",
                expected_loop_id=impl_input.loop_id,
            )
            if issue or receipt is None:
                raise ValueError(f"targeted verification receipt is invalid: {issue}")
            if current_diff_hash and receipt.diff_hash != current_diff_hash:
                continue
            references.append(reference)
            digests[reference] = digest
    ordered = tuple(sorted(set(references)))
    return ordered, digests, tuple(f"{item}:{digests[item]}" for item in ordered)


def _execution_reference(reference: str) -> bool:
    normalized = reference.replace("\\", "/")
    return "/lean/executions/" in normalized and normalized.endswith("/receipt.json")


def _safe_path(root: Path, reference: str) -> Path:
    path = (root / reference).resolve()
    path.relative_to(root.resolve())
    return path


def _digest(payload: bytes) -> str:
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


__all__ = [
    "implementation_verification_refs",
    "implementation_verification_artifacts",
    "regression_evidence_issue",
    "verification_digest",
]
