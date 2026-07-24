"""Evidence and independent-review validation for Lean exceptions."""

from __future__ import annotations

import ast
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from ai_sdlc.core.lean_code_models import (
    LeanException,
    LeanFinding,
    LeanReviewerDecisionArtifact,
    LeanReviewerFindingDecision,
)
from ai_sdlc.core.lean_code_reviewer_authority import (
    TrustedLeanReviewerExecution,
    resolve_reviewer_execution,
    reviewer_independence_issue,
)

_SEMANTIC_CONTRACT_KINDS = frozenset(
    {
        "decorator_registration",
        "direct_caller",
        "factory_registration",
        "module_entrypoint",
        "protocol_implementation",
        "schema_dispatch",
    }
)


def _expiry_issue(expires_at: str) -> str:
    if not expires_at:
        return "expiry is missing"
    try:
        expires = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
    except ValueError:
        return "expiry is malformed"
    if expires.tzinfo is None:
        return "expiry must include a timezone"
    return "exception has expired" if expires <= datetime.now(UTC) else ""


def _evidence_issue(root: Path, exception: LeanException) -> str:
    if not exception.evidence_refs:
        return "evidence is missing"
    for reference in exception.evidence_refs:
        try:
            path = (root / reference).resolve()
            path.relative_to(root.resolve())
            actual = f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"
        except (OSError, ValueError):
            return f"evidence is unavailable: {reference}"
        if exception.evidence_digests.get(reference) != actual:
            return f"evidence digest is stale: {reference}"
    return ""


def _reviewer_decision_issue(
    root: Path,
    exception: LeanException,
    target: LeanFinding,
) -> str:
    refs = exception.reviewer_decision_refs
    if len(refs) != 2 or len(set(refs)) != 2:
        return "exactly two independent reviewer decisions are required"
    artifacts: list[LeanReviewerDecisionArtifact] = []
    for reference in refs:
        artifact, issue = _load_reviewer_decision(root, exception, reference)
        if issue:
            return issue
        assert artifact is not None
        artifacts.append(artifact)
    executions: list[TrustedLeanReviewerExecution] = []
    for artifact in artifacts:
        execution, issue = resolve_reviewer_execution(root, artifact)
        if issue:
            return issue
        assert execution is not None
        executions.append(execution)
    issue = reviewer_independence_issue(executions)
    if issue:
        return issue
    expected_approver = "+".join(sorted(item.reviewer_id for item in artifacts))
    if exception.approver != expected_approver:
        return "approver does not match the bound reviewer identities"
    for artifact in artifacts:
        issue = _reviewer_finding_issue(root, exception, target, artifact)
        if issue:
            return issue
    return ""


def _load_reviewer_decision(
    root: Path,
    exception: LeanException,
    reference: str,
) -> tuple[LeanReviewerDecisionArtifact | None, str]:
    digest_issue, path = _reference_digest_issue(
        root,
        reference,
        exception.reviewer_decision_digests.get(reference, ""),
    )
    if digest_issue:
        return None, f"reviewer decision {digest_issue}"
    try:
        return LeanReviewerDecisionArtifact.model_validate_json(path.read_bytes()), ""
    except ValueError:
        return None, f"reviewer decision is malformed: {reference}"


def _reviewer_finding_issue(
    root: Path,
    exception: LeanException,
    target: LeanFinding,
    artifact: LeanReviewerDecisionArtifact,
) -> str:
    lineage = (artifact.diff_hash, artifact.policy_digest, artifact.evaluation_digest)
    expected = (exception.diff_hash, exception.policy_digest, exception.evaluation_digest)
    if lineage != expected:
        return f"reviewer decision lineage is stale: {artifact.reviewer_id}"
    matches = [
        item
        for item in artifact.decisions
        if item.stable_signature == target.stable_signature
    ]
    if len(matches) != 1:
        return f"reviewer decision does not bind the finding: {artifact.reviewer_id}"
    decision = matches[0]
    if (decision.rule_id, decision.path, decision.symbol) != (
        target.rule_id,
        target.path,
        target.symbol,
    ):
        return f"reviewer decision target diverged: {artifact.reviewer_id}"
    if decision.verdict != "approved":
        return f"reviewer rejected the exception: {artifact.reviewer_id}"
    return _semantic_evidence_issue(root, artifact.reviewer_id, decision)


