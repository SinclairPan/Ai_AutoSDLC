from __future__ import annotations

import json
from pathlib import Path

import pytest

from ai_sdlc.core.loop_models import LoopRound, LoopRun, LoopStatus, LoopType
from ai_sdlc.core.requirement_loop import RequirementLoopCommandResult
from ai_sdlc.core.stage_review.activation_policy_store import current_activation_policy
from ai_sdlc.core.stage_review.activation_store import (
    _read_activation_session_records as read_activation_session_records,
)
from ai_sdlc.core.stage_review.activation_store import (
    _record_enforced_activation_session as record_enforced_activation_session,
)
from ai_sdlc.core.stage_review.adapters import ImplementationStageAdapter
from ai_sdlc.core.stage_review.canonical import CanonicalizationPolicy, canonical_digest
from ai_sdlc.core.stage_review.close_gate_models import (
    GateApplicabilityDecision,
    PreparedStageClose,
)
from ai_sdlc.core.stage_review.close_models import StageCloseAuthorization
from ai_sdlc.core.stage_review.optimization.observations import (
    OptimizationObservationStore,
)
from ai_sdlc.core.stage_review.stage_adapter_registry import (
    default_stage_candidate_adapter_registry,
)
from ai_sdlc.core.stage_review.stage_close_product_runtime import (
    authorize_product_stage_close,
)
from ai_sdlc.core.stage_review.stage_close_result_codec import (
    persist_product_result,
    product_result_path,
    recover_product_result,
)
from ai_sdlc.core.stage_review.stage_review_plan_runtime import HeldStageReviewPlan
from tests.integration.test_canonical_stage_review_executor import _executor_rig


def test_authorized_session_consumes_certificate_before_product_close(
    tmp_path: Path,
) -> None:
    sessions = []
    rig = _executor_rig(
        tmp_path,
        transport_available=True,
        on_authorized=sessions.append,
    )
    outcome = rig.executor.execute(rig.request)
    prepared = _prepared_close(tmp_path)
    runtime = HeldStageReviewPlan(
        planned=rig.request.proposal,
        held=_held_plan(rig.request),
        source_snapshot=rig.request.source_snapshot,
        refs={},
    )

    def writer() -> dict[str, str]:
        path = tmp_path / prepared.close_artifact_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{"status":"closed"}\n', encoding="utf-8")
        return {"status": "ready", "loop_status": "closed"}

    def record_closed(authorization: StageCloseAuthorization) -> None:
        record_enforced_activation_session(
            tmp_path,
            candidate=rig.request.candidate,
            panel_plan_digest=rig.request.plan.plan_digest,
            risk_level=rig.request.proposal.risk_profile.risk_level,
            review_outcome=outcome,
            authorization=authorization,
        )

    result = authorize_product_stage_close(
        prepared,
        _enforce_decision(tmp_path, prepared),
        runtime,
        sessions[0],
        writer,
        on_closed=record_closed,
    )

    assert outcome.status == "completed"
    assert result == {"status": "ready", "loop_status": "closed"}
    assert tuple(tmp_path.rglob("stage-close-authorizations/*.json"))
    assert tuple(tmp_path.rglob("certificates/*.json"))
    assert tuple(tmp_path.rglob("certificate-proofs/*.json"))
    assert tuple(tmp_path.rglob("stage-close-authorizer/claims/*.json"))
    assert tuple(tmp_path.rglob("stage-close-authorizer/receipts/*.json"))
    activation = read_activation_session_records(tmp_path)
    assert len(activation) == 1
    assert activation[0].observation.mode == "enforce"
    assert activation[0].scope.session_id == rig.request.candidate.review_session_id
    observations = OptimizationObservationStore(
        tmp_path,
        project_id=rig.request.candidate.project_id,
    ).read_session(rig.request.candidate.review_session_id)
    assert tuple(item.observation_kind for item in observations) == (
        "created",
        "consumed",
    )


