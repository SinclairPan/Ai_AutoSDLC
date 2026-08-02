"""Session 的可信 Authority 校验与不可变 Cohort/Pass/Event 构建。"""

from __future__ import annotations

from ai_sdlc.core.stage_review.binding_result_models import (
    ReviewerBinding,
    ReviewerDispatchAssignment,
)
from ai_sdlc.core.stage_review.canonical import CanonicalizationPolicy, canonical_digest
from ai_sdlc.core.stage_review.finding_digests import initial_finding_batch_digest
from ai_sdlc.core.stage_review.finding_models import FindingScope
from ai_sdlc.core.stage_review.finding_trust_models import InitialReviewSeal
from ai_sdlc.core.stage_review.panel_plan_models import ReviewerSlot
from ai_sdlc.core.stage_review.provider_journal_models import ProviderInvocation
from ai_sdlc.core.stage_review.resource_builders import stable_id
from ai_sdlc.core.stage_review.resource_ledger_models import ResourceReservation
from ai_sdlc.core.stage_review.session_artifact_models import (
    ArtifactRef,
    CohortReviewer,
    ReviewCohort,
    ReviewPass,
)
from ai_sdlc.core.stage_review.session_authority import (
    SessionAuthority,
    resolve_review_assignment,
    resolve_review_invocation,
    validate_resource_advance,
    validate_review_authority,
)
from ai_sdlc.core.stage_review.session_contracts import (
    SessionEventKind,
    SessionIntegrityError,
    SessionTrustResolver,
    SubmitReviewPassCommand,
)
from ai_sdlc.core.stage_review.session_models import (
    SessionEvent,
    SessionOperation,
    SessionProjectionData,
    StageReviewSession,
)


def build_cohort(
    scope: FindingScope,
    authority: SessionAuthority,
    *,
    candidate_digest: str,
    risk_profile_digest: str,
    risk_profile_lineage_id: str,
    policy_digest: str,
    optimization_snapshot_digest: str,
    ordinal: int,
    initial_pass_head_digest: str,
    predecessor_cohort_id: str,
    activation_reason: str,
    created_at: str,
) -> ReviewCohort:
    reviewers = _cohort_reviewers(authority)
    cohort_id = _cohort_id(
        authority,
        scope.session_id,
        ordinal,
        candidate_digest,
        risk_profile_digest,
        predecessor_cohort_id,
    )
    return ReviewCohort(
        scope=scope,
        cohort_id=cohort_id,
        ordinal=ordinal,
        candidate_digest=candidate_digest,
        risk_profile_digest=risk_profile_digest,
        risk_profile_lineage_id=risk_profile_lineage_id,
        policy_digest=policy_digest,
        optimization_snapshot_digest=optimization_snapshot_digest,
        plan_digest=authority.plan.plan_digest,
        plan_finalization_digest=authority.plan.finalization_digest,
        binding_set_id=authority.binding_set.binding_set_id,
        binding_set_digest=authority.binding_set.binding_set_digest,
        resource_reservation_id=authority.reservation.reservation_id,
        resource_reservation_digest=authority.reservation.reservation_digest,
        reviewers=reviewers,
        required_slot_ids=tuple(item.slot_id for item in reviewers),
        initial_pass_head_digest=initial_pass_head_digest,
        predecessor_cohort_id=predecessor_cohort_id,
        activation_reason=activation_reason,
        created_at=created_at,
    )


def build_review_pass(
    resolver: SessionTrustResolver,
    session: StageReviewSession,
    cohort: ReviewCohort,
    command: SubmitReviewPassCommand,
    *,
    submitted_at: str,
) -> tuple[ReviewPass, ResourceReservation]:
    reviewer, assignment, invocation, reservation = _resolve_pass_context(
        resolver, session, cohort, command
    )
    return (
        _create_review_pass(
            session,
            cohort,
            command,
            reviewer,
            assignment,
            invocation,
            reservation,
            submitted_at,
        ),
        reservation,
    )


