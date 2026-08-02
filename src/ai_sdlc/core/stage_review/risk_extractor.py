"""从冻结 Candidate 变更面提取不可降级的确定性风险事实。"""

from __future__ import annotations

from collections.abc import Iterable

from ai_sdlc.core.stage_review.candidate import (
    CandidateManifest,
    candidate_binding_digest,
)
from ai_sdlc.core.stage_review.canonical import (
    CanonicalizationPolicy,
    canonical_digest,
)
from ai_sdlc.core.stage_review.contracts import (
    RiskFact,
    RiskSeverity,
    TaskRiskProfile,
    reconcile_risk_profile,
)

_SECURITY_MARKERS = (
    "auth",
    "crypto",
    "network",
    "oauth",
    "permission",
    "secret",
    "security",
    "token",
)
_DATA_MARKERS = ("database", "migration", "schema", "transaction")
_DELIVERY_MARKERS = (
    ".github/workflows/",
    "dockerfile",
    "package.json",
    "pyproject.toml",
    "setup.py",
)
_UI_SUFFIXES = (".css", ".html", ".jsx", ".tsx", ".vue")


def _extract_task_risk_profile(candidate: CandidateManifest) -> TaskRiskProfile:
    trusted = CandidateManifest.model_validate(candidate.model_dump(mode="json"))
    paths = tuple(path.lower() for path in trusted.change_surface)
    facts = [
        _fact(trusted, name, severity, capability)
        for name, severity, capability in _risk_rules(trusted.stage_key, paths)
    ]
    return reconcile_risk_profile(
        work_item_id=trusted.work_item_id,
        stage_key=trusted.stage_key,
        deterministic_facts=facts,
        semantic_suggestions=[],
    )


def _classify_change_surface_risk(
    stage_key: str,
    paths: Iterable[str],
) -> RiskSeverity:
    normalized = tuple(path.lower() for path in paths)
    severities = [item[1] for item in _risk_rules(stage_key, normalized)]
    order = {"low": 0, "medium": 1, "high": 2, "critical": 3}
    return max(severities, key=order.__getitem__)


def _risk_rules(
    stage_key: str,
    paths: tuple[str, ...],
) -> tuple[tuple[str, RiskSeverity, str], ...]:
    rules: list[tuple[str, RiskSeverity, str]] = [
        ("correctness", "low", "capability.correctness")
    ]
    if _contains(paths, _DELIVERY_MARKERS):
        rules.append(
            ("delivery-operability", "medium", "capability.delivery-operability")
        )
    if _contains(paths, _SECURITY_MARKERS):
        rules.append(("security", "high", "capability.security"))
    if _contains(paths, _DATA_MARKERS):
        rules.append(("data-integrity", "high", "capability.data-integrity"))
    if _is_user_journey_change(stage_key, paths):
        rules.append(("user-journey", "low", "capability.user-journey"))
    return tuple(rules)


def _fact(
    candidate: CandidateManifest,
    name: str,
    severity: str,
    capability_id: str,
) -> RiskFact:
    candidate_digest = candidate_binding_digest(candidate)
    return RiskFact.model_validate(
        {
            "risk_fact_id": f"risk.{name}",
            "source_ref": f"candidate:{candidate_digest}",
            "extractor_version": "candidate-path-rules.v1",
            "confidence": 1.0,
            "severity": severity,
            "required_capability_ids": [capability_id],
            "evidence_digest": canonical_digest(
                {"candidate_digest": candidate_digest, "rule": name},
                CanonicalizationPolicy(),
            ),
        }
    )


def _contains(paths: Iterable[str], markers: tuple[str, ...]) -> bool:
    return any(marker in path for path in paths for marker in markers)


def _is_user_journey_change(stage_key: str, paths: tuple[str, ...]) -> bool:
    return stage_key in {"frontend-evidence", "local-pr-review"} or any(
        path.endswith(_UI_SUFFIXES) for path in paths
    )
