"""Prospective Shadow Session 的候选分层匹配。"""

from __future__ import annotations

from collections.abc import Mapping

from ai_sdlc.core.stage_review.optimization.models import OptimizationCandidate
from ai_sdlc.core.stage_review.optimization.observations import CommittedSessionBinding


def matches_stratum_candidate(
    binding: CommittedSessionBinding,
    candidate: OptimizationCandidate,
) -> bool:
    return any(
        _matches_stratum(binding, stratum)
        for stratum in candidate.target_stratum_ids
    )


def matches_selection_candidate(
    binding: CommittedSessionBinding,
    candidate: OptimizationCandidate,
) -> bool:
    operation = candidate.patch_operations[0]
    if operation.field_path != "selection_policy.capability_requirement_rules":
        return False
    value = operation.value
    if not isinstance(value, list):
        return False
    rules = tuple(item for item in value if isinstance(item, Mapping))
    return _matches_rules(binding, rules)


def _matches_rules(
    binding: CommittedSessionBinding,
    rules: tuple[Mapping[str, object], ...],
) -> bool:
    return any(
        binding.stage_key in _rule_values(rule, "stage_keys")
        and binding.risk_level in _rule_values(rule, "risk_levels")
        for rule in rules
    )


def _matches_stratum(binding: CommittedSessionBinding, stratum: str) -> bool:
    parts = stratum.split(":", 3)
    if len(parts) < 2 or parts[0] != binding.stage_key:
        return False
    if parts[1] != binding.risk_level:
        return False
    if len(parts) >= 3 and parts[2] != binding.candidate_size_bucket:
        return False
    return len(parts) < 4 or parts[3] == "+".join(binding.provider_ids)


def _rule_values(rule: Mapping[str, object], key: str) -> tuple[str, ...]:
    raw = rule.get(key, ())
    if not isinstance(raw, (list, tuple)):
        return ()
    return tuple(str(item) for item in raw)


__all__ = ["matches_selection_candidate", "matches_stratum_candidate"]