def _resolve_pass_context(
    resolver: SessionTrustResolver,
    session: StageReviewSession,
    cohort: ReviewCohort,
    command: SubmitReviewPassCommand,
) -> tuple[
    CohortReviewer,
    ReviewerDispatchAssignment,
    ProviderInvocation,
    ResourceReservation,
]:
    reviewer = next(
        (item for item in cohort.reviewers if item.slot_id == command.slot_id),
        None,
    )
    if reviewer is None:
        raise SessionIntegrityError("review pass slot is not in active cohort")
    assignment = resolve_review_assignment(resolver, command.assignment_digest)
    invocation = resolve_review_invocation(resolver, command.invocation_id)
    payload_digest = review_submission_digest(
        verdict=command.verdict,
        coverage=command.coverage,
        findings=command.findings,
        evidence_digests=command.evidence_digests,
        observed_peer_pass_ids=command.observed_peer_pass_ids,
    )
    validate_review_authority(
        session,
        cohort,
        reviewer,
        assignment,
        invocation,
        payload_digest,
    )
    if any(
        item.capability_id not in reviewer.capability_ids for item in command.findings
    ):
        raise SessionIntegrityError("review pass finding capability is unauthorized")
    reservation = resolver.resolve_reservation(invocation.settlement_reservation_digest)
    if reservation is None:
        raise SessionIntegrityError("review pass settlement reservation is missing")
    reservation = ResourceReservation.model_validate(
        reservation.model_dump(mode="json")
    )
    validate_resource_advance(
        session,
        reservation,
        required_increment="review_passes",
    )
    return reviewer, assignment, invocation, reservation


def _create_review_pass(
    session: StageReviewSession,
    cohort: ReviewCohort,
    command: SubmitReviewPassCommand,
    reviewer: CohortReviewer,
    assignment: ReviewerDispatchAssignment,
    invocation: ProviderInvocation,
    reservation: ResourceReservation,
    submitted_at: str,
) -> ReviewPass:
    is_first = not any(
        item.cohort_id == cohort.cohort_id and item.slot_id == command.slot_id
        for item in session.pass_refs
    )
    pass_id = _review_pass_id(session, cohort, command)
    return ReviewPass(
        scope=session.scope,
        pass_id=pass_id,
        cohort_id=cohort.cohort_id,
        candidate_digest=cohort.candidate_digest,
        risk_profile_digest=cohort.risk_profile_digest,
        plan_digest=cohort.plan_digest,
        binding_set_digest=cohort.binding_set_digest,
        policy_digest=cohort.policy_digest,
        slot_id=reviewer.slot_id,
        role_profile_id=reviewer.role_profile_id,
        role_contract_digest=reviewer.role_contract_digest,
        binding_id=reviewer.binding_id,
        binding_digest=reviewer.binding_digest,
        actor_id=reviewer.actor_id,
        provider_id=reviewer.provider_id,
        model_family=reviewer.model_family,
        assignment_digest=assignment.assignment_digest,
        invocation_id=invocation.invocation_id,
        invocation_projection_digest=invocation.projection_digest,
        validation_digest=invocation.validation_digest,
        resource_reservation_digest=reservation.reservation_digest,
        is_first_cohort_pass=is_first,
        verdict=command.verdict,
        coverage=command.coverage,
        findings=command.findings,
        evidence_digests=tuple(sorted(set(command.evidence_digests))),
        isolation_receipt_digests=invocation.isolation_receipt_digests,
        egress_receipt_digests=invocation.egress_receipt_digests,
        execution_evidence_root_digest=invocation.execution_evidence_root_digest,
        observed_peer_pass_ids=tuple(sorted(set(command.observed_peer_pass_ids))),
        submitted_at=submitted_at,
    )


