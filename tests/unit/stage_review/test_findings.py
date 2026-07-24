from __future__ import annotations

import json
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event, Thread
from typing import Literal, cast

import pytest

from ai_sdlc.core.stage_review.activation_fence import (
    activation_safety_read_lease,
)
from ai_sdlc.core.stage_review.artifacts import SharedStateIntegrityError
from ai_sdlc.core.stage_review.canonical import (
    CanonicalizationPolicy,
    canonical_digest,
)
from ai_sdlc.core.stage_review.finding_artifact_codec import (
    decode_finding_event,
    decode_finding_waiver,
    validate_finding_artifact_for_write,
)
from ai_sdlc.core.stage_review.finding_command_models import (
    FindingLineageAdvanceCommand,
)
from ai_sdlc.core.stage_review.finding_digests import (
    initial_finding_batch_digest,
    persisted_event_digest,
)
from ai_sdlc.core.stage_review.finding_models import (
    AuthorityKind,
    FindingAppendResult,
    FindingEvent,
    FindingEventType,
    IdentityMappingKind,
)
from ai_sdlc.core.stage_review.finding_reducer import reduce_finding_events
from ai_sdlc.core.stage_review.finding_trust_models import (
    EvidenceConfirmation,
    EvidenceKind,
    EvidenceVisibility,
    InitialReviewSeal,
    TrustedEvidenceDescriptor,
    TrustedIdentityMappingDecision,
)
from ai_sdlc.core.stage_review.findings import (
    FindingAppendCommand,
    FindingCloseContext,
    FindingIdentityInput,
    FindingIdentityMapping,
    FindingIdentityResolver,
    FindingInitialBatchCommand,
    FindingInitialDraft,
    FindingLedgerService,
    FindingScope,
    FindingTrustContext,
    FindingWaiver,
    ProgressSnapshot,
    TrustedFindingAuthority,
    compare_progress,
    evaluate_closeability,
)

SCOPE = FindingScope(
    project_id="project.findings",
    work_item_id="WI-401",
    stage_instance_id="execute.1",
    session_id="review-session.401",
)


def _identity(**updates: object) -> FindingIdentityInput:
    values: dict[str, object] = {
        "rule_id": "python.no-shell-injection",
        "category": "security",
        "asset_identity": "src/app.py:run",
        "semantic_location": "function:run",
        "failure_signature": "unsafe-command-construction",
        "claim": "旧的自然语言描述",
        "risk_text": "可能执行非预期命令",
        "line": 41,
    }
    values.update(updates)
    return FindingIdentityInput.model_validate(values)


def _authority(
    authority_kind: AuthorityKind,
    *,
    actor_id: str,
    slot_id: str = "slot.security",
    slot_kind: str = "required",
    capabilities: tuple[str, ...] = ("security",),
) -> TrustedFindingAuthority:
    return TrustedFindingAuthority(
        actor_id=actor_id,
        slot_id=slot_id,
        slot_kind=slot_kind,
        authority_kind=authority_kind,
        capability_ids=capabilities,
        blocking_authorities=capabilities,
        role_contract_digest="sha256:role",
        binding_digest="sha256:binding.security",
        eligible_for_enforce_quorum=slot_kind == "required",
        valid_until="2030-01-01T00:00:00Z",
        role_profile_id=f"role.{slot_id}",
        capability_coverage_digest=f"sha256:coverage.{slot_id}",
    )


def _initial_draft() -> FindingInitialDraft:
    return FindingInitialDraft(
        identity=_identity(),
        severity="P1",
        evidence_bundle_digest="sha256:evidence.initial",
        actor_id="reviewer.security",
        slot_id="slot.security",
        capability_id="security",
    )


def _initial_seal(
    findings: tuple[FindingInitialDraft, ...] | None = None,
    **updates: object,
) -> InitialReviewSeal:
    values: dict[str, object] = {
        "scope": SCOPE,
        "initial_candidate_digest": "sha256:candidate.2",
        "policy_digest": "sha256:policy.1",
        "plan_digest": "sha256:plan.1",
        "binding_set_digest": "sha256:binding-set.1",
        "initial_cohort_id": "cohort.initial",
        "required_slot_ids": ("slot.security",),
        "required_pass_digests": ("sha256:pass.security",),
        "coverage_declaration_digests": ("sha256:coverage.security",),
        "finding_batch_digest": initial_finding_batch_digest(
            findings if findings is not None else (_initial_draft(),)
        ),
        "sealed_at": "2026-07-20T11:00:00Z",
    }
    values.update(updates)
    return InitialReviewSeal.model_validate(values)


def _trust(**updates: object) -> FindingTrustContext:
    values: dict[str, object] = {
        "scope": SCOPE,
        "candidate_digest": "sha256:candidate.2",
        "policy_digest": "sha256:policy.1",
        "plan_digest": "sha256:plan.1",
        "binding_set_digest": "sha256:binding-set.1",
        "cohort_id": "cohort.initial",
        "reviewer_engine_version": "stage-review.v1",
        "initial_review_seal": _initial_seal(),
        "session_fencing_epoch": 3,
        "authorities": (
            _authority("reviewer", actor_id="reviewer.security"),
            _authority(
                "deterministic_gate",
                actor_id="gate.required-tests",
                slot_id="gate.required-tests",
                capabilities=("required-tests", "protocol-integrity"),
            ),
            _authority(
                "remediator",
                actor_id="remediator.1",
                slot_id="remediator.1",
                capabilities=("remediation",),
            ),
            _authority(
                "coordinator",
                actor_id="coordinator.1",
                slot_id="coordinator.1",
                capabilities=("coordination",),
            ),
            _authority(
                "human_governance",
                actor_id="human.owner",
                slot_id="human.owner",
                capabilities=("waiver", "identity-governance"),
            ),
        ),
        "waivers": (),
        "non_waivable_categories": ("security",),
        "evaluation_at": "2026-07-20T12:00:00Z",
    }
    values.update(updates)
    return FindingTrustContext.model_validate(values)


class _TrustResolver:
    def __init__(self, context: FindingTrustContext) -> None:
        self.context = context
        self.evidence: dict[str, TrustedEvidenceDescriptor] = {}
        self.mappings: dict[str, TrustedIdentityMappingDecision] = {}
        self.authority_history = {
            (item.actor_id, item.slot_id, item.binding_digest): item
            for item in context.authorities
        }

    def resolve(self, scope: FindingScope) -> FindingTrustContext:
        assert scope == self.context.scope
        self.authority_history.update(
            {
                (item.actor_id, item.slot_id, item.binding_digest): item
                for item in self.context.authorities
            }
        )
        return self.context

    def resolve_evidence(
        self, scope: FindingScope, evidence_bundle_digest: str
    ) -> TrustedEvidenceDescriptor | None:
        assert scope.project_id == self.context.scope.project_id
        if evidence_bundle_digest == "sha256:forged":
            return None
        cached = self.evidence.get(evidence_bundle_digest)
        if cached is not None:
            return cached
        kind: EvidenceKind = "finding"
        produced_at = "2026-07-20T10:00:00Z"
        first_visible_at = produced_at
        visibility: EvidenceVisibility = "visible"
        confirmation: EvidenceConfirmation = "confirmed"
        related_key = ""
        related_identity = ""
        related_handoff = ""
        handoff_resolution: Literal["accepted", "rejected"] | None = None
        subject_identity = ""
        source_event = ""
        occurrence_id = ""
        if "new-after-seal" in evidence_bundle_digest:
            kind = "new_critical_evidence"
            produced_at = first_visible_at = "2026-07-20T12:00:00Z"
            visibility = "not_visible"
        elif "preexisting-new" in evidence_bundle_digest:
            kind = "new_critical_evidence"
        elif "required-test" in evidence_bundle_digest:
            kind = "required_test_failure"
        elif "protocol-integrity" in evidence_bundle_digest:
            kind = "protocol_integrity_failure"
        elif "late-confirmed" in evidence_bundle_digest:
            kind = "late_critical_confirmation"
        elif "waiver" in evidence_bundle_digest:
            kind = "waiver_approval"
        elif evidence_bundle_digest.startswith("regression|"):
            kind = "regression"
            parts = evidence_bundle_digest.split("|")
            related_key = parts[1] if len(parts) > 1 else ""
            related_identity = parts[2] if len(parts) > 2 else ""
            source_event = parts[3] if len(parts) > 3 else ""
            occurrence_id = parts[4] if len(parts) > 4 else ""
        elif evidence_bundle_digest.startswith("handoff-receipt|"):
            kind = "handoff_receipt"
            parts = evidence_bundle_digest.split("|")
            source_event = parts[1] if len(parts) > 1 else ""
            related_handoff = parts[2] if len(parts) > 2 else ""
            if len(parts) > 3 and parts[3] in {"accepted", "rejected"}:
                handoff_resolution = cast(Literal["accepted", "rejected"], parts[3])
        elif "macro-rebaseline" in evidence_bundle_digest:
            kind = "macro_rebaseline"
        subject_prefix = "subject:"
        subject_identity = next(
            (
                item.removeprefix(subject_prefix)
                for item in evidence_bundle_digest.split("|")
                if item.startswith(subject_prefix)
            ),
            "",
        )
        if evidence_bundle_digest == "sha256:evidence.initial":
            subject_identity = _identity().identity_digest
        descriptor = TrustedEvidenceDescriptor(
            scope=scope,
            evidence_bundle_digest=evidence_bundle_digest,
            evidence_kind=kind,
            candidate_digest=(
                self.context.candidate_digest
                if scope == self.context.scope
                else "sha256:candidate.target"
            ),
            produced_at=produced_at,
            first_visible_at=first_visible_at,
            initial_visibility=visibility,
            confirmation_result=confirmation,
            related_finding_key=related_key,
            related_identity_digest=related_identity,
            related_handoff_id=related_handoff,
            handoff_resolution=handoff_resolution,
            subject_identity_digest=subject_identity,
            source_event_digest=source_event,
            occurrence_id=occurrence_id,
            signer_actor_id="target.governance" if kind == "handoff_receipt" else "",
            signer_slot_id="target.required" if kind == "handoff_receipt" else "",
            signer_slot_kind="required" if kind == "handoff_receipt" else "",
            signer_authority_kind=(
                "human_governance" if kind == "handoff_receipt" else ""
            ),
            signer_capability_id="handoff" if kind == "handoff_receipt" else "",
            signer_capability_ids=("handoff",) if kind == "handoff_receipt" else (),
            signer_blocking_authorities=("handoff",)
            if kind == "handoff_receipt"
            else (),
            signer_eligible_for_enforce_quorum=kind == "handoff_receipt",
            signer_role_contract_digest=(
                "sha256:role.target" if kind == "handoff_receipt" else ""
            ),
            signer_binding_digest=(
                "sha256:binding.target" if kind == "handoff_receipt" else ""
            ),
        )
        self.evidence[evidence_bundle_digest] = descriptor
        return descriptor

    def event_is_trusted(self, event: object) -> bool:
        if not isinstance(event, FindingEvent):
            return False
        evidence = self.resolve_evidence(
            event.evidence_scope or event.scope, event.evidence_bundle_digest
        )
        authority = self.authority_history.get(
            (event.actor_id, event.slot_id, event.binding_digest)
        )
        if evidence is None or authority is None:
            return False
        frozen_evidence = (
            event.evidence_descriptor_digest,
            event.evidence_scope,
            event.evidence_candidate_digest,
            event.evidence_kind,
            event.evidence_produced_at,
            event.evidence_first_visible_at,
            event.evidence_initial_visibility,
            event.evidence_confirmation_result,
            event.evidence_related_finding_key,
            event.evidence_related_identity_digest,
            event.evidence_related_handoff_id,
            event.evidence_handoff_resolution,
            event.evidence_subject_identity_digest,
            event.evidence_source_event_digest,
            event.evidence_occurrence_id,
            event.evidence_signer_actor_id,
            event.evidence_signer_slot_id,
            event.evidence_signer_slot_kind,
            event.evidence_signer_authority_kind,
            event.evidence_signer_capability_id,
            event.evidence_signer_capability_ids,
            event.evidence_signer_blocking_authorities,
            event.evidence_signer_eligible_for_enforce_quorum,
            event.evidence_signer_role_contract_digest,
            event.evidence_signer_binding_digest,
        )
        trusted_evidence = (
            evidence.descriptor_digest,
            evidence.scope,
            evidence.candidate_digest,
            evidence.evidence_kind,
            evidence.produced_at,
            evidence.first_visible_at,
            evidence.initial_visibility,
            evidence.confirmation_result,
            evidence.related_finding_key,
            evidence.related_identity_digest,
            evidence.related_handoff_id,
            evidence.handoff_resolution,
            evidence.subject_identity_digest,
            evidence.source_event_digest,
            evidence.occurrence_id,
            evidence.signer_actor_id,
            evidence.signer_slot_id,
            evidence.signer_slot_kind,
            evidence.signer_authority_kind,
            evidence.signer_capability_id,
            evidence.signer_capability_ids,
            evidence.signer_blocking_authorities,
            evidence.signer_eligible_for_enforce_quorum,
            evidence.signer_role_contract_digest,
            evidence.signer_binding_digest,
        )
        frozen_authority = (
            event.authority_kind,
            event.authority_slot_kind,
            event.authority_capability_ids,
            event.authority_blocking_authorities,
            event.authority_eligible_for_enforce_quorum,
            event.authority_valid_until,
            event.authority_role_profile_id,
            event.authority_capability_coverage_digest,
            event.role_contract_digest,
            event.binding_digest,
        )
        trusted_authority = (
            authority.authority_kind,
            authority.slot_kind,
            authority.capability_ids,
            authority.blocking_authorities,
            authority.eligible_for_enforce_quorum,
            authority.valid_until,
            authority.role_profile_id,
            authority.capability_coverage_digest,
            authority.role_contract_digest,
            authority.binding_digest,
        )
        return (
            frozen_evidence == trusted_evidence
            and frozen_authority == trusted_authority
            and (
                evidence.evidence_kind == "handoff_receipt"
                or evidence.candidate_digest == event.candidate_digest
            )
            and event.initial_review_seal_digest
            == self.context.initial_review_seal_digest
        )

    def session_lineage_is_trusted(self, event: object) -> bool:
        return isinstance(event, FindingEvent) and (
            event.event_type == "ledger_lineage_advanced"
            and event.evidence_bundle_digest.startswith("sha256:session-event")
        )

    def resolve_mapping(
        self, scope: FindingScope, decision_digest: str
    ) -> TrustedIdentityMappingDecision | None:
        assert scope == self.context.scope
        return self.mappings.get(decision_digest)


