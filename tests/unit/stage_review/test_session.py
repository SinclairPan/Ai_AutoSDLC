from __future__ import annotations

import json
import threading
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

import pytest

from ai_sdlc.core.stage_review import (
    binding_authority_validation,
    session_authority,
    session_operation_pointer,
)
from ai_sdlc.core.stage_review.binding_digests import (
    dispatch_assignment_digest,
    rebind_directive_digest,
    reviewer_binding_digest,
    reviewer_binding_set_digest,
)
from ai_sdlc.core.stage_review.binding_models import (
    BindingAuthoritySnapshot,
    ProviderBindingDescriptor,
)
from ai_sdlc.core.stage_review.binding_result_builders import (
    build_independence_proofs,
)
from ai_sdlc.core.stage_review.binding_result_models import (
    RebindDirective,
    ReviewerBinding,
    ReviewerBindingSet,
    ReviewerDispatchAssignment,
)
from ai_sdlc.core.stage_review.bindings import (
    build_binding_authority_snapshot,
    build_provider_binding_descriptor,
)
from ai_sdlc.core.stage_review.canonical import CanonicalizationPolicy, canonical_digest
from ai_sdlc.core.stage_review.codex_provider_authority import (
    _codex_provider_descriptors,
)
from ai_sdlc.core.stage_review.codex_provider_execution import (
    codex_reviewer_execution_route,
)
from ai_sdlc.core.stage_review.codex_trusted_releases import (
    _trusted_published_codex_release_digests,
)
from ai_sdlc.core.stage_review.contracts import (
    RiskFact,
    TaskRiskProfile,
    reconcile_risk_profile,
)
from ai_sdlc.core.stage_review.finding_command_models import (
    FindingInitialBatchCommand,
    FindingInitialDraft,
    FindingLineageAdvanceCommand,
)
from ai_sdlc.core.stage_review.finding_digests import ledger_digest
from ai_sdlc.core.stage_review.finding_models import (
    FindingAppendResult,
    FindingEvent,
    FindingIdentityInput,
    FindingLedger,
    FindingRecord,
    FindingScope,
)
from ai_sdlc.core.stage_review.finding_support_models import ProgressSnapshot
from ai_sdlc.core.stage_review.finding_trust_models import (
    FindingTrustContext,
    InitialReviewSeal,
    TrustedEvidenceDescriptor,
    TrustedIdentityMappingDecision,
)
from ai_sdlc.core.stage_review.findings import FindingLedgerService
from ai_sdlc.core.stage_review.panel_digests import (
    panel_proposal_digest,
    panel_proposal_lineage_digest,
    plan_request_digest,
    planning_context_digest,
    reviewer_panel_finalization_digest,
    reviewer_panel_plan_digest,
)
from ai_sdlc.core.stage_review.panel_models import (
    CapabilityCoverageRequirement,
    ReviewerPlanRequest,
)
from ai_sdlc.core.stage_review.panel_plan_models import (
    CapabilityCoverageProof,
    FrozenQuorumPolicy,
    PanelResourceRequirement,
    ReviewerDifference,
    ReviewerPanelPlan,
    ReviewerPanelProposal,
    ReviewerSlot,
)
from ai_sdlc.core.stage_review.provider_journal_models import (
    ProviderInvocation,
    ProviderInvocationRequest,
    ProviderRecoveryCapabilities,
    projection_digest,
    provider_execution_evidence_root_digest,
    request_artifact_digest,
)
from ai_sdlc.core.stage_review.provider_transport_trust import (
    _reviewer_transport_contract,
    build_reviewer_execution_identity,
)
from ai_sdlc.core.stage_review.resource_builders import (
    build_resource_event,
    stable_id,
    subtract_resources,
)
from ai_sdlc.core.stage_review.resource_digests import (
    budget_grant_operation_digest,
    reservation_digest,
    resource_operation_effect_digest,
)
from ai_sdlc.core.stage_review.resource_grant_models import (
    BudgetGrant,
    BudgetGrantDecisionClaim,
    BudgetGrantDecisionKind,
    BudgetGrantOperation,
    BudgetGrantResourceError,
)
from ai_sdlc.core.stage_review.resource_ledger_models import ResourceReservation
from ai_sdlc.core.stage_review.resource_models import (
    ResourceAmounts,
    ResourceSoftLimits,
)
from ai_sdlc.core.stage_review.resource_projection_builder import update_reservation
from ai_sdlc.core.stage_review.session import (
    BudgetGrantRequestCommand,
    CandidateUpdateCommand,
    CoverageDeclaration,
    MacroRebaselineCommand,
    PlanRevocationCommand,
    ProgressCommand,
    ProviderRebindCommand,
    ReviewerPlanRevocation,
    RiskEnrichmentCommand,
    RoleGapCommand,
    SessionCasConflictError,
    SessionIntegrityError,
    SessionStartCommand,
    StageReviewSessionService,
    SubmitReviewPassCommand,
    review_submission_digest,
)
from ai_sdlc.core.stage_review.session_artifact_models import CohortReviewer
from ai_sdlc.core.stage_review.session_budget_approval_models import (
    BudgetGrantApproval,
    BudgetGrantApprovalState,
)
from ai_sdlc.core.stage_review.session_budget_grant_authority_store import (
    BoundBudgetGrantRequestAuthority,
)
from ai_sdlc.core.stage_review.session_budget_grant_commands import (
    _build_failure_command as build_failure_command,
)
from ai_sdlc.core.stage_review.session_budget_grant_models import (
    BudgetGrantResourceApplication,
)
from ai_sdlc.core.stage_review.session_budget_grant_request import (
    BudgetGrantRequestProof,
)
from ai_sdlc.core.stage_review.session_budget_reconciliation_models import (
    BudgetGrantResourceReconciliation,
)
from ai_sdlc.core.stage_review.session_contracts import (
    BudgetGrantApprovalChangedError,
)
from ai_sdlc.core.stage_review.session_models import (
    SessionEvent,
    SessionMutationResult,
    StageReviewSession,
)

pytestmark = pytest.mark.usefixtures("allow_synthetic_session_authority")

PROJECT = "project.dynamic-review"
WORK_ITEM = "001-dynamic-review-gate"
STAGE = "implementation"
SESSION = "stage-review-session.001"
CANDIDATE = "sha256:candidate.1"
RISK = "sha256:risk.1"
RISK_LINEAGE = "risk-lineage.1"
POLICY = "sha256:policy.1"
SNAPSHOT = "sha256:optimization-snapshot.1"
LEDGER = "sha256:finding-ledger.1"
NOW = "2026-07-21T06:00:00Z"


@dataclass
class _Resolver:
    plan_requests: dict[str, ReviewerPlanRequest] = field(default_factory=dict)
    plans: dict[str, ReviewerPanelPlan] = field(default_factory=dict)
    bindings: dict[str, ReviewerBindingSet] = field(default_factory=dict)
    binding_authorities: dict[str, BindingAuthoritySnapshot] = field(
        default_factory=dict
    )
    reservations: dict[str, ResourceReservation] = field(default_factory=dict)
    assignments: dict[str, ReviewerDispatchAssignment] = field(default_factory=dict)
    invocations: dict[str, ProviderInvocation] = field(default_factory=dict)
    risk_profiles: dict[str, TaskRiskProfile] = field(default_factory=dict)
    rebind_directives: dict[str, RebindDirective] = field(default_factory=dict)
    revocations: dict[str, ReviewerPlanRevocation] = field(default_factory=dict)
    trusted_macro_evidence: set[tuple[str, str, str]] = field(default_factory=set)

    def resolve_plan_request(self, digest: str) -> ReviewerPlanRequest | None:
        return self.plan_requests.get(digest)

    def resolve_plan(self, digest: str) -> ReviewerPanelPlan | None:
        return self.plans.get(digest)

    def resolve_binding_set(self, digest: str) -> ReviewerBindingSet | None:
        return self.bindings.get(digest)

    def resolve_binding_authority(
        self,
        digest: str,
    ) -> BindingAuthoritySnapshot | None:
        cached = self.binding_authorities.get(digest)
        if cached is not None:
            return cached
        for binding_set in self.bindings.values():
            if binding_set.authority_snapshot_digest != digest:
                continue
            plan = self.plans.get(binding_set.plan_digest)
            if plan is not None:
                authority = _binding_authority_snapshot(plan, binding_set)
                self.binding_authorities[digest] = authority
                return authority
        return None

    def resolve_reservation(self, digest: str) -> ResourceReservation | None:
        return self.reservations.get(digest)

    def resolve_assignment(self, digest: str) -> ReviewerDispatchAssignment | None:
        return self.assignments.get(digest)

    def resolve_invocation(self, invocation_id: str) -> ProviderInvocation | None:
        return self.invocations.get(invocation_id)

    def resolve_risk_profile(self, digest: str) -> TaskRiskProfile | None:
        return self.risk_profiles.get(digest)

    def resolve_rebind_directive(self, digest: str) -> RebindDirective | None:
        return self.rebind_directives.get(digest)

    def resolve_plan_revocation(
        self,
        digest: str,
    ) -> ReviewerPlanRevocation | None:
        return self.revocations.get(digest)

    def macro_evidence_is_trusted(
        self,
        profile_digest: str,
        change_kind: str,
        evidence_digest: str,
    ) -> bool:
        return (
            profile_digest,
            change_kind,
            evidence_digest,
        ) in self.trusted_macro_evidence


@dataclass
class _FindingWriter:
    commands: list[FindingInitialBatchCommand] = field(default_factory=list)
    last_ledger_digest: str = ""
    results: dict[str, FindingAppendResult] = field(default_factory=dict)
    current_ledger: FindingLedger | None = None
    delegate: FindingLedgerService | None = None

    def append(self, command: FindingInitialBatchCommand) -> FindingAppendResult:
        if self.delegate is not None:
            return self.delegate.append(command)
        if command.command_id in self.results:
            return self.results[command.command_id]
        self.commands.append(command)
        records = tuple(
            FindingRecord(
                finding_key=stable_id("finding", item.identity.identity_digest),
                identity_digest=item.identity.identity_digest,
                category=item.identity.category,
                severity=item.severity,
                state="open",
                disposition="blocking",
                blocking=True,
                candidate_digest=command.candidate_digest,
                evidence_bundle_digests=(item.evidence_bundle_digest,),
            )
            for item in command.findings
        )
        values = {
            "scope": command.scope,
            "initialized": True,
            "revision": len(records) + 1,
            "initial_review_seal_digest": command.initial_review_seal_digest,
            "candidate_digest": command.candidate_digest,
            "policy_digest": command.policy_digest,
            "plan_digest": command.plan_digest,
            "binding_set_digest": command.binding_set_digest,
            "records": records,
        }
        draft = FindingLedger.model_validate({**values, "ledger_digest": ""})
        ledger = FindingLedger.model_validate(
            {**values, "ledger_digest": ledger_digest(draft)}
        )
        self.last_ledger_digest = ledger.ledger_digest
        self.current_ledger = ledger
        result = FindingAppendResult(event=None, ledger=ledger)
        self.results[command.command_id] = result
        return result

    def read(self, scope: FindingScope) -> FindingLedger:
        if self.delegate is not None:
            return self.delegate.read(scope)
        if self.current_ledger is None or self.current_ledger.scope != scope:
            raise RuntimeError("finding ledger is not initialized")
        return self.current_ledger

    def advance_lineage(
        self,
        command: FindingLineageAdvanceCommand,
    ) -> FindingAppendResult:
        if self.delegate is not None:
            return self.delegate.advance_lineage(command)
        current = self.read(command.scope)
        if (
            current.candidate_digest,
            current.policy_digest,
            current.plan_digest,
            current.binding_set_digest,
            current.cohort_id,
            current.lineage_contract_version,
        ) == (
            command.candidate_digest,
            command.policy_digest,
            command.plan_digest,
            command.binding_set_digest,
            command.cohort_id,
            "explicit-v2",
        ):
            return FindingAppendResult(
                event=None,
                ledger=current,
                idempotent_replay=True,
            )
        values = {
            **current.model_dump(mode="json"),
            "revision": current.revision + 1,
            "head_event_id": stable_id("finding-lineage-event", command.command_id),
            "head_event_digest": command.session_event_digest,
            "candidate_digest": command.candidate_digest,
            "policy_digest": command.policy_digest,
            "plan_digest": command.plan_digest,
            "binding_set_digest": command.binding_set_digest,
            "cohort_id": command.cohort_id,
            "lineage_contract_version": "explicit-v2",
            "ledger_digest": "",
        }
        draft = FindingLedger.model_validate(values)
        ledger = draft.model_copy(update={"ledger_digest": ledger_digest(draft)})
        self.current_ledger = ledger
        self.last_ledger_digest = ledger.ledger_digest
        return FindingAppendResult(event=None, ledger=ledger)


@dataclass
class _SessionFindingTrustResolver:
    context: FindingTrustContext
    trusted_session_event_digests: set[str] = field(default_factory=set)

    def resolve(self, scope: FindingScope) -> FindingTrustContext:
        assert scope == self.context.scope
        return self.context

    def resolve_evidence(
        self,
        scope: FindingScope,
        evidence_bundle_digest: str,
    ) -> TrustedEvidenceDescriptor | None:
        return None

    def event_is_trusted(self, event: object) -> bool:
        return False

    def session_lineage_is_trusted(self, event: object) -> bool:
        return isinstance(event, FindingEvent) and (
            event.evidence_bundle_digest in self.trusted_session_event_digests
        )

    def resolve_mapping(
        self,
        scope: FindingScope,
        decision_digest: str,
    ) -> TrustedIdentityMappingDecision | None:
        return None


@dataclass
class _Fixture:
    service: StageReviewSessionService
    resolver: _Resolver
    scope: FindingScope
    plan: ReviewerPanelPlan
    binding_set: ReviewerBindingSet
    reservation: ResourceReservation
    finding_writer: _FindingWriter
    budget_coordinator: _BudgetCoordinator | None = None
    budget_approvals: _BudgetApprovalResolver | None = None


@dataclass
class _BudgetApprovalResolver:
    authority_id: str = "budget-grant-approval-authority.test"
    approvals: dict[str, BudgetGrantApproval] = field(default_factory=dict)
    active_digests: set[str] = field(default_factory=set)
    generations: dict[str, int] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def resolve(self, approval_digest: str) -> BudgetGrantApproval | None:
        with self._lock:
            return self.approvals.get(approval_digest)

    def approval_state(
        self,
        approval_digest: str,
    ) -> BudgetGrantApprovalState | None:
        with self._lock:
            return self._approval_state(approval_digest)

    def _approval_state(
        self,
        approval_digest: str,
    ) -> BudgetGrantApprovalState | None:
        if approval_digest not in self.approvals:
            return None
        return BudgetGrantApprovalState(
            authority_id=self.authority_id,
            approval_digest=approval_digest,
            generation=self.generations.get(approval_digest, 1),
            active=approval_digest in self.active_digests,
        )

    def add(self, approval: BudgetGrantApproval) -> None:
        with self._lock:
            self.approvals[approval.approval_digest] = approval
            self.active_digests.add(approval.approval_digest)
            self.generations.setdefault(approval.approval_digest, 1)

    def revoke(self, approval_digest: str) -> None:
        with self._lock:
            self.active_digests.discard(approval_digest)
            self.generations[approval_digest] = (
                self.generations.get(approval_digest, 1) + 1
            )

    def advance_generation(self, approval_digest: str) -> None:
        with self._lock:
            self.active_digests.add(approval_digest)
            self.generations[approval_digest] = (
                self.generations.get(approval_digest, 1) + 1
            )

    @contextmanager
    def hold_session_apply(
        self,
        expected: BudgetGrantApprovalState,
        *,
        decision_digest: str,
        command_id: str,
    ) -> Any:
        del decision_digest, command_id
        with self._lock:
            current = self._approval_state(expected.approval_digest)
            if current != expected or not expected.active:
                raise BudgetGrantApprovalChangedError(
                    "budget grant approval changed before session apply"
                )
            yield


@dataclass
class _BudgetCoordinator:
    resolver: _Resolver
    approval_resolver: _BudgetApprovalResolver
    applications: dict[str, BudgetGrantResourceApplication] = field(
        default_factory=dict
    )
    operations: dict[str, BudgetGrantOperation] = field(default_factory=dict)
    decisions: dict[str, BudgetGrantDecisionClaim] = field(default_factory=dict)
    calls: int = 0
    verify_calls: int = 0
    reconcile_calls: int = 0
    failures_remaining: int = 0
    terminal_failure_code: str = ""
    revoke_after_apply: bool = False
    corrupt_verification: bool = False
    invalidate_before_commit: bool = False
    commit_guard_calls: int = 0
    reconcile_failure_code: str = ""
    reconcile_interruptions_remaining: int = 0

    def apply(
        self,
        grant: BudgetGrant,
        session: StageReviewSession,
        request_proof: BudgetGrantRequestProof,
    ) -> BudgetGrantResourceApplication:
        self.calls += 1
        if self.failures_remaining:
            self.failures_remaining -= 1
            raise RuntimeError("simulated resource grant interruption")
        if self.terminal_failure_code:
            raise BudgetGrantResourceError(self.terminal_failure_code)
        existing = self.applications.get(grant.grant_id)
        if existing is not None:
            return existing
        current = self.resolver.resolve_reservation(session.resource_reservation_digest)
        assert current is not None
        operation_id = stable_id(
            "budget-grant-operation", grant.idempotency_key, "apply"
        )
        effect = resource_operation_effect_digest(
            "budget_grant_apply",
            {"grant_digest": grant.grant_digest},
        )
        expanded = update_reservation(
            current,
            operation_id=operation_id,
            operation_effect_digest=effect,
            reserved=current.reserved + grant.increment,
            hard_limits=current.hard_limits + grant.increment,
            budget_revision=grant.expected_budget_revision + 1,
            last_budget_grant_operation_id=operation_id,
            budget_grant_ids=(*current.budget_grant_ids, grant.grant_id),
            fencing_token=current.fencing_token + 1,
        )
        operation = _budget_operation(
            current,
            expanded,
            grant,
            operation_kind="resource_applied",
            sequence=1,
        )
        application = BudgetGrantResourceApplication(
            grant=grant,
            request_proof_digest=request_proof.proof_digest,
            previous_reservation_digest=current.reservation_digest,
            reservation=expanded,
            resource_operation=operation,
        )
        self.resolver.reservations[expanded.reservation_digest] = expanded
        self.operations[operation.operation_id] = operation
        self.applications[grant.grant_id] = application
        if self.revoke_after_apply:
            self.approval_resolver.revoke(request_proof.approval.approval_digest)
        return application

    def verify(
        self,
        application: BudgetGrantResourceApplication,
        session: StageReviewSession,
        request_proof: BudgetGrantRequestProof,
    ) -> BudgetGrantResourceApplication:
        self.verify_calls += 1
        if self.corrupt_verification:
            raise BudgetGrantResourceError("state_corrupt")
        trusted = self.operations.get(application.resource_operation_id)
        current = self.resolver.resolve_reservation(
            application.reservation.reservation_digest
        )
        if (
            trusted != application.resource_operation
            or current != application.reservation
            or application.request_proof_digest != request_proof.proof_digest
            or application.previous_reservation_digest
            != session.resource_reservation_digest
        ):
            raise BudgetGrantResourceError("state_corrupt")
        return application

    def decide(
        self,
        application: BudgetGrantResourceApplication,
        request_proof: BudgetGrantRequestProof,
        desired_kind: BudgetGrantDecisionKind,
    ) -> BudgetGrantDecisionClaim:
        decision_id = stable_id(
            "budget-grant-decision",
            application.grant.idempotency_key,
        )
        existing = self.decisions.get(decision_id)
        if existing is not None:
            return existing
        reservation = application.reservation
        approval_state = self.approval_resolver.approval_state(
            request_proof.approval.approval_digest
        )
        assert approval_state is not None
        decision = BudgetGrantDecisionClaim(
            decision_id=decision_id,
            decision_kind=desired_kind,
            grant=application.grant,
            request_proof_digest=request_proof.proof_digest,
            approval_state=approval_state,
            resource_reservation_revision=reservation.revision,
            resource_reservation_digest=reservation.reservation_digest,
            resource_fencing_token=reservation.fencing_token,
            resource_reservation=reservation,
            claimed_at=NOW,
        )
        self.decisions[decision_id] = decision
        return decision

    @contextmanager
    def hold_apply_commit(
        self,
        application: BudgetGrantResourceApplication,
        decision: BudgetGrantDecisionClaim,
        session: StageReviewSession,
        request_proof: BudgetGrantRequestProof,
    ) -> Any:
        self.commit_guard_calls += 1
        if self.invalidate_before_commit:
            self.resolver.reservations.pop(
                application.reservation.reservation_digest,
                None,
            )
        self.verify(application, session, request_proof)
        if decision.decision_kind != "session_apply":
            raise BudgetGrantResourceError("decision_conflict")
        yield

    def reconcile(
        self,
        application: BudgetGrantResourceApplication,
        decision: BudgetGrantDecisionClaim,
        request_proof: BudgetGrantRequestProof,
        apply_command_id: str,
    ) -> BudgetGrantResourceReconciliation:
        current_approval = self.approval_resolver.approval_state(
            request_proof.approval.approval_digest
        )
        current_reservation = self.resolver.resolve_reservation(
            application.reservation.reservation_digest
        )
        can_release = (
            decision.decision_kind == "reconcile"
            or bool(apply_command_id)
            or current_approval != decision.approval_state
            or current_reservation != application.reservation
        )
        if not can_release:
            raise BudgetGrantResourceError("decision_conflict")
        self.reconcile_calls += 1
        if self.reconcile_interruptions_remaining:
            self.reconcile_interruptions_remaining -= 1
            raise RuntimeError("simulated resource reconcile interruption")
        if self.reconcile_failure_code:
            raise BudgetGrantResourceError(self.reconcile_failure_code)
        grant = application.grant
        current = application.reservation
        operation_id = stable_id(
            "budget-grant-operation",
            grant.idempotency_key,
            "reconcile",
        )
        effect = resource_operation_effect_digest(
            "budget_grant_reconcile",
            {"grant_digest": grant.grant_digest},
        )
        released = update_reservation(
            current,
            operation_id=operation_id,
            operation_effect_digest=effect,
            reserved=subtract_resources(current.reserved, grant.increment),
            hard_limits=subtract_resources(current.hard_limits, grant.increment),
            last_budget_grant_operation_id=operation_id,
            reconciled_budget_grant_ids=(
                *current.reconciled_budget_grant_ids,
                grant.grant_id,
            ),
            fencing_token=current.fencing_token + 1,
        )
        operation = _budget_operation(
            current,
            released,
            grant,
            operation_kind="reconciled_released",
            sequence=2,
        )
        self.operations[operation.operation_id] = operation
        self.resolver.reservations[released.reservation_digest] = released
        return BudgetGrantResourceReconciliation(
            application=application,
            decision=decision,
            resource_operation=operation,
        )


