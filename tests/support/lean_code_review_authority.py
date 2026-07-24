"""Lean 风险接受测试使用的真实双审查执行夹具。"""

from __future__ import annotations

import ast
import hashlib
import json
import shutil
import tempfile
from pathlib import Path

from ai_sdlc.core.lean_code_exception_review import (
    exact_locator_digest,
    reviewer_decision_payload_digest,
)
from ai_sdlc.core.lean_code_models import LeanFinding, LeanPolicy
from ai_sdlc.core.lean_code_policy import stable_artifact_digest
from ai_sdlc.core.source_snapshot import SourceSnapshot
from ai_sdlc.core.stage_review.artifacts import resolve_canonical_shared_state
from ai_sdlc.core.stage_review.canonical_stage_review_support import execution_scope
from ai_sdlc.core.stage_review.shadow_planning_store import (
    _persist_shadow_plan as persist_shadow_plan,
)
from tests.integration.test_canonical_stage_review_executor import _executor_rig


def trusted_reviewer_decisions(
    root: Path,
    snapshot: SourceSnapshot,
    finding: LeanFinding,
    exception_id: str,
    evaluation_digest: str,
    verification_ref: str,
    verification_digest: str,
    reference_prefix: str,
) -> tuple[list[str], dict[str, str], str]:
    """Create two decisions bound to canonical independent executions."""

    contract = _reviewed_contract(root, finding)
    decision = _finding_decision(
        finding,
        verification_ref,
        verification_digest,
        contract,
    )
    decision_digest = reviewer_decision_payload_digest(
        snapshot.diff_hash,
        stable_artifact_digest(LeanPolicy()),
        evaluation_digest,
        [decision],
    )
    executions = _reviewer_executions(root, decision_digest)
    refs: list[str] = []
    digests: dict[str, str] = {}
    for review_pass, assignment in executions:
        ref = f"{reference_prefix}/{exception_id}-{review_pass.actor_id}.json"
        payload = _decision_payload(
            snapshot,
            exception_id,
            evaluation_digest,
            review_pass,
            assignment,
            decision,
            decision_digest,
        )
        path = root / ref
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
        refs.append(ref)
        digests[ref] = _file_digest(path)
    approver = "+".join(sorted(item.actor_id for item, _ in executions))
    return refs, digests, approver


def _reviewer_executions(root: Path, decision_digest: str):
    with tempfile.TemporaryDirectory(prefix="lean-review-authority-") as directory:
        review_root = Path(directory)
        services = []
        rig = _executor_rig(
            review_root,
            transport_available=True,
            on_authorized=services.append,
        )
        original_exchange = rig.broker.exchange

        def exchange(permit, envelope):
            response = original_exchange(permit, envelope)
            response["review"]["evidence_digests"] = [decision_digest]
            return response

        rig.broker.exchange = exchange
        persist_shadow_plan(
            review_root,
            rig.request.proposal,
            rig.request.plan,
            rig.request.source_snapshot,
        )
        outcome = rig.executor.execute(rig.request)
        assert outcome.status == "completed", outcome
        scope = execution_scope(rig.request)
        session = services[0].get(scope)
        passes = services[0].visible_passes(scope, session.active_cohort_id, "")
        executions = []
        for review_pass in passes:
            assignment = rig.executor._bindings.get_dispatch_assignment(
                review_pass.assignment_digest
            )
            assert assignment is not None
            executions.append((review_pass, assignment))
        assert len(executions) == 2
        _copy_authority(review_root, root, rig.request.candidate.project_id)
        return tuple(executions)


def _reviewed_contract(root: Path, finding: LeanFinding) -> dict[str, object]:
    path = root / finding.path
    source = path.read_text(encoding="utf-8")
    lines = source.splitlines()
    symbol, line = _contract_symbol_and_line(path, source, finding.symbol)
    locator = f"{finding.path}:{symbol}:{line}"
    kind = "direct_caller" if path.suffix == ".py" else "schema_dispatch"
    if symbol == "<module>":
        kind = "module_entrypoint"
    return {
        "kind": kind,
        "path": finding.path,
        "digest": _file_digest(path),
        "symbol": symbol,
        "locator": locator,
        "locator_digest": exact_locator_digest(
            finding.path, symbol, line, lines[line - 1]
        ),
    }


def _contract_symbol_and_line(
    path: Path, source: str, preferred: str
) -> tuple[str, int]:
    if path.suffix == ".py":
        definitions = [
            node
            for node in ast.walk(ast.parse(source))
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
        ]
        if definitions:
            selected = next(
                (item for item in definitions if item.name == preferred),
                definitions[0],
            )
            return selected.name, selected.lineno
        for index, text in enumerate(source.splitlines(), start=1):
            if text.strip():
                return "<module>", index
    for index, text in enumerate(source.splitlines(), start=1):
        if text.strip():
            symbol = preferred if preferred and preferred in text else text.split()[0]
            return symbol.strip("(){}[]:;"), index
    raise AssertionError("contract source is empty")


def _finding_decision(
    finding: LeanFinding,
    verification_ref: str,
    verification_digest: str,
    contract: dict[str, object],
) -> dict[str, object]:
    locator = str(contract["locator"])
    return {
        "stable_signature": finding.stable_signature,
        "rule_id": finding.rule_id,
        "path": finding.path,
        "symbol": finding.symbol,
        "verdict": "approved",
        "rationale": "Exact contract and verification evidence reviewed.",
        "contract_kind": contract["kind"],
        "contract_path": contract["path"],
        "contract_digest": contract["digest"],
        "contract_symbol": contract["symbol"],
        "exact_locators": [locator],
        "exact_locator_digests": {locator: contract["locator_digest"]},
        "verification_evidence_refs": [verification_ref],
        "verification_evidence_digests": {verification_ref: verification_digest},
    }


def _decision_payload(
    snapshot,
    exception_id,
    evaluation_digest,
    review_pass,
    assignment,
    decision,
    decision_digest,
):
    scope = review_pass.scope
    return {
        "artifact_kind": "lean-reviewer-decision",
        "decision_id": f"{exception_id}.{review_pass.actor_id}",
        "reviewer_id": review_pass.actor_id,
        "reviewer_role": review_pass.role_profile_id,
        "review_project_id": scope.project_id,
        "review_work_item_id": scope.work_item_id,
        "review_stage_instance_id": scope.stage_instance_id,
        "review_session_id": scope.session_id,
        "review_pass_id": review_pass.pass_id,
        "review_pass_digest": review_pass.pass_digest,
        "review_assignment_digest": assignment.assignment_digest,
        "decision_payload_digest": decision_digest,
        "diff_hash": snapshot.diff_hash,
        "policy_digest": stable_artifact_digest(LeanPolicy()),
        "evaluation_digest": evaluation_digest,
        "decisions": [decision],
    }


def _copy_authority(source: Path, target: Path, project_id: str) -> None:
    source_root = resolve_canonical_shared_state(source, project_id)
    target_root = resolve_canonical_shared_state(target, project_id)
    shutil.copytree(source_root, target_root, dirs_exist_ok=True)


def _file_digest(path: Path) -> str:
    return f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"