def _service(
    root: Path,
    context: FindingTrustContext | None = None,
    *,
    fault_hook: Callable[[str], None] | None = None,
    event_observer: Callable[[FindingEvent], None] | None = None,
) -> FindingLedgerService:
    return FindingLedgerService(
        root,
        project_id=SCOPE.project_id,
        trust_resolver=_TrustResolver(context or _trust()),
        fault_hook=fault_hook,
        event_observer=event_observer,
    )


def _advance_candidate(
    service: FindingLedgerService,
    digest: str = "sha256:candidate.remediated",
) -> FindingTrustContext:
    before = service.read(SCOPE)
    resolver = service._trust_resolver  # noqa: SLF001
    current = cast(FindingTrustContext, resolver.context)  # type: ignore[attr-defined]
    context = current.model_copy(update={"candidate_digest": digest})
    resolver.context = context  # type: ignore[attr-defined]
    service.advance_lineage(
        FindingLineageAdvanceCommand(
            scope=SCOPE,
            command_id=f"command.test-lineage.{before.revision}",
            idempotency_key=f"idem.test-lineage.{before.revision}",
            expected_revision=before.revision,
            session_fencing_epoch=context.session_fencing_epoch,
            candidate_digest=context.candidate_digest,
            policy_digest=context.policy_digest,
            plan_digest=context.plan_digest,
            binding_set_digest=context.binding_set_digest,
            cohort_id=context.cohort_id,
            previous_ledger_digest=before.ledger_digest,
            session_event_digest=f"sha256:session-event.test.{before.revision}",
            advanced_at=context.evaluation_at,
        )
    )
    return context


def _close_context(service: FindingLedgerService) -> FindingCloseContext:
    resolver = service._trust_resolver  # noqa: SLF001
    return FindingCloseContext.from_trust(resolver.context)  # type: ignore[attr-defined]


def _trusted_mapping(
    service: FindingLedgerService,
    *,
    mapping_kind: IdentityMappingKind,
    source_keys: tuple[str, ...],
    target_identity_digests: tuple[str, ...],
) -> FindingIdentityMapping:
    resolver = service._trust_resolver  # noqa: SLF001
    decision = TrustedIdentityMappingDecision(
        scope=SCOPE,
        candidate_digest=resolver.context.candidate_digest,  # type: ignore[attr-defined]
        mapping_kind=mapping_kind,
        source_keys=source_keys,
        target_identity_digests=target_identity_digests,
        resolver_version="finding-identity.v1",
        lineage_evidence_digest="sha256:symbol-index-proof",
        issued_at="2026-07-20T11:30:00Z",
    )
    resolver.mappings[decision.decision_digest] = decision  # type: ignore[attr-defined]
    return FindingIdentityMapping(
        mapping_kind=mapping_kind,
        source_keys=source_keys,
        target_identity_digests=target_identity_digests,
        evidence_digest=decision.decision_digest,
        resolver_version=decision.resolver_version,
    )


def _initialize(
    service: FindingLedgerService,
    scope: FindingScope = SCOPE,
) -> str:
    trust = service._trust_resolver.resolve(scope)  # noqa: SLF001
    result = service.append(
        FindingInitialBatchCommand(
            scope=scope,
            command_id="command.initial",
            idempotency_key="initial",
            expected_revision=0,
            session_fencing_epoch=trust.session_fencing_epoch,
            candidate_digest=trust.candidate_digest,
            policy_digest=trust.policy_digest,
            plan_digest=trust.plan_digest,
            binding_set_digest=trust.binding_set_digest,
            initial_review_seal_digest=trust.initial_review_seal_digest,
            findings=(_initial_draft(),),
        )
    )
    assert result.ledger.initialized
    return result.ledger.records[0].finding_key


def _append(
    service: FindingLedgerService,
    existing_key: str,
    event_type: FindingEventType,
    *,
    scope: FindingScope = SCOPE,
    actor_id: str,
    slot_id: str,
    capability_id: str,
    **updates: object,
) -> FindingAppendResult:
    ledger = service.read(scope)
    trust = service._trust_resolver.resolve(scope)  # noqa: SLF001
    values: dict[str, object] = {
        "scope": scope,
        "command_id": f"command.{event_type}.{ledger.revision + 1}",
        "idempotency_key": f"idem.{event_type}.{ledger.revision + 1}",
        "expected_revision": ledger.revision,
        "session_fencing_epoch": trust.session_fencing_epoch,
        "finding_key": existing_key,
        "event_type": event_type,
        "actor_id": actor_id,
        "slot_id": slot_id,
        "capability_id": capability_id,
        "candidate_digest": trust.candidate_digest,
        "policy_digest": trust.policy_digest,
        "plan_digest": trust.plan_digest,
        "binding_set_digest": trust.binding_set_digest,
        "evidence_bundle_digest": f"sha256:evidence.{event_type}",
    }
    values.update(updates)
    identity = values.get("identity")
    if event_type == "discovered" and isinstance(identity, FindingIdentityInput):
        bundle = str(values["evidence_bundle_digest"])
        if "|subject:" not in bundle:
            values["evidence_bundle_digest"] = (
                f"{bundle}|subject:{identity.identity_digest}"
            )
    return service.append(FindingAppendCommand.model_validate(values))


def test_lineage_advance_preserves_records_and_is_idempotent(tmp_path: Path) -> None:
    service = _service(tmp_path)
    key = _initialize(service)
    before = service.read(SCOPE)
    resolver = cast(_TrustResolver, service._trust_resolver)  # noqa: SLF001
    resolver.context = resolver.context.model_copy(
        update={
            "candidate_digest": "sha256:candidate.lineage-2",
            "plan_digest": "sha256:plan.lineage-2",
            "binding_set_digest": "sha256:binding-set.lineage-2",
            "cohort_id": "cohort.lineage-2",
            "evaluation_at": "2026-07-20T12:30:00Z",
        }
    )
    command = FindingLineageAdvanceCommand(
        scope=SCOPE,
        command_id="command.lineage-2",
        idempotency_key="idem.lineage-2",
        expected_revision=before.revision,
        session_fencing_epoch=resolver.context.session_fencing_epoch,
        candidate_digest=resolver.context.candidate_digest,
        policy_digest=resolver.context.policy_digest,
        plan_digest=resolver.context.plan_digest,
        binding_set_digest=resolver.context.binding_set_digest,
        cohort_id=resolver.context.cohort_id,
        previous_ledger_digest=before.ledger_digest,
        session_event_digest="sha256:session-event.lineage-2",
        advanced_at=resolver.context.evaluation_at,
    )

    advanced = service.advance_lineage(command)
    replay = service.advance_lineage(command)

    assert advanced.ledger.records[0].finding_key == key
    assert advanced.ledger.records == before.records
    assert advanced.ledger.candidate_digest == resolver.context.candidate_digest
    assert advanced.ledger.plan_digest == resolver.context.plan_digest
    assert advanced.ledger.binding_set_digest == resolver.context.binding_set_digest
    assert replay.idempotent_replay
    assert replay.ledger.ledger_digest == advanced.ledger.ledger_digest


def test_regular_event_observer_runs_after_finding_lock_is_released(
    tmp_path: Path,
) -> None:
    observed: list[tuple[str, int]] = []
    service: FindingLedgerService

    def observe(event: FindingEvent) -> None:
        observed.append((event.event_type, service.read(SCOPE).revision))

    service = _service(tmp_path, event_observer=observe)
    key = _initialize(service)

    result = _append(
        service,
        key,
        "acknowledged",
        actor_id="coordinator.1",
        slot_id="coordinator.1",
        capability_id="coordination",
    )

    assert observed == [("acknowledged", result.ledger.revision)]


def test_lineage_advance_rejects_an_untrusted_session_event(tmp_path: Path) -> None:
    service = _service(tmp_path)
    _initialize(service)
    before = service.read(SCOPE)
    resolver = cast(_TrustResolver, service._trust_resolver)  # noqa: SLF001
    resolver.context = resolver.context.model_copy(
        update={
            "binding_set_digest": "sha256:binding-set.untrusted-lineage",
            "cohort_id": "cohort.untrusted-lineage",
            "evaluation_at": "2026-07-20T12:30:00Z",
        }
    )

    with pytest.raises(SharedStateIntegrityError, match="not trusted"):
        service.advance_lineage(
            FindingLineageAdvanceCommand(
                scope=SCOPE,
                command_id="command.untrusted-lineage",
                idempotency_key="idem.untrusted-lineage",
                expected_revision=before.revision,
                session_fencing_epoch=resolver.context.session_fencing_epoch,
                candidate_digest=resolver.context.candidate_digest,
                policy_digest=resolver.context.policy_digest,
                plan_digest=resolver.context.plan_digest,
                binding_set_digest=resolver.context.binding_set_digest,
                cohort_id=resolver.context.cohort_id,
                previous_ledger_digest=before.ledger_digest,
                session_event_digest="sha256:untrusted-lineage",
                advanced_at=resolver.context.evaluation_at,
            )
        )

    assert service.read(SCOPE).ledger_digest == before.ledger_digest


def test_regular_mutation_requires_a_trusted_lineage_advance(tmp_path: Path) -> None:
    service = _service(tmp_path)
    key = _initialize(service)
    before = service.read(SCOPE)
    resolver = cast(_TrustResolver, service._trust_resolver)  # noqa: SLF001
    resolver.context = resolver.context.model_copy(
        update={
            "candidate_digest": "sha256:candidate.lineage-guard",
            "plan_digest": "sha256:plan.lineage-guard",
            "binding_set_digest": "sha256:binding-set.lineage-guard",
            "cohort_id": "cohort.lineage-guard",
            "evaluation_at": "2026-07-20T12:45:00Z",
        }
    )

    with pytest.raises(SharedStateIntegrityError, match="lineage advance"):
        _append(
            service,
            key,
            "acknowledged",
            actor_id="coordinator.1",
            slot_id="coordinator.1",
            capability_id="coordination",
        )
    assert service.read(SCOPE).ledger_digest == before.ledger_digest

    service.advance_lineage(
        FindingLineageAdvanceCommand(
            scope=SCOPE,
            command_id="command.lineage-guard",
            idempotency_key="idem.lineage-guard",
            expected_revision=before.revision,
            session_fencing_epoch=resolver.context.session_fencing_epoch,
            candidate_digest=resolver.context.candidate_digest,
            policy_digest=resolver.context.policy_digest,
            plan_digest=resolver.context.plan_digest,
            binding_set_digest=resolver.context.binding_set_digest,
            cohort_id=resolver.context.cohort_id,
            previous_ledger_digest=before.ledger_digest,
            session_event_digest="sha256:session-event.lineage-guard",
            advanced_at=resolver.context.evaluation_at,
        )
    )
    result = _append(
        service,
        key,
        "acknowledged",
        actor_id="coordinator.1",
        slot_id="coordinator.1",
        capability_id="coordination",
    )

    assert result.ledger.candidate_digest == resolver.context.candidate_digest
    assert (
        [
            event.event_type
            for event in service._store.load_events(SCOPE)  # noqa: SLF001
        ][-2:]
        == ["ledger_lineage_advanced", "acknowledged"]
    )