def test_required_initial_passes_are_isolated_until_atomic_seal(
    tmp_path: Path,
) -> None:
    fixture = _started(tmp_path)
    session = fixture.service.get(fixture.scope)
    first = _submit(fixture, session.revision, "slot.security", findings=(_finding(),))

    assert first.initial_review_seal is None
    assert first.session.finding_ledger_digest == ""
    assert (
        fixture.service.visible_passes(
            fixture.scope, session.active_cohort_id, "slot.delivery"
        )
        == ()
    )
    assert tuple(
        item.slot_id
        for item in fixture.service.visible_passes(
            fixture.scope, session.active_cohort_id, "slot.security"
        )
    ) == ("slot.security",)

    second = _submit(
        fixture,
        first.session.revision,
        "slot.delivery",
        findings=(),
    )
    assert second.initial_review_seal is not None
    assert second.initial_review_seal.required_slot_ids == (
        "slot.delivery",
        "slot.security",
    )
    assert len(second.initial_review_seal.required_pass_digests) == 2
    assert second.session.finding_ledger_digest
    assert (
        second.session.finding_ledger_digest
        == fixture.finding_writer.last_ledger_digest
    )
    assert second.session.state == "remediation_required"
    assert tuple(
        item.slot_id
        for item in fixture.service.visible_passes(
            fixture.scope, session.active_cohort_id, "slot.delivery"
        )
    ) == ("slot.delivery", "slot.security")


def test_real_loop_id_does_not_replace_stage_key_in_risk_lineage(
    tmp_path: Path,
) -> None:
    fixture, risk_profile = _unstarted(
        tmp_path,
        stage_instance_id="implementation-001-dynamic-review-gate",
    )

    session = _start_fixture(fixture, risk_profile, suffix="real-loop-id")

    assert session.scope.stage_instance_id == "implementation-001-dynamic-review-gate"
    assert risk_profile.stage_key == "implementation"


def test_initial_pass_rejects_peer_visibility_and_untrusted_lineage(
    tmp_path: Path,
) -> None:
    fixture = _started(tmp_path)
    session = fixture.service.get(fixture.scope)
    command = _pass_command(
        fixture,
        session.revision,
        "slot.security",
        observed_peer_pass_ids=("review-pass.foreign",),
    )
    with pytest.raises(SessionIntegrityError, match="peer output"):
        fixture.service.submit_pass(command)

    command = _pass_command(
        fixture,
        session.revision,
        "slot.security",
    )
    assignment, invocation, reservation = _review_authority(
        fixture,
        session,
        "slot.security",
        command,
    )
    foreign = assignment.model_copy(update={"cohort_id": "cohort.foreign"})
    fixture.resolver.assignments[assignment.assignment_digest] = foreign
    fixture.resolver.invocations[invocation.invocation_id] = invocation
    fixture.resolver.reservations[reservation.reservation_digest] = reservation
    with pytest.raises(SessionIntegrityError, match="assignment"):
        fixture.service.submit_pass(command)


def test_candidate_rereview_preserves_budget_progress_and_has_no_loop_fields(
    tmp_path: Path,
) -> None:
    fixture = _started(tmp_path)
    session = _seal_initial(fixture)
    unchanged = _progress(p0=1, calls=2)
    first_progress = fixture.service.record_progress(
        ProgressCommand(
            scope=fixture.scope,
            command_id="session-progress.1",
            idempotency_key="session-progress-key.1",
            expected_revision=session.revision,
            snapshot=unchanged,
        )
    ).session
    updated_binding, charged = _binding_for(
        fixture,
        fixture.plan,
        candidate_digest="sha256:candidate.2",
        suffix="candidate-2",
        usage=first_progress.resource_usage,
    )
    fixture.resolver.bindings[updated_binding.binding_set_digest] = updated_binding
    fixture.resolver.reservations[charged.reservation_digest] = charged

    result = fixture.service.update_candidate(
        CandidateUpdateCommand(
            scope=fixture.scope,
            command_id="candidate-update.1",
            idempotency_key="candidate-update-key.1",
            expected_revision=first_progress.revision,
            candidate_digest="sha256:candidate.2",
            binding_set_digest=updated_binding.binding_set_digest,
        )
    )

    assert result.session.active_candidate_digest == "sha256:candidate.2"
    assert result.session.active_plan_digest == fixture.plan.plan_digest
    assert result.session.resource_usage == first_progress.resource_usage
    assert result.session.no_progress_streak == first_progress.no_progress_streak
    assert result.session.finding_ledger_digest == first_progress.finding_ledger_digest
    fields = type(result.session).model_fields
    assert not {"max_rounds", "current_round", "next_loop"} & fields.keys()


def test_progress_allows_many_improving_passes_but_stops_after_two_non_improvements(
    tmp_path: Path,
) -> None:
    fixture = _started(tmp_path)
    session = fixture.service.get(fixture.scope)
    for index, p0 in enumerate(range(8, 1, -1), start=1):
        result = fixture.service.record_progress(
            ProgressCommand(
                scope=fixture.scope,
                command_id=f"session-progress.improving.{index}",
                idempotency_key=f"session-progress-key.improving.{index}",
                expected_revision=session.revision,
                snapshot=_progress(p0=p0, calls=index),
            )
        )
        session = result.session
        assert session.state != "needs_user"
    assert len(session.progress_records) == 7

    for index in (1, 2):
        result = fixture.service.record_progress(
            ProgressCommand(
                scope=fixture.scope,
                command_id=f"session-progress.same.{index}",
                idempotency_key=f"session-progress-key.same.{index}",
                expected_revision=session.revision,
                snapshot=_progress(p0=2, calls=20 + index),
            )
        )
        session = result.session
    assert session.state == "needs_user"
    assert session.no_progress_streak == 2


def test_role_gap_uses_exact_event_sequence_and_inherits_session_state(
    tmp_path: Path,
) -> None:
    fixture = _started_with_role_gap(tmp_path)
    session = fixture.service.get(fixture.scope)
    session = fixture.service.record_progress(
        ProgressCommand(
            scope=fixture.scope,
            command_id="session-progress.role-gap",
            idempotency_key="session-progress-key.role-gap",
            expected_revision=session.revision,
            snapshot=_progress(p0=2, calls=2),
        )
    ).session
    previous_passes = tuple(item.pass_id for item in session.pass_refs)
    plan, binding, charged = _replacement_authority(
        fixture,
        suffix="role-gap",
        candidate_digest=CANDIDATE,
        usage=session.resource_usage
        + ResourceAmounts(role_replans=1, binding_attempts=1),
    )

    result = fixture.service.handle_role_gap(
        RoleGapCommand(
            scope=fixture.scope,
            command_id="role-gap.1",
            idempotency_key="role-gap-key.1",
            expected_revision=session.revision,
            missing_capability_ids=("capability.new",),
            plan_digest=plan.plan_digest,
            binding_set_digest=binding.binding_set_digest,
        )
    )
    kinds = tuple(
        item.event_kind
        for item in fixture.service.events(fixture.scope)
        if item.command_id == "role-gap.1"
    )
    assert kinds == (
        "role_gap_detected",
        "cohort_superseded",
        "old_passes_invalidated",
        "plan_resolution_requested",
        "panel_plan_frozen",
        "reviewer_bindings_validated",
        "new_cohort_activated",
    )
    assert set(previous_passes) <= set(result.session.invalidated_pass_ids)
    assert result.session.finding_ledger_digest == session.finding_ledger_digest
    assert result.session.no_progress_streak == session.no_progress_streak
    assert result.session.resource_usage == charged.usage
    assert result.session.role_replan_count(RISK_LINEAGE) == 1


def test_second_role_gap_needs_user_even_after_candidate_change(
    tmp_path: Path,
) -> None:
    fixture = _started_with_role_gap(tmp_path)
    session = fixture.service.get(fixture.scope)
    plan, binding, _ = _replacement_authority(
        fixture,
        suffix="role-gap-1",
        candidate_digest=CANDIDATE,
        usage=session.resource_usage + ResourceAmounts(role_replans=1),
    )
    session = fixture.service.handle_role_gap(
        RoleGapCommand(
            scope=fixture.scope,
            command_id="role-gap.first",
            idempotency_key="role-gap-key.first",
            expected_revision=session.revision,
            missing_capability_ids=("capability.new",),
            plan_digest=plan.plan_digest,
            binding_set_digest=binding.binding_set_digest,
        )
    ).session
    session = _seal_initial(fixture)
    candidate_binding, charged = _binding_for(
        fixture,
        plan,
        candidate_digest="sha256:candidate.2",
        suffix="after-role-gap",
        usage=session.resource_usage,
    )
    fixture.resolver.bindings[candidate_binding.binding_set_digest] = candidate_binding
    fixture.resolver.reservations[charged.reservation_digest] = charged
    session = fixture.service.update_candidate(
        CandidateUpdateCommand(
            scope=fixture.scope,
            command_id="candidate-update.after-gap",
            idempotency_key="candidate-update-key.after-gap",
            expected_revision=session.revision,
            candidate_digest="sha256:candidate.2",
            binding_set_digest=candidate_binding.binding_set_digest,
        )
    ).session

    another_profile = _risk_profile(
        "role-gap-second",
        (
            "capability.another",
            "capability.delivery",
            "capability.new",
            "capability.security",
        ),
    )
    fixture.resolver.risk_profiles[another_profile.profile_digest] = another_profile
    second = fixture.service.enrich_risk(
        RiskEnrichmentCommand(
            scope=fixture.scope,
            command_id="risk-enrichment.second-role-gap",
            idempotency_key="risk-enrichment-key.second-role-gap",
            expected_revision=session.revision,
            risk_profile_digest=another_profile.profile_digest,
        )
    )
    assert second.session.state == "needs_user"
    assert second.session.role_replan_count(RISK_LINEAGE) == 1
    assert tuple(
        item.event_kind
        for item in fixture.service.events(fixture.scope)
        if item.command_id == "risk-enrichment.second-role-gap"
    ) == (
        "risk_fact_enriched",
        "cohort_superseded",
        "old_passes_invalidated",
        "user_decision_required",
    )


def test_provider_rebind_and_risk_enrichment_create_new_cohorts_without_replan(
    tmp_path: Path,
) -> None:
    fixture = _started(tmp_path)
    session = fixture.service.get(fixture.scope)
    old_cohort = session.active_cohort_id
    rebound, charged = _binding_for(
        fixture,
        fixture.plan,
        candidate_digest=CANDIDATE,
        suffix="rebind",
        usage=session.resource_usage
        + ResourceAmounts(provider_retries=1, binding_attempts=1),
    )
    fixture.resolver.bindings[rebound.binding_set_digest] = rebound
    fixture.resolver.reservations[charged.reservation_digest] = charged
    directive = _rebind_directive(
        session,
        rebound,
        (fixture.binding_set.bindings[0].provider_id,),
    )
    fixture.resolver.rebind_directives[directive.directive_digest] = directive
    session = fixture.service.rebind_provider(
        ProviderRebindCommand(
            scope=fixture.scope,
            command_id="provider-rebind.1",
            idempotency_key="provider-rebind-key.1",
            expected_revision=session.revision,
            binding_set_digest=rebound.binding_set_digest,
            rebind_directive_digest=directive.directive_digest,
        )
    ).session
    assert session.active_cohort_id != old_cohort
    assert session.active_plan_digest == fixture.plan.plan_digest
    assert session.role_replan_count(RISK_LINEAGE) == 0

    risk = _risk_profile(
        "covered",
        ("capability.delivery", "capability.security"),
    )
    fixture.resolver.risk_profiles[risk.profile_digest] = risk
    enriched = fixture.service.enrich_risk(
        RiskEnrichmentCommand(
            scope=fixture.scope,
            command_id="risk-enrichment.covered",
            idempotency_key="risk-enrichment-key.covered",
            expected_revision=session.revision,
            risk_profile_digest=risk.profile_digest,
        )
    ).session
    assert enriched.active_risk_profile_digest == risk.profile_digest
    assert enriched.active_cohort_id != session.active_cohort_id
    assert enriched.active_plan_digest == session.active_plan_digest
    assert enriched.active_binding_set_digest == session.active_binding_set_digest
    assert enriched.role_replan_count(RISK_LINEAGE) == 0


def test_macro_rebaseline_is_only_a_request_and_plan_revocation_is_explicit(
    tmp_path: Path,
) -> None:
    fixture = _started(tmp_path)
    session = fixture.service.get(fixture.scope)
    macro = fixture.service.request_macro_rebaseline(
        MacroRebaselineCommand(
            scope=fixture.scope,
            command_id="macro-rebaseline.1",
            idempotency_key="macro-rebaseline-key.1",
            expected_revision=session.revision,
            change_kind="architecture_change",
            evidence_digest="sha256:architecture-evidence",
        )
    )
    assert macro.macro_rebaseline_request is not None
    assert macro.session.state == session.state
    assert "loop_round" not in macro.session.model_dump(mode="json")

    unchanged = fixture.service.get(fixture.scope)
    assert unchanged.active_plan_digest == fixture.plan.plan_digest
    revocation = ReviewerPlanRevocation(
        revocation_id="reviewer-plan-revocation.1",
        target_kind="plan",
        plan_digest=fixture.plan.plan_digest,
        profile_ids=(),
        capability_ids=(),
        reason_id="registry.capability-revoked",
        evidence_digest="sha256:revocation-evidence",
        issuer_id="governance.release-authority",
        issuer_authority_digest="sha256:governance-authority.1",
        replacement_version="",
        minimum_version="1.0.1",
        issued_at=NOW,
    )
    fixture.resolver.revocations[revocation.revocation_digest] = revocation
    revoked = fixture.service.revoke_plan(
        PlanRevocationCommand(
            scope=fixture.scope,
            command_id="plan-revocation.1",
            idempotency_key="plan-revocation-key.1",
            expected_revision=unchanged.revision,
            revocation_digest=revocation.revocation_digest,
        )
    ).session
    assert revoked.state == "blocked"
    assert fixture.plan.plan_digest in revoked.revoked_plan_digests


def test_session_event_truth_repairs_projection_and_enforces_cas_and_scope(
    tmp_path: Path,
) -> None:
    fixture = _started(tmp_path)
    session = fixture.service.get(fixture.scope)
    projection = fixture.service.projection_path(fixture.scope)
    projection.unlink()
    repaired = fixture.service.get(fixture.scope)
    assert repaired.session_digest == session.session_digest
    assert projection.exists()

    with pytest.raises(SessionCasConflictError):
        fixture.service.record_progress(
            ProgressCommand(
                scope=fixture.scope,
                command_id="stale-progress.1",
                idempotency_key="stale-progress-key.1",
                expected_revision=0,
                snapshot=_progress(p0=1, calls=1),
            )
        )

    other = FindingScope(
        project_id=PROJECT,
        work_item_id="other-work-item",
        stage_instance_id=STAGE,
        session_id="stage-review-session.other",
    )
    assert fixture.service.maybe_get(other) is None
    assert fixture.service.get(fixture.scope).scope.work_item_id == WORK_ITEM


def test_retry_reuses_operation_timestamp_and_hard_budget_requires_user(
    tmp_path: Path,
) -> None:
    fixture = _started(tmp_path)
    session = fixture.service.get(fixture.scope)
    command = _pass_command(fixture, session.revision, "slot.security")
    assignment, invocation, _ = _review_authority(
        fixture,
        session,
        "slot.security",
        command,
    )
    usage = session.resource_usage + ResourceAmounts(
        provider_calls=1,
        review_passes=1,
        tokens=10,
        cost=1,
        active_wall_clock=1,
    )
    hard = usage + ResourceAmounts(slots=1, parallelism=1)
    reservation = _reservation(
        usage=usage,
        hard=hard,
        revision=fixture.reservation.revision + 1,
        last_operation_id="resource-pass.hard-limit",
    )
    invocation = invocation.model_copy(
        update={"settlement_reservation_digest": reservation.reservation_digest}
    )
    invocation = ProviderInvocation.model_validate(
        {
            **invocation.model_dump(mode="json"),
            "projection_digest": projection_digest(invocation),
        }
    )
    fixture.resolver.assignments[assignment.assignment_digest] = assignment
    fixture.resolver.invocations[invocation.invocation_id] = invocation
    fixture.resolver.reservations[reservation.reservation_digest] = reservation

    committed = fixture.service.submit_pass(command)
    assert committed.session.state == "needs_user"

    later = StageReviewSessionService(
        tmp_path,
        project_id=PROJECT,
        trust_resolver=fixture.resolver,
        finding_ledger_writer=fixture.finding_writer,
        clock=lambda: "2026-07-21T07:00:00Z",
    )
    replay = later.submit_pass(command)
    assert replay.idempotent_replay
    assert replay.session.session_digest == committed.session.session_digest