def test_same_product_close_command_recovers_without_rerunning_writer(
    tmp_path: Path,
) -> None:
    sessions = []
    rig = _executor_rig(
        tmp_path,
        transport_available=True,
        on_authorized=sessions.append,
    )
    outcome = rig.executor.execute(rig.request)
    assert outcome.status == "completed", outcome
    assert sessions
    prepared = _prepared_close(tmp_path)
    runtime = HeldStageReviewPlan(
        planned=rig.request.proposal,
        held=_held_plan(rig.request),
        source_snapshot=rig.request.source_snapshot,
        refs={},
    )
    calls = 0

    def writer() -> dict[str, str]:
        nonlocal calls
        calls += 1
        path = tmp_path / prepared.close_artifact_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{"status":"closed"}\n', encoding="utf-8")
        return {"status": "ready", "loop_status": "closed"}

    decision = _enforce_decision(tmp_path, prepared)

    def record_closed(authorization: StageCloseAuthorization) -> None:
        record_enforced_activation_session(
            tmp_path,
            candidate=rig.request.candidate,
            panel_plan_digest=rig.request.plan.plan_digest,
            risk_level=rig.request.proposal.risk_profile.risk_level,
            review_outcome=outcome,
            authorization=authorization,
        )

    first = authorize_product_stage_close(
        prepared,
        decision,
        runtime,
        sessions[0],
        writer,
        on_closed=record_closed,
    )
    second = authorize_product_stage_close(
        prepared,
        decision,
        runtime,
        sessions[0],
        writer,
        on_closed=record_closed,
    )

    assert second == first
    assert calls == 1
    assert len(read_activation_session_records(tmp_path)) == 1


def test_product_result_codec_restores_governed_model(tmp_path: Path) -> None:
    prepared = _prepared_close(tmp_path)
    result = RequirementLoopCommandResult(
        status="ready",
        loop_id=prepared.loop_id,
        loop_status="passed",
    )

    persist_product_result(prepared, result)

    assert recover_product_result(prepared) == result


def test_product_result_codec_rejects_tampered_payload(tmp_path: Path) -> None:
    prepared = _prepared_close(tmp_path)
    persist_product_result(prepared, {"status": "ready"})
    path = product_result_path(prepared)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["payload"]["status"] = "blocked"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="result_digest does not match content"):
        recover_product_result(prepared)


def _prepared_close(root: Path) -> PreparedStageClose:
    state = LoopRun(
        loop_id="implementation.integration",
        loop_type=LoopType.IMPLEMENTATION,
        status=LoopStatus.PASSED,
        work_item_id="work-item.one",
        current_round=1,
        rounds=[LoopRound(round_number=1, status=LoopStatus.PASSED)],
    )
    contract = (
        default_stage_candidate_adapter_registry()
        .resolve_instance(ImplementationStageAdapter())
        .contract
    )
    return PreparedStageClose(
        root=root,
        adapter_id=contract.adapter_id,
        adapter_version=contract.adapter_version,
        adapter_contract_digest=contract.contract_digest,
        stage_key="implementation",
        loop_id=state.loop_id,
        loop_round_number=1,
        stage_instance_id="implementation",
        work_item_id=state.work_item_id,
        close_kind="implementation-close",
        target_status="closed",
        stage_status="passed",
        close_artifact_path=".ai-sdlc/loops/implementation/integration/close.json",
        stage_input_digest=canonical_digest(state, CanonicalizationPolicy()),
        loop_created_at=state.created_at,
        gate_contract_version="1.0.0",
        risk_level="low",
        stage_state=state,
    )


def _enforce_decision(
    root: Path,
    prepared: PreparedStageClose,
) -> GateApplicabilityDecision:
    policy = current_activation_policy(root)
    return GateApplicabilityDecision(
        decision_id="decision.product-enforce",
        gate_id="stage-close-authorizer",
        stage_key=prepared.stage_key,
        loop_id=prepared.loop_id,
        mode="enforce",
        policy_id=policy.policy_id,
        policy_version=policy.policy_version,
        policy_digest=policy.policy_digest,
        reason_code="test-product-enforce",
    )


def _held_plan(request):
    from ai_sdlc.core.stage_review.shadow_plan_reservation import HeldShadowPanelPlan

    return HeldShadowPanelPlan(
        plan=request.plan,
        governor=request.governor,
        lease_owner=request.lease_owner,
    )
