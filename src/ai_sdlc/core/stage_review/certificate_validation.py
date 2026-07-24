"""关闭证书的 Session、治理、Quorum、Ledger 与资源校验。"""

from __future__ import annotations

from ai_sdlc.core.stage_review.binding_authority_validation import (
    _validate_binding_authority_snapshot,
)
from ai_sdlc.core.stage_review.binding_independence import (
    validate_canonical_independence_proofs,
)
from ai_sdlc.core.stage_review.binding_lineage import (
    dispatch_assignment_matches_binding,
)
from ai_sdlc.core.stage_review.binding_result_models import (
    ReviewerBinding,
    ReviewerBindingSet,
    ReviewerDispatchAssignment,
)
from ai_sdlc.core.stage_review.certificate_models import StageCloseCertificateRequest
from ai_sdlc.core.stage_review.certificate_receipt_store import (
    ReceiptArtifactError,
    ReviewReceiptArtifactResolver,
)
from ai_sdlc.core.stage_review.certificate_receipt_validation import (
    validate_review_pass_receipts,
)
from ai_sdlc.core.stage_review.close_models import CloseConsumptionClaim
from ai_sdlc.core.stage_review.resource_ledger_models import (
    ResourceReconciliation,
    ResourceReservation,
)
from ai_sdlc.core.stage_review.session_artifact_models import (
    CohortReviewer,
    ReviewPass,
)
from ai_sdlc.core.stage_review.session_certificate_inputs import (
    SessionCertificateInputs,
)
from ai_sdlc.core.stage_review.session_contracts import SessionIntegrityError


class CertificateInvalidError(SessionIntegrityError):
    """关闭证书已陈旧、被撤销、被篡改或不完整。"""


def _validate_certificate_inputs(
    request: StageCloseCertificateRequest,
    inputs: SessionCertificateInputs,
    final: ResourceReservation,
    current: ResourceReservation,
    reconciliation: ResourceReconciliation,
    receipt_resolver: ReviewReceiptArtifactResolver,
) -> None:
    _validate_scope_and_session(request, inputs)
    _validate_governance(inputs)
    _validate_quorum(inputs, receipt_resolver)
    _validate_ledger(inputs)
    _validate_resources(inputs, final, current, reconciliation)


def validate_reconciled_certificate_inputs(
    request: StageCloseCertificateRequest,
    inputs: SessionCertificateInputs,
    final: ResourceReservation,
    current: ResourceReservation,
    reconciliation: ResourceReconciliation,
    aborted_claim: CloseConsumptionClaim,
    receipt_resolver: ReviewReceiptArtifactResolver,
) -> None:
    _validate_recovery_scope(request, inputs, aborted_claim)
    _validate_governance(inputs)
    _validate_quorum(inputs, receipt_resolver)
    _validate_ledger(inputs)
    _validate_resources(inputs, final, current, reconciliation)


def _validate_scope_and_session(
    request: StageCloseCertificateRequest,
    inputs: SessionCertificateInputs,
) -> None:
    session = inputs.session
    intent = request.intent
    checks = (
        intent.scope == session.scope,
        request.evidence.candidate_manifest_digest == session.active_candidate_digest,
        request.expected_session_revision == session.revision,
        session.state == "authorized",
        not session.pending_budget_grant_command_id,
        session.active_cohort_id in session.sealed_cohort_ids,
        session.active_cohort_id not in session.superseded_cohort_ids,
        session.active_plan_digest not in session.revoked_plan_digests,
    )
    if not all(checks):
        raise CertificateInvalidError("stage review session is not certificate-ready")


def _validate_recovery_scope(
    request: StageCloseCertificateRequest,
    inputs: SessionCertificateInputs,
    aborted_claim: CloseConsumptionClaim,
) -> None:
    session = inputs.session
    intent = request.intent
    checks = (
        intent.scope == session.scope == aborted_claim.scope,
        request.evidence.candidate_manifest_digest == session.active_candidate_digest,
        request.expected_session_revision == session.revision,
        session.state == "needs_user",
        session.close_failure_reason == "governed_close_abort",
        session.active_close_certificate_id == aborted_claim.certificate_id,
        session.active_close_certificate_digest == aborted_claim.certificate_digest,
        session.active_close_claim_id == aborted_claim.claim_id,
        session.active_close_claim_digest == aborted_claim.claim_digest,
        intent.command_id != aborted_claim.command_id,
        not session.pending_budget_grant_command_id,
        session.active_cohort_id in session.sealed_cohort_ids,
        session.active_cohort_id not in session.superseded_cohort_ids,
        session.active_plan_digest not in session.revoked_plan_digests,
    )
    if not all(checks):
        raise CertificateInvalidError("aborted close is not ready for new certificate")


