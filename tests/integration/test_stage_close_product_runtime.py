from __future__ import annotations

import json
import multiprocessing
import os
from dataclasses import replace
from pathlib import Path
from typing import Protocol

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
from ai_sdlc.core.stage_review.canonical_stage_review_support import execution_scope
from ai_sdlc.core.stage_review.close_gate_models import (
    GateApplicabilityDecision,
    PreparedStageClose,
)
from ai_sdlc.core.stage_review.close_models import StageCloseAuthorization
from ai_sdlc.core.stage_review.close_store import StageCloseStore
from ai_sdlc.core.stage_review.optimization.observations import (
    OptimizationObservationStore,
)
from ai_sdlc.core.stage_review.review_completion import ReviewSessionCompletion
from ai_sdlc.core.stage_review.session import SessionIntegrityError
from ai_sdlc.core.stage_review.stage_adapter_registry import (
    default_stage_candidate_adapter_registry,
)
from ai_sdlc.core.stage_review.stage_close_product_contract import (
    _enforce_partial_stage_close_is_recoverable,
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
from tests.integration.test_canonical_stage_review_executor import (
    _executor_for_request,
    _executor_rig,
)


class _ResultQueue(Protocol):
    def put(self, value: object) -> None: ...

    def close(self) -> None: ...

    def join_thread(self) -> None: ...


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


def test_untracked_product_close_is_not_an_enforce_recovery_authority(
    tmp_path: Path,
) -> None:
    rig = _executor_rig(tmp_path, transport_available=True)
    prepared = _prepared_close(tmp_path)
    path = tmp_path / prepared.close_artifact_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"status":"closed"}\n', encoding="utf-8")

    assert (
        _enforce_partial_stage_close_is_recoverable(
            prepared,
            _enforce_decision(tmp_path, prepared),
            rig.request.candidate,
        )
        is False
    )


