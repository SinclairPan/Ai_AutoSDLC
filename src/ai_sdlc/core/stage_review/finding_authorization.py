"""Finding 命令的可信授权、迟到准入与状态约束。"""

from __future__ import annotations

from typing import cast

from ai_sdlc.core.stage_review.artifacts import SharedStateIntegrityError
from ai_sdlc.core.stage_review.finding_authority import required_terminal_authority
from ai_sdlc.core.stage_review.finding_command_models import FindingAppendCommand
from ai_sdlc.core.stage_review.finding_handoff import (
    authorize_handoff_resolution,
    validate_handoff_target,
)
from ai_sdlc.core.stage_review.finding_history import (
    known_identity_decisions,
    record_for,
    replacement_cycle_exists,
    required_replacement_keys,
)
from ai_sdlc.core.stage_review.finding_identity import FindingIdentityResolver
from ai_sdlc.core.stage_review.finding_models import (
    FindingEvent,
    FindingRecord,
)
from ai_sdlc.core.stage_review.finding_reducer import finding_transition_allowed
from ai_sdlc.core.stage_review.finding_trust_models import (
    FindingTrustContext,
    TrustedEvidenceDescriptor,
    TrustedFindingAuthority,
)
from ai_sdlc.core.stage_review.resource_builders import parse_utc