def test_final_required_pass_cannot_override_hard_budget_stop(tmp_path: Path) -> None:
    fixture = _started(tmp_path)
    initial = fixture.service.get(fixture.scope)
    first = _submit(fixture, initial.revision, "slot.security")
    command = _pass_command(
        fixture,
        first.session.revision,
        "slot.delivery",
    )
    assignment, invocation, _ = _review_authority(
        fixture,
        first.session,
        "slot.delivery",
        command,
    )
    usage = first.session.resource_usage + ResourceAmounts(
        provider_calls=1,
        review_passes=1,
        tokens=10,
        cost=1,
        active_wall_clock=1,
    )
    hard = usage + ResourceAmounts(slots=1, parallelism=1)
    reservation = _reservation(
        usage=usage,
        hard=hard,
        revision=fixture.reservation.revision + command.expected_revision + 1,
        last_operation_id=f"resource-pass.{command.command_id}",
    )
    invocation = invocation.model_copy(
        update={"settlement_reservation_digest": reservation.reservation_digest}
    )
    invocation = ProviderInvocation.model_validate(
        {
            **invocation.model_dump(mode="json"),
            "projection_digest": projection_digest(invocation),
        }
    )
    fixture.resolver.assignments[assignment.assignment_digest] = assignment
    fixture.resolver.invocations[invocation.invocation_id] = invocation
    fixture.resolver.reservations[reservation.reservation_digest] = reservation

    sealed = fixture.service.submit_pass(command)

    assert sealed.session.state == "needs_user"
    assert sealed.session.active_cohort_id in sealed.session.sealed_cohort_ids


def test_budget_grant_is_requested_then_session_applied_and_restores_state(
    tmp_path: Path,
) -> None:
    fixture = _started_with_budget_coordinator(tmp_path)
    stopped = _hard_budget_stop(fixture)

    assert stopped.state == "needs_user"
    assert stopped.budget_resume_state == "authorized"
    command = _budget_grant_command(fixture, stopped, "approved")
    result = fixture.service.extend_budget(command)

    assert fixture.budget_coordinator is not None
    assert fixture.budget_coordinator.calls == 1
    application = next(iter(fixture.budget_coordinator.applications.values()))
    grant = application.grant
    grant_events = tuple(
        item
        for item in fixture.service.events(fixture.scope)
        if item.event_kind in {"budget_grant_requested", "budget_grant_applied"}
    )
    assert tuple(item.event_kind for item in grant_events) == (
        "budget_grant_requested",
        "budget_grant_applied",
    )
    assert grant.requested_event_digest == grant_events[0].event_digest
    assert result.session.state == "authorized"
    assert result.session.budget_resume_state is None
    assert result.session.budget_revision == 1
    assert result.session.budget_grant_ids == (grant.grant_id,)
    assert result.session.budget_grant_digests == (grant.grant_digest,)
    assert result.session.resource_reservation_digest == (
        application.reservation.reservation_digest
    )
    assert (
        result.session.resource_fencing_epoch == application.reservation.fencing_token
    )
    assert not result.session.pending_budget_grant_command_id

    replay = fixture.service.extend_budget(command)
    assert replay.idempotent_replay
    assert replay.session.session_digest == result.session.session_digest
    assert fixture.budget_coordinator.calls == 1


def test_budget_grant_session_operation_recovers_after_resource_expansion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _started_with_budget_coordinator(tmp_path)
    stopped = _hard_budget_stop(fixture)
    command = _budget_grant_command(fixture, stopped, "crash")
    original = fixture.service._store._append_event

    def interrupt_session_apply(event: SessionEvent) -> None:
        if event.event_kind == "budget_grant_applied":
            raise RuntimeError("simulated session apply exit")
        original(event)

    monkeypatch.setattr(
        fixture.service._store,
        "_append_event",
        interrupt_session_apply,
    )
    with pytest.raises(RuntimeError, match="session apply exit"):
        fixture.service.extend_budget(command)
    monkeypatch.setattr(fixture.service._store, "_append_event", original)

    recovered = fixture.service.get(fixture.scope)

    assert fixture.budget_coordinator is not None
    assert fixture.budget_coordinator.calls == 1
    assert recovered.state == "authorized"
    assert recovered.budget_revision == 1
    assert len(recovered.budget_grant_ids) == 1
    assert tuple(
        item.event_kind
        for item in fixture.service.events(fixture.scope)
        if item.event_kind in {"budget_grant_requested", "budget_grant_applied"}
    ) == ("budget_grant_requested", "budget_grant_applied")


def test_pending_budget_apply_revalidates_resource_before_recovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _started_with_budget_coordinator(tmp_path)
    stopped = _hard_budget_stop(fixture)
    command = _budget_grant_command(fixture, stopped, "pending-resource-recheck")
    original_append = fixture.service._store._append_event

    def interrupt_apply(event: SessionEvent) -> None:
        if event.event_kind == "budget_grant_applied":
            raise RuntimeError("simulated apply append exit")
        original_append(event)

    monkeypatch.setattr(fixture.service._store, "_append_event", interrupt_apply)
    with pytest.raises(RuntimeError, match="apply append exit"):
        fixture.service.extend_budget(command)
    monkeypatch.setattr(fixture.service._store, "_append_event", original_append)
    assert fixture.budget_coordinator is not None
    application = next(iter(fixture.budget_coordinator.applications.values()))
    fixture.resolver.reservations.pop(application.reservation.reservation_digest)

    recovered = fixture.service.get(fixture.scope)

    assert recovered.state == "needs_user"
    assert not recovered.budget_grant_ids
    assert recovered.reconciled_budget_grant_ids == (application.grant.grant_id,)
    assert fixture.budget_coordinator.reconcile_calls == 1


def test_pending_budget_apply_reconciles_revoked_approval_on_recovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _started_with_budget_coordinator(tmp_path)
    stopped = _hard_budget_stop(fixture)
    command = _budget_grant_command(fixture, stopped, "pending-approval-recheck")
    original_append = fixture.service._store._append_event

    def interrupt_apply(event: SessionEvent) -> None:
        if event.event_kind == "budget_grant_applied":
            raise RuntimeError("simulated approval append exit")
        original_append(event)

    monkeypatch.setattr(fixture.service._store, "_append_event", interrupt_apply)
    with pytest.raises(RuntimeError, match="approval append exit"):
        fixture.service.extend_budget(command)
    monkeypatch.setattr(fixture.service._store, "_append_event", original_append)
    assert fixture.budget_approvals is not None
    fixture.budget_approvals.revoke(command.approval_digest)

    recovered = fixture.service.get(fixture.scope)

    assert fixture.budget_coordinator is not None
    assert recovered.state == "needs_user"
    assert recovered.reconciled_budget_grant_ids
    assert fixture.budget_coordinator.reconcile_calls == 1
    assert fixture.service._store.pending_operation(fixture.scope) is None


def test_committed_budget_apply_recovery_ignores_later_approval_revocation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _started_with_budget_coordinator(tmp_path)
    stopped = _hard_budget_stop(fixture)
    command = _budget_grant_command(fixture, stopped, "committed-before-exit")
    original_append = fixture.service._store._append_event

    def append_then_exit(event: SessionEvent) -> None:
        original_append(event)
        if event.event_kind == "budget_grant_applied":
            raise RuntimeError("simulated exit after committed apply")

    monkeypatch.setattr(fixture.service._store, "_append_event", append_then_exit)
    with pytest.raises(RuntimeError, match="after committed apply"):
        fixture.service.extend_budget(command)
    monkeypatch.setattr(fixture.service._store, "_append_event", original_append)
    assert fixture.budget_approvals is not None
    fixture.budget_approvals.revoke(command.approval_digest)

    recovered = fixture.service.get(fixture.scope)

    assert fixture.budget_coordinator is not None
    assert recovered.state == "authorized"
    assert recovered.budget_grant_ids
    assert not recovered.reconciled_budget_grant_ids
    assert fixture.budget_coordinator.reconcile_calls == 0
    assert fixture.service._store.pending_operation(fixture.scope) is None


def test_budget_grant_applied_event_requires_immutable_session_operation(
    tmp_path: Path,
) -> None:
    fixture = _started_with_budget_coordinator(tmp_path)
    stopped = _hard_budget_stop(fixture)
    fixture.service.extend_budget(
        _budget_grant_command(fixture, stopped, "operation-proof")
    )
    root = fixture.service.projection_path(fixture.scope).parent
    operation_path = next(root.glob("budget-grant-operations/*.json"))
    operation_path.unlink()
    fixture.service.projection_path(fixture.scope).unlink()

    with pytest.raises(SessionIntegrityError, match="operation"):
        fixture.service.get(fixture.scope)


def test_pending_budget_request_recovers_before_an_unrelated_command(
    tmp_path: Path,
) -> None:
    fixture = _started_with_budget_coordinator(tmp_path)
    stopped = _hard_budget_stop(fixture)
    command = _budget_grant_command(fixture, stopped, "resume")
    assert fixture.budget_coordinator is not None
    fixture.budget_coordinator.failures_remaining = 1

    with pytest.raises(RuntimeError, match="resource grant interruption"):
        fixture.service.extend_budget(command)
    assert fixture.service._store.pending_operation(fixture.scope) is None

    with pytest.raises(SessionCasConflictError):
        fixture.service.record_progress(
            ProgressCommand(
                scope=fixture.scope,
                command_id="progress.after-pending-grant",
                idempotency_key="progress-after-pending-grant-key",
                expected_revision=stopped.revision,
                snapshot=_progress(p0=0, calls=99),
            )
        )
    recovered = fixture.service.get(fixture.scope)
    assert recovered.state == "authorized"
    assert recovered.budget_revision == 1
    assert fixture.budget_coordinator.calls == 2


def test_budget_grant_cannot_clear_non_budget_needs_user(tmp_path: Path) -> None:
    fixture = _started_with_budget_coordinator(tmp_path)
    session = fixture.service.get(fixture.scope)
    for index in (1, 2, 3):
        session = fixture.service.record_progress(
            ProgressCommand(
                scope=fixture.scope,
                command_id=f"progress.non-budget.{index}",
                idempotency_key=f"progress-non-budget-key.{index}",
                expected_revision=session.revision,
                snapshot=_progress(p0=2, calls=index),
            )
        ).session
    assert session.state == "needs_user"
    assert session.budget_resume_state is None

    with pytest.raises(SessionIntegrityError, match="hard budget"):
        fixture.service.extend_budget(
            _budget_grant_command(fixture, session, "invalid")
        )

    assert fixture.budget_coordinator is not None
    assert fixture.budget_coordinator.calls == 0
    assert not any(
        item.event_kind == "budget_grant_requested"
        for item in fixture.service.events(fixture.scope)
    )


def test_budget_grant_rejects_untrusted_approval_before_request(
    tmp_path: Path,
) -> None:
    fixture = _started_with_budget_coordinator(tmp_path)
    stopped = _hard_budget_stop(fixture)
    command = _budget_grant_command(fixture, stopped, "untrusted")
    assert fixture.budget_approvals is not None
    fixture.budget_approvals.revoke(command.approval_digest)

    with pytest.raises(SessionIntegrityError, match="not trusted"):
        fixture.service.extend_budget(command)

    assert fixture.budget_coordinator is not None
    assert fixture.budget_coordinator.calls == 0
    assert not any(
        event.event_kind == "budget_grant_requested"
        for event in fixture.service.events(fixture.scope)
    )


def test_budget_grant_rejects_resolver_key_alias_before_request(
    tmp_path: Path,
) -> None:
    fixture = _started_with_budget_coordinator(tmp_path)
    stopped = _hard_budget_stop(fixture)
    command = _budget_grant_command(fixture, stopped, "resolver-key-alias")
    assert fixture.budget_approvals is not None
    original = fixture.budget_approvals.approvals[command.approval_digest]
    payload = original.model_dump(mode="json")
    payload.update(
        approval_id="budget-grant-approval.divergent",
        approval_digest="",
    )
    fixture.budget_approvals.approvals[command.approval_digest] = (
        BudgetGrantApproval.model_validate(payload)
    )

    with pytest.raises(SessionIntegrityError, match="authority diverged"):
        fixture.service.extend_budget(command)

    assert not any(
        event.event_kind == "budget_grant_requested"
        for event in fixture.service.events(fixture.scope)
    )


def test_budget_grant_rejects_partial_increment_before_resource_apply(
    tmp_path: Path,
) -> None:
    fixture = _started_with_budget_coordinator(tmp_path)
    stopped = _hard_budget_stop(fixture)
    command = _budget_grant_command(
        fixture,
        stopped,
        "partial",
        increment=ResourceAmounts(tokens=100),
    )

    with pytest.raises(SessionIntegrityError, match="does not resolve"):
        fixture.service.extend_budget(command)

    assert fixture.budget_coordinator is not None
    assert fixture.budget_coordinator.calls == 0


def test_budget_grant_revocation_after_resource_apply_is_reconciled(
    tmp_path: Path,
) -> None:
    fixture = _started_with_budget_coordinator(tmp_path)
    stopped = _hard_budget_stop(fixture)
    assert fixture.budget_coordinator is not None
    fixture.budget_coordinator.revoke_after_apply = True

    command = _budget_grant_command(fixture, stopped, "revoked-after-apply")
    result = fixture.service.extend_budget(command)

    grant = next(iter(fixture.budget_coordinator.applications.values())).grant
    assert result.session.state == "needs_user"
    assert result.session.budget_resume_state == "authorized"
    assert result.session.reconciled_budget_grant_ids == (grant.grant_id,)
    assert result.session.budget_grant_ids == ()
    assert result.session.budget_revision == 1
    assert fixture.budget_coordinator.reconcile_calls == 1
    replay = fixture.service.extend_budget(command)
    assert replay.idempotent_replay
    assert fixture.budget_coordinator.reconcile_calls == 1
    assert tuple(
        event.event_kind
        for event in fixture.service.events(fixture.scope)
        if event.event_kind.startswith("budget_grant_")
    ) == ("budget_grant_requested", "budget_grant_reconciled")


