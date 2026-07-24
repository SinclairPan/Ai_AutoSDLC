"""Finding 身份、事件、投影与收敛的不可变合同。"""

from __future__ import annotations

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ai_sdlc.core.stage_review.artifact_compat import (
    ArtifactCompatibility,
    freeze_json_mapping,
)
from ai_sdlc.core.stage_review.canonical import (
    CanonicalizationPolicy,
    canonical_digest,
)
from ai_sdlc.core.stage_review.finding_support_models import (
    FindingAttributionInput,
    LateCriticalFinding,
    ReviewerCoverageLeak,
)

Severity = Literal["P0", "P1", "P2", "P3"]
IdentityStatus = Literal["new", "matched", "needs_user"]
IdentityMappingKind = Literal["alias", "split", "merge", "supersede"]
FindingEventType = Literal[
    "initial_discovered",
    "initial_ledger_sealed",
    "discovered",
    "acknowledged",
    "remediation_started",
    "fixed",
    "verification_failed",
    "verified",
    "waived",
    "superseded",
    "regressed",
    "cross_scope_critical_evidence",
    "cross_scope_handoff_resolved",
    "ledger_lineage_advanced",
]
LateFindingOrigin = Literal[
    "regression_of",
    "new_critical_evidence",
    "protocol_or_required_test_failure",
    "late_confirmed_p0_p1",
]
AuthorityKind = Literal[
    "reviewer",
    "deterministic_gate",
    "remediator",
    "coordinator",
    "human_governance",
    "identity_governance",
]
FindingDisposition = Literal["blocking", "advisory", "protocol_violation"]
FindingState = Literal[
    "open",
    "acknowledged",
    "remediation_started",
    "fixed",
    "verification_failed",
    "verified",
    "waived",
    "superseded",
    "regressed",
]

_MODEL_CONFIG = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class FindingScope(BaseModel):
    model_config = _MODEL_CONFIG

    project_id: str
    work_item_id: str
    stage_instance_id: str
    session_id: str


class FindingIdentityInput(BaseModel):
    model_config = _MODEL_CONFIG

    rule_id: str
    category: str
    asset_identity: str
    semantic_location: str
    failure_signature: str
    finding_key_version: str = "finding-key.v1"
    asset_lineage_ref: str = ""
    semantic_location_version: str = "semantic-location.v1"
    supersedes_finding_key: str = ""
    identity_decision_evidence: str = ""
    claim: str = ""
    risk_text: str = ""
    line: int | None = Field(default=None, ge=1)
    identity_digest: str = ""

    @model_validator(mode="after")
    def _fill_and_verify_digest(self) -> Self:
        payload = self.model_dump(
            include={
                "rule_id",
                "category",
                "asset_identity",
                "semantic_location",
                "failure_signature",
                "finding_key_version",
                "semantic_location_version",
            }
        )
        expected = canonical_digest(payload, CanonicalizationPolicy())
        if self.identity_digest and self.identity_digest != expected:
            raise ValueError("finding identity digest does not match content")
        if not self.identity_digest:
            object.__setattr__(self, "identity_digest", expected)
        return self


class FindingIdentityMapping(BaseModel):
    model_config = _MODEL_CONFIG

    mapping_kind: IdentityMappingKind
    source_keys: tuple[str, ...]
    target_identity_digests: tuple[str, ...]
    evidence_digest: str
    resolver_version: str

    @model_validator(mode="after")
    def _validate_mapping(self) -> Self:
        if not self.source_keys or not self.target_identity_digests:
            raise ValueError("identity replacement cannot be empty or form a cycle")
        if self.source_keys != tuple(sorted(set(self.source_keys))):
            raise ValueError("identity source keys must be canonical")
        if self.target_identity_digests != tuple(
            sorted(set(self.target_identity_digests))
        ):
            raise ValueError("identity targets must be canonical")
        if self.mapping_kind == "alias" and (
            len(self.source_keys) != 1 or len(self.target_identity_digests) != 1
        ):
            raise ValueError("alias mapping must be one-to-one")
        if self.mapping_kind == "split" and (
            len(self.source_keys) != 1 or len(self.target_identity_digests) < 2
        ):
            raise ValueError("split mapping requires one source and multiple targets")
        if self.mapping_kind == "merge" and (
            len(self.source_keys) < 2 or len(self.target_identity_digests) != 1
        ):
            raise ValueError("merge mapping requires multiple sources and one target")
        if self.mapping_kind == "supersede" and (
            len(self.source_keys) != 1 or len(self.target_identity_digests) != 1
        ):
            raise ValueError("supersede mapping must be one-to-one")
        return self


class FindingIdentityDecision(BaseModel):
    model_config = _MODEL_CONFIG

    finding_key: str
    identity_digest: str
    status: IdentityStatus
    resolver_version: str
    source_keys: tuple[str, ...] = ()
    mapping_evidence_digest: str = ""
    reason_id: str = ""


class FindingIdentityRelation(BaseModel):
    model_config = _MODEL_CONFIG

    mapping_kind: IdentityMappingKind
    source_keys: tuple[str, ...]
    target_keys: tuple[str, ...]
    target_identity_digests: tuple[str, ...]
    evidence_digest: str
    resolver_version: str


