"""按归因授权把唯一 Candidate 安全应用为 Challenger Snapshot。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import cast

from ai_sdlc.core.stage_review.artifact_compat import JsonValue
from ai_sdlc.core.stage_review.artifacts import SharedStateIntegrityError
from ai_sdlc.core.stage_review.binding_policy import (
    BindingPolicy,
    independence_satisfies,
)
from ai_sdlc.core.stage_review.capability_mapping import CapabilityMappingPolicy
from ai_sdlc.core.stage_review.optimization.attribution import FindingAttribution
from ai_sdlc.core.stage_review.optimization.candidate_domain_registry import (
    CandidateDomainRegistry,
)
from ai_sdlc.core.stage_review.optimization.models import (
    OptimizationCandidate,
    OptimizationPatchOperation,
)
from ai_sdlc.core.stage_review.optimization.snapshot_models import OptimizationSnapshot
from ai_sdlc.core.stage_review.panel import build_budget_policy
from ai_sdlc.core.stage_review.registry import (
    build_selection_policy,
    default_registry_bundle,
)
from ai_sdlc.core.stage_review.registry_models import CapabilityRequirementRule
from ai_sdlc.core.stage_review.resource_builders import stable_id
from ai_sdlc.core.stage_review.role_profiles import RoleProfilePolicy

_RUNTIME_METADATA = {"created_at", "created_by", "ai_sdlc_version"}


class CandidatePolicyApplier:
    def __init__(self, domain_registry: CandidateDomainRegistry | None = None) -> None:
        if domain_registry is None:
            from ai_sdlc.core.stage_review.optimization.candidate_domain_defaults import (
                default_candidate_domain_registry,
            )

            domain_registry = default_candidate_domain_registry()
        self.domain_registry = domain_registry

    def apply(
        self,
        candidate: OptimizationCandidate,
        *,
        base_snapshot: OptimizationSnapshot,
        attributions: tuple[FindingAttribution, ...],
        evaluation_report_digests: tuple[str, ...],
        created_at: str,
    ) -> OptimizationSnapshot:
        trusted = OptimizationCandidate.model_validate(
            candidate.model_dump(mode="json")
        )
        baseline = OptimizationSnapshot.model_validate(
            base_snapshot.model_dump(mode="json")
        )
        self.domain_registry.require_candidate(trusted)
        _verify_lineage(trusted, baseline, attributions, evaluation_report_digests)
        payload = baseline.model_dump(mode="json")["policy_payload"]
        if not isinstance(payload, dict):  # pragma: no cover - Snapshot 已验证。
            raise SharedStateIntegrityError("optimization policy payload is invalid")
        self.domain_registry.apply_patch(trusted, payload)
        self.domain_registry.validate_payload(
            trusted,
            payload,
            baseline.policy_payload,
        )
        return OptimizationSnapshot(
            snapshot_id=stable_id("optimization-snapshot", trusted.candidate_digest),
            project_id=baseline.project_id,
            parent_snapshot_digest=baseline.snapshot_digest,
            stable_fallback_digest=baseline.snapshot_digest,
            candidate_digest=trusted.candidate_digest,
            evaluation_report_digests=evaluation_report_digests,
            policy_payload=payload,
            created_at=created_at,
        )


def _verify_lineage(
    candidate: OptimizationCandidate,
    baseline: OptimizationSnapshot,
    attributions: tuple[FindingAttribution, ...],
    reports: tuple[str, ...],
) -> None:
    if (
        candidate.base_snapshot_digest != baseline.snapshot_digest
        or candidate.rollback_target != baseline.snapshot_digest
    ):
        raise SharedStateIntegrityError("candidate baseline lineage diverged")
    if not reports or reports != tuple(sorted(set(reports))):
        raise SharedStateIntegrityError("candidate evaluation lineage is invalid")
    if candidate.metric_evidence_digests:
        if attributions or not candidate.metric_evidence_digests:
            raise SharedStateIntegrityError("candidate metric lineage is invalid")
        return
    trusted = {
        item.attribution_digest: FindingAttribution.model_validate(
            item.model_dump(mode="json")
        )
        for item in attributions
    }
    if set(candidate.attribution_digests) != set(trusted):
        raise SharedStateIntegrityError("candidate attribution lineage diverged")
    if any(
        item.status != "candidate_authorized"
        or item.candidate_domain != candidate.candidate_domain
        for item in trusted.values()
    ):
        raise SharedStateIntegrityError("candidate attribution is not authorized")


def _apply_operation(
    payload: dict[str, JsonValue], operation: OptimizationPatchOperation
) -> None:
    if operation.operation != "replace":
        raise SharedStateIntegrityError("optimization patch operation is unsupported")
    parts = operation.field_path.split(".")
    target: dict[str, JsonValue] = payload
    for part in parts[:-1]:
        child = target.get(part)
        if not isinstance(child, Mapping):
            raise SharedStateIntegrityError("optimization patch target is missing")
        target = dict(child)
        _replace_child(payload, parts[: parts.index(part)], part, target)
    if parts[-1] not in target:
        raise SharedStateIntegrityError("optimization patch target is missing")
    target[parts[-1]] = operation.value


def _replace_child(
    root: dict[str, JsonValue], parents: list[str], key: str, value: dict[str, JsonValue]
) -> None:
    target = root
    for parent in parents:
        child = target[parent]
        if not isinstance(child, dict):
            raise SharedStateIntegrityError("optimization patch parent is invalid")
        target = child
    target[key] = value


def validate_selection_domain(
    payload: dict[str, JsonValue],
    _baseline: Mapping[str, JsonValue],
) -> None:
    payload["selection_policy"] = _selection_payload(payload)


def validate_budget_domain(
    payload: dict[str, JsonValue],
    _baseline: Mapping[str, JsonValue],
) -> None:
    _validate_budget_payload(payload)


def validate_binding_domain(
    payload: dict[str, JsonValue],
    baseline: Mapping[str, JsonValue],
) -> None:
    payload["binding_policy"] = _binding_payload(payload, baseline)


def validate_role_profile_domain(
    payload: dict[str, JsonValue],
    baseline: Mapping[str, JsonValue],
) -> None:
    payload["role_profiles"] = _role_profile_payload(payload, baseline)


def validate_capability_mapping_domain(
    payload: dict[str, JsonValue],
    _baseline: Mapping[str, JsonValue],
) -> None:
    payload["capability_mapping"] = _capability_mapping_payload(payload)


def _binding_payload(
    payload: Mapping[str, JsonValue],
    baseline: Mapping[str, JsonValue],
) -> dict[str, JsonValue]:
    current = BindingPolicy.model_validate(payload.get("binding_policy"))
    previous = BindingPolicy.model_validate(baseline.get("binding_policy"))
    if previous.require_independent_blocking_slots and not (
        current.require_independent_blocking_slots
    ):
        raise SharedStateIntegrityError("binding candidate weakens independence")
    if not independence_satisfies(
        current.minimum_blocking_independence_grade,
        previous.minimum_blocking_independence_grade,
    ):
        raise SharedStateIntegrityError("binding candidate lowers independence grade")
    return current.model_dump(mode="json")


def _role_profile_payload(
    payload: Mapping[str, JsonValue],
    baseline: Mapping[str, JsonValue],
) -> dict[str, JsonValue]:
    current = RoleProfilePolicy.model_validate(payload.get("role_profiles"))
    previous = RoleProfilePolicy.model_validate(baseline.get("role_profiles"))
    if current.module_digests != previous.module_digests:
        raise SharedStateIntegrityError("role profile candidate changed module catalog")
    return current.model_dump(mode="json")


def _capability_mapping_payload(
    payload: Mapping[str, JsonValue],
) -> dict[str, JsonValue]:
    policy = CapabilityMappingPolicy.model_validate(
        payload.get("capability_mapping")
    )
    packaged = default_registry_bundle().registry.registry_digest
    if policy.registry_digest != packaged:
        raise SharedStateIntegrityError("capability mapping registry is unavailable")
    return policy.model_dump(mode="json")


def _selection_payload(payload: dict[str, JsonValue]) -> dict[str, JsonValue]:
    raw = payload.get("selection_policy")
    if not isinstance(raw, Mapping):
        raise SharedStateIntegrityError("selection policy payload is missing")
    values = cast(Mapping[str, JsonValue], raw)
    policy = build_selection_policy(
        policy_id=str(values["policy_id"]),
        version=str(values["version"]),
        registry_compatibility_range=str(values["registry_compatibility_range"]),
        merge_semantics_version=str(values["merge_semantics_version"]),
        owner=str(values["owner"]),
        review_date=str(values["review_date"]),
        allowed_blocking_authority_ids=_strings(values["allowed_blocking_authority_ids"]),
        enabled_module_ids=_strings(values["enabled_module_ids"]),
        constraint_conflicts=_pairs(values["constraint_conflicts"]),
        minimum_slots=_integer(values["minimum_slots"]),
        minimum_distinct_primary_dimensions=_integer(
            values["minimum_distinct_primary_dimensions"]
        ),
        optional_slot_limit=_integer(values["optional_slot_limit"]),
        advisory_slot_limit=_integer(values["advisory_slot_limit"]),
        shadow_slot_limit=_integer(values["shadow_slot_limit"]),
        double_coverage_risk_levels=_strings(values["double_coverage_risk_levels"]),
        capability_requirement_rules=_rules(values["capability_requirement_rules"]),
    )
    return policy.model_dump(mode="json", exclude=_RUNTIME_METADATA)


def _strings(value: JsonValue) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise SharedStateIntegrityError("selection policy string set is invalid")
    return tuple(str(item) for item in value)


def _pairs(value: JsonValue) -> tuple[tuple[str, str], ...]:
    if not isinstance(value, list):
        raise SharedStateIntegrityError("selection policy pair set is invalid")
    pairs: list[tuple[str, str]] = []
    for item in value:
        if not isinstance(item, list) or len(item) != 2:
            raise SharedStateIntegrityError("selection policy pair is invalid")
        pairs.append((str(item[0]), str(item[1])))
    return tuple(pairs)


def _integer(value: JsonValue) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise SharedStateIntegrityError("selection policy integer is invalid")
    return value


def _rules(value: JsonValue) -> tuple[CapabilityRequirementRule, ...]:
    if not isinstance(value, list):
        raise SharedStateIntegrityError("selection policy rules are invalid")
    return tuple(CapabilityRequirementRule.model_validate(item) for item in value)


def _validate_budget_payload(payload: dict[str, JsonValue]) -> None:
    policies = payload.get("budget_policy")
    if not isinstance(policies, Mapping):
        raise SharedStateIntegrityError("budget policy payload is missing")
    rebuilt: dict[str, JsonValue] = {}
    for risk, raw in policies.items():
        if not isinstance(raw, Mapping):
            raise SharedStateIntegrityError("budget policy entry is invalid")
        values = dict(raw)
        values.pop("policy_digest", None)
        rebuilt[str(risk)] = build_budget_policy(**values).model_dump(mode="json")
    payload["budget_policy"] = rebuilt
