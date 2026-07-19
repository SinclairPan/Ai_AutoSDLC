"""Structured operator decisions for the bounded Lean repair loop."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from ai_sdlc.core.implementation_store import (
    implementation_artifacts,
    repo_relative_path,
)
from ai_sdlc.core.lean_code_artifacts import read_current_pointer, safe_project_path
from ai_sdlc.core.lean_code_models import LeanEvaluationReport, LeanNoGoDecision
from ai_sdlc.core.lean_code_policy import stable_artifact_digest
from ai_sdlc.core.loop_artifacts import LoopArtifactStore
from ai_sdlc.core.loop_models import LoopRound, LoopStatus, utc_now_iso
from ai_sdlc.core.source_snapshot import SourceSnapshot, revalidate_source_snapshot


@dataclass(frozen=True)
class LeanNoGoOptions:
    """Operator inputs for one explicit bounded-loop No-Go decision."""

    root: Path
    loop_id: str = ""
    reason: str = ""
    owner: str = ""
    repair_cost: str = ""
    expected_benefit: str = ""
    evidence_refs: tuple[str, ...] = ()


def persist_lean_no_go(
    root: Path,
    loop_run,
    impl_input,
    report: LeanEvaluationReport,
    options: LeanNoGoOptions,
) -> tuple[LeanNoGoDecision | None, Path | None, str]:
    """Validate, persist, and attach one No-Go decision to the existing loop."""

    blocker = _no_go_input_blocker(root, options, report)
    if blocker:
        return None, None, blocker
    decision = _build_no_go_decision(root, options, impl_input, report)
    path = (
        implementation_artifacts(root, loop_run.loop_id).loop_dir
        / "lean"
        / "no-go.json"
    )
    LoopArtifactStore(root).write_json_artifact(path, decision)
    _record_no_go_round(root, loop_run, path, decision)
    return decision, path, ""


def _no_go_input_blocker(
    root: Path,
    options: LeanNoGoOptions,
    report: LeanEvaluationReport,
) -> str:
    if report.status not in {LoopStatus.NEEDS_FIX, LoopStatus.NEEDS_USER}:
        return "Lean No-Go is only valid for an unresolved bounded evaluation."
    freshness_blocker = _no_go_freshness_blocker(root, report)
    if freshness_blocker:
        return freshness_blocker
    required = [
        options.reason,
        options.owner,
        options.repair_cost,
        options.expected_benefit,
    ]
    if not all(value.strip() for value in required) or not options.evidence_refs:
        return "Lean No-Go requires reason, owner, cost, benefit, and evidence."
    for reference in options.evidence_refs:
        if not safe_project_path(root, reference).is_file():
            return f"Lean No-Go evidence is missing: {reference}"
    return ""


def _no_go_freshness_blocker(root: Path, report: LeanEvaluationReport) -> str:
    pointer = read_current_pointer(root, report.loop_id)
    report_path = safe_project_path(root, pointer.report_path)
    snapshot = SourceSnapshot.model_validate_json(
        (report_path.parent / "source-snapshot.json").read_text("utf-8")
    )
    freshness = revalidate_source_snapshot(root, snapshot)
    if not freshness.fresh:
        return f"Lean No-Go evaluation is stale: {freshness.reason}."
    if snapshot.diff_hash != report.diff_hash:
        return "Lean No-Go evaluation is stale: diff hash mismatch."
    return ""


def _build_no_go_decision(root, options, impl_input, report) -> LeanNoGoDecision:
    return LeanNoGoDecision(
        decision_id=f"no-go-{report.evaluation_round}-{report.diff_hash[-12:]}",
        loop_id=report.loop_id,
        work_item_id=impl_input.work_item_id,
        reason=options.reason,
        owner=options.owner,
        repair_cost=options.repair_cost,
        expected_benefit=options.expected_benefit,
        evidence_refs=list(options.evidence_refs),
        evidence_digests={
            reference: "sha256:"
            + hashlib.sha256(
                safe_project_path(root, reference).read_bytes()
            ).hexdigest()
            for reference in options.evidence_refs
        },
        diff_hash=report.diff_hash,
        policy_digest=report.policy_digest,
        report_digest=stable_artifact_digest(report),
    )


def _record_no_go_round(root, loop_run, decision_path, decision) -> None:
    sequence = max((item.round_number for item in loop_run.rounds), default=0) + 1
    loop_run.rounds.append(
        LoopRound(
            round_number=sequence,
            round_kind="lean-decision",
            output_artifacts=[repo_relative_path(root, decision_path)],
            command=["ai-sdlc", "loop", "implementation", "lean-no-go"],
            status=LoopStatus.NEEDS_USER,
            result=f"no_go:{decision.decision_id}",
            next_action="Review the No-Go evidence and rescope or stop.",
        )
    )
    loop_run.current_round = sequence
    loop_run.status = LoopStatus.NEEDS_USER
    loop_run.updated_at = utc_now_iso()
    loop_run.next_action = "Review the No-Go evidence and rescope or stop."
    LoopArtifactStore(root).write_json_artifact(
        implementation_artifacts(root, loop_run.loop_id).loop_run_path,
        loop_run,
    )


__all__ = ["LeanNoGoOptions", "persist_lean_no_go"]
