"""授权结果到不可变 FindingEvent 的确定性构建。"""

from __future__ import annotations

from typing import Any

from ai_sdlc.core.stage_review.finding_command_models import (
    FindingAppendCommand,
    FindingInitialBatchCommand,
    FindingInitialDraft,
    FindingLineageAdvanceCommand,
)
from ai_sdlc.core.stage_review.finding_digests import (
    command_digest,
    event_digest,
    scope_digest,
    stable_finding_id,
)
from ai_sdlc.core.stage_review.finding_identity import RESOLVER_VERSION
from ai_sdlc.core.stage_review.finding_models import (
    FindingEvent,
    FindingIdentityDecision,
)
from ai_sdlc.core.stage_review.finding_support_models import (
    FindingAttributionInput,
    LateCriticalFinding,
    ReviewerCoverageLeak,
)
from ai_sdlc.core.stage_review.finding_trust_models import (
    FindingTrustContext,
    TrustedEvidenceDescriptor,
    TrustedFindingAuthority,
)

_FindingCommand = (
    FindingAppendCommand | FindingInitialBatchCommand | FindingLineageAdvanceCommand
)


def build_initial_event(
    command: FindingInitialBatchCommand,
    trust: FindingTrustContext,
    draft: FindingInitialDraft,
    decision: FindingIdentityDecision,
    authority: TrustedFindingAuthority,
    evidence: TrustedEvidenceDescriptor,
    index: int,
    prior: list[FindingEvent],
) -> FindingEvent:
    initial_trust = _initial_trust_snapshot(trust)
    values: dict[str, Any] = {
        "finding_key": decision.finding_key,
        "identity": draft.identity,
        "event_type": "initial_discovered",
        "actor_id": draft.actor_id,
        "slot_id": draft.slot_id,
        "capability_id": draft.capability_id,
        "evidence_bundle_digest": draft.evidence_bundle_digest,
        **_evidence_values(evidence),
        "severity": draft.severity,
        "category": draft.identity.category,
        "disposition": "blocking",
        "blocking": True,
    }
    return _build_event(
        command,
        initial_trust,
        authority,
        prior,
        f"{command.command_id}.{index}",
        values,
    )


def build_seal_event(
    command: FindingInitialBatchCommand,
    trust: FindingTrustContext,
    prior: list[FindingEvent],
) -> FindingEvent:
    initial_trust = _initial_trust_snapshot(trust)
    authority = TrustedFindingAuthority(
        actor_id="system.initial-seal",
        slot_id="system.initial-seal",
        slot_kind="required",
        authority_kind="deterministic_gate",
        capability_ids=("initial-review-seal",),
        blocking_authorities=("initial-review-seal",),
        role_contract_digest="sha256:initial-review-seal",
        binding_digest=trust.initial_review_seal_digest,
        eligible_for_enforce_quorum=True,
        valid_until="9999-12-31T23:59:59Z",
    )
    values: dict[str, Any] = {
        "finding_key": None,
        "event_type": "initial_ledger_sealed",
        "actor_id": authority.actor_id,
        "slot_id": authority.slot_id,
        "capability_id": "initial-review-seal",
        "evidence_bundle_digest": trust.initial_review_seal_digest,
    }
    return _build_event(
        command,
        initial_trust,
        authority,
        prior,
        f"{command.command_id}.seal",
        values,
    )


def build_regular_event(
    command: FindingAppendCommand,
    trust: FindingTrustContext,
    events: tuple[FindingEvent, ...],
    authority: TrustedFindingAuthority,
    evidence: TrustedEvidenceDescriptor,
    key: str | None,
    disposition: str | None,
    blocking: bool,
) -> FindingEvent:
    values: dict[str, Any] = {
        "finding_key": key,
        "identity": command.identity,
        "identity_mapping": command.identity_mapping,
        "event_type": command.event_type,
        "actor_id": command.actor_id,
        "slot_id": command.slot_id,
        "capability_id": command.capability_id,
        "evidence_bundle_digest": command.evidence_bundle_digest,
        **_evidence_values(evidence),
        "severity": command.severity,
        "category": command.identity.category if command.identity else command.category,
        "late_origin": command.late_origin,
        "regression_of": command.regression_of,
        "remediation_batch_id": command.remediation_batch_id,
        "waiver_id": command.waiver_id,
        "waiver_digest": command.waiver_digest,
        "replacement_keys": command.replacement_keys,
        "macro_rebaseline_evidence_digest": command.macro_rebaseline_evidence_digest,
        "handoff_id": command.handoff_id,
        "handoff_resolution": command.handoff_resolution,
        "target_receipt_digest": command.target_receipt_digest,
        "target_scope": command.target_scope,
        "disposition": disposition,
        "blocking": blocking,
    }
    if command.event_type == "cross_scope_critical_evidence" and command.target_scope:
        values["handoff_id"] = stable_finding_id(
            "finding-handoff",
            command.scope.session_id,
            command.command_id,
            command.target_scope.session_id,
            scope_digest(command.scope),
            scope_digest(command.target_scope),
        )
    if command.late_origin == "late_confirmed_p0_p1" and key:
        values.update(_late_attribution(key, command, trust, authority, evidence))
    return _build_event(
        command, trust, authority, list(events), command.command_id, values
    )