def _validate_governance(inputs: SessionCertificateInputs) -> None:
    session, plan, binding, cohort = (
        inputs.session,
        inputs.plan,
        inputs.binding_set,
        inputs.cohort,
    )
    _validate_binding_authority_snapshot(
        plan,
        inputs.authority_snapshot,
        binding,
        inputs.assignments,
    )
    proposal = plan.proposal
    checks = (
        plan.plan_digest == session.active_plan_digest == cohort.plan_digest,
        binding.binding_set_digest
        == session.active_binding_set_digest
        == cohort.binding_set_digest,
        cohort.candidate_digest == session.active_candidate_digest,
        cohort.risk_profile_digest == session.active_risk_profile_digest,
        cohort.policy_digest == session.policy_digest,
        cohort.optimization_snapshot_digest == session.optimization_snapshot_digest,
        plan.finalization_digest == cohort.plan_finalization_digest,
        binding.plan_finalization_digest == plan.finalization_digest,
        proposal.optimization_snapshot_digest == session.optimization_snapshot_digest,
        proposal.quorum_policy_digest == proposal.quorum.source_policy_digest,
        plan.final_reservation_id == session.resource_reservation_id,
        binding.candidate_manifest_digest == session.active_candidate_digest,
        binding.project_id == session.scope.project_id,
        binding.work_item_id == session.scope.work_item_id,
        binding.stage_review_session_id == session.scope.session_id,
        binding.final_reservation_id == session.resource_reservation_id,
        cohort.scope == session.scope,
        cohort.resource_reservation_id == session.resource_reservation_id,
        cohort.resource_reservation_digest == binding.charged_reservation_digest,
        binding.execution_mode == "enforce_eligible",
        binding.enforcement_mode == "enforce",
        binding.budget_policy_digest == proposal.budget_policy_digest,
    )
    if not all(checks):
        raise CertificateInvalidError("certificate governance lineage is invalid")
    _validate_binding_independence(binding)


def _validate_binding_independence(binding: ReviewerBindingSet) -> None:
    try:
        validate_canonical_independence_proofs(
            binding.bindings,
            binding.independence_proofs,
        )
    except ValueError as exc:
        raise CertificateInvalidError(
            "certificate binding independence proof is invalid"
        ) from exc


def _validate_quorum(
    inputs: SessionCertificateInputs,
    receipt_resolver: ReviewReceiptArtifactResolver,
) -> None:
    plan, cohort, passes = inputs.plan, inputs.cohort, inputs.passes
    required = tuple(sorted(plan.proposal.quorum.required_slot_ids))
    slots = tuple(item.slot_id for item in passes)
    assignment_slots = tuple(item.slot_id for item in inputs.assignments)
    if (
        slots != required
        or slots != cohort.required_slot_ids
        or assignment_slots != required
    ):
        raise CertificateInvalidError("required reviewer quorum is incomplete")
    reviewers = {item.slot_id: item for item in cohort.reviewers}
    bindings = {item.slot_id: item for item in inputs.binding_set.bindings}
    assignments = {item.slot_id: item for item in inputs.assignments}
    for review_pass in passes:
        reviewer = reviewers.get(review_pass.slot_id)
        binding = bindings.get(review_pass.slot_id)
        assignment = assignments.get(review_pass.slot_id)
        if reviewer is None or binding is None or assignment is None:
            raise CertificateInvalidError("review pass authority is unavailable")
        _validate_pass_authority(inputs, review_pass, reviewer, binding, assignment)
        try:
            validate_review_pass_receipts(
                review_pass,
                receipt_resolver,
                binding,
                assignment,
                inputs.authority_snapshot,
            )
        except (ReceiptArtifactError, ValueError) as exc:
            raise CertificateInvalidError(
                "review pass receipt authority is invalid"
            ) from exc


