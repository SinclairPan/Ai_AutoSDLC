"""确定性的 AutoPromotionGate 与版本化硬门槛。"""

from __future__ import annotations

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ai_sdlc.core.stage_review.artifact_compat import (
    ArtifactCompatibility,
    fill_artifact_digest,
)
from ai_sdlc.core.stage_review.registry_versions import (
    require_machine_id,
    require_version,
)


class AutoPromotionPolicy(ArtifactCompatibility):
    schema_version: Literal["auto-promotion-policy.v1"] = "auto-promotion-policy.v1"
    artifact_kind: Literal["auto-promotion-policy"] = "auto-promotion-policy"
    policy_version: str
    minimum_holdout_sessions: int = Field(default=10, ge=1)
    minimum_shadow_sessions: int = Field(default=10, ge=1)
    minimum_shadow_days: int = Field(default=14, ge=1)
    require_positive_quality_confidence: bool = True
    policy_digest: str = ""

    @field_validator("policy_version")
    @classmethod
    def _version_is_supported(cls, value: str) -> str:
        return require_version(value)

    @model_validator(mode="after")
    def _verify_digest(self) -> Self:
        return fill_artifact_digest(self, "policy_digest")


class AutoPromotionEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    baseline_snapshot_digest: str
    challenger_snapshot_digest: str
    candidate_digest: str
    evaluation_report_digests: tuple[str, ...]
    invariant_results: dict[str, bool]
    critical_detection_delta: float
    late_critical_delta: float
    reviewer_coverage_leak_delta: float
    false_positive_delta: float
    reversal_delta: float
    stage_reopen_delta: float
    needs_user_delta: float
    blocked_delta: float
    timeout_delta: float
    abandon_delta: float
    hard_budget_exhausted_delta: float
    unknown_or_censored_delta: float
    quality_confidence_lower: float
    holdout_session_count: int = Field(ge=0)
    shadow_session_count: int = Field(ge=0)
    shadow_observation_days: int = Field(ge=0)
    resources_within_constitution: bool
    duties_independent: bool

    @model_validator(mode="after")
    def _verify_evidence(self) -> Self:
        if not self.evaluation_report_digests or not self.invariant_results:
            raise ValueError("promotion evidence is incomplete")
        return self


class AutoPromotionDecision(ArtifactCompatibility):
    schema_version: Literal["auto-promotion-decision.v1"] = (
        "auto-promotion-decision.v1"
    )
    artifact_kind: Literal["auto-promotion-decision"] = "auto-promotion-decision"
    decision_id: str
    policy_digest: str
    baseline_snapshot_digest: str
    challenger_snapshot_digest: str
    candidate_digest: str
    evaluation_report_digests: tuple[str, ...]
    approved: bool
    failed_guards: tuple[str, ...]
    decision_digest: str = ""

    @field_validator("decision_id")
    @classmethod
    def _identity_is_stable(cls, value: str) -> str:
        return require_machine_id(value, "promotion decision identity")

    @model_validator(mode="after")
    def _verify_decision(self) -> Self:
        if self.failed_guards != tuple(sorted(set(self.failed_guards))):
            raise ValueError("promotion failed guards must be canonical")
        if self.approved == bool(self.failed_guards):
            raise ValueError("promotion decision guard result is inconsistent")
        return fill_artifact_digest(self, "decision_digest")


class AutoPromotionGate:
    def __init__(self, policy: AutoPromotionPolicy) -> None:
        self.policy = AutoPromotionPolicy.model_validate(policy.model_dump(mode="json"))

    def evaluate(
        self, evidence: AutoPromotionEvidence, *, decision_id: str
    ) -> AutoPromotionDecision:
        trusted = AutoPromotionEvidence.model_validate(evidence.model_dump(mode="json"))
        failures = _failed_guards(self.policy, trusted)
        return AutoPromotionDecision(
            decision_id=decision_id,
            policy_digest=self.policy.policy_digest,
            baseline_snapshot_digest=trusted.baseline_snapshot_digest,
            challenger_snapshot_digest=trusted.challenger_snapshot_digest,
            candidate_digest=trusted.candidate_digest,
            evaluation_report_digests=tuple(sorted(trusted.evaluation_report_digests)),
            approved=not failures,
            failed_guards=failures,
        )


def _failed_guards(
    policy: AutoPromotionPolicy, evidence: AutoPromotionEvidence
) -> tuple[str, ...]:
    guards = {
        "invariants": all(evidence.invariant_results.values()),
        "critical_detection_non_regression": evidence.critical_detection_delta >= 0,
        "late_critical_non_regression": evidence.late_critical_delta <= 0,
        "coverage_leak_non_regression": evidence.reviewer_coverage_leak_delta <= 0,
        "false_positive_non_regression": evidence.false_positive_delta <= 0,
        "reversal_non_regression": evidence.reversal_delta <= 0,
        "stage_reopen_non_regression": evidence.stage_reopen_delta <= 0,
        "needs_user_non_regression": evidence.needs_user_delta <= 0,
        "blocked_non_regression": evidence.blocked_delta <= 0,
        "timeout_non_regression": evidence.timeout_delta <= 0,
        "abandon_non_regression": evidence.abandon_delta <= 0,
        "hard_budget_exhausted_non_regression": (
            evidence.hard_budget_exhausted_delta <= 0
        ),
        "censoring_non_regression": evidence.unknown_or_censored_delta <= 0,
        "quality_significant": (
            evidence.quality_confidence_lower > 0
            if policy.require_positive_quality_confidence
            else evidence.quality_confidence_lower >= 0
        ),
        "holdout_sample": evidence.holdout_session_count
        >= policy.minimum_holdout_sessions,
        "shadow_sample": evidence.shadow_session_count >= policy.minimum_shadow_sessions,
        "shadow_window": evidence.shadow_observation_days >= policy.minimum_shadow_days,
        "resource_bounds": evidence.resources_within_constitution,
        "duty_independence": evidence.duties_independent,
    }
    return tuple(sorted(name for name, passed in guards.items() if not passed))