def test_replay_rejects_a_regular_event_that_skips_lineage_advance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _service(tmp_path / "source")
    source_key = _initialize(source)
    source_resolver = cast(_TrustResolver, source._trust_resolver)  # noqa: SLF001
    source_resolver.context = source_resolver.context.model_copy(
        update={
            "candidate_digest": "sha256:candidate.replay-bypass",
            "cohort_id": "cohort.replay-bypass",
        }
    )
    monkeypatch.setattr(source, "_validate_mutation", lambda *_: None)
    with pytest.raises(SharedStateIntegrityError, match="lineage advance"):
        _append(
            source,
            source_key,
            "acknowledged",
            actor_id="coordinator.1",
            slot_id="coordinator.1",
            capability_id="coordination",
        )
    bypass = source._store.load_events(SCOPE)[-1]  # noqa: SLF001

    target = _service(tmp_path / "target")
    _initialize(target)
    target_resolver = cast(_TrustResolver, target._trust_resolver)  # noqa: SLF001
    target_resolver.context = target_resolver.context.model_copy(
        update={
            "candidate_digest": "sha256:candidate.replay-bypass",
            "cohort_id": "cohort.replay-bypass",
        }
    )
    target._store.append_event(bypass)  # noqa: SLF001

    with pytest.raises(SharedStateIntegrityError, match="lineage advance"):
        target.read(SCOPE)

    tail_path = sorted(
        (target._store.session_root(SCOPE) / "events").glob("*.json")  # noqa: SLF001
    )[-1]
    tail = json.loads(tail_path.read_text(encoding="utf-8"))
    tail["schema_version"] = "finding-event.v1"
    tail["event_digest"] = persisted_event_digest(tail)
    tail_path.write_text(json.dumps(tail), encoding="utf-8")
    target._store.projection_path(SCOPE).unlink(missing_ok=True)  # noqa: SLF001
    downgraded = target._store.load_events(SCOPE)  # noqa: SLF001
    assert (
        reduce_finding_events(SCOPE, downgraded).lineage_contract_version
        == "explicit-v2"
    )

    with pytest.raises(SharedStateIntegrityError, match="schema downgrade"):
        target.read(SCOPE)


