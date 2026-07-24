"""从已锁定并验证的权威输入构建不可变关闭证书。"""

from __future__ import annotations

from ai_sdlc.core.stage_review.canonical import (
    CanonicalizationPolicy,
    canonical_digest,
)
from ai_sdlc.core.stage_review.certificate_models import (
    StageCloseCertificate,
    StageCloseCertificateRequest,
)
from ai_sdlc.core.stage_review.resource_builders import stable_id
from ai_sdlc.core.stage_review.resource_ledger_models import (
    ResourceReconciliation,
    ResourceReservation,
)
from ai_sdlc.core.stage_review.session_certificate_inputs import (
    SessionCertificateInputs,
)


def build_certificate(
    request: StageCloseCertificateRequest,
    inputs: SessionCertificateInputs,
    final: ResourceReservation,
    reconciliation: ResourceReconciliation,
    *,
    issued_at: str,
) -> StageCloseCertificate:
    payload = {
        "certificate_id": _certificate_id(request, inputs, reconciliation),
        **_intent_and_session_fields(request, inputs),
        **_governance_fields(inputs),
        **_resource_and_evidence_fields(request, inputs, final, reconciliation),
        "issued_at": issued_at,
    }
    return StageCloseCertificate.model_validate(payload)


def _certificate_id(
    request: StageCloseCertificateRequest,
    inputs: SessionCertificateInputs,
    reconciliation: ResourceReconciliation,
) -> str:
    session = inputs.session
    return stable_id(
        "stage-close-certificate",
        session.scope.project_id,
        session.scope.work_item_id,
        session.scope.stage_instance_id,
        session.scope.session_id,
        request.intent.gate_id,
        request.intent.close_kind,
        request.intent.target_status,
        request.intent.loop_id,
        str(request.intent.loop_round_number),
        session.session_digest,
        request.evidence.candidate_manifest_digest,
        request.evidence.evidence_digest,
        reconciliation.reconciliation_digest,
    )


def _intent_and_session_fields(
    request: StageCloseCertificateRequest,
    inputs: SessionCertificateInputs,
) -> dict[str, object]:
    session, intent = inputs.session, request.intent
    return {
        "scope": session.scope,
        "gate_id": intent.gate_id,
        "close_intent_digest": intent.close_intent_digest,
        "close_kind": intent.close_kind,
        "target_status": intent.target_status,
        "command_id": intent.command_id,
        "work_item_id": session.scope.work_item_id,
        "loop_id": intent.loop_id,
        "loop_round_number": intent.loop_round_number,
        "stage_instance_id": session.scope.stage_instance_id,
        "candidate_manifest_digest": session.active_candidate_digest,
        "evidence_digest": request.evidence.evidence_digest,
        "protected_path_set": request.evidence.protected_path_set,
        "task_risk_profile_digest": session.active_risk_profile_digest,
        "session_revision": session.revision,
        "session_digest": session.session_digest,
    }


def _governance_fields(inputs: SessionCertificateInputs) -> dict[str, object]:
    session, plan, binding, cohort = (
        inputs.session,
        inputs.plan,
        inputs.binding_set,
        inputs.cohort,
    )
    proposal = plan.proposal
    return {
        "registry_digest": proposal.registry_digest,
        "selection_policy_digest": proposal.selection_policy_digest,
        "budget_policy_digest": proposal.budget_policy_digest,
        "policy_digest": session.policy_digest,
        "optimization_snapshot_digest": session.optimization_snapshot_digest,
        "panel_plan_digest": plan.plan_digest,
        "binding_digest": binding.binding_set_digest,
        "active_cohort_id": cohort.cohort_id,
        "satisfied_slot_ids": tuple(item.slot_id for item in inputs.passes),
        "required_role_coverage_proof_digest": _coverage_digest(inputs),
        "quorum_policy_digest": proposal.quorum.source_policy_digest,
        "finding_ledger_digest": inputs.ledger.ledger_digest,
    }


def _resource_and_evidence_fields(
    request: StageCloseCertificateRequest,
    inputs: SessionCertificateInputs,
    final: ResourceReservation,
    reconciliation: ResourceReconciliation,
) -> dict[str, object]:
    session = inputs.session
    return {
        "budget_revision": session.budget_revision,
        "budget_grant_digests": session.budget_grant_digests,
        "final_resource_reservation_digest": final.reservation_digest,
        "resource_reconciliation_digest": reconciliation.reconciliation_digest,
        "resource_fencing_epoch": reconciliation.fencing_token,
        "test_evidence_digest": request.evidence.test_evidence_digest,
        "integrity_evidence_digest": request.evidence.integrity_evidence_digest,
    }


def _coverage_digest(inputs: SessionCertificateInputs) -> str:
    return canonical_digest(
        {
            "plan_digest": inputs.plan.plan_digest,
            "cohort_id": inputs.cohort.cohort_id,
            "satisfied_slot_ids": tuple(item.slot_id for item in inputs.passes),
            "coverage_proof": inputs.plan.proposal.coverage_proof,
        },
        CanonicalizationPolicy(
            set_like_fields=frozenset(
                {"satisfied_slot_ids", "coverage_proof.required_slot_ids"}
            )
        ),
    )