def build_lineage_event(
    command: FindingLineageAdvanceCommand,
    trust: FindingTrustContext,
    events: tuple[FindingEvent, ...],
) -> FindingEvent:
    authority = TrustedFindingAuthority(
        actor_id="system.session-lineage",
        slot_id="system.session-lineage",
        slot_kind="required",
        authority_kind="deterministic_gate",
        capability_ids=("finding-ledger-lineage",),
        blocking_authorities=("finding-ledger-lineage",),
        role_contract_digest="sha256:finding-ledger-lineage",
        binding_digest=command.session_event_digest,
        eligible_for_enforce_quorum=True,
        valid_until="9999-12-31T23:59:59Z",
    )
    values: dict[str, Any] = {
        "finding_key": None,
        "event_type": "ledger_lineage_advanced",
        "actor_id": authority.actor_id,
        "slot_id": authority.slot_id,
        "capability_id": "finding-ledger-lineage",
        "evidence_bundle_digest": command.session_event_digest,
    }
    return _build_event(
        command,
        trust,
        authority,
        list(events),
        command.command_id,
        values,
    )


def _late_attribution(
    key: str,
    command: FindingAppendCommand,
    trust: FindingTrustContext,
    authority: TrustedFindingAuthority,
    evidence: TrustedEvidenceDescriptor,
) -> dict[str, object]:
    common = _late_common(key, command)
    return {
        "late_critical_finding": LateCriticalFinding(
            **common,
            original_candidate_digest=trust.initial_review_seal.initial_candidate_digest,
            discovery_candidate_digest=trust.candidate_digest,
            initial_cohort_id=trust.initial_cohort_id,
            discovery_cohort_id=trust.cohort_id,
            confirmation_result=evidence.confirmation_result,
        ),
        "reviewer_coverage_leak": ReviewerCoverageLeak(
            **common,
            capability_id=command.capability_id,
            role_contract_digest=authority.role_contract_digest,
            binding_digest=authority.binding_digest,
            candidate_digest=trust.candidate_digest,
            initial_cohort_id=trust.initial_cohort_id,
            discovery_cohort_id=trust.cohort_id,
            plan_digest=trust.plan_digest,
            binding_set_digest=trust.binding_set_digest,
            engine_version=trust.reviewer_engine_version,
            confirmation_result=evidence.confirmation_result,
            capability_coverage_digest=authority.capability_coverage_digest,
        ),
        "attribution_input": FindingAttributionInput(
            **common,
            original_candidate_digest=trust.initial_review_seal.initial_candidate_digest,
            discovery_candidate_digest=trust.candidate_digest,
            initial_cohort_id=trust.initial_cohort_id,
            discovery_cohort_id=trust.cohort_id,
            capability_id=command.capability_id,
            role_contract_digest=authority.role_contract_digest,
            binding_digest=authority.binding_digest,
            resolver_version=RESOLVER_VERSION,
            engine_version=trust.reviewer_engine_version,
            confirmation_result=evidence.confirmation_result,
            capability_coverage_digest=authority.capability_coverage_digest,
            role_profile_id=authority.role_profile_id,
            provider_binding_digest=authority.binding_digest,
        ),
    }


def _late_common(key: str, command: FindingAppendCommand) -> dict[str, str]:
    return {
        "finding_key": key,
        "evidence_bundle_digest": command.evidence_bundle_digest,
    }