def test_replay_rejects_a_same_lineage_v1_tail_after_v2_activation(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    key = _initialize(service)
    _append(
        service,
        key,
        "acknowledged",
        actor_id="coordinator.1",
        slot_id="coordinator.1",
        capability_id="coordination",
    )
    tail_path = sorted(
        (service._store.session_root(SCOPE) / "events").glob("*.json")  # noqa: SLF001
    )[-1]
    tail = json.loads(tail_path.read_text(encoding="utf-8"))
    tail["schema_version"] = "finding-event.v1"
    tail["event_digest"] = persisted_event_digest(tail)
    tail_path.write_text(json.dumps(tail), encoding="utf-8")
    service._store.projection_path(SCOPE).unlink(missing_ok=True)  # noqa: SLF001

    with pytest.raises(SharedStateIntegrityError, match="schema downgrade"):
        service.read(SCOPE)


def test_legacy_v1_lineage_is_read_only_until_a_trusted_bridge(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service(tmp_path)
    key = _initialize(service)
    resolver = cast(_TrustResolver, service._trust_resolver)  # noqa: SLF001
    resolver.context = resolver.context.model_copy(
        update={
            "candidate_digest": "sha256:candidate.legacy-t401",
            "cohort_id": "cohort.legacy-t401",
        }
    )
    monkeypatch.setattr(service, "_validate_mutation", lambda *_: None)
    with pytest.raises(SharedStateIntegrityError, match="lineage advance"):
        _append(
            service,
            key,
            "acknowledged",
            actor_id="coordinator.1",
            slot_id="coordinator.1",
            capability_id="coordination",
        )
    event_dir = service._store.session_root(SCOPE) / "events"  # noqa: SLF001
    previous_digest = ""
    for path in sorted(event_dir.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["schema_version"] = "finding-event.v1"
        payload["previous_event_digest"] = previous_digest
        payload["event_digest"] = persisted_event_digest(payload)
        path.write_text(json.dumps(payload), encoding="utf-8")
        previous_digest = payload["event_digest"]
    service._store.projection_path(SCOPE).unlink(missing_ok=True)  # noqa: SLF001

    reader = FindingLedgerService(
        tmp_path,
        project_id=SCOPE.project_id,
        trust_resolver=resolver,
    )
    legacy = reader.read(SCOPE)
    assert legacy.candidate_digest == resolver.context.candidate_digest
    assert legacy.lineage_contract_version == "implicit-v1"
    with pytest.raises(SharedStateIntegrityError, match="lineage advance"):
        _append(
            reader,
            key,
            "remediation_started",
            actor_id="remediator.1",
            slot_id="remediator.1",
            capability_id="remediation",
        )

    reader.advance_lineage(
        FindingLineageAdvanceCommand(
            scope=SCOPE,
            command_id="command.legacy-bridge",
            idempotency_key="idem.legacy-bridge",
            expected_revision=legacy.revision,
            session_fencing_epoch=resolver.context.session_fencing_epoch,
            candidate_digest=resolver.context.candidate_digest,
            policy_digest=resolver.context.policy_digest,
            plan_digest=resolver.context.plan_digest,
            binding_set_digest=resolver.context.binding_set_digest,
            cohort_id=resolver.context.cohort_id,
            previous_ledger_digest=legacy.ledger_digest,
            session_event_digest="sha256:session-event.legacy-bridge",
            advanced_at=resolver.context.evaluation_at,
        )
    )
    bridge_path = sorted(
        (reader._store.session_root(SCOPE) / "events").glob("*.json")  # noqa: SLF001
    )[-1]
    bridge = json.loads(bridge_path.read_text(encoding="utf-8"))
    downgraded_bridge = {**bridge, "schema_version": "finding-event.v1"}
    downgraded_bridge["event_digest"] = persisted_event_digest(downgraded_bridge)
    bridge_path.write_text(json.dumps(downgraded_bridge), encoding="utf-8")
    reader._store.projection_path(SCOPE).unlink(missing_ok=True)  # noqa: SLF001
    with pytest.raises(SharedStateIntegrityError, match="lineage event schema"):
        reader.read(SCOPE)
    bridge_path.write_text(json.dumps(bridge), encoding="utf-8")
    reader._store.projection_path(SCOPE).unlink(missing_ok=True)  # noqa: SLF001

    continued = _append(
        reader,
        key,
        "remediation_started",
        actor_id="remediator.1",
        slot_id="remediator.1",
        capability_id="remediation",
    )

    assert continued.ledger.records[0].state == "remediation_started"
    assert continued.ledger.lineage_contract_version == "explicit-v2"
    assert (
        [
            event.event_type
            for event in reader._store.load_events(SCOPE)  # noqa: SLF001
        ][-2:]
        == ["ledger_lineage_advanced", "remediation_started"]
    )


def test_identity_ignores_claim_risk_and_line_but_requires_lineage_for_move() -> None:
    resolver = FindingIdentityResolver()
    original = resolver.resolve(_identity())
    wording_changed = resolver.resolve(
        _identity(claim="全新描述", risk_text="新风险文字", line=900)
    )
    assert original.finding_key == wording_changed.finding_key

    moved = _identity(
        asset_identity="src/runtime.py:execute",
        semantic_location="function:execute",
        line=12,
    )
    unresolved = resolver.resolve(moved, known=(original,))
    assert unresolved.status == "needs_user"

    mapped = resolver.resolve(
        moved,
        known=(original,),
        mapping=FindingIdentityMapping(
            mapping_kind="alias",
            source_keys=(original.finding_key,),
            target_identity_digests=(moved.identity_digest,),
            evidence_digest="sha256:symbol-move",
            resolver_version="finding-identity.v1",
        ),
    )
    assert mapped.status == "matched"
    assert mapped.finding_key == original.finding_key


def test_identity_collision_split_merge_and_cycles_fail_closed() -> None:
    resolver = FindingIdentityResolver()
    source = resolver.resolve(_identity())
    collision = source.model_copy(
        update={"identity_digest": "sha256:different", "status": "new"}
    )
    assert resolver.resolve(_identity(), known=(collision,)).status == "needs_user"

    split_target = _identity(failure_signature="unsafe-shell-quoting")
    second_split_target = _identity(failure_signature="unsafe-argument-quoting")
    split = resolver.resolve(
        split_target,
        known=(source,),
        mapping=FindingIdentityMapping(
            mapping_kind="split",
            source_keys=(source.finding_key,),
            target_identity_digests=tuple(
                sorted(
                    (
                        split_target.identity_digest,
                        second_split_target.identity_digest,
                    )
                )
            ),
            evidence_digest="sha256:rule-split",
            resolver_version="finding-identity.v1",
        ),
    )
    assert split.status == "new"
    assert split.finding_key != source.finding_key
    with pytest.raises(ValueError, match="cycle|replacement"):
        FindingIdentityMapping(
            mapping_kind="supersede",
            source_keys=(source.finding_key,),
            target_identity_digests=(),
            evidence_digest="sha256:bad",
            resolver_version="finding-identity.v1",
        )


def test_initial_batch_is_invisible_until_seal_and_recovers_after_crash(
    tmp_path: Path,
) -> None:
    calls = 0

    def fail_once(point: str) -> None:
        nonlocal calls
        if point == "after_initial_event" and calls == 0:
            calls += 1
            raise RuntimeError("simulated crash")

    service = _service(tmp_path, fault_hook=fail_once)
    with pytest.raises(RuntimeError, match="simulated crash"):
        _initialize(service)
    assert not service.read(SCOPE).initialized

    recovered = _service(tmp_path)
    key = _initialize(recovered)
    assert recovered.read(SCOPE).records[0].finding_key == key


def test_fixed_still_blocks_and_only_current_required_verification_unblocks(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    key = _initialize(service)
    _append(
        service,
        key,
        "acknowledged",
        actor_id="coordinator.1",
        slot_id="coordinator.1",
        capability_id="coordination",
    )
    _append(
        service,
        key,
        "remediation_started",
        actor_id="remediator.1",
        slot_id="remediator.1",
        capability_id="remediation",
    )
    _advance_candidate(service)
    _append(
        service,
        key,
        "fixed",
        actor_id="remediator.1",
        slot_id="remediator.1",
        capability_id="remediation",
        remediation_batch_id="batch.1",
    )
    ledger = service.read(SCOPE)
    assert not evaluate_closeability(ledger, _close_context(service)).closeable

    _append(
        service,
        key,
        "verified",
        actor_id="reviewer.security",
        slot_id="slot.security",
        capability_id="security",
    )
    ledger = service.read(SCOPE)
    assert evaluate_closeability(ledger, _close_context(service)).closeable

    _append(
        service,
        key,
        "regressed",
        actor_id="reviewer.security",
        slot_id="slot.security",
        capability_id="security",
        regression_of=key,
    )
    assert not evaluate_closeability(
        service.read(SCOPE), _close_context(service)
    ).closeable


def test_fixed_requires_a_new_candidate_before_persisting(tmp_path: Path) -> None:
    service = _service(tmp_path)
    key = _initialize(service)
    _append(
        service,
        key,
        "remediation_started",
        actor_id="remediator.1",
        slot_id="remediator.1",
        capability_id="remediation",
    )
    before = len(service._store.load_events(SCOPE))  # noqa: SLF001
    with pytest.raises(ValueError, match="candidate"):
        _append(
            service,
            key,
            "fixed",
            actor_id="remediator.1",
            slot_id="remediator.1",
            capability_id="remediation",
            remediation_batch_id="batch.same-candidate",
        )
    assert len(service._store.load_events(SCOPE)) == before  # noqa: SLF001


@pytest.mark.parametrize("slot_kind", ["optional", "advisory", "shadow"])
def test_non_required_reviewer_cannot_verify(tmp_path: Path, slot_kind: str) -> None:
    optional = _authority(
        "reviewer",
        actor_id="reviewer.optional",
        slot_id="slot.optional",
        slot_kind=slot_kind,
    )
    service = _service(tmp_path, _trust(authorities=(*_trust().authorities, optional)))
    key = _initialize(service)
    _append(
        service,
        key,
        "remediation_started",
        actor_id="remediator.1",
        slot_id="remediator.1",
        capability_id="remediation",
    )
    _advance_candidate(service)
    _append(
        service,
        key,
        "fixed",
        actor_id="remediator.1",
        slot_id="remediator.1",
        capability_id="remediation",
        remediation_batch_id=f"batch.{slot_kind}",
    )
    with pytest.raises(PermissionError, match="required|authority"):
        _append(
            service,
            key,
            "verified",
            actor_id="reviewer.optional",
            slot_id="slot.optional",
            capability_id="security",
        )


def test_optional_deterministic_gate_cannot_persist_verification(
    tmp_path: Path,
) -> None:
    optional_gate = _authority(
        "deterministic_gate",
        actor_id="gate.optional",
        slot_id="gate.optional",
        slot_kind="optional",
        capabilities=("security",),
    )
    service = _service(
        tmp_path,
        _trust(authorities=(*_trust().authorities, optional_gate)),
    )
    key = _initialize(service)
    _append(
        service,
        key,
        "remediation_started",
        actor_id="remediator.1",
        slot_id="remediator.1",
        capability_id="remediation",
    )
    _advance_candidate(service)
    _append(
        service,
        key,
        "fixed",
        actor_id="remediator.1",
        slot_id="remediator.1",
        capability_id="remediation",
        remediation_batch_id="batch.optional-gate",
    )
    before = len(service._store.load_events(SCOPE))  # noqa: SLF001
    with pytest.raises(PermissionError, match="required|authority"):
        _append(
            service,
            key,
            "verified",
            actor_id="gate.optional",
            slot_id="gate.optional",
            capability_id="security",
        )
    assert len(service._store.load_events(SCOPE)) == before  # noqa: SLF001


def test_forged_or_stale_lineage_cannot_change_closeability(tmp_path: Path) -> None:
    service = _service(tmp_path)
    key = _initialize(service)
    with pytest.raises(PermissionError, match="trusted|authority"):
        _append(
            service,
            key,
            "verified",
            actor_id="forged.reviewer",
            slot_id="slot.security",
            capability_id="security",
        )
    with pytest.raises(PermissionError, match="evidence"):
        _append(
            service,
            key,
            "acknowledged",
            actor_id="coordinator.1",
            slot_id="coordinator.1",
            capability_id="coordination",
            evidence_bundle_digest="sha256:forged",
        )
    with pytest.raises(SharedStateIntegrityError, match="lineage"):
        _append(
            service,
            key,
            "verified",
            actor_id="reviewer.security",
            slot_id="slot.security",
            capability_id="security",
            candidate_digest="sha256:old-candidate",
        )
    assert not evaluate_closeability(
        service.read(SCOPE), FindingCloseContext.from_trust(_trust())
    ).closeable


def test_waiver_requires_governance_and_cannot_override_non_waivable(
    tmp_path: Path,
) -> None:
    waiver = FindingWaiver(
        waiver_id="waiver.1",
        scope=SCOPE,
        finding_key="pending",
        candidate_digest="sha256:candidate.2",
        policy_digest="sha256:policy.1",
        approved_by_actor_id="human.owner",
        approved_by_slot_id="human.owner",
        authority_binding_digest="sha256:binding.security",
        reason="risk accepted",
        issued_at="2026-07-20T00:00:00Z",
        expires_at="2026-08-20T00:00:00Z",
        evidence_digest="sha256:evidence.waiver",
    )
    service = _service(tmp_path)
    key = _initialize(service)
    bound_waiver = FindingWaiver.model_validate(
        waiver.model_dump(exclude={"waiver_digest"}) | {"finding_key": key}
    )
    context = _trust(
        waivers=(bound_waiver,),
        non_waivable_categories=(),
    )
    service = _service(tmp_path, context)
    _append(
        service,
        key,
        "waived",
        actor_id="human.owner",
        slot_id="human.owner",
        capability_id="waiver",
        waiver_id="waiver.1",
        waiver_digest=bound_waiver.waiver_digest,
        evidence_bundle_digest="sha256:evidence.waiver",
    )
    assert evaluate_closeability(
        service.read(SCOPE), FindingCloseContext.from_trust(context)
    ).closeable

    blocked_service = _service(tmp_path / "blocked")
    blocked_key = _initialize(blocked_service)
    with pytest.raises(PermissionError, match="non-waivable"):
        _append(
            blocked_service,
            blocked_key,
            "waived",
            actor_id="human.owner",
            slot_id="human.owner",
            capability_id="waiver",
            waiver_id="waiver.1",
        )


@pytest.mark.parametrize(
    "origin,actor,slot,capability",
    [
        ("regression_of", "reviewer.security", "slot.security", "security"),
        ("new_critical_evidence", "reviewer.security", "slot.security", "security"),
        (
            "protocol_or_required_test_failure",
            "gate.required-tests",
            "gate.required-tests",
            "required-tests",
        ),
        ("late_confirmed_p0_p1", "reviewer.security", "slot.security", "security"),
    ],
)
def test_exactly_four_late_origins_can_block(
    tmp_path: Path,
    origin: str,
    actor: str,
    slot: str,
    capability: str,
) -> None:
    service = _service(tmp_path)
    existing_key = _initialize(service)
    late_identity = _identity(
        asset_identity=f"src/late.py:{origin}",
        semantic_location=f"function:{origin}",
        failure_signature=f"late-{origin}",
    )
    if origin == "regression_of":
        _append(
            service,
            existing_key,
            "remediation_started",
            actor_id="remediator.1",
            slot_id="remediator.1",
            capability_id="remediation",
        )
        _advance_candidate(service)
        _append(
            service,
            existing_key,
            "fixed",
            actor_id="remediator.1",
            slot_id="remediator.1",
            capability_id="remediation",
            remediation_batch_id="batch.before-regression",
        )
        _append(
            service,
            existing_key,
            "verified",
            actor_id="reviewer.security",
            slot_id="slot.security",
            capability_id="security",
        )
    source_event_digest = service._store.load_events(SCOPE)[-1].event_digest  # noqa: SLF001
    evidence_by_origin = {
        "regression_of": (
            f"regression|{existing_key}|{_identity().identity_digest}|"
            f"{source_event_digest}|occurrence.{origin}"
        ),
        "new_critical_evidence": "sha256:evidence.new-after-seal",
        "protocol_or_required_test_failure": "sha256:evidence.required-test",
        "late_confirmed_p0_p1": "sha256:evidence.late-confirmed",
    }
    result = _append(
        service,
        existing_key,
        "discovered",
        actor_id=actor,
        slot_id=slot,
        capability_id=capability,
        identity=late_identity,
        finding_key=None,
        severity="P1",
        category="security",
        late_origin=origin,
        regression_of=existing_key if origin == "regression_of" else None,
        evidence_bundle_digest=evidence_by_origin[origin],
    )
    late_record = next(
        item for item in result.ledger.records if item.finding_key != existing_key
    )
    assert late_record.blocking
    if origin == "late_confirmed_p0_p1":
        event = result.event
        assert event is not None
        assert event.late_critical_finding is not None
        assert event.reviewer_coverage_leak is not None
        assert event.attribution_input is not None


def test_late_coverage_leak_requires_evidence_visible_before_seal(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    key = _initialize(service)
    bundle = "sha256:evidence.late-confirmed-after-seal"
    identity = _identity(failure_signature="late-after-seal")
    actual_bundle = f"{bundle}|subject:{identity.identity_digest}"
    resolver = service._trust_resolver  # noqa: SLF001
    evidence = resolver.resolve_evidence(SCOPE, actual_bundle)
    assert evidence is not None
    after_seal = TrustedEvidenceDescriptor.model_validate(
        evidence.model_dump(exclude={"descriptor_digest"})
        | {
            "produced_at": "2026-07-20T12:00:00Z",
            "first_visible_at": "2026-07-20T12:00:00Z",
            "initial_visibility": "visible",
        }
    )
    resolver.evidence[actual_bundle] = after_seal  # type: ignore[attr-defined]
    with pytest.raises(ValueError, match="seal|visible"):
        _append(
            service,
            key,
            "discovered",
            actor_id="reviewer.security",
            slot_id="slot.security",
            capability_id="security",
            finding_key=None,
            identity=identity,
            severity="P1",
            category="security",
            late_origin="late_confirmed_p0_p1",
            evidence_bundle_digest=bundle,
        )


def test_new_evidence_cannot_hide_a_preexisting_coverage_leak(tmp_path: Path) -> None:
    service = _service(tmp_path)
    key = _initialize(service)
    with pytest.raises(ValueError, match="seal|coverage"):
        _append(
            service,
            key,
            "discovered",
            actor_id="reviewer.security",
            slot_id="slot.security",
            capability_id="security",
            finding_key=None,
            identity=_identity(
                asset_identity="src/preexisting.py:item",
                semantic_location="function:item",
            ),
            severity="P1",
            category="security",
            late_origin="new_critical_evidence",
            evidence_bundle_digest="sha256:evidence.preexisting-new",
        )


def test_non_critical_late_finding_is_advisory_and_not_a_blocker(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    key = _initialize(service)
    result = _append(
        service,
        key,
        "discovered",
        actor_id="reviewer.security",
        slot_id="slot.security",
        capability_id="security",
        finding_key=None,
        identity=_identity(
            asset_identity="docs/readme.md:wording",
            semantic_location="section:introduction",
            failure_signature="minor-wording",
            category="documentation",
        ),
        severity="P2",
        category="documentation",
        late_origin=None,
    )
    record = next(item for item in result.ledger.records if item.finding_key != key)
    assert record.disposition == "advisory"
    assert not record.blocking


def test_idempotency_stale_revision_and_projection_recovery(tmp_path: Path) -> None:
    service = _service(tmp_path)
    key = _initialize(service)
    command = FindingAppendCommand(
        scope=SCOPE,
        command_id="command.ack",
        idempotency_key="idem.ack",
        expected_revision=service.read(SCOPE).revision,
        session_fencing_epoch=3,
        finding_key=key,
        event_type="acknowledged",
        actor_id="coordinator.1",
        slot_id="coordinator.1",
        capability_id="coordination",
        candidate_digest="sha256:candidate.2",
        policy_digest="sha256:policy.1",
        plan_digest="sha256:plan.1",
        binding_set_digest="sha256:binding-set.1",
        evidence_bundle_digest="sha256:evidence.ack",
    )
    first = service.append(command)
    replay = service.append(command)
    assert replay.event == first.event
    with pytest.raises(SharedStateIntegrityError, match="idempotency|command"):
        service.append(
            command.model_copy(update={"evidence_bundle_digest": "sha256:fork"})
        )

    projection = service._store.projection_path(SCOPE)  # noqa: SLF001
    projection.unlink()
    recovered = service.read(SCOPE)
    assert recovered.revision == first.ledger.revision
    projection.write_text("{}", encoding="utf-8")
    assert service.read(SCOPE) == recovered


def test_cross_scope_evidence_never_writes_target_ledger(tmp_path: Path) -> None:
    service = _service(tmp_path)
    key = _initialize(service)
    target = FindingScope(
        project_id=SCOPE.project_id,
        work_item_id="WI-OTHER",
        stage_instance_id="execute.1",
        session_id="review-session.other",
    )
    result = _append(
        service,
        key,
        "cross_scope_critical_evidence",
        actor_id="reviewer.security",
        slot_id="slot.security",
        capability_id="security",
        target_scope=target,
    )
    assert result.event is not None and result.event.handoff_id
    target_root = service._store.session_root(target)  # noqa: SLF001
    assert not (target_root / "events").exists()
    assert service._store.handoff_path(result.event.handoff_id).exists()  # noqa: SLF001


def test_progress_comparator_is_quality_lexicographic_and_ignores_resources() -> None:
    previous = ProgressSnapshot(
        comparison_policy_digest="sha256:progress-policy.1",
        p0_open=1,
        required_test_failures=0,
        integrity_failures=0,
        reopened_or_regressed=0,
        p1_open=2,
        unreviewed_change=0,
        provider_calls=1,
        tokens=100,
        estimated_cost=0.0,
        active_execution_seconds=4.0,
    )
    more_expensive_but_better = previous.model_copy(
        update={"p0_open": 0, "provider_calls": 50, "tokens": 100_000}
    )
    assert compare_progress(previous, more_expensive_but_better).outcome == "improved"
    mixed_regression = previous.model_copy(update={"p0_open": 0, "p1_open": 3})
    assert compare_progress(previous, mixed_regression).outcome == "regressed"
    lower_priority_change = previous.model_copy(
        update={"p0_open": 0, "unreviewed_change": 1}
    )
    assert compare_progress(previous, lower_priority_change).outcome == "improved"
    resource_only = previous.model_copy(update={"tokens": 1_000_000})
    assert compare_progress(previous, resource_only).outcome == "same"
    different_policy = previous.model_copy(
        update={"comparison_policy_digest": "sha256:progress-policy.2"}
    )
    assert compare_progress(previous, different_policy).outcome == "uncomparable"


def test_verified_finding_requires_current_binding_and_unexpired_authority(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    key = _initialize(service)
    _append(
        service,
        key,
        "remediation_started",
        actor_id="remediator.1",
        slot_id="remediator.1",
        capability_id="remediation",
    )
    _advance_candidate(service)
    _append(
        service,
        key,
        "fixed",
        actor_id="remediator.1",
        slot_id="remediator.1",
        capability_id="remediation",
        remediation_batch_id="batch.current-binding",
    )
    _append(
        service,
        key,
        "verified",
        actor_id="reviewer.security",
        slot_id="slot.security",
        capability_id="security",
    )
    ledger = service.read(SCOPE)
    resolver = service._trust_resolver  # noqa: SLF001
    current = resolver.context  # type: ignore[attr-defined]

    rebound_authorities = tuple(
        item.model_copy(update={"binding_digest": "sha256:binding.rebound"})
        if item.actor_id == "reviewer.security"
        else item
        for item in current.authorities
    )
    rebound = FindingCloseContext.from_trust(
        current.model_copy(update={"authorities": rebound_authorities})
    )
    expired = FindingCloseContext.from_trust(
        current.model_copy(update={"evaluation_at": "2031-01-01T00:00:00Z"})
    )
    drifted = FindingCloseContext.from_trust(
        current.model_copy(update={"binding_set_digest": "sha256:binding-set.2"})
    )
    assert not evaluate_closeability(ledger, rebound).closeable
    assert not evaluate_closeability(ledger, expired).closeable
    assert not evaluate_closeability(ledger, drifted).closeable


def test_split_and_supersede_transfer_blocking_responsibility(tmp_path: Path) -> None:
    service = _service(tmp_path)
    source_key = _initialize(service)
    replacement_identity = _identity(
        failure_signature="unsafe-shell-quoting",
        semantic_location="function:run:quoting",
    )
    mapping = _trusted_mapping(
        service,
        mapping_kind="supersede",
        source_keys=(source_key,),
        target_identity_digests=(replacement_identity.identity_digest,),
    )
    replacement = _append(
        service,
        source_key,
        "discovered",
        actor_id="reviewer.security",
        slot_id="slot.security",
        capability_id="security",
        finding_key=None,
        identity=replacement_identity,
        identity_mapping=mapping,
        severity="P2",
        category="security",
    ).event
    assert replacement is not None and replacement.finding_key != source_key
    replacement_key = replacement.finding_key
    assert replacement_key is not None

    _append(
        service,
        source_key,
        "superseded",
        actor_id="human.owner",
        slot_id="human.owner",
        capability_id="identity-governance",
        replacement_keys=(replacement_key,),
    )
    ledger = service.read(SCOPE)
    assert not evaluate_closeability(
        ledger, FindingCloseContext.from_trust(_trust())
    ).closeable
    assert next(
        item for item in ledger.records if item.finding_key == replacement_key
    ).blocking
    assert ledger.identity_relations[0].source_keys == (source_key,)

    _append(
        service,
        replacement_key,
        "remediation_started",
        actor_id="remediator.1",
        slot_id="remediator.1",
        capability_id="remediation",
    )
    _advance_candidate(service)
    _append(
        service,
        replacement_key,
        "fixed",
        actor_id="remediator.1",
        slot_id="remediator.1",
        capability_id="remediation",
        remediation_batch_id="batch.replacement",
    )
    _append(
        service,
        replacement_key,
        "verified",
        actor_id="reviewer.security",
        slot_id="slot.security",
        capability_id="security",
    )
    assert evaluate_closeability(service.read(SCOPE), _close_context(service)).closeable


def test_committed_event_recovers_projection_without_reexecuting_command(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    key = _initialize(service)
    crashes = 0

    def fail_once(point: str) -> None:
        nonlocal crashes
        if point == "after_event_commit" and crashes == 0:
            crashes += 1
            raise RuntimeError("projection crash")

    crashing = _service(tmp_path, fault_hook=fail_once)
    revision = crashing.read(SCOPE).revision
    command = FindingAppendCommand(
        scope=SCOPE,
        command_id="command.recover-event",
        idempotency_key="idem.recover-event",
        expected_revision=revision,
        session_fencing_epoch=3,
        finding_key=key,
        event_type="acknowledged",
        actor_id="coordinator.1",
        slot_id="coordinator.1",
        capability_id="coordination",
        candidate_digest="sha256:candidate.2",
        policy_digest="sha256:policy.1",
        plan_digest="sha256:plan.1",
        binding_set_digest="sha256:binding-set.1",
        evidence_bundle_digest="sha256:evidence.recover-event",
    )
    with pytest.raises(RuntimeError, match="projection crash"):
        crashing.append(command)
    replay = _service(tmp_path).append(command)
    assert replay.idempotent_replay
    assert replay.ledger.revision == revision + 1


def test_concurrent_appends_are_serialized_without_event_loss(tmp_path: Path) -> None:
    service = _service(tmp_path)
    key = _initialize(service)
    target = FindingScope(
        project_id=SCOPE.project_id,
        work_item_id="WI-TARGET",
        stage_instance_id="execute.1",
        session_id="review-session.target",
    )

    def append(index: int) -> None:
        worker = _service(tmp_path)
        for _ in range(100):
            revision = worker.read(SCOPE).revision
            command = FindingAppendCommand(
                scope=SCOPE,
                command_id=f"command.concurrent.{index}",
                idempotency_key=f"idem.concurrent.{index}",
                expected_revision=revision,
                session_fencing_epoch=3,
                finding_key=key,
                event_type="cross_scope_critical_evidence",
                actor_id="reviewer.security",
                slot_id="slot.security",
                capability_id="security",
                candidate_digest="sha256:candidate.2",
                policy_digest="sha256:policy.1",
                plan_digest="sha256:plan.1",
                binding_set_digest="sha256:binding-set.1",
                evidence_bundle_digest=f"sha256:evidence.concurrent.{index}",
                target_scope=target,
            )
            try:
                worker.append(command)
                return
            except SharedStateIntegrityError as exc:
                if "stale expected revision" not in str(exc):
                    raise
        raise AssertionError("concurrent CAS retry budget exhausted")

    with ThreadPoolExecutor(max_workers=8) as pool:
        tuple(pool.map(append, range(16)))
    ledger = service.read(SCOPE)
    assert ledger.revision == 18
    assert len(ledger.pending_handoff_ids) == 16
    sequences = tuple(
        item.sequence
        for item in service._store.load_events(SCOPE)  # noqa: SLF001
    )
    assert sequences == tuple(range(1, 19))


def test_regular_finding_mutation_waits_for_product_writer_read_lease(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    key = _initialize(service)
    completed = Event()
    errors: list[BaseException] = []

    def mutate() -> None:
        try:
            _append(
                service,
                key,
                "acknowledged",
                actor_id="coordinator.1",
                slot_id="coordinator.1",
                capability_id="coordination",
            )
            completed.set()
        except BaseException as exc:
            errors.append(exc)

    with activation_safety_read_lease(tmp_path, SCOPE.project_id):
        thread = Thread(target=mutate)
        thread.start()
        assert completed.wait(0.2) is False

    thread.join(timeout=5)
    assert thread.is_alive() is False
    assert errors == []
    assert completed.is_set()


def test_stale_fencing_and_event_chain_tampering_fail_closed(tmp_path: Path) -> None:
    service = _service(tmp_path)
    key = _initialize(service)
    with pytest.raises(SharedStateIntegrityError, match="lineage"):
        _append(
            service,
            key,
            "acknowledged",
            actor_id="coordinator.1",
            slot_id="coordinator.1",
            capability_id="coordination",
            session_fencing_epoch=2,
        )

    event_paths = sorted((service._store.session_root(SCOPE) / "events").glob("*.json"))  # noqa: SLF001
    payload = json.loads(event_paths[-1].read_text(encoding="utf-8"))
    payload["previous_event_digest"] = "sha256:tampered"
    event_paths[-1].write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(SharedStateIntegrityError, match="digest|chain"):
        service.read(SCOPE)


def test_terminal_state_cannot_be_reopened_by_coordinator(tmp_path: Path) -> None:
    service = _service(tmp_path)
    key = _initialize(service)
    _append(
        service,
        key,
        "remediation_started",
        actor_id="remediator.1",
        slot_id="remediator.1",
        capability_id="remediation",
    )
    _advance_candidate(service)
    _append(
        service,
        key,
        "fixed",
        actor_id="remediator.1",
        slot_id="remediator.1",
        capability_id="remediation",
        remediation_batch_id="batch.terminal",
    )
    _append(
        service,
        key,
        "verified",
        actor_id="reviewer.security",
        slot_id="slot.security",
        capability_id="security",
    )
    with pytest.raises(ValueError, match="transition"):
        _append(
            service,
            key,
            "acknowledged",
            actor_id="coordinator.1",
            slot_id="coordinator.1",
            capability_id="coordination",
        )


def test_duplicate_discovery_is_rejected_without_downgrading_blocker(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    key = _initialize(service)
    before = service.read(SCOPE)
    with pytest.raises(ValueError, match="rediscovered"):
        _append(
            service,
            key,
            "discovered",
            actor_id="reviewer.security",
            slot_id="slot.security",
            capability_id="security",
            finding_key=None,
            identity=_identity(claim="同一问题的新描述", line=999),
            severity="P3",
            category="security",
        )
    after = service.read(SCOPE)
    assert after.revision == before.revision
    assert after.records[0].finding_key == key
    assert after.records[0].blocking


def test_replay_rejects_hash_consistent_but_unauthorized_verification(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    key = _initialize(service)
    _append(
        service,
        key,
        "remediation_started",
        actor_id="remediator.1",
        slot_id="remediator.1",
        capability_id="remediation",
    )
    _advance_candidate(service)
    _append(
        service,
        key,
        "fixed",
        actor_id="remediator.1",
        slot_id="remediator.1",
        capability_id="remediation",
        remediation_batch_id="batch.before-forgery",
    )
    _append(
        service,
        key,
        "verified",
        actor_id="reviewer.security",
        slot_id="slot.security",
        capability_id="security",
    )
    path = sorted((service._store.session_root(SCOPE) / "events").glob("*.json"))[-1]  # noqa: SLF001
    payload = json.loads(path.read_text(encoding="utf-8"))
    human = next(
        item for item in _trust().authorities if item.actor_id == "human.owner"
    )
    payload.update(
        actor_id=human.actor_id,
        slot_id=human.slot_id,
        capability_id="waiver",
        authority_kind=human.authority_kind,
        authority_slot_kind=human.slot_kind,
        authority_capability_ids=list(human.capability_ids),
        authority_blocking_authorities=list(human.blocking_authorities),
        authority_eligible_for_enforce_quorum=human.eligible_for_enforce_quorum,
        authority_valid_until=human.valid_until,
        role_contract_digest=human.role_contract_digest,
        binding_digest=human.binding_digest,
    )
    payload["event_digest"] = persisted_event_digest(payload)
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(
        SharedStateIntegrityError, match="trust proof|replay authorization"
    ):
        service.read(SCOPE)


def test_initial_seal_requires_complete_proofs_and_exact_finding_batch(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="pass and coverage proof"):
        _initial_seal(required_pass_digests=())
    service = _service(tmp_path)
    trust = service._trust_resolver.resolve(SCOPE)  # noqa: SLF001
    command = FindingInitialBatchCommand(
        scope=SCOPE,
        command_id="command.initial.empty",
        idempotency_key="initial.empty",
        expected_revision=0,
        session_fencing_epoch=3,
        candidate_digest=trust.candidate_digest,
        policy_digest=trust.policy_digest,
        plan_digest=trust.plan_digest,
        binding_set_digest=trust.binding_set_digest,
        initial_review_seal_digest=trust.initial_review_seal_digest,
        findings=(),
    )
    with pytest.raises(SharedStateIntegrityError, match="batch seal mismatch"):
        service.append(command)
    foreign_seal = _initial_seal(
        initial_candidate_digest="sha256:candidate.foreign",
        plan_digest="sha256:plan.foreign",
        binding_set_digest="sha256:binding-set.foreign",
    )
    foreign = _service(tmp_path / "foreign", _trust(initial_review_seal=foreign_seal))
    with pytest.raises(SharedStateIntegrityError, match="snapshot differs from seal"):
        _initialize(foreign)


@pytest.mark.parametrize(
    ("event_index", "tampered_field", "tampered_value"),
    (
        (0, "category", "correctness"),
        (-1, "idempotency_key", "initial.forged"),
    ),
)
def test_initial_events_and_seal_are_fully_rederived(
    tmp_path: Path,
    event_index: int,
    tampered_field: str,
    tampered_value: str,
) -> None:
    service = _service(tmp_path)
    _initialize(service)
    paths = sorted((service._store.session_root(SCOPE) / "events").glob("*.json"))  # noqa: SLF001
    selected = event_index % len(paths)
    previous_digest = ""
    for index, path in enumerate(paths):
        payload = json.loads(path.read_text(encoding="utf-8"))
        if index == selected:
            payload[tampered_field] = tampered_value
        if index:
            payload["previous_event_digest"] = previous_digest
        payload["event_digest"] = persisted_event_digest(payload)
        previous_digest = payload["event_digest"]
        path.write_text(json.dumps(payload), encoding="utf-8")
    service._store.projection_path(SCOPE).unlink()  # noqa: SLF001

    with pytest.raises(SharedStateIntegrityError, match="initial"):
        service.read(SCOPE)


@pytest.mark.parametrize("severity", ["P2", "P3"])
def test_required_test_failure_remains_blocking_at_lower_severity(
    tmp_path: Path,
    severity: str,
) -> None:
    service = _service(tmp_path)
    key = _initialize(service)
    result = _append(
        service,
        key,
        "discovered",
        actor_id="gate.required-tests",
        slot_id="gate.required-tests",
        capability_id="required-tests",
        finding_key=None,
        identity=_identity(
            asset_identity=f"tests/required.py:{severity}",
            semantic_location=f"test:{severity}",
            failure_signature=f"required-test-{severity}",
        ),
        severity=severity,
        category="security",
        late_origin="protocol_or_required_test_failure",
        evidence_bundle_digest="sha256:evidence.required-test",
    )
    assert result.event is not None and result.event.blocking


def test_regression_requires_resolved_and_matching_source_proof(tmp_path: Path) -> None:
    service = _service(tmp_path)
    key = _initialize(service)
    with pytest.raises(ValueError, match="resolved finding"):
        _append(
            service,
            key,
            "discovered",
            actor_id="reviewer.security",
            slot_id="slot.security",
            capability_id="security",
            finding_key=None,
            identity=_identity(failure_signature="premature-regression"),
            severity="P1",
            category="security",
            late_origin="regression_of",
            regression_of=key,
            evidence_bundle_digest=f"regression|{key}|{_identity().identity_digest}",
        )
    _append(
        service,
        key,
        "remediation_started",
        actor_id="remediator.1",
        slot_id="remediator.1",
        capability_id="remediation",
    )
    _advance_candidate(service)
    _append(
        service,
        key,
        "fixed",
        actor_id="remediator.1",
        slot_id="remediator.1",
        capability_id="remediation",
        remediation_batch_id="batch.regression-source",
    )
    _append(
        service,
        key,
        "verified",
        actor_id="reviewer.security",
        slot_id="slot.security",
        capability_id="security",
    )
    with pytest.raises(ValueError, match="lineage"):
        _append(
            service,
            key,
            "discovered",
            actor_id="reviewer.security",
            slot_id="slot.security",
            capability_id="security",
            finding_key=None,
            identity=_identity(failure_signature="wrong-regression-proof"),
            severity="P1",
            category="security",
            late_origin="regression_of",
            regression_of=key,
            evidence_bundle_digest=f"regression|{key}|sha256:unrelated-identity",
        )


def test_regression_occurrence_cannot_create_multiple_findings(tmp_path: Path) -> None:
    service = _service(tmp_path)
    key = _initialize(service)
    _append(
        service,
        key,
        "remediation_started",
        actor_id="remediator.1",
        slot_id="remediator.1",
        capability_id="remediation",
    )
    _advance_candidate(service)
    _append(
        service,
        key,
        "fixed",
        actor_id="remediator.1",
        slot_id="remediator.1",
        capability_id="remediation",
        remediation_batch_id="batch.occurrence",
    )
    terminal = _append(
        service,
        key,
        "verified",
        actor_id="reviewer.security",
        slot_id="slot.security",
        capability_id="security",
    ).event
    assert terminal is not None
    bundle = (
        f"regression|{key}|{_identity().identity_digest}|"
        f"{terminal.event_digest}|occurrence.same"
    )
    first = _identity(failure_signature="regression-occurrence-a")
    _append(
        service,
        key,
        "discovered",
        actor_id="reviewer.security",
        slot_id="slot.security",
        capability_id="security",
        finding_key=None,
        identity=first,
        severity="P1",
        category="security",
        late_origin="regression_of",
        regression_of=key,
        evidence_bundle_digest=bundle,
    )
    with pytest.raises(ValueError, match="lineage"):
        _append(
            service,
            key,
            "discovered",
            actor_id="reviewer.security",
            slot_id="slot.security",
            capability_id="security",
            finding_key=None,
            identity=_identity(failure_signature="regression-occurrence-b"),
            severity="P1",
            category="security",
            late_origin="regression_of",
            regression_of=key,
            evidence_bundle_digest=bundle,
        )


def test_waiver_is_content_addressed_candidate_bound_and_immutable(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    key = _initialize(service)
    waiver = FindingWaiver(
        waiver_id="waiver.immutable",
        scope=SCOPE,
        finding_key=key,
        candidate_digest="sha256:candidate.2",
        policy_digest="sha256:policy.1",
        approved_by_actor_id="human.owner",
        approved_by_slot_id="human.owner",
        authority_binding_digest="sha256:binding.security",
        reason="accepted",
        issued_at="2026-07-20T00:00:00Z",
        expires_at="2026-08-20T00:00:00Z",
        evidence_digest="sha256:evidence.waiver",
    )
    service._store.persist_waiver(waiver)  # noqa: SLF001
    altered = FindingWaiver.model_validate(
        waiver.model_dump(exclude={"waiver_digest"}) | {"reason": "changed"}
    )
    with pytest.raises(SharedStateIntegrityError, match="immutable fork"):
        service._store.persist_waiver(altered)  # noqa: SLF001
    wrong_candidate = FindingWaiver.model_validate(
        waiver.model_dump(exclude={"waiver_digest"})
        | {"candidate_digest": "sha256:candidate.other"}
    )
    context = _trust(waivers=(wrong_candidate,), non_waivable_categories=())
    with pytest.raises(PermissionError, match="not trusted"):
        _append(
            _service(tmp_path, context),
            key,
            "waived",
            actor_id="human.owner",
            slot_id="human.owner",
            capability_id="waiver",
            waiver_id=wrong_candidate.waiver_id,
            waiver_digest=wrong_candidate.waiver_digest,
            evidence_bundle_digest="sha256:evidence.waiver",
        )


@pytest.mark.parametrize("resolution", ["accepted", "rejected"])
def test_cross_scope_handoff_receipt_resolves_pending_state(
    tmp_path: Path,
    resolution: str,
) -> None:
    service = _service(tmp_path)
    key = _initialize(service)
    target = FindingScope(
        project_id=SCOPE.project_id,
        work_item_id="WI-HANDOFF",
        stage_instance_id="execute.1",
        session_id="review-session.handoff",
    )
    source = _append(
        service,
        key,
        "cross_scope_critical_evidence",
        actor_id="reviewer.security",
        slot_id="slot.security",
        capability_id="security",
        target_scope=target,
    ).event
    assert source is not None and source.handoff_id is not None
    bundle = f"handoff-receipt|{source.event_digest}|{source.handoff_id}|{resolution}"
    evidence = service._trust_resolver.resolve_evidence(target, bundle)  # noqa: SLF001
    assert evidence is not None
    if resolution == "accepted":
        optional = TrustedEvidenceDescriptor.model_validate(
            evidence.model_dump(exclude={"descriptor_digest"})
            | {
                "signer_authority_kind": "reviewer",
                "signer_slot_kind": "optional",
                "signer_eligible_for_enforce_quorum": False,
            }
        )
        service._trust_resolver.evidence[bundle] = optional  # type: ignore[attr-defined]  # noqa: SLF001
        with pytest.raises(ValueError, match="receipt lineage"):
            _append(
                service,
                key,
                "cross_scope_handoff_resolved",
                actor_id="human.owner",
                slot_id="human.owner",
                capability_id="waiver",
                evidence_bundle_digest=bundle,
                handoff_id=source.handoff_id,
                handoff_resolution=resolution,
                target_receipt_digest=optional.descriptor_digest,
                target_scope=target,
            )
        service._trust_resolver.evidence[bundle] = evidence  # type: ignore[attr-defined]  # noqa: SLF001
    result = _append(
        service,
        key,
        "cross_scope_handoff_resolved",
        actor_id="human.owner",
        slot_id="human.owner",
        capability_id="waiver",
        evidence_bundle_digest=bundle,
        handoff_id=source.handoff_id,
        handoff_resolution=resolution,
        target_receipt_digest=evidence.descriptor_digest,
        target_scope=target,
    )
    receipt_path = service._store.handoff_receipt_path(source.handoff_id)  # noqa: SLF001
    if resolution == "accepted":
        assert not result.ledger.pending_handoff_ids
        assert receipt_path.exists()
        with pytest.raises(ValueError, match="already resolved"):
            _append(
                service,
                key,
                "cross_scope_handoff_resolved",
                actor_id="human.owner",
                slot_id="human.owner",
                capability_id="waiver",
                evidence_bundle_digest=bundle,
                handoff_id=source.handoff_id,
                handoff_resolution=resolution,
                target_receipt_digest=evidence.descriptor_digest,
                target_scope=target,
            )
    else:
        assert result.ledger.pending_handoff_ids == (source.handoff_id,)
        assert not receipt_path.exists()


def test_handoff_receipt_binds_handoff_and_target_resolution(tmp_path: Path) -> None:
    service = _service(tmp_path)
    key = _initialize(service)
    target = SCOPE.model_copy(update={"work_item_id": "WI-HANDOFF-BOUND"})
    source = _append(
        service,
        key,
        "cross_scope_critical_evidence",
        actor_id="reviewer.security",
        slot_id="slot.security",
        capability_id="security",
        target_scope=target,
    ).event
    assert source is not None and source.handoff_id is not None
    bundle = f"handoff-receipt|{source.event_digest}|{source.handoff_id}|accepted"
    evidence = service._trust_resolver.resolve_evidence(target, bundle)  # noqa: SLF001
    assert evidence is not None
    accepted = TrustedEvidenceDescriptor.model_validate(
        evidence.model_dump(exclude={"descriptor_digest"})
        | {
            "related_handoff_id": source.handoff_id,
            "handoff_resolution": "accepted",
        }
    )
    service._trust_resolver.evidence[bundle] = accepted  # type: ignore[attr-defined]  # noqa: SLF001
    with pytest.raises(ValueError, match="receipt lineage"):
        _append(
            service,
            key,
            "cross_scope_handoff_resolved",
            actor_id="human.owner",
            slot_id="human.owner",
            capability_id="waiver",
            evidence_bundle_digest=bundle,
            handoff_id=source.handoff_id,
            handoff_resolution="rejected",
            target_receipt_digest=accepted.descriptor_digest,
            target_scope=target,
        )


def test_handoff_receipt_cannot_resolve_a_different_finding(tmp_path: Path) -> None:
    service = _service(tmp_path)
    key = _initialize(service)
    target = SCOPE.model_copy(update={"work_item_id": "WI-HANDOFF-IDENTITY"})
    source = _append(
        service,
        key,
        "cross_scope_critical_evidence",
        actor_id="reviewer.security",
        slot_id="slot.security",
        capability_id="security",
        target_scope=target,
    ).event
    assert source is not None and source.handoff_id is not None
    bundle = f"handoff-receipt|{source.event_digest}|{source.handoff_id}|accepted"
    evidence = service._trust_resolver.resolve_evidence(target, bundle)  # noqa: SLF001
    assert evidence is not None
    with pytest.raises(ValueError, match="receipt lineage"):
        _append(
            service,
            "finding.fake",
            "cross_scope_handoff_resolved",
            actor_id="human.owner",
            slot_id="human.owner",
            capability_id="waiver",
            evidence_bundle_digest=bundle,
            handoff_id=source.handoff_id,
            handoff_resolution="accepted",
            target_receipt_digest=evidence.descriptor_digest,
            target_scope=target,
        )
    assert service.read(SCOPE).pending_handoff_ids == (source.handoff_id,)


def test_late_attribution_preserves_candidate_lineage_without_early_cause(
    tmp_path: Path,
) -> None:
    seal = _initial_seal(initial_candidate_digest="sha256:candidate.original")
    context = _trust(
        candidate_digest="sha256:candidate.original",
        initial_review_seal=seal,
    )
    service = _service(tmp_path, context)
    key = _initialize(service)
    service._trust_resolver.context = _trust(  # type: ignore[attr-defined]  # noqa: SLF001
        cohort_id="cohort.discovery",
        initial_review_seal=seal,
    )
    _advance_candidate(
        service,
        service._trust_resolver.context.candidate_digest,  # type: ignore[attr-defined]  # noqa: SLF001
    )
    event = _append(
        service,
        key,
        "discovered",
        actor_id="reviewer.security",
        slot_id="slot.security",
        capability_id="security",
        finding_key=None,
        identity=_identity(failure_signature="late-confirmed-lineage"),
        severity="P1",
        category="security",
        late_origin="late_confirmed_p0_p1",
        evidence_bundle_digest="sha256:evidence.late-confirmed",
    ).event
    assert event is not None and event.attribution_input is not None
    payload = event.attribution_input.model_dump(mode="json")
    assert payload["original_candidate_digest"] == "sha256:candidate.original"
    assert payload["discovery_candidate_digest"] == "sha256:candidate.2"
    assert "primary_cause" not in payload


def test_identity_mapping_records_full_targets_and_macro_rebaseline_closes(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    source_key = _initialize(service)
    targets = (
        _identity(failure_signature="split-a"),
        _identity(failure_signature="split-b"),
    )
    mapping = _trusted_mapping(
        service,
        mapping_kind="split",
        source_keys=(source_key,),
        target_identity_digests=tuple(sorted(item.identity_digest for item in targets)),
    )
    result = _append(
        service,
        source_key,
        "discovered",
        actor_id="reviewer.security",
        slot_id="slot.security",
        capability_id="security",
        finding_key=None,
        identity=targets[0],
        identity_mapping=mapping,
        severity="P1",
        category="security",
    )
    relation = result.ledger.identity_relations[0]
    assert relation.target_identity_digests == mapping.target_identity_digests
    assert len(relation.target_keys) == 2
    first_key = result.event.finding_key if result.event is not None else None
    assert first_key is not None
    assert result.ledger.pending_identity_target_keys == tuple(
        key for key in relation.target_keys if key != first_key
    )
    assert (
        "finding.identity-target-pending"
        in evaluate_closeability(
            result.ledger,
            FindingCloseContext.from_trust(_trust()),
        ).reason_ids
    )
    with pytest.raises(ValueError, match="fulfill identity mapping"):
        _append(
            service,
            source_key,
            "superseded",
            actor_id="human.owner",
            slot_id="human.owner",
            capability_id="identity-governance",
            replacement_keys=(first_key,),
        )
    second_result = _append(
        service,
        source_key,
        "discovered",
        actor_id="reviewer.security",
        slot_id="slot.security",
        capability_id="security",
        finding_key=None,
        identity=targets[1],
        identity_mapping=mapping,
        severity="P1",
        category="security",
    )
    second = second_result.event
    assert second is not None and second.finding_key is not None
    assert not second_result.ledger.pending_identity_target_keys
    _append(
        service,
        source_key,
        "superseded",
        actor_id="human.owner",
        slot_id="human.owner",
        capability_id="identity-governance",
        replacement_keys=tuple(sorted((first_key, second.finding_key))),
    )

    macro_service = _service(tmp_path / "macro")
    macro_key = _initialize(macro_service)
    _append(
        macro_service,
        macro_key,
        "superseded",
        actor_id="human.owner",
        slot_id="human.owner",
        capability_id="identity-governance",
        evidence_bundle_digest="sha256:evidence.macro-rebaseline",
        macro_rebaseline_evidence_digest="sha256:evidence.macro-rebaseline",
    )
    assert evaluate_closeability(
        macro_service.read(SCOPE), FindingCloseContext.from_trust(_trust())
    ).closeable


def test_alias_mapping_preserves_terminal_state_and_identity_contract(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    key = _initialize(service)
    _append(
        service,
        key,
        "remediation_started",
        actor_id="remediator.1",
        slot_id="remediator.1",
        capability_id="remediation",
    )
    _advance_candidate(service)
    _append(
        service,
        key,
        "fixed",
        actor_id="remediator.1",
        slot_id="remediator.1",
        capability_id="remediation",
        remediation_batch_id="batch.alias",
    )
    _append(
        service,
        key,
        "verified",
        actor_id="reviewer.security",
        slot_id="slot.security",
        capability_id="security",
    )
    moved = _identity(
        asset_identity="src/moved.py:run",
        asset_lineage_ref="git:rename-src-app-to-moved",
        semantic_location="function:run_moved",
        supersedes_finding_key=key,
        identity_decision_evidence="sha256:symbol-index-proof",
    )
    mapping = _trusted_mapping(
        service,
        mapping_kind="alias",
        source_keys=(key,),
        target_identity_digests=(moved.identity_digest,),
    )
    result = _append(
        service,
        key,
        "discovered",
        actor_id="reviewer.security",
        slot_id="slot.security",
        capability_id="security",
        finding_key=None,
        identity=moved,
        identity_mapping=mapping,
        severity="P3",
        category="security",
    )
    record = result.ledger.records[0]
    assert record.state == "verified"
    assert record.finding_key == key
    assert record.identity_digest == moved.identity_digest
    assert evaluate_closeability(result.ledger, _close_context(service)).closeable
    resolver = service._trust_resolver  # noqa: SLF001
    resolver.context = _trust(  # type: ignore[attr-defined]
        candidate_digest="sha256:candidate.3",
        cohort_id="cohort.next",
    )
    _advance_candidate(service, "sha256:candidate.3")
    moved_again = _identity(
        asset_identity="src/moved_again.py:run",
        semantic_location="function:run_moved_again",
        asset_lineage_ref="git:second-rename",
        identity_decision_evidence="sha256:second-symbol-proof",
    )
    next_mapping = _trusted_mapping(
        service,
        mapping_kind="alias",
        source_keys=(key,),
        target_identity_digests=(moved_again.identity_digest,),
    )
    evolved = _append(
        service,
        key,
        "discovered",
        actor_id="reviewer.security",
        slot_id="slot.security",
        capability_id="security",
        finding_key=None,
        identity=moved_again,
        identity_mapping=next_mapping,
        severity="P1",
        category="security",
    )
    assert evolved.ledger.records[0].state == "open"
    assert not evaluate_closeability(
        evolved.ledger,
        FindingCloseContext.from_trust(resolver.context),  # type: ignore[attr-defined]
    ).closeable
    assert moved.finding_key_version == "finding-key.v1"
    assert moved.semantic_location_version == "semantic-location.v1"
    assert _identity(finding_key_version="finding-key.v2").identity_digest != (
        _identity().identity_digest
    )
    assert (
        FindingIdentityResolver()
        .resolve(_identity(finding_key_version="finding-key.v999"))
        .status
        == "needs_user"
    )
    with pytest.raises(ValueError, match="merge mapping"):
        FindingIdentityMapping(
            mapping_kind="merge",
            source_keys=(key,),
            target_identity_digests=(moved.identity_digest,),
            evidence_digest="sha256:invalid-merge",
            resolver_version="finding-identity.v1",
        )


def test_event_reader_validates_raw_v1_digest_before_default_injection(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    _initialize(service)
    path = sorted((service._store.session_root(SCOPE) / "events").glob("*.json"))[0]  # noqa: SLF001
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload.pop("waiver_digest")
    payload["event_digest"] = persisted_event_digest(payload)
    path.write_text(json.dumps(payload), encoding="utf-8")
    restored = service._store._read_event(SCOPE, 1, path)  # noqa: SLF001
    assert restored.waiver_digest is None
    assert restored.event_digest == payload["event_digest"]


def test_finding_fact_payloads_are_deeply_immutable_and_rechecked_on_write(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    _initialize(service)
    event = service._store.load_events(SCOPE)[0]  # noqa: SLF001
    with pytest.raises(TypeError):
        event.extensions["future"] = "mutated"
    with pytest.raises(TypeError):
        event.command_payload["candidate_digest"] = "sha256:mutated"
    tampered = event.model_copy(
        update={
            "command_payload": event.command_payload
            | {"candidate_digest": "sha256:mutated"}
        }
    )
    with pytest.raises(SharedStateIntegrityError, match="digest|immutable"):
        service._store.append_event(tampered)  # noqa: SLF001


def test_finding_fact_payloads_reject_non_json_nested_values(tmp_path: Path) -> None:
    service = _service(tmp_path)
    _initialize(service)
    event = service._store.load_events(SCOPE)[0]  # noqa: SLF001
    extension_payload = event.model_dump(mode="json")
    extension_payload["extensions"] = {"nested": {"values": {1, 2}}}
    with pytest.raises(ValueError, match="JSON"):
        FindingEvent.model_validate(extension_payload)
    command_payload = event.model_dump(mode="json")
    command_payload["command_payload"]["mutable"] = bytearray(b"unsafe")
    with pytest.raises(ValueError, match="JSON"):
        FindingEvent.model_validate(command_payload)
    valid_payload = event.model_dump(mode="json")
    valid_payload["extensions"] = {"nested": {"values": [1, 2]}}
    frozen = FindingEvent.model_validate(valid_payload)
    nested = frozen.extensions["nested"]
    assert isinstance(nested, dict)
    values = nested["values"]
    assert isinstance(values, list)
    with pytest.raises(TypeError, match="frozen JSON array"):
        values.append(3)
    frozen.model_dump(mode="json", warnings="error")
    bypassed = event.model_copy(update={"extensions": {"nested": {"values": {1, 2}}}})
    bypassed_raw = bypassed.model_dump(mode="json", warnings=False)
    bypassed = bypassed.model_copy(
        update={"event_digest": persisted_event_digest(bypassed_raw)}
    )
    with pytest.raises(SharedStateIntegrityError, match="model contract"):
        service._store.append_event(bypassed)  # noqa: SLF001


def test_json_value_static_contract_rejects_plain_tuple() -> None:
    from mypy import api as mypy_api

    code = """\
from ai_sdlc.core.stage_review.artifact_compat import ArtifactCompatibility
ArtifactCompatibility(extensions={"x": (1, 2)})
"""
    stdout, stderr, status = mypy_api.run(["--no-incremental", "-c", code])
    assert status == 1, stderr
    assert 'incompatible type "str": "tuple[int, int]"' in stdout


def test_write_validation_rechecks_full_model_after_model_copy(tmp_path: Path) -> None:
    service = _service(tmp_path)
    _initialize(service)
    event = service._store.load_events(SCOPE)[0]  # noqa: SLF001
    bypassed = event.model_copy(
        update={
            "sequence": 99,
            "event_id": "finding-event." + "f" * 24,
            "event_type": "forged-event-type",
            "event_digest": "",
        }
    )
    raw = bypassed.model_dump(mode="json", warnings=False)
    bypassed = bypassed.model_copy(update={"event_digest": persisted_event_digest(raw)})
    with pytest.raises(SharedStateIntegrityError, match="model contract"):
        service._store.append_event(bypassed)  # noqa: SLF001


@pytest.mark.parametrize(
    "updates",
    (
        {"event_id": "finding-event.not-canonical"},
        {"sequence": 1_000_000_000_000},
    ),
)
def test_write_validation_matches_event_filename_contract(
    tmp_path: Path,
    updates: dict[str, object],
) -> None:
    service = _service(tmp_path)
    _initialize(service)
    event = service._store.load_events(SCOPE)[0]  # noqa: SLF001
    bypassed = event.model_copy(update=updates | {"event_digest": ""})
    raw = bypassed.model_dump(mode="json", warnings=False)
    bypassed = bypassed.model_copy(update={"event_digest": persisted_event_digest(raw)})
    with pytest.raises(SharedStateIntegrityError, match="model contract"):
        service._store.append_event(bypassed)  # noqa: SLF001


def test_write_validation_rechecks_nested_model_instances(tmp_path: Path) -> None:
    service = _service(tmp_path)
    _initialize(service)
    event = service._store.load_events(SCOPE)[0]  # noqa: SLF001
    assert event.identity is not None
    invalid_identity = event.identity.model_copy(update={"line": 0})
    bypassed = event.model_copy(
        update={
            "sequence": 99,
            "event_id": "finding-event." + "e" * 24,
            "identity": invalid_identity,
            "event_digest": "",
        }
    )
    raw = bypassed.model_dump(mode="json", warnings=False)
    bypassed = bypassed.model_copy(update={"event_digest": persisted_event_digest(raw)})
    with pytest.raises(SharedStateIntegrityError, match="model contract"):
        service._store.append_event(bypassed)  # noqa: SLF001


def test_old_v1_waiver_envelope_is_read_only_compatible(tmp_path: Path) -> None:
    waiver = FindingWaiver(
        waiver_id="waiver.compat",
        scope=SCOPE,
        finding_key="finding.compat",
        candidate_digest="sha256:candidate.2",
        policy_digest="sha256:policy.1",
        approved_by_actor_id="human.owner",
        approved_by_slot_id="human.owner",
        authority_binding_digest="sha256:binding.security",
        reason="legacy",
        issued_at="2026-07-20T00:00:00Z",
        expires_at="2026-08-20T00:00:00Z",
        evidence_digest="sha256:evidence.legacy-waiver",
    )
    raw = waiver.model_dump(mode="json")
    for field in ("canonicalization_version", "compatibility_mode", "extensions"):
        raw.pop(field)
    raw["waiver_digest"] = canonical_digest(
        raw,
        CanonicalizationPolicy(excluded_fields=frozenset({"waiver_digest"})),
    )
    decoded = decode_finding_waiver(raw)
    assert decoded.compatibility_mode == "read-only-legacy"
    assert decoded.waiver_digest == raw["waiver_digest"]
    service = _service(tmp_path)
    with pytest.raises(SharedStateIntegrityError, match="read-only"):
        service._store.persist_waiver(decoded)  # noqa: SLF001


def test_old_v1_event_chain_replays_as_read_only_semantic_history(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    key = _initialize(service)
    _append(
        service,
        key,
        "acknowledged",
        actor_id="coordinator.1",
        slot_id="coordinator.1",
        capability_id="coordination",
    )
    event_dir = service._store.session_root(SCOPE) / "events"  # noqa: SLF001
    previous_digest = ""
    for path in sorted(event_dir.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["schema_version"] = "finding-event.v1"
        for field in ("canonicalization_version", "compatibility_mode", "extensions"):
            payload.pop(field)
        payload["previous_event_digest"] = previous_digest
        payload["event_digest"] = persisted_event_digest(payload)
        path.write_text(json.dumps(payload), encoding="utf-8")
        previous_digest = payload["event_digest"]
    service._store.projection_path(SCOPE).unlink()  # noqa: SLF001
    ledger = service.read(SCOPE)
    events = service._store.load_events(SCOPE)  # noqa: SLF001
    assert ledger.records[0].finding_key == key
    assert ledger.records[0].state == "acknowledged"
    assert all(event.schema_version == "finding-event.v1" for event in events)
    assert all(event.compatibility_mode == "read-only-legacy" for event in events)


def test_old_v1_event_rejects_reserved_compatibility_proof_collision(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    _initialize(service)
    event_dir = service._store.session_root(SCOPE) / "events"  # noqa: SLF001
    previous_digest = ""
    for path in sorted(event_dir.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["schema_version"] = "finding-event.v1"
        payload.pop("canonicalization_version")
        payload.pop("compatibility_mode")
        payload["extensions"] = {
            "source_digest": "raw-business-value",
            "missing_envelope_fields": ["event_type"],
        }
        payload["previous_event_digest"] = previous_digest
        payload["event_digest"] = persisted_event_digest(payload)
        path.write_text(json.dumps(payload), encoding="utf-8")
        previous_digest = payload["event_digest"]
    service._store.projection_path(SCOPE).unlink()  # noqa: SLF001
    with pytest.raises(SharedStateIntegrityError, match="proof keys conflict"):
        service.read(SCOPE)


def test_append_command_requires_compare_and_swap_revision() -> None:
    values = {
        "scope": SCOPE,
        "command_id": "command.no-cas",
        "idempotency_key": "idem.no-cas",
        "session_fencing_epoch": 3,
        "finding_key": "finding.missing",
        "event_type": "acknowledged",
        "actor_id": "coordinator.1",
        "slot_id": "coordinator.1",
        "capability_id": "coordination",
        "candidate_digest": "sha256:candidate.2",
        "policy_digest": "sha256:policy.1",
        "plan_digest": "sha256:plan.1",
        "binding_set_digest": "sha256:binding-set.1",
        "evidence_bundle_digest": "sha256:evidence.ack",
    }
    with pytest.raises(ValueError, match="expected_revision"):
        FindingAppendCommand.model_validate(values)
    path = (
        Path(__file__).resolve().parents[2]
        / "fixtures/stage_review/finding-event.v1.golden.json"
    )
    raw_event = json.loads(path.read_text(encoding="utf-8"))
    raw_event.pop("expected_revision")
    raw_event["event_digest"] = persisted_event_digest(raw_event)
    with pytest.raises(SharedStateIntegrityError, match="v1 is invalid"):
        decode_finding_event(raw_event)


def test_candidate_evolution_replays_history_without_reauthorizing_it(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    key = _initialize(service)
    resolver = service._trust_resolver  # noqa: SLF001
    rebound = tuple(
        item.model_copy(update={"binding_digest": f"{item.binding_digest}.next"})
        for item in _trust().authorities
    )
    resolver.context = _trust(  # type: ignore[attr-defined]
        candidate_digest="sha256:candidate.3",
        cohort_id="cohort.next",
        binding_set_digest="sha256:binding-set.2",
        authorities=rebound,
        evaluation_at="2031-01-01T00:00:00Z",
    )
    historical = service.read(SCOPE)
    assert historical.records[0].finding_key == key
    assert not evaluate_closeability(
        historical,
        FindingCloseContext.from_trust(resolver.context),  # type: ignore[attr-defined]
    ).closeable
    resolver.context = _trust(  # type: ignore[attr-defined]
        candidate_digest="sha256:candidate.3",
        cohort_id="cohort.next",
    )
    _advance_candidate(service, "sha256:candidate.3")
    result = _append(
        service,
        key,
        "remediation_started",
        actor_id="remediator.1",
        slot_id="remediator.1",
        capability_id="remediation",
    )
    assert result.ledger.candidate_digest == "sha256:candidate.3"


def test_expired_waiver_history_remains_readable_but_not_closeable(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path, _trust(non_waivable_categories=()))
    key = _initialize(service)
    waiver = FindingWaiver(
        waiver_id="waiver.history",
        scope=SCOPE,
        finding_key=key,
        candidate_digest="sha256:candidate.2",
        policy_digest="sha256:policy.1",
        approved_by_actor_id="human.owner",
        approved_by_slot_id="human.owner",
        authority_binding_digest="sha256:binding.security",
        reason="time bounded",
        issued_at="2026-07-20T00:00:00Z",
        expires_at="2026-07-21T00:00:00Z",
        evidence_digest="sha256:evidence.waiver-history",
    )
    resolver = service._trust_resolver  # noqa: SLF001
    resolver.context = _trust(  # type: ignore[attr-defined]
        waivers=(waiver,),
        non_waivable_categories=(),
    )
    _append(
        service,
        key,
        "waived",
        actor_id="human.owner",
        slot_id="human.owner",
        capability_id="waiver",
        waiver_id=waiver.waiver_id,
        waiver_digest=waiver.waiver_digest,
        evidence_bundle_digest=waiver.evidence_digest,
    )
    resolver.context = _trust(  # type: ignore[attr-defined]
        waivers=(),
        non_waivable_categories=("security",),
        evaluation_at="2026-07-22T00:00:00Z",
    )
    ledger = service.read(SCOPE)
    assert ledger.records[0].state == "waived"
    assert not evaluate_closeability(
        ledger,
        FindingCloseContext.from_trust(resolver.context),  # type: ignore[attr-defined]
    ).closeable
    continued = _append(
        service,
        key,
        "regressed",
        actor_id="reviewer.security",
        slot_id="slot.security",
        capability_id="security",
        regression_of=key,
    )
    assert continued.ledger.records[0].state == "regressed"


def test_untrusted_alias_mapping_cannot_reuse_verified_state(tmp_path: Path) -> None:
    service = _service(tmp_path)
    key = _initialize(service)
    moved = _identity(
        asset_identity="src/untrusted.py:run",
        semantic_location="function:untrusted",
    )
    mapping = FindingIdentityMapping(
        mapping_kind="alias",
        source_keys=(key,),
        target_identity_digests=(moved.identity_digest,),
        evidence_digest="sha256:untrusted-mapping-decision",
        resolver_version="finding-identity.v1",
    )
    with pytest.raises(ValueError, match="trusted decision"):
        _append(
            service,
            key,
            "discovered",
            actor_id="reviewer.security",
            slot_id="slot.security",
            capability_id="security",
            finding_key=None,
            identity=moved,
            identity_mapping=mapping,
            severity="P1",
            category="security",
        )


def test_frozen_v1_golden_and_unknown_major_version_fail_closed() -> None:
    path = (
        Path(__file__).resolve().parents[2]
        / "fixtures/stage_review/finding-event.v1.golden.json"
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    event = decode_finding_event(payload)
    assert event.schema_version == "finding-event.v1"
    assert event.canonicalization_version == "canonical-json.v1"
    with pytest.raises(SharedStateIntegrityError, match="previous schema is read-only"):
        validate_finding_artifact_for_write("finding-event", event)
    unsupported = payload | {"schema_version": "finding-event.v3"}
    unsupported["event_digest"] = persisted_event_digest(unsupported)
    with pytest.raises(SharedStateIntegrityError, match="unsupported"):
        decode_finding_event(unsupported)


def test_project_global_ids_include_complete_finding_scope(tmp_path: Path) -> None:
    second_scope = SCOPE.model_copy(
        update={
            "work_item_id": "WI-402",
            "stage_instance_id": "verify.1",
        }
    )
    second_seal = _initial_seal(scope=second_scope)
    first = _service(tmp_path)
    second = _service(
        tmp_path,
        _trust(scope=second_scope, initial_review_seal=second_seal),
    )
    first_key = _initialize(first)
    second_key = _initialize(second, second_scope)
    operations = tuple((first._store.root / "operations").glob("*.json"))  # noqa: SLF001
    assert len(operations) == 2
    target_a = SCOPE.model_copy(update={"work_item_id": "WI-TARGET-A"})
    target_b = second_scope.model_copy(update={"work_item_id": "WI-TARGET-B"})
    event_a = _append(
        first,
        first_key,
        "cross_scope_critical_evidence",
        actor_id="reviewer.security",
        slot_id="slot.security",
        capability_id="security",
        target_scope=target_a,
    ).event
    event_b = _append(
        second,
        second_key,
        "cross_scope_critical_evidence",
        scope=second_scope,
        actor_id="reviewer.security",
        slot_id="slot.security",
        capability_id="security",
        target_scope=target_b,
    ).event
    assert event_a is not None and event_b is not None
    assert event_a.handoff_id != event_b.handoff_id
