"""Finding 写入与关闭所依赖的可信不可变工件。"""

from __future__ import annotations

from typing import Literal, Protocol, Self

from pydantic import BaseModel, ConfigDict, model_validator

from ai_sdlc.core.stage_review.artifact_compat import (
    ArtifactCompatibility,
    fill_artifact_digest,
)
from ai_sdlc.core.stage_review.finding_models import FindingScope, IdentityMappingKind
from ai_sdlc.core.stage_review.resource_builders import parse_utc

_MODEL_CONFIG = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)
EvidenceKind = Literal[
    "finding",
    "regression",
    "new_critical_evidence",
    "required_test_failure",
    "protocol_integrity_failure",
    "late_critical_confirmation",
    "waiver_approval",
    "handoff_receipt",
    "macro_rebaseline",
]
EvidenceVisibility = Literal["visible", "not_visible"]
EvidenceConfirmation = Literal["confirmed", "rejected", "unconfirmed"]


class InitialReviewSeal(ArtifactCompatibility):
    model_config = _MODEL_CONFIG

    schema_version: Literal["initial-review-seal.v1"] = "initial-review-seal.v1"
    scope: FindingScope
    initial_candidate_digest: str
    policy_digest: str
    plan_digest: str
    binding_set_digest: str
    initial_cohort_id: str
    required_slot_ids: tuple[str, ...]
    required_pass_digests: tuple[str, ...]
    coverage_declaration_digests: tuple[str, ...]
    finding_batch_digest: str
    sealed_at: str
    seal_digest: str = ""

    @model_validator(mode="after")
    def _validate_seal(self) -> Self:
        parse_utc(self.sealed_at)
        groups = (
            self.required_slot_ids,
            self.required_pass_digests,
            self.coverage_declaration_digests,
        )
        if any(not group or len(group) != len(set(group)) for group in groups) or any(
            len(group) != len(self.required_slot_ids) for group in groups[1:]
        ):
            raise ValueError("initial seal requires unique pass and coverage proof")
        return fill_artifact_digest(self, "seal_digest")


class TrustedEvidenceDescriptor(ArtifactCompatibility):
    model_config = _MODEL_CONFIG

    schema_version: Literal["finding-evidence.v1"] = "finding-evidence.v1"
    scope: FindingScope
    evidence_bundle_digest: str
    evidence_kind: EvidenceKind
    candidate_digest: str
    produced_at: str
    first_visible_at: str
    initial_visibility: EvidenceVisibility
    confirmation_result: EvidenceConfirmation
    related_finding_key: str = ""
    related_identity_digest: str = ""
    related_handoff_id: str = ""
    handoff_resolution: Literal["accepted", "rejected"] | None = None
    subject_identity_digest: str = ""
    source_event_digest: str = ""
    occurrence_id: str = ""
    signer_actor_id: str = ""
    signer_slot_id: str = ""
    signer_slot_kind: str = ""
    signer_authority_kind: str = ""
    signer_capability_id: str = ""
    signer_capability_ids: tuple[str, ...] = ()
    signer_blocking_authorities: tuple[str, ...] = ()
    signer_eligible_for_enforce_quorum: bool = False
    signer_role_contract_digest: str = ""
    signer_binding_digest: str = ""
    descriptor_digest: str = ""

    @model_validator(mode="after")
    def _validate_descriptor(self) -> Self:
        if parse_utc(self.first_visible_at) < parse_utc(self.produced_at):
            raise ValueError("evidence visibility precedes production")
        if self.evidence_kind == "handoff_receipt" and not all(
            (
                self.signer_actor_id,
                self.signer_slot_id,
                self.related_handoff_id,
                self.handoff_resolution,
                self.signer_slot_kind,
                self.signer_authority_kind,
                self.signer_capability_id,
                self.signer_capability_ids,
                self.signer_role_contract_digest,
                self.signer_binding_digest,
            )
        ):
            raise ValueError("handoff receipt lacks target authority")
        return fill_artifact_digest(self, "descriptor_digest")


class TrustedFindingAuthority(BaseModel):
    model_config = _MODEL_CONFIG

    actor_id: str
    slot_id: str
    slot_kind: str
    authority_kind: Literal[
        "reviewer",
        "deterministic_gate",
        "remediator",
        "coordinator",
        "human_governance",
        "identity_governance",
    ]
    capability_ids: tuple[str, ...]
    blocking_authorities: tuple[str, ...]
    role_profile_id: str = ""
    role_contract_digest: str
    binding_digest: str
    eligible_for_enforce_quorum: bool
    valid_until: str
    capability_coverage_digest: str = ""

    @model_validator(mode="after")
    def _validate_authority(self) -> Self:
        parse_utc(self.valid_until)
        if len(self.capability_ids) != len(set(self.capability_ids)):
            raise ValueError("authority capabilities must be unique")
        if len(self.blocking_authorities) != len(set(self.blocking_authorities)):
            raise ValueError("blocking authorities must be unique")
        return self