def build_initial_review_seal(
    session: StageReviewSession,
    cohort: ReviewCohort,
    passes: tuple[ReviewPass, ...],
    *,
    sealed_at: str,
) -> InitialReviewSeal:
    by_slot = {item.slot_id: item for item in passes if item.is_first_cohort_pass}
    if tuple(sorted(by_slot)) != cohort.required_slot_ids:
        raise SessionIntegrityError("initial review seal lacks every required pass")
    ordered = tuple(by_slot[slot_id] for slot_id in cohort.required_slot_ids)
    findings = tuple(finding for item in ordered for finding in item.findings)
    return InitialReviewSeal(
        scope=session.scope,
        initial_candidate_digest=cohort.candidate_digest,
        policy_digest=cohort.policy_digest,
        plan_digest=cohort.plan_digest,
        binding_set_digest=cohort.binding_set_digest,
        initial_cohort_id=cohort.cohort_id,
        required_slot_ids=cohort.required_slot_ids,
        required_pass_digests=tuple(item.pass_digest for item in ordered),
        coverage_declaration_digests=tuple(
            item.coverage.declaration_digest for item in ordered
        ),
        finding_batch_digest=initial_finding_batch_digest(findings),
        sealed_at=sealed_at,
    )


def build_session_event(
    operation: SessionOperation,
    *,
    kind: SessionEventKind,
    sequence: int,
    previous_event_id: str,
    previous_event_digest: str,
    projection: SessionProjectionData,
    artifact_refs: tuple[ArtifactRef, ...] = (),
) -> SessionEvent:
    event_id = stable_id(
        "session-event",
        operation.scope.session_id,
        str(sequence),
        kind,
        operation.command_id,
    )
    return SessionEvent(
        scope=operation.scope,
        sequence=sequence,
        event_id=event_id,
        event_kind=kind,
        command_id=operation.command_id,
        command_digest=operation.command_digest,
        previous_event_id=previous_event_id,
        previous_event_digest=previous_event_digest,
        occurred_at=operation.prepared_at,
        projection_after=projection,
        artifact_refs=artifact_refs,
    )


def review_submission_digest(
    *,
    verdict: object,
    coverage: object,
    findings: object,
    evidence_digests: object,
    observed_peer_pass_ids: object,
) -> str:
    return canonical_digest(
        {
            "verdict": verdict,
            "coverage": coverage,
            "findings": findings,
            "evidence_digests": evidence_digests,
            "observed_peer_pass_ids": observed_peer_pass_ids,
        },
        CanonicalizationPolicy(),
    )


def _cohort_reviewer(
    slot: ReviewerSlot,
    binding: ReviewerBinding,
) -> CohortReviewer:
    return CohortReviewer(
        slot_id=slot.slot_id,
        role_profile_id=binding.role_profile_id,
        role_contract_digest=binding.role_contract_digest,
        capability_ids=tuple(sorted(binding.capability_ids)),
        binding_id=binding.binding_id,
        binding_digest=binding.binding_digest,
        actor_id=binding.actor_id,
        provider_id=binding.provider_id,
        model_family=binding.model_family,
        assignment_input_packet_digest=binding.input_packet_digest,
        visibility_barrier_id=binding.visibility_barrier_id,
        eligible_for_enforce_quorum=binding.eligible_for_enforce_quorum,
    )


def _cohort_reviewers(authority: SessionAuthority) -> tuple[CohortReviewer, ...]:
    by_slot = {item.slot_id: item for item in authority.binding_set.bindings}
    slots = sorted(
        authority.plan.proposal.required_slots,
        key=lambda item: item.slot_id,
    )
    return tuple(_cohort_reviewer(slot, by_slot[slot.slot_id]) for slot in slots)


def _review_pass_id(
    session: StageReviewSession,
    cohort: ReviewCohort,
    command: SubmitReviewPassCommand,
) -> str:
    return stable_id(
        "review-pass",
        session.scope.session_id,
        cohort.cohort_id,
        command.slot_id,
        command.command_id,
    )


def _cohort_id(
    authority: SessionAuthority,
    session_id: str,
    ordinal: int,
    candidate_digest: str,
    risk_profile_digest: str,
    predecessor_cohort_id: str,
) -> str:
    return stable_id(
        "review-cohort",
        session_id,
        str(ordinal),
        candidate_digest,
        risk_profile_digest,
        authority.plan.plan_digest,
        authority.binding_set.binding_set_digest,
        predecessor_cohort_id,
    )