def test_enforce_prepared_claim_authorizes_product_close_recovery(
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
    prepared = _prepared_close(tmp_path)
    decision = _enforce_decision(tmp_path, prepared)
    runtime = HeldStageReviewPlan(
        planned=rig.request.proposal,
        held=_held_plan(rig.request),
        source_snapshot=rig.request.source_snapshot,
        refs={},
    )

    def interrupted_writer() -> dict[str, str]:
        path = tmp_path / prepared.close_artifact_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{"status":"closed"}\n', encoding="utf-8")
        raise RuntimeError("simulated product writer interruption")

    with pytest.raises(RuntimeError, match="simulated product writer interruption"):
        authorize_product_stage_close(
            prepared,
            decision,
            runtime,
            sessions[0],
            interrupted_writer,
        )

    assert _enforce_partial_stage_close_is_recoverable(
        prepared,
        decision,
        rig.request.candidate,
    )
    changed = replace(
        prepared,
        stage_input_digest="sha256:" + ("f" * 64),
    )
    assert (
        _enforce_partial_stage_close_is_recoverable(
            changed,
            decision,
            rig.request.candidate,
        )
        is False
    )


def test_enforce_prepared_claim_resumes_original_review_and_close_transaction(
    tmp_path: Path,
) -> None:
    context = multiprocessing.get_context("spawn")
    result_queue = context.Queue()
    interrupted = context.Process(
        target=_hard_exit_product_close_worker,
        args=(str(tmp_path), result_queue),
    )
    interrupted.start()
    interrupted.join(timeout=60)

    assert not interrupted.is_alive()
    assert interrupted.exitcode == 86
    request, prepared, decision, first_outcome = result_queue.get(timeout=5)
    result_queue.close()
    result_queue.join_thread()
    runtime = HeldStageReviewPlan(
        planned=request.proposal,
        held=_held_plan(request),
        source_snapshot=request.source_snapshot,
        refs={},
    )
    assert (tmp_path / prepared.close_artifact_path).is_file()
    assert not product_result_path(prepared).exists()
    close_store = StageCloseStore(
        tmp_path,
        project_id=request.candidate.project_id,
        lock_timeout_seconds=2,
    )
    claim_paths = tuple(tmp_path.rglob("stage-close-authorizer/claims/*.json"))
    assert len(claim_paths) == 1
    claim = close_store.read_claim(claim_paths[0].stem)
    assert claim is not None
    interrupted_state = close_store.require_consumable_state(claim)
    assert interrupted_state.status == "consuming"
    assert interrupted_state.revision == 1
    assert interrupted_state.event_kinds == ("prepared",)
    assert not interrupted_state.close_artifact_digest
    assert close_store.read_receipt(claim.claim_id) is None
    recovered_sessions = []
    replay_executor = _executor_for_request(
        tmp_path,
        request,
        on_authorized=recovered_sessions.append,
    )
    replay_outcome = replay_executor.execute(request)

    assert replay_outcome == first_outcome
    assert len(recovered_sessions) == 1
    resumed_writer_calls = 0

    def resumed_writer() -> dict[str, str]:
        nonlocal resumed_writer_calls
        resumed_writer_calls += 1
        path = tmp_path / prepared.close_artifact_path
        path.write_text('{"status":"closed"}\n', encoding="utf-8")
        return {"status": "ready", "loop_status": "closed"}

    second = authorize_product_stage_close(
        prepared,
        decision,
        runtime,
        recovered_sessions[0],
        resumed_writer,
    )
    third = authorize_product_stage_close(
        prepared,
        decision,
        runtime,
        recovered_sessions[0],
        lambda: (_ for _ in ()).throw(
            AssertionError("closed command reran product writer")
        ),
    )

    assert second == {"status": "ready", "loop_status": "closed"}
    assert third == second
    assert resumed_writer_calls == 1
    assert recover_product_result(prepared) == second
    closed_state = close_store.require_consumable_state(claim)
    assert closed_state.event_kinds == (
        "prepared",
        "close_written",
        "reconciled",
        "committed",
    )
    assert recovered_sessions[0].get(execution_scope(request)).state == "consumed"
    assert len(tuple(tmp_path.rglob("certificates/*.json"))) == 1
    assert len(tuple(tmp_path.rglob("stage-close-authorizer/claims/*.json"))) == 1
    assert len(tuple(tmp_path.rglob("stage-close-authorizer/receipts/*.json"))) == 1


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("session_head_event_digest", "sha256:" + ("a" * 64)),
        ("initial_review_seal_digest", "sha256:" + ("b" * 64)),
        ("required_pass_digests", ("sha256:" + ("c" * 64),)),
        ("completed_at", "2099-01-01T00:00:00Z"),
    ),
)
def test_consuming_review_recovery_rejects_rehashed_completion_tamper(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    sessions = []
    rig = _executor_rig(
        tmp_path,
        transport_available=True,
        on_authorized=sessions.append,
    )
    assert rig.executor.execute(rig.request).status == "completed"
    prepared = _prepared_close(tmp_path)
    runtime = HeldStageReviewPlan(
        planned=rig.request.proposal,
        held=_held_plan(rig.request),
        source_snapshot=rig.request.source_snapshot,
        refs={},
    )

    def interrupted_writer() -> None:
        raise RuntimeError("simulated product writer interruption")

    with pytest.raises(RuntimeError, match="simulated product writer interruption"):
        authorize_product_stage_close(
            prepared,
            _enforce_decision(tmp_path, prepared),
            runtime,
            sessions[0],
            interrupted_writer,
        )
    path = sessions[0].projection_path(execution_scope(rig.request)).parent / "completion.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload[field] = value
    payload["completion_digest"] = ""
    tampered = ReviewSessionCompletion.model_validate(payload)
    path.write_text(
        json.dumps(tampered.model_dump(mode="json"), sort_keys=True) + "\n",
        encoding="utf-8",
    )
    recovered = []
    replay = _executor_for_request(
        tmp_path,
        rig.request,
        on_authorized=recovered.append,
    )

    with pytest.raises(
        SessionIntegrityError,
        match="consuming review session completion lineage diverged",
    ):
        replay.execute(rig.request)
    assert recovered == []


def _hard_exit_product_close_worker(
    root_value: str,
    result_queue: _ResultQueue,
) -> None:
    root = Path(root_value)
    sessions = []
    rig = _executor_rig(
        root,
        transport_available=True,
        on_authorized=sessions.append,
    )
    outcome = rig.executor.execute(rig.request)
    if outcome.status != "completed" or len(sessions) != 1:
        raise SystemExit(85)
    prepared = _prepared_close(root)
    decision = _enforce_decision(root, prepared)
    result_queue.put((rig.request, prepared, decision, outcome))
    result_queue.close()
    result_queue.join_thread()
    runtime = HeldStageReviewPlan(
        planned=rig.request.proposal,
        held=_held_plan(rig.request),
        source_snapshot=rig.request.source_snapshot,
        refs={},
    )

    def hard_exit_writer() -> None:
        path = root / prepared.close_artifact_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{"status":"closed"}\n', encoding="utf-8")
        os._exit(86)

    authorize_product_stage_close(
        prepared,
        decision,
        runtime,
        sessions[0],
        hard_exit_writer,
    )
    raise SystemExit(87)


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
