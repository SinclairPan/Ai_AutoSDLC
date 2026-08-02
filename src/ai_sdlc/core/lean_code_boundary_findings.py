"""生成需要人工确认的动态调用边界 finding。"""

from __future__ import annotations

from ai_sdlc.core.lean_code_findings import make_finding
from ai_sdlc.core.lean_code_models import FileMetric, FunctionMetric, LeanFinding
from ai_sdlc.core.pr_review_models import FindingSeverity


def _invocation_boundary_finding(
    file: FileMetric,
    function: FunctionMetric,
    round_number: int,
) -> LeanFinding:
    finding = make_finding(
        rule_id="lean.invocation-boundary",
        severity=FindingSeverity.ADVISORY,
        path=file.path,
        symbol=function.symbol,
        claim="Dynamic or framework invocation needs a bounded manual disposition.",
        measured=function.invocation_boundary,
        budget="manual-review-required",
        risk="Mechanical zero-caller enforcement could reject a real external entry point.",
        fix="Confirm the framework or callback registration with structured evidence.",
        verification="Bind an approved exception to this exact symbol and frozen diff.",
        round_number=round_number,
    )
    finding.evidence.extend(
        sorted(
            set(function.invocation_evidence)
            | set(function.unlinked_evidence)
            | set(function.reference_evidence)
        )
    )
    return finding


__all__: list[str] = []