class FindingEvent(ArtifactCompatibility):
    model_config = _MODEL_CONFIG

    schema_version: Literal["finding-event.v1", "finding-event.v2"] = (
        "finding-event.v2"
    )
    scope: FindingScope
    sequence: int = Field(ge=1, le=999_999_999_999)
    previous_event_id: str = ""
    previous_event_digest: str = ""
    event_id: str = Field(pattern=r"^finding-event\.[0-9a-f]{24}$")
    event_digest: str
    command_id: str
    command_digest: str
    idempotency_key: str
    expected_revision: int = Field(ge=0)
    command_payload: dict[str, object]
    session_fencing_epoch: int
    finding_key: str | None
    identity: FindingIdentityInput | None = None
    identity_mapping: FindingIdentityMapping | None = None
    event_type: FindingEventType
    actor_id: str
    slot_id: str
    capability_id: str
    authority_kind: AuthorityKind
    authority_slot_kind: str = ""
    authority_capability_ids: tuple[str, ...] = ()
    authority_blocking_authorities: tuple[str, ...] = ()
    authority_eligible_for_enforce_quorum: bool = False
    authority_valid_until: str = ""
    authority_role_profile_id: str = ""
    authority_capability_coverage_digest: str = ""
    authorized_at: str = ""
    role_contract_digest: str
    binding_digest: str
    candidate_digest: str
    policy_digest: str
    plan_digest: str
    binding_set_digest: str
    cohort_id: str
    initial_review_seal_digest: str
    evidence_bundle_digest: str
    evidence_descriptor_digest: str = ""
    evidence_scope: FindingScope | None = None
    evidence_candidate_digest: str = ""
    evidence_kind: str = ""
    evidence_produced_at: str = ""
    evidence_first_visible_at: str = ""
    evidence_initial_visibility: str = ""
    evidence_confirmation_result: str = ""
    evidence_related_finding_key: str = ""
    evidence_related_identity_digest: str = ""
    evidence_related_handoff_id: str = ""
    evidence_handoff_resolution: Literal["accepted", "rejected"] | None = None
    evidence_subject_identity_digest: str = ""
    evidence_source_event_digest: str = ""
    evidence_occurrence_id: str = ""
    evidence_signer_actor_id: str = ""
    evidence_signer_slot_id: str = ""
    evidence_signer_slot_kind: str = ""
    evidence_signer_authority_kind: str = ""
    evidence_signer_capability_id: str = ""
    evidence_signer_capability_ids: tuple[str, ...] = ()
    evidence_signer_blocking_authorities: tuple[str, ...] = ()
    evidence_signer_eligible_for_enforce_quorum: bool = False
    evidence_signer_role_contract_digest: str = ""
    evidence_signer_binding_digest: str = ""
    severity: Severity | None = None
    category: str | None = None
    disposition: FindingDisposition | None = None
    blocking: bool = False
    late_origin: LateFindingOrigin | None = None
    regression_of: str | None = None
    remediation_batch_id: str | None = None
    waiver_id: str | None = None
    waiver_digest: str | None = None
    replacement_keys: tuple[str, ...] = ()
    macro_rebaseline_evidence_digest: str | None = None
    handoff_id: str | None = None
    handoff_resolution: Literal["accepted", "rejected"] | None = None
    target_receipt_digest: str | None = None
    target_scope: FindingScope | None = None
    late_critical_finding: LateCriticalFinding | None = None
    reviewer_coverage_leak: ReviewerCoverageLeak | None = None
    attribution_input: FindingAttributionInput | None = None

    @model_validator(mode="after")
    def _freeze_command_payload(self) -> Self:
        object.__setattr__(
            self,
            "command_payload",
            freeze_json_mapping(self.command_payload),
        )
        return self


class FindingRecord(BaseModel):
    model_config = _MODEL_CONFIG

    finding_key: str
    identity_digest: str
    category: str
    severity: Severity
    state: FindingState
    disposition: FindingDisposition
    blocking: bool
    candidate_digest: str
    evidence_bundle_digests: tuple[str, ...]
    waiver_id: str | None = None
    waiver_digest: str | None = None
    replacement_keys: tuple[str, ...] = ()
    macro_rebaseline_evidence_digest: str | None = None
    regression_of: str | None = None
    late_origin: LateFindingOrigin | None = None
    verification_actor_id: str = ""
    verification_slot_id: str = ""
    verification_capability_id: str = ""
    verification_binding_digest: str = ""


class FindingLedger(ArtifactCompatibility):
    model_config = _MODEL_CONFIG

    schema_version: Literal["finding-ledger.v1", "finding-ledger.v2"] = (
        "finding-ledger.v2"
    )
    scope: FindingScope
    initialized: bool
    revision: int
    head_event_id: str = ""
    head_event_digest: str = ""
    initial_review_seal_digest: str = ""
    candidate_digest: str = ""
    policy_digest: str = ""
    plan_digest: str = ""
    binding_set_digest: str = ""
    cohort_id: str = ""
    lineage_contract_version: Literal["implicit-v1", "explicit-v2"] = "implicit-v1"
    records: tuple[FindingRecord, ...] = ()
    identity_relations: tuple[FindingIdentityRelation, ...] = ()
    advisory_keys: tuple[str, ...] = ()
    protocol_violation_keys: tuple[str, ...] = ()
    pending_handoff_ids: tuple[str, ...] = ()
    pending_identity_target_keys: tuple[str, ...] = ()
    integrity_ok: bool = True
    ledger_digest: str


class FindingAppendResult(BaseModel):
    model_config = _MODEL_CONFIG

    event: FindingEvent | None
    ledger: FindingLedger
    idempotent_replay: bool = False