class FindingAuthorizer:
    def __init__(self, identity: FindingIdentityResolver) -> None:
        self._identity = identity

    def authorize(
        self,
        command: FindingAppendCommand,
        trust: FindingTrustContext,
        events: tuple[FindingEvent, ...],
        evidence: TrustedEvidenceDescriptor,
    ) -> tuple[TrustedFindingAuthority, str | None, str | None, bool]:
        authority = self.authority(trust, command.actor_id, command.slot_id)
        self._require_capability(authority, command.capability_id, trust.evaluation_at)
        key: str | None
        disposition: str | None
        if command.event_type == "discovered":
            key, disposition, blocking = self._discovery(
                command, trust, events, authority, evidence
            )
        elif command.event_type == "cross_scope_critical_evidence":
            self.require_reviewer(authority, command.capability_id)
            validate_handoff_target(command, trust)
            key, disposition, blocking = command.finding_key, None, False
        elif command.event_type == "cross_scope_handoff_resolved":
            authorize_handoff_resolution(command, authority, evidence, events)
            key, disposition, blocking = command.finding_key, None, False
        else:
            record = record_for(events, command.finding_key)
            assert record is not None
            self._transition(command, trust, authority, evidence, record, events)
            key, disposition, blocking = command.finding_key, None, record.blocking
        return authority, key, disposition, blocking

    def authority(
        self,
        trust: FindingTrustContext,
        actor_id: str,
        slot_id: str,
    ) -> TrustedFindingAuthority:
        authority = next(
            (
                item
                for item in trust.authorities
                if item.actor_id == actor_id and item.slot_id == slot_id
            ),
            None,
        )
        if authority is None:
            raise PermissionError("finding actor and slot are not trusted authority")
        return authority

    def require_reviewer(
        self,
        authority: TrustedFindingAuthority,
        capability_id: str,
    ) -> None:
        if not required_terminal_authority(authority, capability_id):
            raise PermissionError("finding action requires current required authority")

    def require_reviewer_at(
        self,
        authority: TrustedFindingAuthority,
        capability_id: str,
        evaluation_at: str,
    ) -> None:
        self._require_capability(authority, capability_id, evaluation_at)
        self.require_reviewer(authority, capability_id)

    def _discovery(
        self,
        command: FindingAppendCommand,
        trust: FindingTrustContext,
        events: tuple[FindingEvent, ...],
        authority: TrustedFindingAuthority,
        evidence: TrustedEvidenceDescriptor,
    ) -> tuple[str, str, bool]:
        self.require_reviewer(authority, command.capability_id)
        assert command.identity is not None
        if evidence.subject_identity_digest != command.identity.identity_digest:
            raise ValueError("finding evidence does not match discovered identity")
        if (
            command.category is not None
            and command.category != command.identity.category
        ):
            raise ValueError("finding category must come from identity")
        known = known_identity_decisions(events)
        decision = self._identity.resolve(
            command.identity,
            known=known if command.identity_mapping else (),
            mapping=command.identity_mapping,
        )
        if decision.status == "needs_user":
            raise ValueError(f"finding identity needs user: {decision.reason_id}")
        existing = record_for(events, decision.finding_key, required=False)
        if existing is not None and command.identity_mapping is None:
            raise ValueError("existing finding must not be rediscovered")
        if (
            existing is not None
            and existing.identity_digest != decision.identity_digest
            and command.identity_mapping is None
        ):
            raise SharedStateIntegrityError("finding identity collision")
        severity = command.severity or "P3"
        blocking = self._late_origin_blocks(
            command, trust, authority, evidence, events, severity
        )
        if command.identity_mapping is not None:
            sources = tuple(record_for(events, key) for key in decision.source_keys)
            blocking = blocking or any(
                item is not None and item.blocking for item in sources
            )
        return decision.finding_key, "blocking" if blocking else "advisory", blocking

    def _late_origin_blocks(
        self,
        command: FindingAppendCommand,
        trust: FindingTrustContext,
        authority: TrustedFindingAuthority,
        evidence: TrustedEvidenceDescriptor,
        events: tuple[FindingEvent, ...],
        severity: str,
    ) -> bool:
        origin = command.late_origin
        if origin is None:
            return False
        if origin == "protocol_or_required_test_failure":
            kinds = {"required_test_failure", "protocol_integrity_failure"}
            if (
                authority.authority_kind != "deterministic_gate"
                or evidence.evidence_kind not in kinds
            ):
                raise PermissionError(
                    "protocol blocker requires deterministic gate proof"
                )
            return True
        if severity not in {"P0", "P1"}:
            return False
        if authority.slot_kind != "required" and (
            authority.authority_kind != "deterministic_gate"
        ):
            return False
        if origin == "regression_of":
            self._validate_regression(command, evidence, events)
        elif origin == "new_critical_evidence":
            if (
                evidence.evidence_kind != "new_critical_evidence"
                or evidence.initial_visibility != "not_visible"
                or parse_utc(evidence.first_visible_at)
                <= parse_utc(trust.initial_review_seal.sealed_at)
            ):
                raise ValueError("new critical evidence existed at initial seal")
        elif origin == "late_confirmed_p0_p1":
            if not _late_confirmation_proved(evidence):
                raise ValueError("late critical finding lacks confirmation proof")
            if parse_utc(evidence.first_visible_at) > parse_utc(
                trust.initial_review_seal.sealed_at
            ):
                raise ValueError(
                    "late critical finding was not visible at initial seal"
                )
        return True

    def _validate_regression(
        self,
        command: FindingAppendCommand,
        evidence: TrustedEvidenceDescriptor,
        events: tuple[FindingEvent, ...],
    ) -> None:
        prior = record_for(events, command.regression_of)
        assert prior is not None
        terminal = next(
            (
                item
                for item in reversed(events)
                if item.finding_key == prior.finding_key
                and item.event_type in {"verified", "waived", "superseded"}
            ),
            None,
        )
        reused = any(
            item.evidence_kind == "regression"
            and item.evidence_occurrence_id == evidence.occurrence_id
            for item in events
        )
        if prior.state not in {"verified", "waived", "superseded"}:
            raise ValueError("regression must reference a resolved finding")
        if (
            evidence.evidence_kind != "regression"
            or evidence.related_finding_key != prior.finding_key
            or evidence.related_identity_digest != prior.identity_digest
            or command.identity is None
            or evidence.subject_identity_digest != command.identity.identity_digest
            or terminal is None
            or evidence.source_event_digest != terminal.event_digest
            or not evidence.occurrence_id
            or reused
        ):
            raise ValueError("regression evidence does not match finding lineage")

    def _transition(
        self,
        command: FindingAppendCommand,
        trust: FindingTrustContext,
        authority: TrustedFindingAuthority,
        evidence: TrustedEvidenceDescriptor,
        record: FindingRecord,
        events: tuple[FindingEvent, ...],
    ) -> None:
        event = command.event_type
        if not finding_transition_allowed(event, record.state):
            raise ValueError("finding state transition is invalid")
        if event in {"acknowledged", "remediation_started"}:
            if authority.authority_kind not in {"coordinator", "remediator"}:
                raise PermissionError(
                    "finding transition requires coordination authority"
                )
        elif event == "fixed":
            if (
                authority.authority_kind != "remediator"
                or not command.remediation_batch_id
            ):
                raise PermissionError("fixed requires remediator and remediation batch")
            if command.candidate_digest == record.candidate_digest:
                raise ValueError("fixed requires a new candidate digest")
        elif event in {"verified", "verification_failed", "regressed"}:
            self._verification(command, authority, record)
        elif event == "waived":
            self._waiver(command, trust, authority, evidence, record.category)
        elif event == "superseded":
            self._supersede(command, authority, evidence, events)
        else:
            raise PermissionError("finding event has no authorized transition")

    def _verification(
        self,
        command: FindingAppendCommand,
        authority: TrustedFindingAuthority,
        record: FindingRecord,
    ) -> None:
        self.require_reviewer(authority, command.capability_id)
        if command.event_type == "verified" and record.state not in {
            "fixed",
            "verification_failed",
        }:
            raise ValueError("finding must be fixed before verification")
        if command.event_type == "regressed" and (
            command.regression_of != command.finding_key
        ):
            raise ValueError("regressed event must reference the existing finding")

    def _waiver(
        self,
        command: FindingAppendCommand,
        trust: FindingTrustContext,
        authority: TrustedFindingAuthority,
        evidence: TrustedEvidenceDescriptor,
        category: str,
    ) -> None:
        if category in trust.non_waivable_categories:
            raise PermissionError("finding category is non-waivable")
        if authority.authority_kind not in {"human_governance", "identity_governance"}:
            raise PermissionError("waiver requires governance authority")
        waiver = next(
            (item for item in trust.waivers if item.waiver_id == command.waiver_id),
            None,
        )
        if waiver is None or (
            waiver.finding_key != command.finding_key
            or waiver.waiver_digest != command.waiver_digest
            or waiver.scope != trust.scope
            or waiver.candidate_digest != trust.candidate_digest
            or waiver.policy_digest != trust.policy_digest
            or waiver.approved_by_actor_id != authority.actor_id
            or waiver.approved_by_slot_id != authority.slot_id
            or waiver.authority_binding_digest != authority.binding_digest
            or waiver.evidence_digest != evidence.evidence_bundle_digest
            or evidence.evidence_kind != "waiver_approval"
        ):
            raise PermissionError("waiver is not trusted for finding")
        moment = parse_utc(trust.evaluation_at)
        if not parse_utc(waiver.issued_at) <= moment < parse_utc(waiver.expires_at):
            raise PermissionError("waiver is expired")

    def _supersede(
        self,
        command: FindingAppendCommand,
        authority: TrustedFindingAuthority,
        evidence: TrustedEvidenceDescriptor,
        events: tuple[FindingEvent, ...],
    ) -> None:
        if authority.authority_kind not in {"human_governance", "identity_governance"}:
            raise PermissionError("superseded requires identity governance authority")
        macro = command.macro_rebaseline_evidence_digest
        if not command.replacement_keys and (
            not macro
            or macro != evidence.evidence_bundle_digest
            or evidence.evidence_kind != "macro_rebaseline"
        ):
            raise ValueError("superseded requires replacement or macro rebaseline")
        if command.finding_key in command.replacement_keys:
            raise ValueError("superseded requires non-cyclic replacement")
        if not command.replacement_keys:
            return
        source = cast(FindingRecord, record_for(events, command.finding_key))
        required = required_replacement_keys(events, source.finding_key)
        if required and tuple(sorted(command.replacement_keys)) != required:
            raise ValueError("superseded replacements do not fulfill identity mapping")
        replacements = tuple(
            cast(FindingRecord, record_for(events, key))
            for key in command.replacement_keys
        )
        if any(
            item.candidate_digest != source.candidate_digest for item in replacements
        ):
            raise ValueError("superseded replacements cross candidate lineage")
        replacement_keys = tuple(item.finding_key for item in replacements)
        if replacement_cycle_exists(events, source.finding_key, replacement_keys):
            raise ValueError("superseded replacement graph contains a cycle")

    def _require_capability(
        self,
        authority: TrustedFindingAuthority,
        capability_id: str,
        evaluation_at: str,
    ) -> None:
        if capability_id not in authority.capability_ids:
            raise PermissionError("finding capability is not trusted authority")
        if parse_utc(authority.valid_until) <= parse_utc(evaluation_at):
            raise PermissionError("finding authority is expired")


def _late_confirmation_proved(evidence: TrustedEvidenceDescriptor) -> bool:
    return (
        evidence.evidence_kind == "late_critical_confirmation"
        and evidence.confirmation_result == "confirmed"
        and evidence.initial_visibility == "visible"
    )