def _validate_pass_authority(
    inputs: SessionCertificateInputs,
    review_pass: ReviewPass,
    reviewer: CohortReviewer,
    binding: ReviewerBinding,
    assignment: ReviewerDispatchAssignment,
) -> None:
    cohort = inputs.cohort
    checks = (
        review_pass.verdict == "passed",
        review_pass.cohort_id == assignment.cohort_id == cohort.cohort_id,
        review_pass.candidate_digest
        == assignment.candidate_manifest_digest
        == cohort.candidate_digest,
        review_pass.risk_profile_digest == cohort.risk_profile_digest,
        review_pass.plan_digest == cohort.plan_digest,
        review_pass.binding_set_digest
        == assignment.binding_set_digest
        == cohort.binding_set_digest,
        review_pass.policy_digest == cohort.policy_digest,
        review_pass.assignment_digest == assignment.assignment_digest,
        review_pass.binding_id == binding.binding_id == reviewer.binding_id,
        review_pass.binding_digest
        == assignment.binding_digest
        == binding.binding_digest
        == reviewer.binding_digest,
        review_pass.role_profile_id
        == binding.role_profile_id
        == reviewer.role_profile_id,
        review_pass.role_contract_digest
        == binding.role_contract_digest
        == reviewer.role_contract_digest,
        review_pass.actor_id == binding.actor_id == reviewer.actor_id,
        review_pass.provider_id
        == assignment.provider_id
        == binding.provider_id
        == reviewer.provider_id,
        review_pass.model_family
        == assignment.model_family
        == binding.model_family
        == reviewer.model_family,
        dispatch_assignment_matches_binding(inputs.binding_set, binding, assignment),
        bool(review_pass.isolation_receipt_digests),
        bool(review_pass.execution_evidence_root_digest),
    )
    if not all(checks):
        raise CertificateInvalidError("review pass quorum lineage is invalid")


def _validate_ledger(inputs: SessionCertificateInputs) -> None:
    session, cohort, ledger = inputs.session, inputs.cohort, inputs.ledger
    checks = (
        ledger.initialized,
        ledger.integrity_ok,
        ledger.scope == session.scope,
        ledger.ledger_digest == session.finding_ledger_digest,
        ledger.candidate_digest == cohort.candidate_digest,
        ledger.policy_digest == cohort.policy_digest,
        ledger.plan_digest == cohort.plan_digest,
        ledger.binding_set_digest == cohort.binding_set_digest,
        not ledger.pending_handoff_ids,
        not ledger.pending_identity_target_keys,
        not any(record.blocking for record in ledger.records),
    )
    if not all(checks):
        raise CertificateInvalidError("finding ledger is not closeable")


def _validate_resources(
    inputs: SessionCertificateInputs,
    final: ResourceReservation,
    current: ResourceReservation,
    reconciliation: ResourceReconciliation,
) -> None:
    session, plan, binding = inputs.session, inputs.plan, inputs.binding_set
    checks = (
        final.state == "final",
        final.reservation_id == session.resource_reservation_id,
        final.reservation_digest == session.resource_reservation_digest,
        final.project_id == session.scope.project_id,
        final.work_item_id == session.scope.work_item_id,
        final.stage_review_session_id == session.scope.session_id,
        final.budget_policy_digest == plan.proposal.budget_policy_digest,
        final.budget_policy_digest == binding.budget_policy_digest,
        final.budget_revision == session.budget_revision,
        final.budget_grant_ids == session.budget_grant_ids,
        current.reservation_id == final.reservation_id,
        current.state == "reconciled",
        current.last_operation_id == reconciliation.operation_id,
        not current.provider_permits,
        not current.authorized_pending.any_positive(),
        reconciliation.reservation_id == final.reservation_id,
        reconciliation.reservation_digest == final.reservation_digest,
        reconciliation.fencing_token == current.fencing_token,
    )
    if not all(checks):
        raise CertificateInvalidError("certificate resource lineage is invalid")
