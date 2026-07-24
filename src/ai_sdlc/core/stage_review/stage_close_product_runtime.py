"""把已授权 Review Session 原子消费为产品 Stage Close。"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from ai_sdlc.core.source_snapshot import SourceSnapshot, revalidate_source_snapshot
from ai_sdlc.core.stage_review.activation_policy_store import (
    current_activation_policy,
)
from ai_sdlc.core.stage_review.authorizer import StageCloseAuthorizer
from ai_sdlc.core.stage_review.candidate import (
    CandidateManifest,
    candidate_binding_digest,
)
from ai_sdlc.core.stage_review.canonical import CanonicalizationPolicy, canonical_digest
from ai_sdlc.core.stage_review.certificate_models import (
    StageCloseCertificateRequest,
    StageCloseEvidence,
    StageCloseIntent,
)
from ai_sdlc.core.stage_review.certificates import StageCloseCertificateAuthority
from ai_sdlc.core.stage_review.close_gate_models import (
    GateApplicabilityDecision,
    PreparedStageClose,
)
from ai_sdlc.core.stage_review.close_gate_observation import stage_close_operation_id
from ai_sdlc.core.stage_review.close_governance import (
    StageCloseGovernanceAuthority,
)
from ai_sdlc.core.stage_review.close_models import (
    CloseArtifactContract,
    StageCloseAuthorization,
    StageCloseContext,
)
from ai_sdlc.core.stage_review.finding_models import FindingScope
from ai_sdlc.core.stage_review.resource_builders import stable_id, utc_iso
from ai_sdlc.core.stage_review.session import StageReviewSessionService
from ai_sdlc.core.stage_review.stage_close_command_recovery import (
    recover_closed_command,
)
from ai_sdlc.core.stage_review.stage_close_result_codec import (
    persist_product_result,
    product_result_path,
    recover_product_result,
)
from ai_sdlc.core.stage_review.stage_review_execution import (
    StageReviewExecutionRequest,
)
from ai_sdlc.core.stage_review.stage_review_plan_runtime import HeldStageReviewPlan

_GOVERNANCE_ACTOR = "actor.ai-sdlc-stage-close"


class PreparedStageCloseEvidenceAuthority:
    """每次签发或恢复都从当前仓库重新构建候选证据。"""

    def __init__(
        self,
        prepared: PreparedStageClose,
        decision: GateApplicabilityDecision,
        candidate: CandidateManifest,
        source_snapshot: SourceSnapshot,
        marker_path: str,
    ) -> None:
        self._prepared = prepared
        self._decision = decision
        self._candidate = candidate
        self._source_snapshot = source_snapshot
        self._marker_path = marker_path

    def resolve_current(self, intent: StageCloseIntent) -> StageCloseEvidence | None:
        if not _intent_matches(
            self._prepared,
            self._decision,
            self._candidate,
            intent,
        ):
            return None
        if not _protected_inputs_are_current(
            self._prepared,
            self._decision,
            self._source_snapshot,
        ):
            return None
        return _build_evidence(
            self._prepared,
            self._decision,
            self._candidate,
            self._marker_path,
        )


def authorize_product_stage_close(
    prepared: PreparedStageClose,
    decision: GateApplicabilityDecision,
    runtime: HeldStageReviewPlan,
    sessions: StageReviewSessionService,
    writer: Callable[[], object],
    *,
    on_closed: Callable[[StageCloseAuthorization], None] | None = None,
) -> object:
    recovered = _recover_closed_authorization(prepared, decision, runtime)
    if recovered is not None:
        if on_closed is not None:
            on_closed(recovered)
        return recover_product_result(prepared)
    authorizer, context = _close_authority_context(
        prepared,
        decision,
        runtime,
        sessions,
    )
    captured: list[object] = []
    authorization = authorizer.authorize_stage_close(
        context,
        before_close_artifact=lambda: _run_product_writer(
            prepared,
            writer,
            captured,
        ),
    )
    if authorization.status != "closed":
        raise ValueError("stage close authorization did not commit")
    if on_closed is not None:
        on_closed(authorization)
    return captured[0] if captured else recover_product_result(prepared)


def _recover_closed_authorization(
    prepared: PreparedStageClose,
    decision: GateApplicabilityDecision,
    runtime: HeldStageReviewPlan,
) -> StageCloseAuthorization | None:
    candidate = runtime.planned.candidate
    authorization = recover_closed_command(
        prepared.root,
        project_id=candidate.project_id,
        command_id=_intent(prepared, decision, runtime).command_id,
        contract=_marker_contract(prepared, decision, candidate),
    )
    if authorization is None:
        return None
    return authorization


def _close_authority_context(
    prepared: PreparedStageClose,
    decision: GateApplicabilityDecision,
    runtime: HeldStageReviewPlan,
    sessions: StageReviewSessionService,
) -> tuple[StageCloseAuthorizer, StageCloseContext]:
    request = runtime.execution_request(mode="enforce")
    reconciliation_digest = _reconcile_review_resources(prepared, request)
    marker = _marker_contract(prepared, decision, runtime.planned.candidate)
    evidence_authority = PreparedStageCloseEvidenceAuthority(
        prepared,
        decision,
        runtime.planned.candidate,
        runtime.source_snapshot,
        marker.artifact_path,
    )
    evidence = evidence_authority.resolve_current(_intent(prepared, decision, runtime))
    if evidence is None:
        raise ValueError("stage close protected evidence is unavailable")
    intent = _intent(prepared, decision, runtime)
    session = sessions.get(intent.scope)
    certificates = StageCloseCertificateAuthority(
        sessions,
        request.governor,
        context_authority=evidence_authority,
        clock=_clock,
    )
    certificate_request = StageCloseCertificateRequest(
        intent=intent,
        evidence=evidence,
        expected_session_revision=session.revision,
        resource_reconciliation_digest=reconciliation_digest,
    )
    certificate = certificates.issue(certificate_request)
    authorizer = _authorizer(prepared.root, certificates, request)
    context = StageCloseContext(
        certificate=certificate,
        certificate_request=certificate_request,
        close_artifact=marker,
        worktree_identity=authorizer.worktree_identity,
        lease_owner=request.lease_owner,
        lease_seconds=60,
    )
    return authorizer, context


def _reconcile_review_resources(
    prepared: PreparedStageClose,
    request: StageReviewExecutionRequest,
) -> str:
    operation_id = stable_id(
        "stage-close-reconcile",
        stage_close_operation_id(prepared),
    )
    existing = request.governor.get_operation_event(operation_id)
    if existing is not None:
        if existing.reconciliation is None:
            raise ValueError("stage close reconciliation fact is incomplete")
        return existing.reconciliation.reconciliation_digest
    current = request.governor.get_reservation(request.plan.final_reservation_id)
    reconciled = request.governor.reconcile(
        current.reservation_id,
        lease_owner=request.lease_owner,
        expected_fencing_token=current.fencing_token,
        operation_id=operation_id,
        now=datetime.now(UTC),
    )
    if reconciled.reconciliation is None:
        raise ValueError(
            f"stage close resource reconciliation failed: {reconciled.result_code}"
        )
    return reconciled.reconciliation.reconciliation_digest


def _authorizer(
    root: Path,
    certificates: StageCloseCertificateAuthority,
    request: StageReviewExecutionRequest,
) -> StageCloseAuthorizer:
    governance = StageCloseGovernanceAuthority(
        root,
        project_id=request.candidate.project_id,
        authority_id="authority.ai-sdlc-stage-close",
        authorized_actor_ids=(_GOVERNANCE_ACTOR,),
        clock=_clock,
    )
    return StageCloseAuthorizer(
        root,
        project_id=request.candidate.project_id,
        certificate_authority=certificates,
        governance_authority=governance,
        clock=_clock,
    )


def _intent(
    prepared: PreparedStageClose,
    decision: GateApplicabilityDecision,
    runtime: HeldStageReviewPlan,
) -> StageCloseIntent:
    candidate = runtime.planned.candidate
    operation_id = stage_close_operation_id(prepared)
    return StageCloseIntent(
        scope=_scope(candidate),
        gate_id=decision.gate_id,
        close_kind=prepared.close_kind,
        target_status=prepared.target_status,
        command_id=stable_id("stage-close-command", operation_id),
        idempotency_key=stable_id("stage-close-key", operation_id),
        loop_id=prepared.loop_id,
        loop_round_number=prepared.loop_round_number,
    )


def _scope(candidate: CandidateManifest) -> FindingScope:
    return FindingScope(
        project_id=candidate.project_id,
        work_item_id=candidate.work_item_id,
        stage_instance_id=candidate.stage_instance_id,
        session_id=candidate.review_session_id,
    )


def _marker_contract(
    prepared: PreparedStageClose,
    decision: GateApplicabilityDecision,
    candidate: CandidateManifest,
) -> CloseArtifactContract:
    operation_id = stage_close_operation_id(prepared)
    path = f".ai-sdlc/state/stage-close-authorizations/{operation_id}.json"
    result_path = product_result_path(prepared).relative_to(prepared.root).as_posix()
    return CloseArtifactContract(
        artifact_path=path,
        payload={
            "schema_version": "stage-close-authorization.v1",
            "artifact_kind": "stage-close-authorization",
            "operation_id": operation_id,
            "stage_key": prepared.stage_key,
            "close_kind": prepared.close_kind,
            "target_status": prepared.target_status,
            "stage_input_digest": prepared.stage_input_digest,
            "product_close_artifact_path": prepared.close_artifact_path,
            "product_result_artifact_path": result_path,
            "candidate_manifest_digest": candidate_binding_digest(candidate),
            "gate_decision_digest": decision.decision_digest,
        },
    )


def _build_evidence(
    prepared: PreparedStageClose,
    decision: GateApplicabilityDecision,
    candidate: CandidateManifest,
    marker_path: str,
) -> StageCloseEvidence:
    protected = tuple(
        sorted(
            {
                *candidate.protected_source_set,
                prepared.close_artifact_path,
                marker_path,
                product_result_path(prepared).relative_to(prepared.root).as_posix(),
            }
        )
    )
    return StageCloseEvidence(
        candidate_manifest_digest=candidate_binding_digest(candidate),
        test_evidence_digest=canonical_digest(
            {"test_evidence_digests": candidate.test_evidence_digests},
            CanonicalizationPolicy(),
        ),
        integrity_evidence_digest=canonical_digest(
            {
                "source_snapshot_digest": candidate.source_snapshot_digest,
                "source_tree_digest": candidate.source_tree_digest,
                "stage_input_digest": prepared.stage_input_digest,
                "gate_decision_digest": decision.decision_digest,
            },
            CanonicalizationPolicy(),
        ),
        protected_path_set=protected,
    )


def _intent_matches(
    prepared: PreparedStageClose,
    decision: GateApplicabilityDecision,
    candidate: CandidateManifest,
    intent: StageCloseIntent,
) -> bool:
    return (
        intent.scope == _scope(candidate)
        and candidate.work_item_id == prepared.work_item_id
        and candidate.loop_id == prepared.loop_id
        and candidate.loop_round_number == prepared.loop_round_number
        and candidate.stage_key == prepared.stage_key
        and candidate.stage_instance_id == prepared.stage_instance_id
        and intent.gate_id == decision.gate_id
        and intent.close_kind == prepared.close_kind
        and intent.target_status == prepared.target_status
        and intent.loop_id == prepared.loop_id
        and intent.loop_round_number == prepared.loop_round_number
    )


def _protected_inputs_are_current(
    prepared: PreparedStageClose,
    decision: GateApplicabilityDecision,
    source_snapshot: SourceSnapshot,
) -> bool:
    stage_digest = canonical_digest(prepared.stage_state, CanonicalizationPolicy())
    if stage_digest != prepared.stage_input_digest:
        return False
    policy = current_activation_policy(prepared.root)
    if policy.policy_digest != decision.policy_digest:
        return False
    return revalidate_source_snapshot(prepared.root, source_snapshot).fresh


def _run_product_writer(
    prepared: PreparedStageClose,
    writer: Callable[[], object],
    captured: list[object],
) -> object:
    result = writer()
    if not (prepared.root / prepared.close_artifact_path).is_file():
        raise ValueError("product close writer did not create its close artifact")
    persist_product_result(prepared, result)
    captured.append(result)
    return result


def _clock() -> str:
    return utc_iso(datetime.now(UTC))


__all__ = ["PreparedStageCloseEvidenceAuthority", "authorize_product_stage_close"]