def test_budget_grant_revocation_after_decision_prevents_session_apply(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _started_with_budget_coordinator(tmp_path)
    stopped = _hard_budget_stop(fixture)
    command = _budget_grant_command(fixture, stopped, "revoked-after-decision")
    budget_ops = fixture.service._budget
    original_apply = budget_ops.apply

    def revoke_then_apply(apply_command: object) -> SessionMutationResult:
        assert fixture.budget_approvals is not None
        fixture.budget_approvals.revoke(command.approval_digest)
        return original_apply(cast(Any, apply_command))

    monkeypatch.setattr(budget_ops, "apply", revoke_then_apply)
    result = fixture.service.extend_budget(command)

    assert fixture.budget_coordinator is not None
    application = next(iter(fixture.budget_coordinator.applications.values()))
    apply_command_id = stable_id(
        "budget-grant-session-apply",
        application.grant.grant_id,
    )
    assert fixture.service._store.operation_was_rejected(
        fixture.scope,
        apply_command_id,
    )
    assert result.session.state == "needs_user"
    assert not result.session.budget_grant_ids
    assert result.session.reconciled_budget_grant_ids
    assert fixture.budget_coordinator.reconcile_calls == 1


def test_budget_grant_resource_change_before_commit_is_reconciled(
    tmp_path: Path,
) -> None:
    fixture = _started_with_budget_coordinator(tmp_path)
    stopped = _hard_budget_stop(fixture)
    assert fixture.budget_coordinator is not None
    fixture.budget_coordinator.invalidate_before_commit = True

    result = fixture.service.extend_budget(
        _budget_grant_command(fixture, stopped, "resource-change-before-commit")
    )

    assert result.session.state == "needs_user"
    assert not result.session.budget_grant_ids
    assert result.session.reconciled_budget_grant_ids
    assert fixture.budget_coordinator.commit_guard_calls == 1
    assert fixture.budget_coordinator.reconcile_calls == 1


def test_budget_grant_session_commit_serializes_concurrent_revocation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _started_with_budget_coordinator(tmp_path)
    stopped = _hard_budget_stop(fixture)
    command = _budget_grant_command(fixture, stopped, "commit-serialization")
    original_append = fixture.service._store._append_event
    mutation_was_blocked: list[bool] = []
    revokers: list[threading.Thread] = []

    def append_while_revocation_competes(event: SessionEvent) -> None:
        if event.event_kind == "budget_grant_applied":
            attempted = threading.Event()
            completed = threading.Event()

            def revoke() -> None:
                attempted.set()
                assert fixture.budget_approvals is not None
                fixture.budget_approvals.revoke(command.approval_digest)
                completed.set()

            revoker = threading.Thread(target=revoke)
            revokers.append(revoker)
            revoker.start()
            assert attempted.wait(timeout=1)
            mutation_was_blocked.append(not completed.wait(timeout=0.05))
        original_append(event)

    monkeypatch.setattr(
        fixture.service._store,
        "_append_event",
        append_while_revocation_competes,
    )
    result = fixture.service.extend_budget(command)
    for revoker in revokers:
        revoker.join(timeout=1)

    assert mutation_was_blocked == [True]
    assert result.session.state == "authorized"
    assert fixture.budget_approvals is not None
    approval_state = fixture.budget_approvals.approval_state(command.approval_digest)
    assert approval_state is not None
    assert not approval_state.active


def test_budget_grant_apply_status_is_atomic_with_session_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _started_with_budget_coordinator(tmp_path)
    stopped = _hard_budget_stop(fixture)
    request = _budget_grant_command(fixture, stopped, "status-commit-race")
    budget_ops = fixture.service._budget
    original_apply = budget_ops.apply
    captured: list[Any] = []

    def interrupt_before_apply(command: object) -> SessionMutationResult:
        captured.append(command)
        raise RuntimeError("stop before session apply")

    monkeypatch.setattr(budget_ops, "apply", interrupt_before_apply)
    with pytest.raises(RuntimeError, match="stop before session apply"):
        fixture.service.extend_budget(request)
    monkeypatch.setattr(budget_ops, "apply", original_apply)
    apply_command = captured[0]
    request_event = next(
        event
        for event in fixture.service._store.load_events(fixture.scope)
        if event.command_id == request.command_id
        and event.event_kind == "budget_grant_requested"
    )
    request_operation = fixture.service._store.get_operation(
        fixture.scope,
        request.command_id,
    )
    assert request_operation is not None
    approval = fixture.service._store.get_budget_grant_approval(
        fixture.scope,
        request_event.artifact_refs[0].artifact_id,
    )
    proof = BudgetGrantRequestProof(
        approval=approval,
        request_operation=request_operation,
        requested_event=request_event,
    )
    assert fixture.budget_approvals is not None
    authority = BoundBudgetGrantRequestAuthority(
        fixture.service._store,
        fixture.budget_approvals,
    )
    original_rejected = fixture.service._store.operation_was_rejected
    committed = threading.Event()
    committers: list[threading.Thread] = []

    def commit_during_status(scope: FindingScope, command_id: str) -> bool:
        def commit() -> None:
            original_apply(cast(Any, apply_command))
            committed.set()

        committer = threading.Thread(target=commit)
        committers.append(committer)
        committer.start()
        committed.wait(timeout=0.1)
        return original_rejected(scope, command_id)

    monkeypatch.setattr(
        fixture.service._store,
        "operation_was_rejected",
        commit_during_status,
    )
    status = authority.budget_grant_apply_status(
        proof,
        cast(Any, apply_command).command_id,
    )
    for committer in committers:
        committer.join(timeout=1)

    assert status == "pending"
    assert committed.is_set()
    assert fixture.service.get(fixture.scope).state == "authorized"


def test_rejected_budget_apply_recovery_continues_reconcile_after_generation_aba(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _started_with_budget_coordinator(tmp_path)
    stopped = _hard_budget_stop(fixture)
    command = _budget_grant_command(fixture, stopped, "generation-aba-recovery")
    budget_ops = fixture.service._budget
    original_apply = budget_ops.apply
    assert fixture.budget_coordinator is not None
    fixture.budget_coordinator.reconcile_interruptions_remaining = 1

    def advance_then_apply(apply_command: object) -> SessionMutationResult:
        assert fixture.budget_approvals is not None
        fixture.budget_approvals.advance_generation(command.approval_digest)
        return original_apply(cast(Any, apply_command))

    monkeypatch.setattr(budget_ops, "apply", advance_then_apply)
    with pytest.raises(RuntimeError, match="reconcile interruption"):
        fixture.service.extend_budget(command)
    monkeypatch.setattr(budget_ops, "apply", original_apply)

    recovered = fixture.service.get(fixture.scope)
    replay = fixture.service.get(fixture.scope)

    assert recovered == replay
    assert recovered.state == "needs_user"
    assert not recovered.budget_grant_ids
    assert recovered.reconciled_budget_grant_ids
    assert fixture.budget_coordinator.reconcile_calls == 2
    assert fixture.budget_approvals is not None
    approval_state = fixture.budget_approvals.approval_state(command.approval_digest)
    assert approval_state is not None
    assert approval_state.active


def test_recovery_does_not_apply_stale_session_decision(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _started_with_budget_coordinator(tmp_path)
    stopped = _hard_budget_stop(fixture)
    command = _budget_grant_command(fixture, stopped, "stale-decision-recovery")
    budget_ops = fixture.service._budget
    original_apply = budget_ops.apply

    def interrupt_after_decision(_command: object) -> SessionMutationResult:
        raise RuntimeError("simulated exit after decision")

    monkeypatch.setattr(budget_ops, "apply", interrupt_after_decision)
    with pytest.raises(RuntimeError, match="after decision"):
        fixture.service.extend_budget(command)
    assert fixture.budget_coordinator is not None
    application = next(iter(fixture.budget_coordinator.applications.values()))
    assert next(iter(fixture.budget_coordinator.decisions.values())).decision_kind == (
        "session_apply"
    )
    fixture.resolver.reservations.pop(application.reservation.reservation_digest)
    monkeypatch.setattr(budget_ops, "apply", original_apply)

    recovered = fixture.service.get(fixture.scope)

    assert recovered.state == "needs_user"
    assert not recovered.budget_grant_ids
    assert recovered.reconciled_budget_grant_ids == (application.grant.grant_id,)
    assert fixture.budget_coordinator.reconcile_calls == 1


def test_stale_session_apply_compensates_resource_without_overwriting_superseder(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _started_with_budget_coordinator(tmp_path)
    stopped = _hard_budget_stop(fixture)
    request = _budget_grant_command(fixture, stopped, "stale-session-apply")
    budget_ops = fixture.service._budget

    def supersede_then_conflict(_command: object) -> SessionMutationResult:
        event = next(
            item
            for item in fixture.service._store.load_events(fixture.scope)
            if item.command_id == request.command_id
            and item.event_kind == "budget_grant_requested"
        )
        budget_ops.fail(
            build_failure_command(
                request,
                event,
                BudgetGrantResourceError("superseded"),
            )
        )
        raise SessionCasConflictError("simulated concurrent superseder")

    monkeypatch.setattr(budget_ops, "apply", supersede_then_conflict)
    result = fixture.service.extend_budget(request)

    assert fixture.budget_coordinator is not None
    application = next(iter(fixture.budget_coordinator.applications.values()))
    reconcile_id = stable_id(
        "budget-grant-operation",
        application.grant.idempotency_key,
        "reconcile",
    )
    released = fixture.budget_coordinator.operations[
        reconcile_id
    ].target_event.reservation
    assert result.session.budget_grant_failure_code == "superseded"
    assert not result.session.pending_budget_grant_command_id
    assert fixture.budget_coordinator.reconcile_calls == 1
    assert application.grant.grant_id in released.reconciled_budget_grant_ids
    assert (
        released.reserved + application.grant.increment
        == application.reservation.reserved
    )


def test_unverifiable_resource_application_never_authorizes_session(
    tmp_path: Path,
) -> None:
    fixture = _started_with_budget_coordinator(tmp_path)
    stopped = _hard_budget_stop(fixture)
    assert fixture.budget_coordinator is not None
    fixture.budget_coordinator.corrupt_verification = True

    result = fixture.service.extend_budget(
        _budget_grant_command(fixture, stopped, "unverifiable")
    )

    assert result.session.state == "needs_user"
    assert result.session.reconciled_budget_grant_ids
    assert not result.session.budget_grant_ids
    assert fixture.budget_coordinator.reconcile_calls == 1


def test_deterministic_resource_failure_is_terminal_and_not_retried(
    tmp_path: Path,
) -> None:
    fixture = _started_with_budget_coordinator(tmp_path)
    stopped = _hard_budget_stop(fixture)
    assert fixture.budget_coordinator is not None
    fixture.budget_coordinator.terminal_failure_code = "capacity_exhausted"
    command = _budget_grant_command(fixture, stopped, "capacity")

    failed = fixture.service.extend_budget(command)
    calls = fixture.budget_coordinator.calls
    recovered = fixture.service.get(fixture.scope)
    replay = fixture.service.extend_budget(command)

    assert failed.session.state == "needs_user"
    assert failed.session.budget_grant_failure_code == "capacity_exhausted"
    assert not failed.session.pending_budget_grant_command_id
    assert recovered.session_digest == failed.session.session_digest
    assert replay.idempotent_replay
    assert fixture.budget_coordinator.calls == calls == 1
    assert tuple(
        event.event_kind
        for event in fixture.service.events(fixture.scope)
        if event.event_kind.startswith("budget_grant_")
    ) == ("budget_grant_requested", "budget_grant_failed")


def test_new_request_can_reuse_approval_after_terminal_capacity_failure(
    tmp_path: Path,
) -> None:
    fixture = _started_with_budget_coordinator(tmp_path)
    stopped = _hard_budget_stop(fixture)
    assert fixture.budget_coordinator is not None
    fixture.budget_coordinator.terminal_failure_code = "capacity_exhausted"
    first = _budget_grant_command(fixture, stopped, "approval-reuse")
    failed = fixture.service.extend_budget(first)
    fixture.budget_coordinator.terminal_failure_code = ""
    second = first.model_copy(
        update={
            "command_id": "budget-grant.approval-reuse.second",
            "idempotency_key": "budget-grant-key.approval-reuse.second",
            "expected_revision": failed.session.revision,
        }
    )

    recovered = fixture.service.extend_budget(second)
    proof_dir = (
        fixture.service.projection_path(fixture.scope).parent
        / "budget-grant-request-proofs"
    )

    assert recovered.session.state == "authorized"
    assert recovered.session.budget_revision == 1
    assert len(tuple(proof_dir.glob("*.json"))) == 2
    assert fixture.budget_coordinator.calls == 2


def test_integrity_reconciliation_failure_blocks_without_retry(
    tmp_path: Path,
) -> None:
    fixture = _started_with_budget_coordinator(tmp_path)
    stopped = _hard_budget_stop(fixture)
    assert fixture.budget_coordinator is not None
    fixture.budget_coordinator.revoke_after_apply = True
    fixture.budget_coordinator.reconcile_failure_code = "state_corrupt"
    command = _budget_grant_command(fixture, stopped, "reconcile-failure")

    failed = fixture.service.extend_budget(command)
    recovered = fixture.service.get(fixture.scope)

    assert failed.session.state == "blocked"
    assert failed.session.budget_resume_state is None
    assert failed.session.budget_grant_failure_code == "reconciliation_state_corrupt"
    assert recovered.session_digest == failed.session.session_digest
    assert fixture.budget_coordinator.reconcile_calls == 1


def test_clean_initial_seal_initializes_ledger_and_authorizes(tmp_path: Path) -> None:
    fixture = _started(tmp_path)
    session = fixture.service.get(fixture.scope)
    first = _submit(fixture, session.revision, "slot.security")
    sealed = _submit(fixture, first.session.revision, "slot.delivery")

    assert len(fixture.finding_writer.commands) == 1
    assert fixture.finding_writer.commands[0].findings == ()
    assert (
        sealed.session.finding_ledger_digest
        == fixture.finding_writer.last_ledger_digest
    )
    assert sealed.session.state == "authorized"


def test_review_pass_binds_validated_output_not_provider_request_payload(
    tmp_path: Path,
) -> None:
    fixture = _started(tmp_path)
    session = fixture.service.get(fixture.scope)
    command = _pass_command(fixture, session.revision, "slot.security")
    assignment, invocation, reservation = _review_authority(
        fixture, session, "slot.security", command
    )
    request_draft = invocation.request.model_copy(
        update={
            "request_digest": "sha256:provider-prompt-packet",
            "request_artifact_digest": "",
        }
    )
    request = ProviderInvocationRequest.model_validate(
        {
            **request_draft.model_dump(mode="json"),
            "request_artifact_digest": request_artifact_digest(request_draft),
        }
    )
    invocation_draft = invocation.model_copy(
        update={"request": request, "projection_digest": ""}
    )
    invocation = ProviderInvocation.model_validate(
        {
            **invocation_draft.model_dump(mode="json"),
            "projection_digest": projection_digest(invocation_draft),
        }
    )
    fixture.resolver.assignments[assignment.assignment_digest] = assignment
    fixture.resolver.invocations[invocation.invocation_id] = invocation
    fixture.resolver.reservations[reservation.reservation_digest] = reservation

    result = fixture.service.submit_pass(command)

    assert result.review_pass is not None
    assert result.review_pass.validation_digest == invocation.validation_digest


@pytest.mark.parametrize("interrupt_after", range(1, 8))
def test_partial_multi_event_operation_auto_resumes_before_read_or_next_command(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    interrupt_after: int,
) -> None:
    fixture = _started_with_role_gap(tmp_path)
    session = fixture.service.get(fixture.scope)
    plan, binding, _ = _replacement_authority(
        fixture,
        suffix="crash-gap",
        candidate_digest=CANDIDATE,
        usage=session.resource_usage + ResourceAmounts(role_replans=1),
    )
    command = RoleGapCommand(
        scope=fixture.scope,
        command_id="role-gap.crash",
        idempotency_key="role-gap-key.crash",
        expected_revision=session.revision,
        missing_capability_ids=("capability.new",),
        plan_digest=plan.plan_digest,
        binding_set_digest=binding.binding_set_digest,
    )
    original = fixture.service._store._append_event
    appended = 0

    def interrupt_after_first(event: SessionEvent) -> None:
        nonlocal appended
        original(event)
        appended += 1
        if appended == interrupt_after:
            raise RuntimeError("simulated process exit")

    monkeypatch.setattr(fixture.service._store, "_append_event", interrupt_after_first)
    with pytest.raises(RuntimeError, match="simulated"):
        fixture.service.handle_role_gap(command)
    monkeypatch.setattr(fixture.service._store, "_append_event", original)

    recovered = fixture.service.get(fixture.scope)
    assert recovered.role_replan_count(RISK_LINEAGE) == 1
    assert tuple(
        item.event_kind
        for item in fixture.service.events(fixture.scope)
        if item.command_id == command.command_id
    ) == (
        "role_gap_detected",
        "cohort_superseded",
        "old_passes_invalidated",
        "plan_resolution_requested",
        "panel_plan_frozen",
        "reviewer_bindings_validated",
        "new_cohort_activated",
    )
    progressed = fixture.service.record_progress(
        ProgressCommand(
            scope=fixture.scope,
            command_id="progress.after-recovery",
            idempotency_key="progress-key.after-recovery",
            expected_revision=recovered.revision,
            snapshot=_progress(p0=1, calls=1),
        )
    )
    assert progressed.session.revision == recovered.revision + 1


def test_lock_rechecks_pending_operation_before_persisting_a_peer_command(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _started(tmp_path)
    session = fixture.service.get(fixture.scope)
    owner_command = ProgressCommand(
        scope=fixture.scope,
        command_id="progress.concurrent-owner",
        idempotency_key="progress-key.concurrent-owner",
        expected_revision=session.revision,
        snapshot=_progress(p0=1, calls=1),
    )
    peer_command = ProgressCommand(
        scope=fixture.scope,
        command_id="progress.concurrent-peer",
        idempotency_key="progress-key.concurrent-peer",
        expected_revision=session.revision,
        snapshot=_progress(p0=2, calls=2),
    )
    peer = StageReviewSessionService(
        tmp_path,
        project_id=PROJECT,
        trust_resolver=fixture.resolver,
        finding_ledger_writer=fixture.finding_writer,
        clock=lambda: NOW,
    )
    owner_append = fixture.service._store._append_event
    peer_resume = peer._resume_pending

    def owner_exit(event: SessionEvent) -> None:
        raise RuntimeError(f"simulated owner exit: {event.event_kind}")

    def interleaved_resume(scope: FindingScope, incoming_id: str = "") -> None:
        peer_resume(scope, incoming_id)
        monkeypatch.setattr(fixture.service._store, "_append_event", owner_exit)
        with pytest.raises(RuntimeError, match="owner exit"):
            fixture.service.record_progress(owner_command)
        monkeypatch.setattr(
            fixture.service._store,
            "_append_event",
            owner_append,
        )

    monkeypatch.setattr(peer, "_resume_pending", interleaved_resume)

    with pytest.raises(SessionIntegrityError, match="requires recovery"):
        peer.record_progress(peer_command)

    recovered = fixture.service.get(fixture.scope)
    assert len(recovered.progress_records) == 1
    assert fixture.service._store.pending_operation(fixture.scope) is None


def test_rejected_command_identity_cannot_be_reused_after_trust_changes(
    tmp_path: Path,
) -> None:
    fixture = _started(tmp_path)
    session = fixture.service.get(fixture.scope)
    command = _pass_command(fixture, session.revision, "slot.security")

    with pytest.raises(SessionIntegrityError, match="assignment"):
        fixture.service.submit_pass(command)

    assignment, invocation, reservation = _review_authority(
        fixture,
        session,
        "slot.security",
        command,
    )
    fixture.resolver.assignments[assignment.assignment_digest] = assignment
    fixture.resolver.invocations[invocation.invocation_id] = invocation
    fixture.resolver.reservations[reservation.reservation_digest] = reservation

    with pytest.raises(SessionIntegrityError, match="rejected"):
        fixture.service.submit_pass(command)
    assert fixture.service.events(fixture.scope) == (
        fixture.service.events(fixture.scope)[0],
    )


def test_completed_historical_pass_and_first_role_gap_are_idempotent(
    tmp_path: Path,
) -> None:
    fixture = _started(tmp_path)
    initial = fixture.service.get(fixture.scope)
    first_command = _pass_command(fixture, initial.revision, "slot.security")
    assignment, invocation, reservation = _review_authority(
        fixture,
        initial,
        "slot.security",
        first_command,
    )
    fixture.resolver.assignments[assignment.assignment_digest] = assignment
    fixture.resolver.invocations[invocation.invocation_id] = invocation
    fixture.resolver.reservations[reservation.reservation_digest] = reservation
    first = fixture.service.submit_pass(first_command)
    sealed = _submit(fixture, first.session.revision, "slot.delivery")
    historical = fixture.service.submit_pass(first_command)
    assert historical.idempotent_replay
    assert historical.session.session_digest == sealed.session.session_digest

    gap_fixture = _started_with_role_gap(tmp_path / "role-gap-replay")
    gap_session = gap_fixture.service.get(gap_fixture.scope)
    plan, binding, _ = _replacement_authority(
        gap_fixture,
        suffix="gap-replay",
        candidate_digest=CANDIDATE,
        usage=gap_session.resource_usage + ResourceAmounts(role_replans=1),
    )
    command = RoleGapCommand(
        scope=gap_fixture.scope,
        command_id="role-gap.replay",
        idempotency_key="role-gap-key.replay",
        expected_revision=gap_session.revision,
        missing_capability_ids=("capability.new",),
        plan_digest=plan.plan_digest,
        binding_set_digest=binding.binding_set_digest,
    )
    first_gap = gap_fixture.service.handle_role_gap(command)
    replay = gap_fixture.service.handle_role_gap(command)
    assert replay.idempotent_replay
    assert replay.session.session_digest == first_gap.session.session_digest


@pytest.mark.parametrize("interrupt_after", (1, 2))
def test_two_event_initial_seal_recovers_at_every_commit_point(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    interrupt_after: int,
) -> None:
    fixture = _started(tmp_path)
    session = fixture.service.get(fixture.scope)
    first = _submit(fixture, session.revision, "slot.security")
    command = _pass_command(fixture, first.session.revision, "slot.delivery")
    assignment, invocation, reservation = _review_authority(
        fixture,
        first.session,
        "slot.delivery",
        command,
    )
    fixture.resolver.assignments[assignment.assignment_digest] = assignment
    fixture.resolver.invocations[invocation.invocation_id] = invocation
    fixture.resolver.reservations[reservation.reservation_digest] = reservation
    original = fixture.service._store._append_event
    appended = 0

    def interrupt(event: SessionEvent) -> None:
        nonlocal appended
        original(event)
        appended += 1
        if appended == interrupt_after:
            raise RuntimeError("simulated seal exit")

    monkeypatch.setattr(fixture.service._store, "_append_event", interrupt)
    with pytest.raises(RuntimeError, match="seal exit"):
        fixture.service.submit_pass(command)
    monkeypatch.setattr(fixture.service._store, "_append_event", original)

    recovered = fixture.service.get(fixture.scope)
    assert recovered.state == "authorized"
    assert recovered.finding_ledger_digest == fixture.finding_writer.last_ledger_digest
    assert len(fixture.finding_writer.commands) == 1
    assert tuple(
        item.event_kind
        for item in fixture.service.events(fixture.scope)
        if item.command_id == command.command_id
    ) == ("review_pass_committed", "initial_reviews_sealed")


@pytest.mark.parametrize("interrupt_after", range(1, 6))
def test_five_event_provider_rebind_recovers_at_every_commit_point(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    interrupt_after: int,
) -> None:
    fixture = _started(tmp_path)
    session = fixture.service.get(fixture.scope)
    binding, reservation = _binding_for(
        fixture,
        fixture.plan,
        candidate_digest=CANDIDATE,
        suffix="rebind-crash",
        usage=session.resource_usage
        + ResourceAmounts(provider_retries=1, binding_attempts=1),
    )
    fixture.resolver.bindings[binding.binding_set_digest] = binding
    fixture.resolver.reservations[reservation.reservation_digest] = reservation
    directive = _rebind_directive(
        session,
        binding,
        (fixture.binding_set.bindings[0].provider_id,),
    )
    fixture.resolver.rebind_directives[directive.directive_digest] = directive
    command = ProviderRebindCommand(
        scope=fixture.scope,
        command_id="provider-rebind.crash",
        idempotency_key="provider-rebind-key.crash",
        expected_revision=session.revision,
        binding_set_digest=binding.binding_set_digest,
        rebind_directive_digest=directive.directive_digest,
    )
    original = fixture.service._store._append_event
    appended = 0

    def interrupt(event: SessionEvent) -> None:
        nonlocal appended
        original(event)
        appended += 1
        if appended == interrupt_after:
            raise RuntimeError("simulated rebind exit")

    monkeypatch.setattr(fixture.service._store, "_append_event", interrupt)
    with pytest.raises(RuntimeError, match="rebind exit"):
        fixture.service.rebind_provider(command)
    monkeypatch.setattr(fixture.service._store, "_append_event", original)

    recovered = fixture.service.get(fixture.scope)
    assert recovered.active_binding_set_digest == binding.binding_set_digest
    assert tuple(
        item.event_kind
        for item in fixture.service.events(fixture.scope)
        if item.command_id == command.command_id
    ) == (
        "provider_rebind_required",
        "cohort_superseded",
        "old_passes_invalidated",
        "reviewer_bindings_validated",
        "new_cohort_activated",
    )


def test_role_gap_cannot_bypass_needs_user_or_claim_existing_coverage(
    tmp_path: Path,
) -> None:
    fixture = _started(tmp_path)
    session = fixture.service.get(fixture.scope)
    for index in range(3):
        session = fixture.service.record_progress(
            ProgressCommand(
                scope=fixture.scope,
                command_id=f"stop-progress.{index}",
                idempotency_key=f"stop-progress-key.{index}",
                expected_revision=session.revision,
                snapshot=_progress(p0=2, calls=index + 1),
            )
        ).session
    assert session.state == "needs_user"
    plan, binding, _ = _replacement_authority(
        fixture,
        suffix="stopped-gap",
        candidate_digest=CANDIDATE,
        usage=session.resource_usage + ResourceAmounts(role_replans=1),
    )
    before = fixture.service.events(fixture.scope)
    with pytest.raises(SessionIntegrityError, match="not active"):
        fixture.service.handle_role_gap(
            RoleGapCommand(
                scope=fixture.scope,
                command_id="role-gap.stopped",
                idempotency_key="role-gap-key.stopped",
                expected_revision=session.revision,
                missing_capability_ids=("capability.new",),
                plan_digest=plan.plan_digest,
                binding_set_digest=binding.binding_set_digest,
            )
        )
    assert fixture.service.events(fixture.scope) == before

    fresh = _started(tmp_path / "false-gap")
    current = fresh.service.get(fresh.scope)
    plan, binding, _ = _replacement_authority(
        fresh,
        suffix="false-gap",
        candidate_digest=CANDIDATE,
        usage=current.resource_usage + ResourceAmounts(role_replans=1),
    )
    with pytest.raises(SessionIntegrityError, match="risk profile"):
        fresh.service.handle_role_gap(
            RoleGapCommand(
                scope=fresh.scope,
                command_id="role-gap.false",
                idempotency_key="role-gap-key.false",
                expected_revision=current.revision,
                missing_capability_ids=("capability.security",),
                plan_digest=plan.plan_digest,
                binding_set_digest=binding.binding_set_digest,
            )
        )


def test_risk_enrichment_derives_covered_or_role_gap_from_trusted_profile(
    tmp_path: Path,
) -> None:
    fixture = _started(tmp_path)
    session = fixture.service.get(fixture.scope)
    enriched = _risk_profile(
        "new-capability",
        ("capability.delivery", "capability.new", "capability.security"),
    )
    fixture.resolver.risk_profiles[enriched.profile_digest] = enriched
    plan, binding, _ = _replacement_authority(
        fixture,
        suffix="risk-gap",
        candidate_digest=CANDIDATE,
        usage=session.resource_usage + ResourceAmounts(role_replans=1),
    )
    result = fixture.service.enrich_risk(
        RiskEnrichmentCommand(
            scope=fixture.scope,
            command_id="risk-enrichment.gap",
            idempotency_key="risk-enrichment-key.gap",
            expected_revision=session.revision,
            risk_profile_digest=enriched.profile_digest,
            plan_digest=plan.plan_digest,
            binding_set_digest=binding.binding_set_digest,
        )
    )
    assert result.session.active_risk_profile_digest == enriched.profile_digest
    assert result.session.role_replan_count(RISK_LINEAGE) == 1

    with pytest.raises(SessionIntegrityError, match="risk profile"):
        fixture.service.enrich_risk(
            RiskEnrichmentCommand(
                scope=fixture.scope,
                command_id="risk-enrichment.untrusted",
                idempotency_key="risk-enrichment-key.untrusted",
                expected_revision=result.session.revision,
                risk_profile_digest="sha256:untrusted-risk",
            )
        )


def test_covered_risk_enrichment_preserves_usage_after_a_review_pass(
    tmp_path: Path,
) -> None:
    fixture = _started(tmp_path)
    session = fixture.service.get(fixture.scope)
    first = _submit(fixture, session.revision, "slot.security")
    profile = _risk_profile(
        "covered-after-pass",
        ("capability.delivery", "capability.security"),
    )
    fixture.resolver.risk_profiles[profile.profile_digest] = profile

    enriched = fixture.service.enrich_risk(
        RiskEnrichmentCommand(
            scope=fixture.scope,
            command_id="risk-enrichment.covered-after-pass",
            idempotency_key="risk-enrichment-key.covered-after-pass",
            expected_revision=first.session.revision,
            risk_profile_digest=profile.profile_digest,
        )
    )

    assert enriched.session.active_risk_profile_digest == profile.profile_digest
    assert enriched.session.resource_usage == first.session.resource_usage
    assert enriched.session.state == "collecting_initial_reviews"


@pytest.mark.parametrize("coverage_kind,event_count", (("covered", 4), ("gap", 8)))
def test_risk_enrichment_recovers_at_every_commit_point(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    coverage_kind: str,
    event_count: int,
) -> None:
    for interrupt_after in range(1, event_count + 1):
        case_root = tmp_path / f"{coverage_kind}-{interrupt_after}"
        fixture = _started(case_root)
        session = fixture.service.get(fixture.scope)
        capabilities = ["capability.delivery", "capability.security"]
        plan_digest = ""
        binding_set_digest = ""
        if coverage_kind == "gap":
            capabilities.append("capability.new")
            plan, binding, _ = _replacement_authority(
                fixture,
                suffix=f"risk-crash-{interrupt_after}",
                candidate_digest=CANDIDATE,
                usage=session.resource_usage + ResourceAmounts(role_replans=1),
            )
            plan_digest = plan.plan_digest
            binding_set_digest = binding.binding_set_digest
        profile = _risk_profile(
            f"risk-crash-{coverage_kind}-{interrupt_after}",
            tuple(capabilities),
        )
        fixture.resolver.risk_profiles[profile.profile_digest] = profile
        command = RiskEnrichmentCommand(
            scope=fixture.scope,
            command_id=f"risk-enrichment.crash.{interrupt_after}",
            idempotency_key=f"risk-enrichment-key.crash.{interrupt_after}",
            expected_revision=session.revision,
            risk_profile_digest=profile.profile_digest,
            plan_digest=plan_digest,
            binding_set_digest=binding_set_digest,
        )
        original = fixture.service._store._append_event
        appended = 0

        def interrupt(
            event: SessionEvent,
            append_event: Callable[[SessionEvent], None] = original,
            target: int = interrupt_after,
        ) -> None:
            nonlocal appended
            append_event(event)
            appended += 1
            if appended == target:
                raise RuntimeError("simulated risk exit")

        monkeypatch.setattr(fixture.service._store, "_append_event", interrupt)
        with pytest.raises(RuntimeError, match="risk exit"):
            fixture.service.enrich_risk(command)
        monkeypatch.setattr(fixture.service._store, "_append_event", original)

        recovered = fixture.service.get(fixture.scope)
        assert recovered.active_risk_profile_digest == profile.profile_digest
        expected_replans = 1 if coverage_kind == "gap" else 0
        assert recovered.role_replan_count(RISK_LINEAGE) == expected_replans


def test_rebind_directive_is_verified_before_any_session_event(tmp_path: Path) -> None:
    fixture = _started(tmp_path)
    session = fixture.service.get(fixture.scope)
    before = fixture.service.events(fixture.scope)
    same = _rebind_directive(
        session,
        fixture.binding_set,
        (fixture.binding_set.bindings[0].provider_id,),
    )
    fixture.resolver.rebind_directives[same.directive_digest] = same
    with pytest.raises(SessionIntegrityError, match="must change binding"):
        fixture.service.rebind_provider(
            ProviderRebindCommand(
                scope=fixture.scope,
                command_id="provider-rebind.same",
                idempotency_key="provider-rebind-key.same",
                expected_revision=session.revision,
                binding_set_digest=fixture.binding_set.binding_set_digest,
                rebind_directive_digest=same.directive_digest,
            )
        )
    assert fixture.service.events(fixture.scope) == before

    unchanged_cost, reservation = _binding_for(
        fixture,
        fixture.plan,
        candidate_digest=CANDIDATE,
        suffix="free-rebind",
        usage=session.resource_usage,
    )
    fixture.resolver.bindings[unchanged_cost.binding_set_digest] = unchanged_cost
    fixture.resolver.reservations[reservation.reservation_digest] = reservation
    directive = _rebind_directive(
        session,
        unchanged_cost,
        (fixture.binding_set.bindings[0].provider_id,),
    )
    fixture.resolver.rebind_directives[directive.directive_digest] = directive
    with pytest.raises(SessionIntegrityError, match="provider_retries"):
        fixture.service.rebind_provider(
            ProviderRebindCommand(
                scope=fixture.scope,
                command_id="provider-rebind.free",
                idempotency_key="provider-rebind-key.free",
                expected_revision=session.revision,
                binding_set_digest=unchanged_cost.binding_set_digest,
                rebind_directive_digest=directive.directive_digest,
            )
        )
    assert fixture.service.events(fixture.scope) == before

    retained, charged = _binding_for(
        fixture,
        fixture.plan,
        candidate_digest=CANDIDATE,
        suffix="retained-unavailable",
        usage=session.resource_usage + ResourceAmounts(binding_attempts=1),
    )
    fixture.resolver.bindings[retained.binding_set_digest] = retained
    fixture.resolver.reservations[charged.reservation_digest] = charged
    retained_directive = _rebind_directive(
        session,
        retained,
        (
            fixture.binding_set.bindings[0].provider_id,
            retained.bindings[0].provider_id,
        ),
    )
    fixture.resolver.rebind_directives[retained_directive.directive_digest] = (
        retained_directive
    )
    with pytest.raises(SessionIntegrityError, match="retained"):
        fixture.service.rebind_provider(
            ProviderRebindCommand(
                scope=fixture.scope,
                command_id="provider-rebind.retained",
                idempotency_key="provider-rebind-key.retained",
                expected_revision=session.revision,
                binding_set_digest=retained.binding_set_digest,
                rebind_directive_digest=retained_directive.directive_digest,
            )
        )
    assert fixture.service.events(fixture.scope) == before


def test_provider_rebind_at_hard_budget_enters_needs_user(tmp_path: Path) -> None:
    fixture = _started(tmp_path)
    session = fixture.service.get(fixture.scope)
    usage = session.resource_usage + ResourceAmounts(
        provider_retries=1,
        binding_attempts=1,
    )
    hard = usage + ResourceAmounts(slots=1)
    reservation = _reservation(
        usage=usage,
        hard=hard,
        revision=fixture.reservation.revision + 40,
        last_operation_id="resource-binding.hard-rebind",
    )
    binding = _binding_set(
        fixture.plan,
        reservation,
        CANDIDATE,
        suffix="hard-rebind",
    )
    binding = _binding_with_previous(binding, session.active_binding_set_digest)
    fixture.resolver.bindings[binding.binding_set_digest] = binding
    fixture.resolver.reservations[reservation.reservation_digest] = reservation
    directive = _rebind_directive(
        session,
        binding,
        (fixture.binding_set.bindings[0].provider_id,),
    )
    fixture.resolver.rebind_directives[directive.directive_digest] = directive
    result = fixture.service.rebind_provider(
        ProviderRebindCommand(
            scope=fixture.scope,
            command_id="provider-rebind.hard",
            idempotency_key="provider-rebind-key.hard",
            expected_revision=session.revision,
            binding_set_digest=binding.binding_set_digest,
            rebind_directive_digest=directive.directive_digest,
        )
    )
    assert result.session.state == "needs_user"


def test_plan_revocation_requires_trusted_authority_and_matching_target(
    tmp_path: Path,
) -> None:
    fixture = _started(tmp_path)
    session = fixture.service.get(fixture.scope)
    untrusted = ReviewerPlanRevocation(
        revocation_id="reviewer-plan-revocation.untrusted",
        target_kind="plan",
        plan_digest=fixture.plan.plan_digest,
        profile_ids=(),
        capability_ids=(),
        reason_id="governance.withdrawn",
        evidence_digest="sha256:revocation-evidence.untrusted",
        issuer_id="governance.release-authority",
        issuer_authority_digest="sha256:governance-authority.1",
        replacement_version="",
        minimum_version="1.0.1",
        issued_at=NOW,
    )
    with pytest.raises(SessionIntegrityError, match="trusted"):
        fixture.service.revoke_plan(
            PlanRevocationCommand(
                scope=fixture.scope,
                command_id="plan-revocation.untrusted",
                idempotency_key="plan-revocation-key.untrusted",
                expected_revision=session.revision,
                revocation_digest=untrusted.revocation_digest,
            )
        )

    other = untrusted.model_copy(
        update={
            "revocation_id": "reviewer-plan-revocation.other",
            "plan_digest": "sha256:other-plan",
            "revocation_digest": "",
        }
    )
    other = ReviewerPlanRevocation.model_validate(other.model_dump(mode="json"))
    fixture.resolver.revocations[other.revocation_digest] = other
    with pytest.raises(SessionIntegrityError, match="target"):
        fixture.service.revoke_plan(
            PlanRevocationCommand(
                scope=fixture.scope,
                command_id="plan-revocation.other",
                idempotency_key="plan-revocation-key.other",
                expected_revision=session.revision,
                revocation_digest=other.revocation_digest,
            )
        )


def test_session_event_codec_reads_previous_major_and_rejects_future(
    tmp_path: Path,
) -> None:
    fixture = _started(tmp_path)
    event_path = next(
        fixture.service.projection_path(fixture.scope).parent.glob("events/*.json")
    )
    payload = json.loads(event_path.read_text(encoding="utf-8"))
    payload["schema_version"] = "stage-review-session-event.v0"
    payload.pop("canonicalization_version")
    payload.pop("compatibility_mode")
    payload.pop("extensions")
    payload["event_digest"] = canonical_digest(
        {key: value for key, value in payload.items() if key != "event_digest"},
        CanonicalizationPolicy(),
    )
    event_path.write_text(json.dumps(payload), encoding="utf-8")
    fixture.service.projection_path(fixture.scope).unlink()
    assert fixture.service.get(fixture.scope).revision == 1

    payload["schema_version"] = "stage-review-session-event.v99"
    payload["event_digest"] = canonical_digest(
        {key: value for key, value in payload.items() if key != "event_digest"},
        CanonicalizationPolicy(),
    )
    event_path.write_text(json.dumps(payload), encoding="utf-8")
    fixture.service.projection_path(fixture.scope).unlink()
    with pytest.raises(SessionIntegrityError, match="schema"):
        fixture.service.get(fixture.scope)


def test_zero_event_seal_operation_recovers_ledger_before_other_commands(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _started(tmp_path)
    session = fixture.service.get(fixture.scope)
    first = _submit(fixture, session.revision, "slot.security")
    command = _pass_command(
        fixture,
        first.session.revision,
        "slot.delivery",
    )
    assignment, invocation, reservation = _review_authority(
        fixture,
        first.session,
        "slot.delivery",
        command,
    )
    fixture.resolver.assignments[assignment.assignment_digest] = assignment
    fixture.resolver.invocations[invocation.invocation_id] = invocation
    fixture.resolver.reservations[reservation.reservation_digest] = reservation
    original = fixture.service._store._append_event

    def interrupt_before_first_event(event: SessionEvent) -> None:
        raise RuntimeError(f"simulated zero-event exit: {event.event_kind}")

    monkeypatch.setattr(
        fixture.service._store,
        "_append_event",
        interrupt_before_first_event,
    )
    with pytest.raises(RuntimeError, match="zero-event"):
        fixture.service.submit_pass(command)
    assert len(fixture.finding_writer.commands) == 1
    monkeypatch.setattr(fixture.service._store, "_append_event", original)

    recovered = fixture.service.get(fixture.scope)
    assert recovered.finding_ledger_digest == fixture.finding_writer.last_ledger_digest
    assert recovered.state == "authorized"
    assert fixture.service._store.pending_operation(fixture.scope) is None


def test_zero_event_start_operation_recovers_before_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture, risk_profile = _unstarted(tmp_path)
    command = _start_command(fixture, risk_profile, suffix="zero-event")
    original = fixture.service._store._append_event

    def interrupt_before_first_event(event: SessionEvent) -> None:
        raise RuntimeError(f"simulated start exit: {event.event_kind}")

    monkeypatch.setattr(
        fixture.service._store,
        "_append_event",
        interrupt_before_first_event,
    )
    with pytest.raises(RuntimeError, match="start exit"):
        fixture.service.start(command)
    monkeypatch.setattr(fixture.service._store, "_append_event", original)

    recovered = fixture.service.get(fixture.scope)

    assert recovered.revision == 1
    assert fixture.service._store.pending_operation(fixture.scope) is None


def test_operation_without_pointer_is_discovered_and_recovered(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture, risk_profile = _unstarted(tmp_path)
    command = _start_command(fixture, risk_profile, suffix="orphan-operation")
    original = session_operation_pointer.claim_operation

    def interrupt_before_claim(path: Path, operation: object) -> None:
        raise RuntimeError(f"simulated claim exit: {operation!r}")

    monkeypatch.setattr(
        session_operation_pointer,
        "claim_operation",
        interrupt_before_claim,
    )
    with pytest.raises(RuntimeError, match="claim exit"):
        fixture.service.start(command)
    monkeypatch.setattr(session_operation_pointer, "claim_operation", original)

    recovered = fixture.service.get(fixture.scope)

    assert recovered.revision == 1
    assert fixture.service._store.pending_operation(fixture.scope) is None


def test_effects_started_operation_recovers_when_pointer_is_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture, risk_profile = _unstarted(tmp_path)
    command = _start_command(fixture, risk_profile, suffix="missing-pointer")
    original = fixture.service._store._append_event

    def interrupt_before_first_event(event: SessionEvent) -> None:
        raise RuntimeError(f"simulated effects exit: {event.event_kind}")

    monkeypatch.setattr(
        fixture.service._store,
        "_append_event",
        interrupt_before_first_event,
    )
    with pytest.raises(RuntimeError, match="effects exit"):
        fixture.service.start(command)
    fixture.service._store._operation_pointer_path(fixture.scope).unlink()
    monkeypatch.setattr(fixture.service._store, "_append_event", original)

    recovered = fixture.service.get(fixture.scope)

    assert recovered.revision == 1
    assert fixture.service._store.pending_operation(fixture.scope) is None


def test_candidate_update_is_rejected_until_initial_barrier_is_sealed(
    tmp_path: Path,
) -> None:
    fixture = _started(tmp_path)
    session = fixture.service.get(fixture.scope)
    first = _submit(
        fixture,
        session.revision,
        "slot.security",
        findings=(_finding(),),
    )
    binding, reservation = _binding_for(
        fixture,
        fixture.plan,
        candidate_digest="sha256:candidate.before-seal",
        suffix="candidate-before-seal",
        usage=first.session.resource_usage,
    )
    fixture.resolver.bindings[binding.binding_set_digest] = binding
    fixture.resolver.reservations[reservation.reservation_digest] = reservation

    with pytest.raises(SessionIntegrityError, match="initial review"):
        fixture.service.update_candidate(
            CandidateUpdateCommand(
                scope=fixture.scope,
                command_id="candidate-update.before-seal",
                idempotency_key="candidate-update-key.before-seal",
                expected_revision=first.session.revision,
                candidate_digest="sha256:candidate.before-seal",
                binding_set_digest=binding.binding_set_digest,
            )
        )
    assert first.session.state == "collecting_initial_reviews"
    assert fixture.service.get(fixture.scope).active_candidate_digest == CANDIDATE


def test_clean_rereview_seal_uses_current_ledger_and_authorizes(
    tmp_path: Path,
) -> None:
    fixture = _started(tmp_path)
    session = _seal_initial(fixture)
    binding, reservation = _binding_for(
        fixture,
        fixture.plan,
        candidate_digest="sha256:candidate.rereview",
        suffix="candidate-rereview",
        usage=session.resource_usage,
    )
    fixture.resolver.bindings[binding.binding_set_digest] = binding
    fixture.resolver.reservations[reservation.reservation_digest] = reservation
    session = fixture.service.update_candidate(
        CandidateUpdateCommand(
            scope=fixture.scope,
            command_id="candidate-update.rereview",
            idempotency_key="candidate-update-key.rereview",
            expected_revision=session.revision,
            candidate_digest="sha256:candidate.rereview",
            binding_set_digest=binding.binding_set_digest,
        )
    ).session
    fixture.finding_writer.current_ledger = _ledger_snapshot(
        fixture,
        candidate_digest=session.active_candidate_digest,
        plan_digest=session.active_plan_digest,
        binding_set_digest=session.active_binding_set_digest,
    )
    first = _submit(fixture, session.revision, "slot.security")
    sealed = _submit(fixture, first.session.revision, "slot.delivery")

    assert sealed.session.state == "authorized"
    assert (
        sealed.session.finding_ledger_digest
        == fixture.finding_writer.current_ledger.ledger_digest
    )
    assert (
        fixture.finding_writer.current_ledger.cohort_id
        == sealed.session.active_cohort_id
    )
    assert (
        fixture.finding_writer.current_ledger.lineage_contract_version == "explicit-v2"
    )


def test_provider_rebind_advances_the_unique_ledger_before_rereview_seal(
    tmp_path: Path,
) -> None:
    fixture = _started(tmp_path)
    initial = fixture.service.get(fixture.scope)
    first_initial = _submit(fixture, initial.revision, "slot.security")
    initial_result = _submit(
        fixture,
        first_initial.session.revision,
        "slot.delivery",
    )
    assert initial_result.initial_review_seal is not None
    session = initial_result.session
    finding_service, trust = _attach_real_finding_ledger(
        fixture,
        tmp_path,
        initial_result.initial_review_seal,
    )
    binding, reservation = _binding_for(
        fixture,
        fixture.plan,
        candidate_digest=CANDIDATE,
        suffix="rebind-ledger-lineage",
        usage=session.resource_usage
        + ResourceAmounts(provider_retries=1, binding_attempts=1),
    )
    fixture.resolver.bindings[binding.binding_set_digest] = binding
    fixture.resolver.reservations[reservation.reservation_digest] = reservation
    directive = _rebind_directive(
        session,
        binding,
        (fixture.binding_set.bindings[0].provider_id,),
    )
    fixture.resolver.rebind_directives[directive.directive_digest] = directive
    rebound = fixture.service.rebind_provider(
        ProviderRebindCommand(
            scope=fixture.scope,
            command_id="provider-rebind.ledger-lineage",
            idempotency_key="provider-rebind-key.ledger-lineage",
            expected_revision=session.revision,
            binding_set_digest=binding.binding_set_digest,
            rebind_directive_digest=directive.directive_digest,
        )
    ).session
    _trust_active_lineage(fixture, trust, rebound)

    first = _submit(fixture, rebound.revision, "slot.security")
    sealed = _submit(fixture, first.session.revision, "slot.delivery")

    assert sealed.session.state == "authorized"
    ledger = finding_service.read(fixture.scope)
    assert ledger.binding_set_digest == rebound.active_binding_set_digest
    assert ledger.revision == 2


def test_role_replan_advances_the_unique_ledger_before_rereview_seal(
    tmp_path: Path,
) -> None:
    fixture = _started(tmp_path)
    initial = fixture.service.get(fixture.scope)
    first_initial = _submit(fixture, initial.revision, "slot.security")
    initial_result = _submit(
        fixture,
        first_initial.session.revision,
        "slot.delivery",
    )
    assert initial_result.initial_review_seal is not None
    session = initial_result.session
    finding_service, trust = _attach_real_finding_ledger(
        fixture,
        tmp_path,
        initial_result.initial_review_seal,
    )
    profile = _risk_profile(
        "role-replan-ledger-lineage",
        ("capability.delivery", "capability.new", "capability.security"),
    )
    fixture.resolver.risk_profiles[profile.profile_digest] = profile
    plan, binding, _ = _replacement_authority(
        fixture,
        suffix="role-replan-ledger-lineage",
        candidate_digest=CANDIDATE,
        usage=session.resource_usage + ResourceAmounts(role_replans=1),
    )
    replanned = fixture.service.enrich_risk(
        RiskEnrichmentCommand(
            scope=fixture.scope,
            command_id="risk-enrichment.role-replan-ledger-lineage",
            idempotency_key="risk-enrichment-key.role-replan-ledger-lineage",
            expected_revision=session.revision,
            risk_profile_digest=profile.profile_digest,
            plan_digest=plan.plan_digest,
            binding_set_digest=binding.binding_set_digest,
        )
    ).session
    _trust_active_lineage(fixture, trust, replanned)

    first = _submit(fixture, replanned.revision, "slot.security")
    sealed = _submit(fixture, first.session.revision, "slot.delivery")

    assert sealed.session.state == "authorized"
    ledger = finding_service.read(fixture.scope)
    assert ledger.plan_digest == replanned.active_plan_digest
    assert ledger.revision == 2


def test_rereview_finding_must_already_exist_in_authoritative_ledger(
    tmp_path: Path,
) -> None:
    fixture = _started(tmp_path)
    session = _seal_initial(fixture)
    binding, reservation = _binding_for(
        fixture,
        fixture.plan,
        candidate_digest="sha256:candidate.missing-ledger-finding",
        suffix="candidate-missing-ledger-finding",
        usage=session.resource_usage,
    )
    fixture.resolver.bindings[binding.binding_set_digest] = binding
    fixture.resolver.reservations[reservation.reservation_digest] = reservation
    session = fixture.service.update_candidate(
        CandidateUpdateCommand(
            scope=fixture.scope,
            command_id="candidate-update.missing-ledger-finding",
            idempotency_key="candidate-update-key.missing-ledger-finding",
            expected_revision=session.revision,
            candidate_digest="sha256:candidate.missing-ledger-finding",
            binding_set_digest=binding.binding_set_digest,
        )
    ).session
    fixture.finding_writer.current_ledger = _ledger_snapshot(
        fixture,
        candidate_digest=session.active_candidate_digest,
        plan_digest=session.active_plan_digest,
        binding_set_digest=session.active_binding_set_digest,
    )
    reviewer = next(
        item
        for item in fixture.service.active_cohort(fixture.scope).reviewers
        if item.slot_id == "slot.security"
    )
    finding = _finding().model_copy(
        update={
            "actor_id": reviewer.actor_id,
            "slot_id": reviewer.slot_id,
            "capability_id": "capability.security",
        }
    )
    first = _submit(
        fixture,
        session.revision,
        "slot.security",
        findings=(finding,),
    )

    with pytest.raises(SessionIntegrityError, match="ledger"):
        _submit(fixture, first.session.revision, "slot.delivery")


def test_rereview_cannot_reuse_a_resolved_ledger_record_as_a_new_finding(
    tmp_path: Path,
) -> None:
    fixture = _started(tmp_path)
    session = _seal_initial(fixture)
    candidate = "sha256:candidate.regressed-finding"
    binding, reservation = _binding_for(
        fixture,
        fixture.plan,
        candidate_digest=candidate,
        suffix="candidate-regressed-finding",
        usage=session.resource_usage,
    )
    fixture.resolver.bindings[binding.binding_set_digest] = binding
    fixture.resolver.reservations[reservation.reservation_digest] = reservation
    session = fixture.service.update_candidate(
        CandidateUpdateCommand(
            scope=fixture.scope,
            command_id="candidate-update.regressed-finding",
            idempotency_key="candidate-update-key.regressed-finding",
            expected_revision=session.revision,
            candidate_digest=candidate,
            binding_set_digest=binding.binding_set_digest,
        )
    ).session
    draft = _finding()
    resolved = FindingRecord(
        finding_key=stable_id("finding", draft.identity.identity_digest),
        identity_digest=draft.identity.identity_digest,
        category=draft.identity.category,
        severity=draft.severity,
        state="verified",
        disposition="blocking",
        blocking=False,
        candidate_digest=candidate,
        evidence_bundle_digests=(draft.evidence_bundle_digest,),
    )
    fixture.finding_writer.current_ledger = _ledger_snapshot(
        fixture,
        candidate_digest=candidate,
        plan_digest=session.active_plan_digest,
        binding_set_digest=session.active_binding_set_digest,
        records=(resolved,),
    )
    reviewer = next(
        item
        for item in fixture.service.active_cohort(fixture.scope).reviewers
        if item.slot_id == "slot.security"
    )
    repeated = draft.model_copy(
        update={
            "actor_id": reviewer.actor_id,
            "slot_id": reviewer.slot_id,
            "capability_id": "capability.security",
        }
    )
    first = _submit(
        fixture,
        session.revision,
        "slot.security",
        findings=(repeated,),
    )

    with pytest.raises(SessionIntegrityError, match="resolved"):
        _submit(fixture, first.session.revision, "slot.delivery")


def test_start_routes_uncovered_risk_profile_to_replanning(
    tmp_path: Path,
) -> None:
    fixture, risk_profile = _unstarted(
        tmp_path,
        risk_capabilities=(
            "capability.delivery",
            "capability.privacy",
            "capability.security",
        ),
    )
    started = fixture.service.start(
        SessionStartCommand(
            scope=fixture.scope,
            command_id="session-start.uncovered-risk",
            idempotency_key="session-start-key.uncovered-risk",
            expected_revision=0,
            candidate_digest=CANDIDATE,
            risk_profile_digest=risk_profile.profile_digest,
            risk_profile_lineage_id=RISK_LINEAGE,
            policy_digest=POLICY,
            optimization_snapshot_digest=SNAPSHOT,
            plan_digest=fixture.plan.plan_digest,
            binding_set_digest=fixture.binding_set.binding_set_digest,
        )
    )

    assert started.session.state == "replanning"
    assert started.session.pending_role_gap_capability_ids == ("capability.privacy",)


def test_start_rejects_unregistered_binding_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        session_authority,
        "_validate_binding_authority_snapshot",
        binding_authority_validation._validate_binding_authority_snapshot,
    )
    fixture, risk = _unstarted(tmp_path)

    with pytest.raises(SessionIntegrityError, match="binding authority is invalid"):
        _start_fixture(fixture, risk, suffix="unregistered-authority")

    assert fixture.service.events(fixture.scope) == ()


def test_direct_role_gap_must_equal_active_risk_profile_gap(tmp_path: Path) -> None:
    fixture = _started(tmp_path)
    session = fixture.service.get(fixture.scope)
    plan, binding, _ = _replacement_authority(
        fixture,
        suffix="untrusted-direct-gap",
        candidate_digest=CANDIDATE,
        usage=session.resource_usage + ResourceAmounts(role_replans=1),
    )

    with pytest.raises(SessionIntegrityError, match="risk profile"):
        fixture.service.handle_role_gap(
            RoleGapCommand(
                scope=fixture.scope,
                command_id="role-gap.untrusted-direct",
                idempotency_key="role-gap-key.untrusted-direct",
                expected_revision=session.revision,
                missing_capability_ids=("capability.new",),
                plan_digest=plan.plan_digest,
                binding_set_digest=binding.binding_set_digest,
            )
        )


def test_second_risk_enrichment_persists_profile_before_needs_user(
    tmp_path: Path,
) -> None:
    fixture = _started(tmp_path)
    session = fixture.service.get(fixture.scope)
    first_profile = _risk_profile(
        "first-gap",
        ("capability.delivery", "capability.new", "capability.security"),
    )
    fixture.resolver.risk_profiles[first_profile.profile_digest] = first_profile
    plan, binding, _ = _replacement_authority(
        fixture,
        suffix="first-risk-gap",
        candidate_digest=CANDIDATE,
        usage=session.resource_usage + ResourceAmounts(role_replans=1),
    )
    session = fixture.service.enrich_risk(
        RiskEnrichmentCommand(
            scope=fixture.scope,
            command_id="risk-enrichment.first-gap",
            idempotency_key="risk-enrichment-key.first-gap",
            expected_revision=session.revision,
            risk_profile_digest=first_profile.profile_digest,
            plan_digest=plan.plan_digest,
            binding_set_digest=binding.binding_set_digest,
        )
    ).session
    old_cohort = session.active_cohort_id
    later_profile = _risk_profile(
        "second-gap",
        (
            "capability.delivery",
            "capability.later",
            "capability.new",
            "capability.security",
        ),
    )
    fixture.resolver.risk_profiles[later_profile.profile_digest] = later_profile
    result = fixture.service.enrich_risk(
        RiskEnrichmentCommand(
            scope=fixture.scope,
            command_id="risk-enrichment.second-gap",
            idempotency_key="risk-enrichment-key.second-gap",
            expected_revision=session.revision,
            risk_profile_digest=later_profile.profile_digest,
        )
    )

    assert result.session.state == "needs_user"
    assert result.session.active_risk_profile_digest == later_profile.profile_digest
    assert old_cohort in result.session.superseded_cohort_ids
    assert tuple(
        item.event_kind
        for item in fixture.service.events(fixture.scope)
        if item.command_id == "risk-enrichment.second-gap"
    ) == (
        "risk_fact_enriched",
        "cohort_superseded",
        "old_passes_invalidated",
        "user_decision_required",
    )


def test_historical_macro_replay_returns_its_own_request(tmp_path: Path) -> None:
    fixture = _started(tmp_path)
    session = fixture.service.get(fixture.scope)
    first_command = MacroRebaselineCommand(
        scope=fixture.scope,
        command_id="macro-rebaseline.history-a",
        idempotency_key="macro-rebaseline-key.history-a",
        expected_revision=session.revision,
        change_kind="architecture_change",
        evidence_digest="sha256:architecture-evidence.a",
    )
    first = fixture.service.request_macro_rebaseline(first_command)
    second = fixture.service.request_macro_rebaseline(
        MacroRebaselineCommand(
            scope=fixture.scope,
            command_id="macro-rebaseline.history-b",
            idempotency_key="macro-rebaseline-key.history-b",
            expected_revision=first.session.revision,
            change_kind="requirements_change",
            evidence_digest="sha256:requirements-evidence.b",
        )
    )
    replay = fixture.service.request_macro_rebaseline(first_command)

    assert replay.idempotent_replay
    assert replay.session.session_digest == second.session.session_digest
    assert replay.macro_rebaseline_request == first.macro_rebaseline_request


def test_previous_operation_without_current_command_payload_is_read_only(
    tmp_path: Path,
) -> None:
    fixture = _started(tmp_path)
    operation_path = next(
        fixture.service.projection_path(fixture.scope).parent.glob("operations/*.json")
    )
    payload = json.loads(operation_path.read_text(encoding="utf-8"))
    payload["schema_version"] = "stage-review-operation.v0"
    payload.pop("canonicalization_version")
    payload.pop("compatibility_mode")
    payload.pop("extensions")
    payload.pop("command_type")
    payload.pop("command_payload")
    payload["operation_digest"] = canonical_digest(
        {key: value for key, value in payload.items() if key != "operation_digest"},
        CanonicalizationPolicy(),
    )
    operation_path.write_text(json.dumps(payload), encoding="utf-8")

    assert fixture.service.get(fixture.scope).revision == 1


def test_provider_rebind_requires_retry_and_binding_budget(tmp_path: Path) -> None:
    fixture = _started(tmp_path)
    session = fixture.service.get(fixture.scope)
    binding, reservation = _binding_for(
        fixture,
        fixture.plan,
        candidate_digest=CANDIDATE,
        suffix="binding-only-charge",
        usage=session.resource_usage + ResourceAmounts(binding_attempts=1),
    )
    fixture.resolver.bindings[binding.binding_set_digest] = binding
    fixture.resolver.reservations[reservation.reservation_digest] = reservation
    directive = _rebind_directive(
        session,
        binding,
        (fixture.binding_set.bindings[0].provider_id,),
    )
    fixture.resolver.rebind_directives[directive.directive_digest] = directive
    before = fixture.service.events(fixture.scope)

    with pytest.raises(SessionIntegrityError, match="provider_retries"):
        fixture.service.rebind_provider(
            ProviderRebindCommand(
                scope=fixture.scope,
                command_id="provider-rebind.binding-only-charge",
                idempotency_key="provider-rebind-key.binding-only-charge",
                expected_revision=session.revision,
                binding_set_digest=binding.binding_set_digest,
                rebind_directive_digest=directive.directive_digest,
            )
        )
    assert fixture.service.events(fixture.scope) == before


def test_provider_rebind_cannot_clear_an_unresolved_role_gap(tmp_path: Path) -> None:
    fixture = _started_with_role_gap(tmp_path)
    session = fixture.service.get(fixture.scope)
    binding, reservation = _binding_for(
        fixture,
        fixture.plan,
        candidate_digest=CANDIDATE,
        suffix="rebind-during-role-gap",
        usage=session.resource_usage
        + ResourceAmounts(provider_retries=1, binding_attempts=1),
    )
    fixture.resolver.bindings[binding.binding_set_digest] = binding
    fixture.resolver.reservations[reservation.reservation_digest] = reservation
    directive = _rebind_directive(
        session,
        binding,
        (fixture.binding_set.bindings[0].provider_id,),
    )
    fixture.resolver.rebind_directives[directive.directive_digest] = directive

    with pytest.raises(SessionIntegrityError, match="role gap"):
        fixture.service.rebind_provider(
            ProviderRebindCommand(
                scope=fixture.scope,
                command_id="provider-rebind.during-role-gap",
                idempotency_key="provider-rebind-key.during-role-gap",
                expected_revision=session.revision,
                binding_set_digest=binding.binding_set_digest,
                rebind_directive_digest=directive.directive_digest,
            )
        )


def _attach_real_finding_ledger(
    fixture: _Fixture,
    root: Path,
    seal: InitialReviewSeal,
) -> tuple[FindingLedgerService, _SessionFindingTrustResolver]:
    initial_command = fixture.finding_writer.commands[0]
    context = FindingTrustContext(
        scope=fixture.scope,
        candidate_digest=initial_command.candidate_digest,
        policy_digest=initial_command.policy_digest,
        plan_digest=initial_command.plan_digest,
        binding_set_digest=initial_command.binding_set_digest,
        cohort_id=seal.initial_cohort_id,
        reviewer_engine_version="stage-review.v1",
        initial_review_seal=seal,
        session_fencing_epoch=initial_command.session_fencing_epoch,
        authorities=(),
        evaluation_at=seal.sealed_at,
    )
    resolver = _SessionFindingTrustResolver(context)
    service = FindingLedgerService(
        root,
        project_id=PROJECT,
        trust_resolver=resolver,
    )
    service.append(initial_command)
    fixture.finding_writer.delegate = service
    return service, resolver


def _trust_active_lineage(
    fixture: _Fixture,
    resolver: _SessionFindingTrustResolver,
    session: StageReviewSession,
) -> None:
    cohort = fixture.service.active_cohort(fixture.scope)
    activation = next(
        event
        for event in reversed(fixture.service._store.load_events(fixture.scope))
        if event.event_kind == "new_cohort_activated"
        and event.projection_after.active_cohort_id == cohort.cohort_id
    )
    resolver.context = resolver.context.model_copy(
        update={
            "candidate_digest": cohort.candidate_digest,
            "policy_digest": cohort.policy_digest,
            "plan_digest": cohort.plan_digest,
            "binding_set_digest": cohort.binding_set_digest,
            "cohort_id": cohort.cohort_id,
            "session_fencing_epoch": session.resource_fencing_epoch,
            "evaluation_at": activation.occurred_at,
        }
    )
    resolver.trusted_session_event_digests.add(activation.event_digest)


def _ledger_snapshot(
    fixture: _Fixture,
    *,
    candidate_digest: str,
    plan_digest: str,
    binding_set_digest: str,
    records: tuple[FindingRecord, ...] = (),
) -> FindingLedger:
    previous = fixture.finding_writer.current_ledger
    values = {
        "scope": fixture.scope,
        "initialized": True,
        "revision": (previous.revision + 1) if previous is not None else 1,
        "head_event_id": "finding-event.rereview",
        "head_event_digest": "sha256:finding-event.rereview",
        "initial_review_seal_digest": (
            previous.initial_review_seal_digest if previous is not None else ""
        ),
        "candidate_digest": candidate_digest,
        "policy_digest": POLICY,
        "plan_digest": plan_digest,
        "binding_set_digest": binding_set_digest,
        "records": records,
    }
    draft = FindingLedger.model_validate({**values, "ledger_digest": ""})
    return FindingLedger.model_validate(
        {**values, "ledger_digest": ledger_digest(draft)}
    )


def _unstarted(
    tmp_path: Path,
    *,
    risk_capabilities: tuple[str, ...] = (
        "capability.delivery",
        "capability.security",
    ),
    with_budget_coordinator: bool = False,
    stage_instance_id: str = STAGE,
) -> tuple[_Fixture, TaskRiskProfile]:
    resolver = _Resolver()
    finding_writer = _FindingWriter()
    scope = FindingScope(
        project_id=PROJECT,
        work_item_id=WORK_ITEM,
        stage_instance_id=stage_instance_id,
        session_id=SESSION,
    )
    reservation = _reservation()
    risk_profile = _risk_profile(
        "initial",
        risk_capabilities,
    )
    plan = _plan(
        resolver,
        reservation,
        suffix="initial",
        stage_instance_id=stage_instance_id,
        required_capabilities=risk_capabilities,
        risk_profile_digest=risk_profile.profile_digest,
    )
    binding = _binding_set(plan, reservation, CANDIDATE, suffix="initial")
    resolver.plans[plan.plan_digest] = plan
    resolver.bindings[binding.binding_set_digest] = binding
    resolver.reservations[reservation.reservation_digest] = reservation
    resolver.risk_profiles[risk_profile.profile_digest] = risk_profile
    budget_approvals = _BudgetApprovalResolver() if with_budget_coordinator else None
    budget_coordinator = (
        _BudgetCoordinator(resolver, budget_approvals)
        if budget_approvals is not None
        else None
    )
    service = StageReviewSessionService(
        tmp_path,
        project_id=PROJECT,
        trust_resolver=resolver,
        finding_ledger_writer=finding_writer,
        budget_grant_coordinator=budget_coordinator,
        budget_grant_approval_resolver=budget_approvals,
        clock=lambda: NOW,
    )
    return (
        _Fixture(
            service,
            resolver,
            scope,
            plan,
            binding,
            reservation,
            finding_writer,
            budget_coordinator,
            budget_approvals,
        ),
        risk_profile,
    )


def _started(tmp_path: Path) -> _Fixture:
    fixture, risk_profile = _unstarted(tmp_path)
    _start_fixture(fixture, risk_profile, suffix="1")
    return fixture


def _started_with_budget_coordinator(tmp_path: Path) -> _Fixture:
    fixture, risk_profile = _unstarted(tmp_path, with_budget_coordinator=True)
    _start_fixture(fixture, risk_profile, suffix="budget")
    return fixture


def _hard_budget_stop(fixture: _Fixture) -> StageReviewSession:
    initial = fixture.service.get(fixture.scope)
    first = _submit(fixture, initial.revision, "slot.security")
    command = _pass_command(fixture, first.session.revision, "slot.delivery")
    assignment, invocation, _ = _review_authority(
        fixture,
        first.session,
        "slot.delivery",
        command,
    )
    usage = first.session.resource_usage + ResourceAmounts(
        provider_calls=1,
        review_passes=1,
        tokens=10,
        cost=1,
        active_wall_clock=1,
    )
    hard = usage + ResourceAmounts(slots=1, parallelism=1)
    reservation = _reservation(
        usage=usage,
        hard=hard,
        revision=fixture.reservation.revision + command.expected_revision + 1,
        last_operation_id=f"resource-pass.{command.command_id}",
    )
    invocation = invocation.model_copy(
        update={"settlement_reservation_digest": reservation.reservation_digest}
    )
    invocation = ProviderInvocation.model_validate(
        {
            **invocation.model_dump(mode="json"),
            "projection_digest": projection_digest(invocation),
        }
    )
    fixture.resolver.assignments[assignment.assignment_digest] = assignment
    fixture.resolver.invocations[invocation.invocation_id] = invocation
    fixture.resolver.reservations[reservation.reservation_digest] = reservation
    return fixture.service.submit_pass(command).session


def _budget_grant_command(
    fixture: _Fixture,
    session: StageReviewSession,
    suffix: str,
    *,
    increment: ResourceAmounts | None = None,
) -> BudgetGrantRequestCommand:
    approved_increment = increment or ResourceAmounts(
        provider_calls=2,
        review_passes=2,
        tokens=100,
        cost=10,
        active_wall_clock=10,
    )
    reservation = fixture.resolver.resolve_reservation(
        session.resource_reservation_digest
    )
    assert reservation is not None
    approval = BudgetGrantApproval(
        approval_id=stable_id("budget-grant-approval", suffix),
        scope=session.scope,
        final_reservation_id=session.resource_reservation_id,
        final_reservation_digest=session.resource_reservation_digest,
        final_reservation_revision=reservation.revision,
        final_fencing_token=session.resource_fencing_epoch,
        expected_budget_revision=session.budget_revision,
        increment=approved_increment,
        authority_id="user.test",
        approved_at=NOW,
    )
    assert fixture.budget_approvals is not None
    fixture.budget_approvals.add(approval)
    return BudgetGrantRequestCommand(
        scope=session.scope,
        command_id=f"budget-grant.{suffix}",
        idempotency_key=f"budget-grant-key.{suffix}",
        expected_revision=session.revision,
        expected_budget_revision=session.budget_revision,
        increment=approved_increment,
        approval_digest=approval.approval_digest,
    )


def _budget_operation(
    before: ResourceReservation,
    after: ResourceReservation,
    grant: BudgetGrant,
    *,
    operation_kind: str,
    sequence: int,
) -> BudgetGrantOperation:
    suffix = "apply" if operation_kind == "resource_applied" else "reconcile"
    event_kind = (
        "reservation_expanded"
        if operation_kind == "resource_applied"
        else "budget_grant_reconciled"
    )
    operation_id = stable_id("budget-grant-operation", grant.idempotency_key, suffix)
    event = build_resource_event(
        sequence=sequence,
        event_kind=event_kind,
        operation_id=operation_id,
        previous_event_digest="",
        previous_reservation_digest=before.reservation_digest,
        reservation=after,
    )
    draft = BudgetGrantOperation.model_construct(
        operation_id=operation_id,
        operation_kind=operation_kind,
        grant=grant,
        expected_reservation_revision=before.revision,
        expected_reservation_digest=before.reservation_digest,
        operation_effect_digest=after.operation_effect_digest,
        target_projection_digest=after.reservation_digest,
        target_event_id=event.event_id,
        target_event_digest=event.event_digest,
        target_event=event,
        operation_digest="",
    )
    payload = draft.model_dump(mode="json", warnings=False)
    payload["grant"] = grant
    payload["target_event"] = event
    payload["operation_digest"] = budget_grant_operation_digest(draft)
    return BudgetGrantOperation.model_validate(payload)


def _started_with_role_gap(tmp_path: Path) -> _Fixture:
    fixture, risk_profile = _unstarted(
        tmp_path,
        risk_capabilities=(
            "capability.delivery",
            "capability.new",
            "capability.security",
        ),
    )
    _start_fixture(fixture, risk_profile, suffix="role-gap")
    return fixture


def _start_fixture(
    fixture: _Fixture,
    risk_profile: TaskRiskProfile,
    *,
    suffix: str,
) -> StageReviewSession:
    return fixture.service.start(
        _start_command(fixture, risk_profile, suffix=suffix)
    ).session


def _start_command(
    fixture: _Fixture,
    risk_profile: TaskRiskProfile,
    *,
    suffix: str,
) -> SessionStartCommand:
    return SessionStartCommand(
        scope=fixture.scope,
        command_id=f"session-start.{suffix}",
        idempotency_key=f"session-start-key.{suffix}",
        expected_revision=0,
        candidate_digest=CANDIDATE,
        risk_profile_digest=risk_profile.profile_digest,
        risk_profile_lineage_id=RISK_LINEAGE,
        policy_digest=POLICY,
        optimization_snapshot_digest=SNAPSHOT,
        plan_digest=fixture.plan.plan_digest,
        binding_set_digest=fixture.binding_set.binding_set_digest,
    )


def _seal_initial(fixture: _Fixture) -> StageReviewSession:
    session = fixture.service.get(fixture.scope)
    first = _submit(fixture, session.revision, "slot.security")
    return _submit(fixture, first.session.revision, "slot.delivery").session


def _submit(
    fixture: _Fixture,
    revision: int,
    slot_id: str,
    *,
    findings: tuple[FindingInitialDraft, ...] = (),
) -> SessionMutationResult:
    command = _pass_command(fixture, revision, slot_id, findings=findings)
    session = fixture.service.get(fixture.scope)
    assignment, invocation, reservation = _review_authority(
        fixture, session, slot_id, command
    )
    fixture.resolver.assignments[assignment.assignment_digest] = assignment
    fixture.resolver.invocations[invocation.invocation_id] = invocation
    fixture.resolver.reservations[reservation.reservation_digest] = reservation
    return fixture.service.submit_pass(command)


def _pass_command(
    fixture: _Fixture,
    revision: int,
    slot_id: str,
    *,
    findings: tuple[FindingInitialDraft, ...] = (),
    observed_peer_pass_ids: tuple[str, ...] = (),
) -> SubmitReviewPassCommand:
    session = fixture.service.get(fixture.scope)
    operation = slot_id.rsplit(".", maxsplit=1)[-1]
    coverage = CoverageDeclaration(
        reviewed_area_ids=(f"area.{operation}",),
        uncovered_area_ids=(f"area.{operation}.uncovered",),
        evidence_gap_ids=(f"evidence-gap.{operation}",),
    )
    payload_digest = review_submission_digest(
        verdict="findings" if findings else "passed",
        coverage=coverage,
        findings=findings,
        evidence_digests=(f"sha256:evidence.{operation}",),
        observed_peer_pass_ids=observed_peer_pass_ids,
    )
    binding = next(
        item
        for item in fixture.service.active_cohort(fixture.scope).reviewers
        if item.slot_id == slot_id
    )
    assignment = _assignment(
        fixture.binding_set
        if session.active_binding_set_digest == fixture.binding_set.binding_set_digest
        else fixture.resolver.bindings[session.active_binding_set_digest],
        binding,
        session.active_cohort_id,
        session.active_cohort_initial_head_digest,
    )
    invocation = _invocation(
        assignment,
        payload_digest,
        revision=revision,
        prior_usage=session.resource_usage,
    )
    return SubmitReviewPassCommand(
        scope=fixture.scope,
        command_id=f"review-pass.{operation}.{revision}",
        idempotency_key=f"review-pass-key.{operation}.{revision}",
        expected_revision=revision,
        cohort_id=session.active_cohort_id,
        slot_id=slot_id,
        assignment_digest=assignment.assignment_digest,
        invocation_id=invocation.invocation_id,
        verdict="findings" if findings else "passed",
        coverage=coverage,
        findings=findings,
        evidence_digests=(f"sha256:evidence.{operation}",),
        observed_peer_pass_ids=observed_peer_pass_ids,
    )


def _review_authority(
    fixture: _Fixture,
    session: StageReviewSession,
    slot_id: str,
    command: SubmitReviewPassCommand,
) -> tuple[ReviewerDispatchAssignment, ProviderInvocation, ResourceReservation]:
    binding_set = fixture.resolver.bindings[session.active_binding_set_digest]
    reviewer = next(
        item
        for item in fixture.service.active_cohort(fixture.scope).reviewers
        if item.slot_id == slot_id
    )
    assignment = _assignment(
        binding_set,
        reviewer,
        session.active_cohort_id,
        session.active_cohort_initial_head_digest,
    )
    payload_digest = review_submission_digest(
        verdict=command.verdict,
        coverage=command.coverage,
        findings=command.findings,
        evidence_digests=command.evidence_digests,
        observed_peer_pass_ids=command.observed_peer_pass_ids,
    )
    invocation = _invocation(
        assignment,
        payload_digest,
        revision=command.expected_revision,
        prior_usage=session.resource_usage,
    )
    usage = session.resource_usage + ResourceAmounts(
        provider_calls=1,
        review_passes=1,
        tokens=10,
        cost=1,
        active_wall_clock=1,
    )
    reservation = _reservation(
        usage=usage,
        revision=fixture.reservation.revision + command.expected_revision + 1,
        last_operation_id=f"resource-pass.{command.command_id}",
    )
    invocation = invocation.model_copy(
        update={"settlement_reservation_digest": reservation.reservation_digest}
    )
    invocation = ProviderInvocation.model_validate(
        {
            **invocation.model_dump(mode="json"),
            "projection_digest": projection_digest(invocation),
        }
    )
    return assignment, invocation, reservation


def _progress(*, p0: int, calls: int) -> ProgressSnapshot:
    return ProgressSnapshot(
        comparison_policy_digest="sha256:progress-policy.1",
        p0_open=p0,
        required_test_failures=0,
        integrity_failures=0,
        reopened_or_regressed=0,
        p1_open=0,
        unreviewed_change=0,
        provider_calls=calls,
        tokens=calls * 10,
        estimated_cost=float(calls),
        active_execution_seconds=float(calls),
    )


def _risk_profile(suffix: str, capabilities: tuple[str, ...]) -> TaskRiskProfile:
    facts = [
        RiskFact(
            risk_fact_id=f"risk-fact.{suffix}.{capability}",
            source_ref=f"specs/{suffix}.md",
            extractor_version="risk-extractor.v1",
            confidence=1,
            severity="high",
            required_capability_ids=[capability],
            evidence_digest=f"sha256:risk-evidence.{suffix}.{capability}",
        )
        for capability in capabilities
    ]
    return reconcile_risk_profile(
        work_item_id=WORK_ITEM,
        stage_key=STAGE,
        deterministic_facts=facts,
        semantic_suggestions=[],
    )


def _finding() -> FindingInitialDraft:
    return FindingInitialDraft(
        identity=FindingIdentityInput(
            rule_id="rule.initial",
            category="correctness",
            asset_identity="src/session.py",
            semantic_location="StageReviewSession",
            failure_signature="initial failure",
        ),
        severity="P1",
        evidence_bundle_digest="sha256:finding-evidence.1",
        actor_id="actor.security.initial",
        slot_id="slot.security",
        capability_id="capability.security",
    )


def _slot(
    name: str,
    capability: str,
    suffix: str,
    *,
    extra_capabilities: tuple[str, ...] = (),
) -> ReviewerSlot:
    capabilities = tuple(sorted({capability, *extra_capabilities}))
    return ReviewerSlot(
        slot_id=f"slot.{name}",
        slot_kind="required",
        role_profile_id=f"role.{name}.{suffix}",
        role_contract_digest=f"sha256:role-contract.{name}.{suffix}",
        capability_ids=capabilities,
        blocking_authority=capabilities,
        primary_dimensions=(name,),
        prompt_template_digest=f"sha256:prompt.{name}.{suffix}",
        provider_constraints=(f"provider-class.{name}",),
        tool_permission_ids=("tool.read",),
        evidence_source_ids=(f"evidence-source.{name}",),
        independence_key=f"independence.{name}.{suffix}",
        counts_for_quorum=True,
        allows_abstain=False,
        selection_reason_ids=(f"selection.{name}",),
        estimated_provider_calls=1,
        estimated_review_passes=1,
        estimated_tokens=100,
        estimated_cost=1,
        estimated_wall_clock=10,
    )


def _plan_request(
    suffix: str,
    *,
    stage_instance_id: str,
    required_capabilities: tuple[str, ...],
    risk_profile_digest: str,
) -> ReviewerPlanRequest:
    capabilities = tuple(sorted(set(required_capabilities)))
    draft = ReviewerPlanRequest.model_construct(
        request_id=f"reviewer-plan-request.{suffix}",
        work_item_id=WORK_ITEM,
        loop_id=stage_instance_id,
        loop_round_number=1,
        stage_key=STAGE,
        stage_instance_id=stage_instance_id,
        risk_level="high",
        required_capability_ids=capabilities,
        coverage_requirements=tuple(
            CapabilityCoverageRequirement(
                capability_id=capability,
                minimum_required_slots=1,
            )
            for capability in capabilities
        ),
        blocking_capability_ids=capabilities,
        planning_context_digest="",
        candidate_manifest_ref=".ai-sdlc/reviews/candidate.json",
        candidate_manifest_digest=CANDIDATE,
        task_risk_profile_ref=".ai-sdlc/reviews/risk.json",
        task_risk_profile_digest=risk_profile_digest,
        change_surface_digest="sha256:change-surface.1",
        registry_ref=".ai-sdlc/reviewer-registry.json",
        registry_digest="sha256:registry.1",
        registry_version="1.0.0",
        role_catalog_ref=".ai-sdlc/reviewer-roles.json",
        role_catalog_digest="sha256:roles.1",
        selection_policy_ref=".ai-sdlc/reviewer-selection-policy.json",
        selection_policy_digest="sha256:selection-policy.1",
        selection_policy_version="1.0.0",
        quorum_policy_ref=".ai-sdlc/reviewer-quorum-policy.json",
        quorum_policy_digest="sha256:quorum-policy.1",
        quorum_policy_version="1.0.0",
        budget_policy_ref=".ai-sdlc/reviewer-budget-policy.json",
        budget_policy_digest="sha256:budget-policy.1",
        budget_envelope_digest="sha256:budget-envelope.1",
        planning_authorization_digest="sha256:planning-authorization.1",
        solver_version="panel-solver.v1",
        optimization_snapshot_ref=".ai-sdlc/optimization/snapshot.json",
        optimization_snapshot_digest=SNAPSHOT,
        enforcement_mode="enforce",
        request_digest="",
    )
    with_context = draft.model_copy(
        update={"planning_context_digest": planning_context_digest(draft)}
    )
    return ReviewerPlanRequest.model_validate(
        {
            **with_context.model_dump(mode="json"),
            "request_digest": plan_request_digest(with_context),
        }
    )


def _plan(
    resolver: _Resolver,
    reservation: ResourceReservation,
    *,
    suffix: str,
    stage_instance_id: str,
    required_capabilities: tuple[str, ...],
    risk_profile_digest: str,
    extra_capability: str = "",
) -> ReviewerPanelPlan:
    delivery = _slot("delivery", "capability.delivery", suffix)
    security = _slot(
        "security",
        "capability.security",
        suffix,
        extra_capabilities=(extra_capability,) if extra_capability else (),
    )
    slots = (delivery, security)
    request = _plan_request(
        suffix,
        stage_instance_id=stage_instance_id,
        required_capabilities=required_capabilities,
        risk_profile_digest=risk_profile_digest,
    )
    resolver.plan_requests[request.request_digest] = request
    proposal = ReviewerPanelProposal.model_construct(
        request_digest=request.request_digest,
        planning_context_digest=request.planning_context_digest,
        solver_version="panel-solver.v1",
        registry_digest="sha256:registry.1",
        role_catalog_digest="sha256:roles.1",
        selection_policy_digest="sha256:selection-policy.1",
        quorum_policy_digest="sha256:quorum-policy.1",
        budget_policy_digest="sha256:budget-policy.1",
        budget_envelope_digest="sha256:budget-envelope.1",
        optimization_snapshot_digest=SNAPSHOT,
        required_slots=slots,
        optional_slots=(),
        advisory_slots=(),
        shadow_slots=(),
        coverage_proof=tuple(
            CapabilityCoverageProof(
                capability_id=capability,
                required_slot_ids=(slot.slot_id,),
                minimum_required_slots=1,
                blocking_slot_ids=(slot.slot_id,),
            )
            for slot in slots
            for capability in slot.capability_ids
        ),
        difference_matrix=(
            ReviewerDifference(
                left_slot_id=delivery.slot_id,
                right_slot_id=security.slot_id,
                difference_dimensions=("capability", "prompt", "provider"),
            ),
        ),
        quorum=FrozenQuorumPolicy(
            required_slot_ids=tuple(item.slot_id for item in slots),
            required_capability_expressions=tuple(
                sorted({item for slot in slots for item in slot.capability_ids})
            ),
            minimum_pass_count=2,
            veto_authorities=("capability.security",),
            allowed_abstentions=(),
            source_policy_digest="sha256:quorum-policy.1",
        ),
        resource_requirement=PanelResourceRequirement(
            required_slot_count=2,
            total_slot_count=2,
            required_provider_calls=2,
            total_provider_calls=2,
            required_review_passes=2,
            total_review_passes=2,
            required_tokens=200,
            total_tokens=200,
            required_cost=2,
            total_cost=2,
            required_wall_clock=20,
            total_wall_clock=20,
            parallelism=2,
        ),
        rejected_role_reasons=(),
        planning_explanations=(f"plan.{suffix}",),
        proposal_digest="",
    )
    proposal = ReviewerPanelProposal.model_validate(
        {
            **proposal.model_dump(mode="json"),
            "proposal_digest": panel_proposal_digest(proposal),
        }
    )
    plan = ReviewerPanelPlan.model_construct(
        proposal=proposal,
        proposal_lineage_digest=panel_proposal_lineage_digest(proposal),
        final_reservation_id=reservation.reservation_id,
        final_reservation_digest=reservation.reservation_digest,
        resource_fencing_token=reservation.fencing_token,
        plan_digest="",
        finalization_digest="",
    )
    plan = plan.model_copy(update={"plan_digest": reviewer_panel_plan_digest(plan)})
    return ReviewerPanelPlan.model_validate(
        {
            **plan.model_dump(mode="json"),
            "finalization_digest": reviewer_panel_finalization_digest(plan),
        }
    )


def _binding(
    slot: ReviewerSlot,
    *,
    suffix: str,
    trusted_catalog: bool = False,
) -> ReviewerBinding:
    provider_id = f"provider.openai-codex.{slot.slot_id.removeprefix('slot.')}.{suffix}"
    descriptor = (
        _codex_provider_descriptors(
            slot,
            _trusted_published_codex_release_digests()[0],
        )[0]
        if trusted_catalog
        else _provider_descriptor(slot, provider_id)
    )
    identity = build_reviewer_execution_identity(descriptor)
    contract = _reviewer_transport_contract(descriptor)
    route = descriptor.execution_route
    values = {
        "binding_id": f"binding.{slot.slot_id}.{suffix}",
        "slot_id": slot.slot_id,
        "slot_kind": slot.slot_kind,
        "role_profile_id": slot.role_profile_id,
        "role_contract_digest": slot.role_contract_digest,
        "capability_ids": slot.capability_ids,
        "actor_id": f"actor.{slot.slot_id.removeprefix('slot.')}.{suffix}",
        "provider_id": descriptor.provider_id,
        "model_family": descriptor.model_family,
        "session_id": SESSION,
        "provider_descriptor_digest": descriptor.descriptor_digest,
        "equivalence_class_id": descriptor.equivalence_class_id,
        "physical_provider_id": route.physical_provider_id,
        "physical_equivalence_class_id": route.physical_equivalence_class_id,
        "execution_identity": identity,
        "transport_profile_digest": route.transport_profile_digest,
        "transport_contract_digest": contract.contract_digest,
        "transport_authority_digest": contract.authority_artifact_digest,
        "allocation_digest": f"sha256:allocation.{suffix}.{slot.slot_id}",
        "input_packet_digest": f"sha256:input.{suffix}.{slot.slot_id}",
        "tool_allowlist": descriptor.tool_allowlist,
        "isolation_evidence_digest": f"sha256:isolation.{suffix}.{slot.slot_id}",
        "isolation_grade": "enforced",
        "isolation_backend": descriptor.isolation_backend,
        "supported_independence_grade": descriptor.supported_independence_grade,
        "visibility_barrier_id": f"visibility-barrier.{suffix}",
        "binding_status": "active",
        "recovery_capabilities": descriptor.recovery_capabilities,
        "eligible_for_enforce_quorum": True,
    }
    return ReviewerBinding.model_validate(
        {**values, "binding_digest": reviewer_binding_digest(values)}
    )


def _binding_set(
    plan: ReviewerPanelPlan,
    reservation: ResourceReservation,
    candidate_digest: str,
    *,
    suffix: str,
    trusted_catalog: bool = False,
) -> ReviewerBindingSet:
    bindings = tuple(
        sorted(
            (
                _binding(
                    slot,
                    suffix=suffix,
                    trusted_catalog=trusted_catalog,
                )
                for slot in plan.proposal.required_slots
            ),
            key=lambda item: item.slot_id,
        )
    )
    authority = _binding_authority_for(plan, bindings)
    values = {
        "binding_set_id": f"reviewer-binding-set.{suffix}",
        "project_id": PROJECT,
        "work_item_id": WORK_ITEM,
        "stage_review_session_id": SESSION,
        "candidate_manifest_digest": candidate_digest,
        "plan_digest": plan.plan_digest,
        "plan_finalization_digest": plan.finalization_digest,
        "final_reservation_id": plan.final_reservation_id,
        "final_reservation_digest": plan.final_reservation_digest,
        "resource_fencing_token": reservation.fencing_token,
        "charged_reservation_digest": reservation.reservation_digest,
        "resource_operation_id": f"resource-operation.{suffix}",
        "resource_event_digest": f"sha256:resource-event.{suffix}",
        "budget_policy_digest": "sha256:budget-policy.1",
        "authority_snapshot_digest": authority.snapshot_digest,
        "host_snapshot_digest": "sha256:host.1",
        "attempt_operation_id": f"binding-attempt.{suffix}",
        "attempt_operation_digest": f"sha256:binding-attempt.{suffix}",
        "attempt_index": 1,
        "previous_binding_set_digest": "",
        "enforcement_mode": "enforce",
        "execution_mode": "enforce_eligible",
        "bindings": bindings,
        "unbound_slot_ids": (),
        "independence_proofs": build_independence_proofs(bindings),
    }
    draft = ReviewerBindingSet.model_construct(
        **cast(Any, values),
        binding_set_digest="",
    )
    return ReviewerBindingSet.model_validate(
        {**values, "binding_set_digest": reviewer_binding_set_digest(draft)}
    )


def _binding_authority_snapshot(
    plan: ReviewerPanelPlan,
    binding_set: ReviewerBindingSet,
) -> BindingAuthoritySnapshot:
    return _binding_authority_for(plan, binding_set.bindings)


def _binding_authority_for(
    plan: ReviewerPanelPlan,
    bindings: tuple[ReviewerBinding, ...],
) -> BindingAuthoritySnapshot:
    slots = {item.slot_id: item for item in plan.proposal.required_slots}
    descriptors = tuple(
        _descriptor_for_binding(slots[item.slot_id], item)
        for item in bindings
    )
    release_digest = _trusted_published_codex_release_digests()[0]
    return build_binding_authority_snapshot(
        plan=plan,
        risk_level="high",
        enforcement_mode="enforce",
        provider_descriptors=descriptors,
        attestor_id="ai-sdlc.codex-runtime",
        attestor_version="1.0.0",
        attestation_evidence_digest=release_digest,
    )


def _descriptor_for_binding(
    slot: ReviewerSlot,
    binding: ReviewerBinding,
) -> ProviderBindingDescriptor:
    trusted = _codex_provider_descriptors(
        slot,
        _trusted_published_codex_release_digests()[0],
    )[0]
    if binding.provider_id == trusted.provider_id:
        return trusted
    return _provider_descriptor(slot, binding.provider_id)


def _provider_descriptor(
    slot: ReviewerSlot,
    provider_id: str,
) -> ProviderBindingDescriptor:
    recovery = ProviderRecoveryCapabilities(
        idempotency_support=True,
        invocation_query_support=True,
        cost_metering_support=True,
    )
    return build_provider_binding_descriptor(
        descriptor_id=f"descriptor.{provider_id}",
        provider_id=provider_id,
        equivalence_class_id="provider.openai-codex",
        model_family=f"model.openai-codex.{slot.slot_id.removeprefix('slot.')}",
        role_contract_digests=(slot.role_contract_digest,),
        capability_ids=slot.capability_ids,
        provider_tags=slot.provider_constraints,
        tool_allowlist=slot.tool_permission_ids,
        recovery_capabilities=recovery,
        execution_route=codex_reviewer_execution_route(),
        isolation_backend="codex.permission-profile",
        network_enforcement=True,
        supported_independence_grade="session_independent",
        provider_policy_evidence_digest=(
            _trusted_published_codex_release_digests()[0]
        ),
    )


def _binding_for(
    fixture: _Fixture,
    plan: ReviewerPanelPlan,
    *,
    candidate_digest: str,
    suffix: str,
    usage: ResourceAmounts,
) -> tuple[ReviewerBindingSet, ResourceReservation]:
    reservation = _reservation(
        usage=usage,
        revision=fixture.reservation.revision + 20,
        last_operation_id=f"resource-binding.{suffix}",
    )
    binding = _binding_set(plan, reservation, candidate_digest, suffix=suffix)
    binding = _binding_with_previous(binding, fixture.binding_set.binding_set_digest)
    return binding, reservation


def _binding_with_previous(
    binding: ReviewerBindingSet,
    previous_digest: str,
) -> ReviewerBindingSet:
    draft = binding.model_copy(
        update={
            "previous_binding_set_digest": previous_digest,
            "binding_set_digest": "",
        }
    )
    return ReviewerBindingSet.model_validate(
        {
            **draft.model_dump(mode="json"),
            "binding_set_digest": reviewer_binding_set_digest(draft),
        }
    )


def _rebind_directive(
    session: StageReviewSession,
    binding: ReviewerBindingSet,
    unavailable_provider_ids: tuple[str, ...],
) -> RebindDirective:
    values = {
        "directive_id": stable_id("rebind-directive", binding.binding_set_digest),
        "previous_binding_set_digest": session.active_binding_set_digest,
        "new_binding_set_digest": binding.binding_set_digest,
        "expected_cohort_id": session.active_cohort_id,
        "expected_pass_head_digest": session.active_cohort_initial_head_digest,
        "rebind_reason": "provider_unavailable",
        "unavailable_provider_ids": tuple(sorted(set(unavailable_provider_ids))),
        "requires_session_cas": True,
    }
    return RebindDirective.model_validate(
        {**values, "directive_digest": rebind_directive_digest(values)}
    )


def _replacement_authority(
    fixture: _Fixture,
    *,
    suffix: str,
    candidate_digest: str,
    usage: ResourceAmounts,
) -> tuple[ReviewerPanelPlan, ReviewerBindingSet, ResourceReservation]:
    reservation = _reservation(
        usage=usage,
        revision=fixture.reservation.revision + 30,
        last_operation_id=f"resource-replan.{suffix}",
    )
    plan = _plan(
        fixture.resolver,
        reservation,
        suffix=suffix,
        stage_instance_id=fixture.scope.stage_instance_id,
        required_capabilities=(
            "capability.delivery",
            "capability.new",
            "capability.security",
        ),
        risk_profile_digest=fixture.service.get(
            fixture.scope
        ).active_risk_profile_digest,
        extra_capability="capability.new",
    )
    binding = _binding_set(plan, reservation, candidate_digest, suffix=suffix)
    fixture.resolver.plans[plan.plan_digest] = plan
    fixture.resolver.bindings[binding.binding_set_digest] = binding
    fixture.resolver.reservations[reservation.reservation_digest] = reservation
    return plan, binding, reservation


def _reservation(
    *,
    usage: ResourceAmounts | None = None,
    hard: ResourceAmounts | None = None,
    revision: int = 1,
    last_operation_id: str = "resource-final.initial",
) -> ResourceReservation:
    hard = hard or ResourceAmounts(
        slots=10,
        provider_calls=50,
        review_passes=50,
        tokens=10000,
        cost=1000,
        active_wall_clock=1000,
        parallelism=10,
        role_replans=2,
        provider_retries=10,
        binding_attempts=20,
    )
    soft = ResourceSoftLimits.model_validate(
        {name: getattr(hard, name) * 0.8 for name in ResourceAmounts.ALL_FIELDS}
    )
    values = {
        "reservation_id": "resource-reservation.session-001",
        "project_id": PROJECT,
        "work_item_id": WORK_ITEM,
        "stage_review_session_id": SESSION,
        "pool": "foreground",
        "state": "final",
        "admission_operation_id": "resource-admission.session-001",
        "idempotency_key": "resource-session-001",
        "budget_envelope_digest": "sha256:budget-envelope.1",
        "budget_policy_digest": "sha256:budget-policy.1",
        "proposal_digest": "sha256:proposal.1",
        "proposal_lineage_digest": "sha256:proposal-lineage.1",
        "provider_scope_ids": (),
        "reserved": hard,
        "usage": usage or ResourceAmounts(),
        "authorized_pending": ResourceAmounts(),
        "provider_permits": (),
        "provider_invocation_ids": (),
        "policy_hard_limits": hard,
        "hard_limits": hard,
        "soft_limits": soft,
        "budget_revision": 0,
        "last_budget_grant_operation_id": "",
        "budget_grant_ids": (),
        "reconciled_budget_grant_ids": (),
        "revision": revision,
        "fencing_token": 1,
        "lease_owner": "session-test",
        "lease_expires_at": "2026-07-22T00:00:00Z",
        "last_operation_id": last_operation_id,
        "operation_effect_digest": f"sha256:{last_operation_id}",
    }
    draft = ResourceReservation.model_construct(
        **cast(Any, values),
        reservation_digest="",
    )
    return ResourceReservation.model_validate(
        {**values, "reservation_digest": reservation_digest(draft)}
    )


def _assignment(
    binding_set: ReviewerBindingSet,
    reviewer: CohortReviewer,
    cohort_id: str,
    expected_head: str,
) -> ReviewerDispatchAssignment:
    binding = next(
        item for item in binding_set.bindings if item.slot_id == reviewer.slot_id
    )
    assignment_id = stable_id(
        "reviewer-dispatch",
        binding_set.binding_set_digest,
        binding.binding_digest,
        binding_set.host_snapshot_digest,
        cohort_id,
        expected_head,
    )
    values = {
        "assignment_id": assignment_id,
        "binding_set_digest": binding_set.binding_set_digest,
        "binding_digest": binding.binding_digest,
        "host_snapshot_digest": binding_set.host_snapshot_digest,
        "isolation_evidence_digest": binding.isolation_evidence_digest,
        "cohort_id": cohort_id,
        "expected_pass_head_digest": expected_head,
        "slot_id": binding.slot_id,
        "candidate_manifest_digest": binding_set.candidate_manifest_digest,
        "provider_id": binding.provider_id,
        "provider_descriptor_digest": binding.provider_descriptor_digest,
        "provider_execution_identity_digest": (
            binding.execution_identity.identity_digest
        ),
        "physical_provider_id": binding.physical_provider_id,
        "physical_equivalence_class_id": binding.physical_equivalence_class_id,
        "transport_profile_digest": binding.transport_profile_digest,
        "transport_contract_digest": binding.transport_contract_digest,
        "transport_authority_digest": binding.transport_authority_digest,
        "model_family": binding.model_family,
        "session_id": binding.session_id,
        "recovery_capabilities": binding.recovery_capabilities,
    }
    draft = ReviewerDispatchAssignment.model_construct(
        **cast(Any, values),
        assignment_digest="",
    )
    return ReviewerDispatchAssignment.model_validate(
        {**values, "assignment_digest": dispatch_assignment_digest(draft)}
    )


def _invocation(
    assignment: ReviewerDispatchAssignment,
    payload_digest: str,
    *,
    revision: int,
    prior_usage: ResourceAmounts,
) -> ProviderInvocation:
    idempotency = f"provider-review.{assignment.slot_id}.{revision}"
    invocation_id = stable_id(
        "provider-invocation",
        PROJECT,
        SESSION,
        assignment.provider_id,
        idempotency,
    )
    anticipated = ResourceAmounts(
        provider_calls=1,
        tokens=10,
        cost=1,
        active_wall_clock=1,
        parallelism=1,
    )
    request = ProviderInvocationRequest.model_construct(
        invocation_id=invocation_id,
        project_id=PROJECT,
        work_item_id=WORK_ITEM,
        stage_review_session_id=SESSION,
        owner_scope_id=assignment.slot_id,
        candidate_digest=assignment.candidate_manifest_digest,
        assignment_digest=assignment.assignment_digest,
        authorization_scope="reviewer_binding",
        provider_id=assignment.provider_id,
        request_digest=payload_digest,
        reservation_id="resource-reservation.session-001",
        expected_reservation_digest="sha256:expected-reservation",
        expected_fencing_token=1,
        anticipated_usage=anticipated,
        capabilities=assignment.recovery_capabilities,
        command_id=f"provider-command.{assignment.slot_id}.{revision}",
        idempotency_key=idempotency,
        request_artifact_digest="",
    )
    request = ProviderInvocationRequest.model_validate(
        {
            **request.model_dump(mode="json"),
            "request_artifact_digest": request_artifact_digest(request),
        }
    )
    isolation_receipts = (f"sha256:isolation-receipt.{assignment.slot_id}.{revision}",)
    invocation = ProviderInvocation.model_construct(
        request=request,
        state="committed",
        revision=5,
        authorized_reservation_digest="sha256:authorized-reservation",
        submission_digest=f"sha256:submission.{assignment.slot_id}.{revision}",
        isolation_receipt_digests=isolation_receipts,
        execution_evidence_root_digest=provider_execution_evidence_root_digest(
            isolation_receipts
        ),
        validation_digest=payload_digest,
        resource_settlement_operation_id=f"settlement.{assignment.slot_id}.{revision}",
        settlement_reservation_digest="sha256:settlement-reservation.pending",
        resource_settlement_event_digest=f"sha256:settlement-event.{assignment.slot_id}.{revision}",
        last_event_digest=f"sha256:invocation-event.{assignment.slot_id}.{revision}",
        projection_digest="",
    )
    return ProviderInvocation.model_validate(
        {
            **invocation.model_dump(mode="json"),
            "projection_digest": projection_digest(invocation),
        }
    )