def _evidence_values(evidence: TrustedEvidenceDescriptor) -> dict[str, object]:
    return {
        "evidence_descriptor_digest": evidence.descriptor_digest,
        "evidence_scope": evidence.scope,
        "evidence_candidate_digest": evidence.candidate_digest,
        "evidence_kind": evidence.evidence_kind,
        "evidence_produced_at": evidence.produced_at,
        "evidence_first_visible_at": evidence.first_visible_at,
        "evidence_initial_visibility": evidence.initial_visibility,
        "evidence_confirmation_result": evidence.confirmation_result,
        "evidence_related_finding_key": evidence.related_finding_key,
        "evidence_related_identity_digest": evidence.related_identity_digest,
        "evidence_related_handoff_id": evidence.related_handoff_id,
        "evidence_handoff_resolution": evidence.handoff_resolution,
        "evidence_subject_identity_digest": evidence.subject_identity_digest,
        "evidence_source_event_digest": evidence.source_event_digest,
        "evidence_occurrence_id": evidence.occurrence_id,
        "evidence_signer_actor_id": evidence.signer_actor_id,
        "evidence_signer_slot_id": evidence.signer_slot_id,
        "evidence_signer_slot_kind": evidence.signer_slot_kind,
        "evidence_signer_authority_kind": evidence.signer_authority_kind,
        "evidence_signer_capability_id": evidence.signer_capability_id,
        "evidence_signer_capability_ids": evidence.signer_capability_ids,
        "evidence_signer_blocking_authorities": (evidence.signer_blocking_authorities),
        "evidence_signer_eligible_for_enforce_quorum": (
            evidence.signer_eligible_for_enforce_quorum
        ),
        "evidence_signer_role_contract_digest": evidence.signer_role_contract_digest,
        "evidence_signer_binding_digest": evidence.signer_binding_digest,
    }


def _initial_trust_snapshot(trust: FindingTrustContext) -> FindingTrustContext:
    seal = trust.initial_review_seal
    return trust.model_copy(
        update={
            "candidate_digest": seal.initial_candidate_digest,
            "policy_digest": seal.policy_digest,
            "plan_digest": seal.plan_digest,
            "binding_set_digest": seal.binding_set_digest,
            "cohort_id": seal.initial_cohort_id,
            "evaluation_at": seal.sealed_at,
        }
    )


def _build_event(
    command: _FindingCommand,
    trust: FindingTrustContext,
    authority: TrustedFindingAuthority,
    prior: list[FindingEvent],
    event_command_id: str,
    values: dict[str, Any],
) -> FindingEvent:
    previous = prior[-1] if prior else None
    identifier = stable_finding_id(
        "finding-event", command.scope.session_id, event_command_id
    )
    payload: dict[str, Any] = {
        "scope": command.scope,
        "sequence": len(prior) + 1,
        "previous_event_id": previous.event_id if previous else "",
        "previous_event_digest": previous.event_digest if previous else "",
        "event_id": identifier,
        "event_digest": "",
        "command_id": event_command_id,
        "command_digest": command_digest(command),
        "command_payload": command.model_dump(mode="json"),
        "idempotency_key": command.idempotency_key,
        "expected_revision": command.expected_revision,
        "session_fencing_epoch": command.session_fencing_epoch,
        "authority_kind": authority.authority_kind,
        "authority_slot_kind": authority.slot_kind,
        "authority_capability_ids": authority.capability_ids,
        "authority_blocking_authorities": authority.blocking_authorities,
        "authority_eligible_for_enforce_quorum": (
            authority.eligible_for_enforce_quorum
        ),
        "authority_valid_until": authority.valid_until,
        "authority_role_profile_id": authority.role_profile_id,
        "authority_capability_coverage_digest": (authority.capability_coverage_digest),
        "authorized_at": trust.evaluation_at,
        "role_contract_digest": authority.role_contract_digest,
        "binding_digest": authority.binding_digest,
        "candidate_digest": trust.candidate_digest,
        "policy_digest": trust.policy_digest,
        "plan_digest": trust.plan_digest,
        "binding_set_digest": trust.binding_set_digest,
        "cohort_id": trust.cohort_id,
        "initial_review_seal_digest": trust.initial_review_seal_digest,
        **values,
    }
    draft = FindingEvent.model_validate(payload)
    return draft.model_copy(update={"event_digest": event_digest(draft)})
