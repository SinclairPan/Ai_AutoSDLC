"""Status projection for deterministic Lean evaluation findings."""

from __future__ import annotations

from ai_sdlc.core.lean_code_models import LeanFinding, LeanPolicy
from ai_sdlc.core.loop_models import LoopStatus
from ai_sdlc.core.pr_review_models import FindingResolutionStatus, FindingSeverity
from ai_sdlc.models.work import WorkType


def _evaluation_status(
    work_type: WorkType,
    unknown_files: list[str],
    unsupported_files: list[str],
    findings: list[LeanFinding],
    policy: LeanPolicy,
) -> LoopStatus:
    if work_type == WorkType.UNCERTAIN or _capability_boundary_needs_user(
        unknown_files, unsupported_files, findings
    ):
        return LoopStatus.NEEDS_USER
    unresolved = [
        item
        for item in findings
        if item.resolution not in _ACCEPTED_RESOLUTIONS
    ]
    if any(item.severity == FindingSeverity.BLOCKER for item in unresolved):
        return LoopStatus.NEEDS_FIX
    if any(item.severity == FindingSeverity.REQUIRED for item in unresolved):
        mode = str(policy.enforcement_mode)
        if mode == "blocking":
            return LoopStatus.BLOCKED
        if mode == "warning":
            return LoopStatus.NEEDS_FIX
    return LoopStatus.PASSED


def _capability_boundary_needs_user(
    unknown_files: list[str],
    unsupported_files: list[str],
    findings: list[LeanFinding],
) -> bool:
    dispositions = {
        (item.rule_id, item.path, item.symbol): item.resolution for item in findings
    }
    boundaries = [
        *(("lean.classification-unknown", path, "") for path in unknown_files),
        *(("lean.semantic-capability", path, "") for path in unsupported_files),
        *(
            (item.rule_id, item.path, item.symbol)
            for item in findings
            if item.rule_id == "lean.invocation-boundary"
        ),
    ]
    return any(
        dispositions.get(boundary) not in _ACCEPTED_RESOLUTIONS
        for boundary in boundaries
    )


def _unresolved_actionable_signatures(current: list[LeanFinding]) -> list[str]:
    return sorted(
        item.stable_signature
        for item in current
        if item.severity in {FindingSeverity.BLOCKER, FindingSeverity.REQUIRED}
        and item.resolution not in _ACCEPTED_RESOLUTIONS
    )


_ACCEPTED_RESOLUTIONS = {
    FindingResolutionStatus.FIXED,
    FindingResolutionStatus.WAIVED,
    FindingResolutionStatus.NOT_APPLICABLE,
}


__all__: list[str] = []
