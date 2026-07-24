"""Session 内不可变 Cohort、Pass、覆盖、重基线与撤销工件。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ai_sdlc.core.stage_review.artifact_compat import (
    ArtifactCompatibility,
    fill_artifact_digest,
)
from ai_sdlc.core.stage_review.finding_command_models import FindingInitialDraft
from ai_sdlc.core.stage_review.finding_models import FindingScope
from ai_sdlc.core.stage_review.provider_execution_evidence import (
    provider_execution_evidence_root_digest,
)
from ai_sdlc.core.stage_review.resource_builders import parse_utc

_MODEL_CONFIG = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)
ReviewVerdict = Literal["passed", "findings"]
ProgressOutcome = Literal["improved", "same", "regressed", "uncomparable"]
MacroChangeKind = Literal[
    "requirements_change",
    "architecture_change",
    "technical_route_change",
    "acceptance_baseline_change",
    "risk_profile_change",
]


class ArtifactRef(BaseModel):
    model_config = _MODEL_CONFIG

    artifact_id: str
    artifact_digest: str


class CohortReviewer(BaseModel):
    model_config = _MODEL_CONFIG

    slot_id: str
    role_profile_id: str
    role_contract_digest: str
    capability_ids: tuple[str, ...]
    binding_id: str
    binding_digest: str
    actor_id: str
    provider_id: str
    model_family: str
    assignment_input_packet_digest: str
    visibility_barrier_id: str
    eligible_for_enforce_quorum: bool

    @field_validator("capability_ids")
    @classmethod
    def _canonical_capabilities(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value or value != tuple(sorted(set(value))):
            raise ValueError("cohort reviewer capabilities must be unique and sorted")
        return value


class ReviewCohort(ArtifactCompatibility):
    model_config = _MODEL_CONFIG

    schema_version: Literal["review-cohort.v1"] = "review-cohort.v1"
    scope: FindingScope
    cohort_id: str
    ordinal: int = Field(ge=1)
    candidate_digest: str
    risk_profile_digest: str
    risk_profile_lineage_id: str
    policy_digest: str
    optimization_snapshot_digest: str
    plan_digest: str
    plan_finalization_digest: str
    binding_set_id: str
    binding_set_digest: str
    resource_reservation_id: str
    resource_reservation_digest: str
    reviewers: tuple[CohortReviewer, ...]
    required_slot_ids: tuple[str, ...]
    initial_pass_head_digest: str
    predecessor_cohort_id: str = ""
    activation_reason: str
    created_at: str
    cohort_digest: str = ""

    @model_validator(mode="after")
    def _validate_cohort(self) -> ReviewCohort:
        parse_utc(self.created_at)
        reviewer_slots = tuple(item.slot_id for item in self.reviewers)
        if reviewer_slots != tuple(sorted(set(reviewer_slots))):
            raise ValueError("cohort reviewers must be unique and sorted")
        if self.required_slot_ids != reviewer_slots:
            raise ValueError("cohort required slots must match reviewers")
        if len({item.visibility_barrier_id for item in self.reviewers}) != 1:
            raise ValueError(
                "cohort required reviewers must share one visibility barrier"
            )
        if not all(item.eligible_for_enforce_quorum for item in self.reviewers):
            raise ValueError("cohort required reviewer is not enforce eligible")
        return fill_artifact_digest(self, "cohort_digest")


class CoverageDeclaration(ArtifactCompatibility):
    model_config = _MODEL_CONFIG

    schema_version: Literal["coverage-declaration.v1"] = "coverage-declaration.v1"
    reviewed_area_ids: tuple[str, ...]
    uncovered_area_ids: tuple[str, ...] = ()
    evidence_gap_ids: tuple[str, ...] = ()
    absolute_completeness_claimed: Literal[False] = False
    declaration_digest: str = ""

    @model_validator(mode="after")
    def _validate_declaration(self) -> CoverageDeclaration:
        groups = (
            self.reviewed_area_ids,
            self.uncovered_area_ids,
            self.evidence_gap_ids,
        )
        if not self.reviewed_area_ids or any(
            group != tuple(sorted(set(group))) for group in groups
        ):
            raise ValueError("coverage declaration values must be canonical")
        if set(self.reviewed_area_ids) & set(self.uncovered_area_ids):
            raise ValueError("coverage area cannot be both reviewed and uncovered")
        return fill_artifact_digest(self, "declaration_digest")


class ReviewPass(ArtifactCompatibility):
    model_config = _MODEL_CONFIG

    schema_version: Literal["review-pass.v1"] = "review-pass.v1"
    scope: FindingScope
    pass_id: str
    cohort_id: str
    candidate_digest: str
    risk_profile_digest: str
    plan_digest: str
    binding_set_digest: str
    policy_digest: str
    slot_id: str
    role_profile_id: str
    role_contract_digest: str
    binding_id: str
    binding_digest: str
    actor_id: str
    provider_id: str
    model_family: str
    assignment_digest: str
    invocation_id: str
    invocation_projection_digest: str
    validation_digest: str
    resource_reservation_digest: str
    is_first_cohort_pass: bool
    verdict: ReviewVerdict
    coverage: CoverageDeclaration
    findings: tuple[FindingInitialDraft, ...] = ()
    evidence_digests: tuple[str, ...]
    isolation_receipt_digests: tuple[str, ...] = ()
    egress_receipt_digests: tuple[str, ...] = ()
    execution_evidence_root_digest: str = ""
    observed_peer_pass_ids: tuple[str, ...] = ()
    submitted_at: str
    pass_digest: str = ""

    @model_validator(mode="after")
    def _validate_pass(self) -> ReviewPass:
        parse_utc(self.submitted_at)
        if self.evidence_digests != tuple(sorted(set(self.evidence_digests))):
            raise ValueError("review pass evidence must be unique and sorted")
        if self.observed_peer_pass_ids != tuple(
            sorted(set(self.observed_peer_pass_ids))
        ):
            raise ValueError("review pass peer references must be unique and sorted")
        if self.execution_evidence_root_digest != provider_execution_evidence_root_digest(
            self.isolation_receipt_digests,
            self.egress_receipt_digests,
        ):
            raise ValueError("review pass execution evidence lineage is invalid")
        if (self.verdict == "findings") != bool(self.findings):
            raise ValueError("review pass verdict does not match finding submission")
        for finding in self.findings:
            if finding.slot_id != self.slot_id or finding.actor_id != self.actor_id:
                raise ValueError("review pass finding authority is inconsistent")
        return fill_artifact_digest(self, "pass_digest")


class ReviewPassRef(BaseModel):
    model_config = _MODEL_CONFIG

    pass_id: str
    pass_digest: str
    cohort_id: str
    slot_id: str
    is_first_cohort_pass: bool


class ProgressRecord(BaseModel):
    model_config = _MODEL_CONFIG

    snapshot_digest: str
    outcome: ProgressOutcome | None = None
    decisive_dimension: str = ""


class RoleReplanCounter(BaseModel):
    model_config = _MODEL_CONFIG

    risk_profile_lineage_id: str
    count: int = Field(ge=0)


class MacroRebaselineRequest(ArtifactCompatibility):
    model_config = _MODEL_CONFIG

    schema_version: Literal["macro-rebaseline-request.v1"] = (
        "macro-rebaseline-request.v1"
    )
    request_id: str
    scope: FindingScope
    candidate_digest: str
    change_kind: MacroChangeKind
    evidence_digest: str
    requested_at: str
    request_digest: str = ""

    @model_validator(mode="after")
    def _validate_request(self) -> MacroRebaselineRequest:
        parse_utc(self.requested_at)
        return fill_artifact_digest(self, "request_digest")


class ReviewerPlanRevocation(ArtifactCompatibility):
    model_config = _MODEL_CONFIG

    schema_version: Literal["reviewer-plan-revocation.v1"] = (
        "reviewer-plan-revocation.v1"
    )
    revocation_id: str
    target_kind: Literal["plan", "profile", "capability"]
    plan_digest: str = ""
    profile_ids: tuple[str, ...] = ()
    capability_ids: tuple[str, ...] = ()
    reason_id: str
    evidence_digest: str
    issuer_id: str
    issuer_authority_digest: str
    replacement_version: str = ""
    minimum_version: str = ""
    issued_at: str
    revocation_digest: str = ""

    @model_validator(mode="after")
    def _validate_revocation(self) -> ReviewerPlanRevocation:
        parse_utc(self.issued_at)
        targets = {
            "plan": bool(self.plan_digest)
            and not self.profile_ids
            and not self.capability_ids,
            "profile": bool(self.profile_ids)
            and not self.plan_digest
            and not self.capability_ids,
            "capability": bool(self.capability_ids)
            and not self.plan_digest
            and not self.profile_ids,
        }
        if not targets[self.target_kind]:
            raise ValueError("reviewer plan revocation target is inconsistent")
        if self.profile_ids != tuple(sorted(set(self.profile_ids))):
            raise ValueError("revoked profiles must be canonical")
        if self.capability_ids != tuple(sorted(set(self.capability_ids))):
            raise ValueError("revoked capabilities must be canonical")
        required = (
            self.reason_id,
            self.evidence_digest,
            self.issuer_id,
            self.issuer_authority_digest,
        )
        if not all(item.strip() for item in required):
            raise ValueError("reviewer plan revocation authority is incomplete")
        if not self.replacement_version and not self.minimum_version:
            raise ValueError("reviewer plan revocation requires a replacement boundary")
        return fill_artifact_digest(self, "revocation_digest")