def _semantic_evidence_issue(
    root: Path,
    reviewer_id: str,
    decision: LeanReviewerFindingDecision,
) -> str:
    if decision.contract_kind not in _SEMANTIC_CONTRACT_KINDS:
        return f"reviewer contract kind is unsupported: {reviewer_id}"
    contract_issue, _ = _reference_digest_issue(
        root, decision.contract_path, decision.contract_digest
    )
    if contract_issue:
        return f"reviewer contract {contract_issue}: {reviewer_id}"
    locator_issue = _locator_issue(root, decision)
    if locator_issue:
        return f"reviewer locator {locator_issue}: {reviewer_id}"
    for reference in decision.verification_evidence_refs:
        issue, _ = _reference_digest_issue(
            root,
            reference,
            decision.verification_evidence_digests.get(reference, ""),
        )
        if issue:
            return f"reviewer verification {issue}: {reviewer_id}"
    return ""


def _locator_issue(root: Path, decision: LeanReviewerFindingDecision) -> str:
    matched_contract = False
    for locator in decision.exact_locators:
        try:
            reference, symbol, raw_line = locator.rsplit(":", 2)
            line = int(raw_line)
            path = (root / reference).resolve()
            path.relative_to(root.resolve())
            source = path.read_text(encoding="utf-8")
            lines = source.splitlines()
        except (OSError, UnicodeError, ValueError):
            return f"is invalid: {locator}"
        if not symbol.strip() or line < 1 or line > len(lines):
            return f"is out of bounds: {locator}"
        actual = exact_locator_digest(reference, symbol, line, lines[line - 1])
        if decision.exact_locator_digests.get(locator) != actual:
            return f"digest is stale: {locator}"
        if not _locator_symbol_matches(path, source, symbol, line):
            return f"symbol does not bind the source: {locator}"
        matched_contract |= (
            reference == decision.contract_path and symbol == decision.contract_symbol
        )
    return "" if matched_contract else "does not bind the declared contract"


def exact_locator_digest(reference: str, symbol: str, line: int, text: str) -> str:
    payload = f"{reference}\0{symbol}\0{line}\0{text}".encode()
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def reviewer_decision_payload_digest(
    diff_hash: str,
    policy_digest: str,
    evaluation_digest: str,
    decisions: list[LeanReviewerFindingDecision] | list[dict[str, object]],
) -> str:
    normalized = [
        item.model_dump(mode="json")
        if isinstance(item, LeanReviewerFindingDecision)
        else item
        for item in decisions
    ]
    payload = {
        "diff_hash": diff_hash,
        "policy_digest": policy_digest,
        "evaluation_digest": evaluation_digest,
        "decisions": normalized,
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f"sha256:{hashlib.sha256(encoded.encode()).hexdigest()}"


def _locator_symbol_matches(path: Path, source: str, symbol: str, line: int) -> bool:
    if symbol == "<module>":
        return bool(source.splitlines()[line - 1].strip())
    if path.suffix != ".py":
        return symbol in source.splitlines()[line - 1]
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return False
    spans = _python_symbol_spans(tree)
    return any(name == symbol and start <= line <= end for name, start, end in spans)


def _python_symbol_spans(tree: ast.AST) -> list[tuple[str, int, int]]:
    spans: list[tuple[str, int, int]] = []

    def visit(node: ast.AST, prefix: tuple[str, ...]) -> None:
        for child in ast.iter_child_nodes(node):
            current = prefix
            if isinstance(child, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                current = (*prefix, child.name)
                spans.append(
                    (".".join(current), child.lineno, child.end_lineno or child.lineno)
                )
            visit(child, current)

    visit(tree, ())
    return spans


def _reference_digest_issue(
    root: Path,
    reference: str,
    expected_digest: str,
) -> tuple[str, Path]:
    path = (root / reference).resolve()
    try:
        path.relative_to(root.resolve())
        actual = f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"
    except (OSError, ValueError):
        return f"is unavailable: {reference}", path
    if expected_digest and expected_digest != actual:
        return f"digest is stale: {reference}", path
    return "", path


__all__ = ["exact_locator_digest", "reviewer_decision_payload_digest"]
