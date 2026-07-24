"""把确认事实唯一归因到允许的策略候选域或产品缺陷域。"""

from __future__ import annotations

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ai_sdlc.core.stage_review.artifact_compat import (
    ArtifactCompatibility,
    fill_artifact_digest,
)
from ai_sdlc.core.stage_review.optimization.models import CandidateDomain
from ai_sdlc.core.stage_review.registry_versions import require_machine_id
from ai_sdlc.core.stage_review.resource_builders import parse_utc, stable_id

AttributionCause = Literal[
    "panel_selection_gap",
    "reviewer_execution_miss",
    "evidence_visibility_gap",
    "risk_classification_gap",
    "role_contract_gap",
    "deterministic_gate_gap",
    "provider_quality_gap",
]
AttributionStatus = Literal["candidate_authorized", "product_defect", "no_change"]


class AttributionCausalFacts(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    required_capability_missing_from_panel: bool = False
    reviewer_completed_with_visible_evidence: bool = False
    evidence_not_visible_to_initial_cohort: bool = False
    risk_profile_omitted_required_capability: bool = False
    role_contract_omitted_required_capability: bool = False
    deterministic_gate_omitted_or_failed: bool = False
    provider_failure_confirmed: bool = False


def _causes_from_facts(facts: AttributionCausalFacts) -> tuple[AttributionCause, ...]:
    mapping: tuple[tuple[bool, AttributionCause], ...] = (
        (facts.required_capability_missing_from_panel, "panel_selection_gap"),
        (facts.reviewer_completed_with_visible_evidence, "reviewer_execution_miss"),
        (facts.evidence_not_visible_to_initial_cohort, "evidence_visibility_gap"),
        (facts.risk_profile_omitted_required_capability, "risk_classification_gap"),
        (facts.role_contract_omitted_required_capability, "role_contract_gap"),
        (facts.deterministic_gate_omitted_or_failed, "deterministic_gate_gap"),
        (facts.provider_failure_confirmed, "provider_quality_gap"),
    )
    return tuple(sorted(cause for present, cause in mapping if present))


class AttributionEvidence(ArtifactCompatibility):
    schema_version: Literal["attribution-evidence.v1"] = "attribution-evidence.v1"
    artifact_kind: Literal["attribution-evidence"] = "attribution-evidence"
    project_id: str
    session_id: str
    finding_key: str
    finding_event_digest: str
    evidence_digest: str
    original_candidate_digest: str
    discovery_candidate_digest: str
    initial_cohort_id: str
    discovery_cohort_id: str
    capability_coverage_digest: str
    capability_id: str
    role_profile_id: str
    provider_binding_digest: str
    attribution_engine_version: str
    causal_facts: AttributionCausalFacts = Field(default_factory=AttributionCausalFacts)
    confirmed_cause_ids: tuple[AttributionCause, ...]
    confidence: float = Field(ge=0, le=1)
    late_critical_finding: bool
    reviewer_coverage_leak: bool
    observed_at: str
    attribution_input_digest: str = ""

    @field_validator("project_id", "session_id", "finding_key")
    @classmethod
    def _identity_is_stable(cls, value: str) -> str:
        return require_machine_id(value, "attribution identity")

    @field_validator("confirmed_cause_ids")
    @classmethod
    def _causes_are_canonical(
        cls,
        value: tuple[AttributionCause, ...],
    ) -> tuple[AttributionCause, ...]:
        if value != tuple(sorted(set(value))):
            raise ValueError("attribution causes must be sorted and unique")
        return value

    @field_validator("observed_at")
    @classmethod
    def _time_is_utc(cls, value: str) -> str:
        parse_utc(value)
        return value

    @model_validator(mode="after")
    def _verify_digest(self) -> Self:
        if self.confirmed_cause_ids != _causes_from_facts(self.causal_facts):
            raise ValueError("attribution cause is not derived from causal facts")
        return fill_artifact_digest(self, "attribution_input_digest")


class AttributionPolicy(ArtifactCompatibility):
    schema_version: Literal["attribution-policy.v1"] = "attribution-policy.v1"
    artifact_kind: Literal["attribution-policy"] = "attribution-policy"
    policy_version: str
    minimum_confidence: float = Field(ge=0, le=1)
    cause_domain_map: dict[AttributionCause, CandidateDomain]
    non_optimizable_causes: tuple[AttributionCause, ...]
    policy_digest: str = ""

    @model_validator(mode="after")
    def _verify_policy(self) -> Self:
        overlap = set(self.cause_domain_map) & set(self.non_optimizable_causes)
        if overlap:
            raise ValueError(
                "attribution cause cannot be optimizable and product defect"
            )
        return fill_artifact_digest(self, "policy_digest")

    @classmethod
    def baseline(cls) -> AttributionPolicy:
        return cls(
            policy_version="attribution-policy.v1",
            minimum_confidence=0.8,
            cause_domain_map={
                "panel_selection_gap": "selection",
                "reviewer_execution_miss": "role_profile",
                "role_contract_gap": "role_profile",
                "provider_quality_gap": "binding",
            },
            non_optimizable_causes=(
                "deterministic_gate_gap",
                "evidence_visibility_gap",
                "risk_classification_gap",
            ),
        )


class FindingAttribution(ArtifactCompatibility):
    schema_version: Literal["finding-attribution.v1"] = "finding-attribution.v1"
    artifact_kind: Literal["finding-attribution"] = "finding-attribution"
    attribution_id: str
    project_id: str
    session_id: str
    finding_key: str
    finding_event_digest: str
    attribution_evidence_digest: str
    source_evidence_digest: str
    original_candidate_digest: str
    discovery_candidate_digest: str
    initial_cohort_id: str
    discovery_cohort_id: str
    capability_coverage_digest: str
    capability_id: str
    role_profile_id: str
    provider_binding_digest: str
    attribution_engine_version: str
    policy_digest: str
    primary_cause_id: str = ""
    candidate_domain: str = ""
    confidence: float = Field(ge=0, le=1)
    status: AttributionStatus
    reason_code: str
    attribution_digest: str = ""

    @model_validator(mode="after")
    def _verify_digest(self) -> Self:
        return fill_artifact_digest(self, "attribution_digest")


class ProductDefectSignal(ArtifactCompatibility):
    schema_version: Literal["product-defect-signal.v1"] = "product-defect-signal.v1"
    artifact_kind: Literal["product-defect-signal"] = "product-defect-signal"
    signal_id: str
    project_id: str
    session_id: str
    finding_key: str
    cause_id: str
    evidence_digest: str
    attribution_digest: str
    signal_digest: str = ""

    @model_validator(mode="after")
    def _verify_digest(self) -> Self:
        return fill_artifact_digest(self, "signal_digest")


class AttributionDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    attribution: FindingAttribution
    product_defect_signal: ProductDefectSignal | None = None


def _attribute_finding(
    evidence: AttributionEvidence,
    policy: AttributionPolicy,
) -> AttributionDecision:
    trusted = AttributionEvidence.model_validate(evidence.model_dump(mode="json"))
    governed = AttributionPolicy.model_validate(policy.model_dump(mode="json"))
    status, reason, cause, domain = _classify(trusted, governed)
    attribution = FindingAttribution(
        attribution_id=stable_id(
            "finding-attribution",
            trusted.attribution_input_digest,
            governed.policy_digest,
        ),
        project_id=trusted.project_id,
        session_id=trusted.session_id,
        finding_key=trusted.finding_key,
        finding_event_digest=trusted.finding_event_digest,
        attribution_evidence_digest=trusted.attribution_input_digest,
        source_evidence_digest=trusted.evidence_digest,
        original_candidate_digest=trusted.original_candidate_digest,
        discovery_candidate_digest=trusted.discovery_candidate_digest,
        initial_cohort_id=trusted.initial_cohort_id,
        discovery_cohort_id=trusted.discovery_cohort_id,
        capability_coverage_digest=trusted.capability_coverage_digest,
        capability_id=trusted.capability_id,
        role_profile_id=trusted.role_profile_id,
        provider_binding_digest=trusted.provider_binding_digest,
        attribution_engine_version=trusted.attribution_engine_version,
        policy_digest=governed.policy_digest,
        primary_cause_id=cause,
        candidate_domain=domain,
        confidence=trusted.confidence,
        status=status,
        reason_code=reason,
    )
    signal = (
        _product_defect_signal(attribution, trusted)
        if status == "product_defect"
        else None
    )
    return AttributionDecision(attribution=attribution, product_defect_signal=signal)


def _classify(
    evidence: AttributionEvidence,
    policy: AttributionPolicy,
) -> tuple[AttributionStatus, str, str, str]:
    if not evidence.late_critical_finding or not evidence.evidence_digest:
        return "no_change", "critical_fact_not_confirmed", "", ""
    if len(evidence.confirmed_cause_ids) != 1:
        reason = (
            "attribution_conflict"
            if evidence.confirmed_cause_ids
            else "attribution_unclassifiable"
        )
        return "no_change", reason, "", ""
    cause = evidence.confirmed_cause_ids[0]
    if evidence.confidence < policy.minimum_confidence:
        return "no_change", "attribution_confidence_insufficient", cause, ""
    if cause in policy.non_optimizable_causes:
        return "product_defect", "non_optimizable_product_defect", cause, ""
    domain = policy.cause_domain_map.get(cause, "")
    if not domain:
        return "no_change", "attribution_unclassifiable", cause, ""
    return "candidate_authorized", "candidate_domain_authorized", cause, domain


def _product_defect_signal(
    attribution: FindingAttribution,
    evidence: AttributionEvidence,
) -> ProductDefectSignal:
    return ProductDefectSignal(
        signal_id=stable_id("product-defect-signal", attribution.attribution_digest),
        project_id=evidence.project_id,
        session_id=evidence.session_id,
        finding_key=evidence.finding_key,
        cause_id=attribution.primary_cause_id,
        evidence_digest=evidence.evidence_digest,
        attribution_digest=attribution.attribution_digest,
    )
