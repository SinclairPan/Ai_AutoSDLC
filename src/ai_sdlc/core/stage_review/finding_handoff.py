"""跨作用域 Finding handoff 的目标、回执与重放校验。"""

from __future__ import annotations

from ai_sdlc.core.stage_review.artifacts import SharedStateIntegrityError
from ai_sdlc.core.stage_review.finding_command_models import FindingAppendCommand
from ai_sdlc.core.stage_review.finding_models import FindingEvent
from ai_sdlc.core.stage_review.finding_trust_models import (
    FindingTrustContext,
    TrustedEvidenceDescriptor,
    TrustedFindingAuthority,
)


def validate_handoff_target(
    command: FindingAppendCommand,
    trust: FindingTrustContext,
) -> None:
    target = command.target_scope
    if target is None or target.project_id != trust.scope.project_id:
        raise ValueError("cross-scope handoff target is invalid")
    if target == trust.scope:
        raise ValueError("cross-scope handoff target must differ from source")


def authorize_handoff_resolution(
    command: FindingAppendCommand,
    authority: TrustedFindingAuthority,
    evidence: TrustedEvidenceDescriptor,
    events: tuple[FindingEvent, ...],
) -> None:
    allowed = {"human_governance", "identity_governance", "deterministic_gate"}
    source = next(
        (
            item
            for item in events
            if item.handoff_id == command.handoff_id
            and item.event_type == "cross_scope_critical_evidence"
        ),
        None,
    )
    resolved = any(
        item.handoff_id == command.handoff_id
        and item.event_type == "cross_scope_handoff_resolved"
        and item.handoff_resolution == "accepted"
        for item in events
    )
    if authority.authority_kind not in allowed or source is None:
        raise PermissionError("cross-scope handoff resolution lacks authority")
    if resolved:
        raise ValueError("cross-scope handoff is already resolved")
    if not _receipt_matches(command, evidence, source):
        raise ValueError("cross-scope handoff receipt lineage is invalid")


def _receipt_matches(
    command: FindingAppendCommand,
    evidence: TrustedEvidenceDescriptor,
    source: FindingEvent,
) -> bool:
    return bool(
        command.handoff_resolution
        and command.target_receipt_digest == evidence.descriptor_digest
        and evidence.evidence_kind == "handoff_receipt"
        and evidence.related_handoff_id == command.handoff_id
        and evidence.handoff_resolution == command.handoff_resolution
        and evidence.scope == source.target_scope
        and evidence.source_event_digest == source.event_digest
        and command.finding_key == source.finding_key
        and command.target_scope == source.target_scope
        and target_receipt_authorized(evidence)
    )


def validate_replayed_handoff(
    event: FindingEvent,
    prefix: tuple[FindingEvent, ...],
) -> None:
    if event.event_type == "cross_scope_critical_evidence":
        if not event.handoff_id or event.target_scope is None:
            raise SharedStateIntegrityError("cross-scope handoff is incomplete")
        return
    source = next(
        (
            item
            for item in prefix
            if item.handoff_id == event.handoff_id
            and item.event_type == "cross_scope_critical_evidence"
        ),
        None,
    )
    if source is None or (
        event.evidence_source_event_digest != source.event_digest
        or event.finding_key != source.finding_key
        or event.target_scope != source.target_scope
        or not event.target_receipt_digest
    ):
        raise SharedStateIntegrityError("cross-scope handoff receipt is invalid")


def target_receipt_authorized(evidence: TrustedEvidenceDescriptor) -> bool:
    capability = evidence.signer_capability_id
    if evidence.signer_authority_kind == "reviewer":
        return (
            evidence.signer_slot_kind == "required"
            and evidence.signer_eligible_for_enforce_quorum
            and capability in evidence.signer_capability_ids
            and capability in evidence.signer_blocking_authorities
        )
    if evidence.signer_authority_kind == "deterministic_gate":
        return capability in evidence.signer_capability_ids
    return (
        evidence.signer_authority_kind == "human_governance"
        and capability in evidence.signer_capability_ids
    )