class TrustedIdentityMappingDecision(ArtifactCompatibility):
    model_config = _MODEL_CONFIG

    schema_version: Literal["identity-mapping-decision.v1"] = (
        "identity-mapping-decision.v1"
    )
    scope: FindingScope
    candidate_digest: str
    mapping_kind: IdentityMappingKind
    source_keys: tuple[str, ...]
    target_identity_digests: tuple[str, ...]
    resolver_version: str
    lineage_evidence_digest: str
    issued_at: str
    decision_digest: str = ""

    @model_validator(mode="after")
    def _validate_decision(self) -> Self:
        parse_utc(self.issued_at)
        if not self.lineage_evidence_digest:
            raise ValueError("identity mapping decision lacks lineage evidence")
        return fill_artifact_digest(self, "decision_digest")


class FindingWaiver(ArtifactCompatibility):
    model_config = _MODEL_CONFIG

    schema_version: Literal["finding-waiver.v1"] = "finding-waiver.v1"
    waiver_id: str
    scope: FindingScope
    finding_key: str
    candidate_digest: str
    policy_digest: str
    approved_by_actor_id: str
    approved_by_slot_id: str
    authority_binding_digest: str
    reason: str
    issued_at: str
    expires_at: str
    evidence_digest: str
    waiver_digest: str = ""

    @model_validator(mode="after")
    def _validate_waiver(self) -> Self:
        if parse_utc(self.expires_at) <= parse_utc(self.issued_at):
            raise ValueError("waiver expiry is invalid")
        return fill_artifact_digest(self, "waiver_digest")


class FindingTrustContext(BaseModel):
    model_config = _MODEL_CONFIG

    scope: FindingScope
    candidate_digest: str
    policy_digest: str
    plan_digest: str
    binding_set_digest: str
    cohort_id: str
    reviewer_engine_version: str
    initial_review_seal: InitialReviewSeal
    session_fencing_epoch: int
    authorities: tuple[TrustedFindingAuthority, ...]
    waivers: tuple[FindingWaiver, ...] = ()
    non_waivable_categories: tuple[str, ...] = ()
    evaluation_at: str

    @property
    def initial_review_seal_digest(self) -> str:
        return self.initial_review_seal.seal_digest

    @property
    def initial_cohort_id(self) -> str:
        return self.initial_review_seal.initial_cohort_id

    @model_validator(mode="after")
    def _validate_context(self) -> Self:
        parse_utc(self.evaluation_at)
        seal = self.initial_review_seal
        if seal.scope != self.scope or seal.policy_digest != self.policy_digest:
            raise ValueError("initial seal lineage is inconsistent")
        identities = tuple((item.actor_id, item.slot_id) for item in self.authorities)
        if len(identities) != len(set(identities)):
            raise ValueError("trusted authorities must be unique")
        if self.non_waivable_categories != tuple(
            sorted(set(self.non_waivable_categories))
        ):
            raise ValueError("non-waivable categories must be canonical")
        return self


class FindingTrustResolver(Protocol):
    def resolve(self, scope: FindingScope) -> FindingTrustContext: ...

    def resolve_evidence(
        self, scope: FindingScope, evidence_bundle_digest: str
    ) -> TrustedEvidenceDescriptor | None: ...

    def event_is_trusted(self, event: object) -> bool: ...

    def session_lineage_is_trusted(self, event: object) -> bool: ...

    def resolve_mapping(
        self, scope: FindingScope, decision_digest: str
    ) -> TrustedIdentityMappingDecision | None: ...


class FindingCloseContext(BaseModel):
    model_config = _MODEL_CONFIG

    candidate_digest: str
    policy_digest: str
    binding_set_digest: str
    authorities: tuple[TrustedFindingAuthority, ...]
    waivers: tuple[FindingWaiver, ...]
    non_waivable_categories: tuple[str, ...]
    evaluation_at: str

    @classmethod
    def from_trust(cls, trust: FindingTrustContext) -> FindingCloseContext:
        return cls(
            candidate_digest=trust.candidate_digest,
            policy_digest=trust.policy_digest,
            binding_set_digest=trust.binding_set_digest,
            authorities=trust.authorities,
            waivers=trust.waivers,
            non_waivable_categories=trust.non_waivable_categories,
            evaluation_at=trust.evaluation_at,
        )
