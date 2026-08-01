from __future__ import annotations

import json
import multiprocessing
import os
import threading
from dataclasses import replace
from pathlib import Path
from typing import Protocol

import pytest

from ai_sdlc.core.loop_models import LoopRound, LoopRun, LoopStatus, LoopType
from ai_sdlc.core.requirement_loop import RequirementLoopCommandResult
from ai_sdlc.core.stage_review import (
    canonical_stage_review_executor,
    codex_review_runtime,
)
from ai_sdlc.core.stage_review.activation_policy_store import current_activation_policy
from ai_sdlc.core.stage_review.activation_store import (
    _read_activation_session_records as read_activation_session_records,
)
from ai_sdlc.core.stage_review.activation_store import (
    _record_enforced_activation_session as record_enforced_activation_session,
)
from ai_sdlc.core.stage_review.adapters import ImplementationStageAdapter
from ai_sdlc.core.stage_review.authorizer import StageCloseAuthorizer
from ai_sdlc.core.stage_review.canonical import CanonicalizationPolicy, canonical_digest
from ai_sdlc.core.stage_review.canonical_stage_review_support import execution_scope
from ai_sdlc.core.stage_review.close_gate_models import (
    GateApplicabilityDecision,
    PreparedStageClose,
)
from ai_sdlc.core.stage_review.close_models import StageCloseAuthorization
from ai_sdlc.core.stage_review.close_store import StageCloseStore
from ai_sdlc.core.stage_review.finding_models import FindingScope
from ai_sdlc.core.stage_review.optimization.observations import (
    OptimizationObservationStore,
)
from ai_sdlc.core.stage_review.optimization.session_coordinator import (
    SessionOptimizationCoordinator,
)
from ai_sdlc.core.stage_review.review_completion import ReviewSessionCompletion
from ai_sdlc.core.stage_review.session import (
    SessionIntegrityError,
    StageReviewSessionService,
)
from ai_sdlc.core.stage_review.session_models import SessionEvent
from ai_sdlc.core.stage_review.session_store import SessionEventStore
from ai_sdlc.core.stage_review.shadow_planning_runtime import ShadowPlanningPreflight
from ai_sdlc.core.stage_review.shadow_planning_store import (
    _persist_shadow_plan as persist_shadow_plan,
)
from ai_sdlc.core.stage_review.stage_adapter_registry import (
    default_stage_candidate_adapter_registry,
)
from ai_sdlc.core.stage_review.stage_close_product_contract import (
    _enforce_partial_stage_close_is_recoverable,
)
from ai_sdlc.core.stage_review.stage_close_product_runtime import (
    authorize_product_stage_close,
    recover_product_stage_close,
)
from ai_sdlc.core.stage_review.stage_close_result_codec import (
    persist_product_result,
    product_result_path,
    recover_product_result,
)
from ai_sdlc.core.stage_review.stage_review_execution import (
    StageCloseGateUnavailableError,
    StageReviewExecutionOutcome,
    StageReviewExecutionRequest,
)
from ai_sdlc.core.stage_review.stage_review_plan_runtime import (
    HeldStageReviewPlan,
    _recover_stage_review_plan,
)
from tests.integration.test_canonical_stage_review_executor import (
    _executor_for_request,
    _executor_rig,
    _ExecutorRig,
)


class _ResultQueue(Protocol):
    def put(self, value: object) -> None: ...

    def close(self) -> None: ...

    def join_thread(self) -> None: ...


class _ReviewExecutor(Protocol):
    def execute(
        self,
        request: StageReviewExecutionRequest,
    ) -> StageReviewExecutionOutcome: ...


class _WaitForWinnerExecutor:
    def __init__(
        self,
        inner: _ReviewExecutor,
        winner_done: threading.Event,
    ) -> None:
        self._inner = inner
        self._winner_done = winner_done

    def execute(
        self,
        request: StageReviewExecutionRequest,
    ) -> StageReviewExecutionOutcome:
        if not self._winner_done.wait(timeout=30):
            raise TimeoutError("winning recovery did not complete")
        return self._inner.execute(request)


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


def test_committed_close_recovery_consumes_the_review_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = multiprocessing.get_context("spawn")
    result_queue = context.Queue()
    interrupted = context.Process(
        target=_hard_exit_after_close_commit_worker,
        args=(str(tmp_path), result_queue),
    )
    interrupted.start()
    interrupted.join(timeout=60)

    assert not interrupted.is_alive()
    assert interrupted.exitcode == 88
    request, prepared, decision, first_outcome = result_queue.get(timeout=5)
    result_queue.close()
    result_queue.join_thread()
    close_store = StageCloseStore(
        tmp_path,
        project_id=request.candidate.project_id,
        lock_timeout_seconds=2,
    )
    claim_path = next(tmp_path.rglob("stage-close-authorizer/claims/*.json"))
    claim = close_store.read_claim(claim_path.stem)
    assert claim is not None
    assert close_store.require_consumable_state(claim).status == "closed"
    recovered_runtime = _recover_stage_review_plan(
        prepared,
        decision,
        request.candidate,
        request.source_snapshot,
    )
    replay_executor = _executor_for_request(
        tmp_path,
        recovered_runtime.execution_request(mode="enforce"),
    )

    def build_replay_executor(*_args, **kwargs):
        replay_executor._on_authorized = kwargs.get("on_authorized")
        return replay_executor

    monkeypatch.setattr(
        codex_review_runtime,
        "resolve_codex_runtime_prerequisites",
        lambda: ("deterministic-provider", object()),
    )
    monkeypatch.setattr(
        codex_review_runtime,
        "_build_executor",
        build_replay_executor,
    )
    monkeypatch.setattr(
        codex_review_runtime,
        "_recover_stage_review_plan",
        lambda *_args: recovered_runtime,
    )
    monkeypatch.setattr(
        canonical_stage_review_executor,
        "build_session_optimization_coordinator",
        lambda *_args, **_kwargs: None,
    )
    result = codex_review_runtime.CodexStageReviewExecutor(tmp_path).enforce_close(
        prepared,
        decision,
        ShadowPlanningPreflight(
            candidate=request.candidate,
            source_snapshot=request.source_snapshot,
            risk_profile=request.proposal.risk_profile,
            failure=None,
        ),
        lambda: (_ for _ in ()).throw(
            AssertionError("committed close reran product writer")
        ),
    )

    assert result == {"status": "ready", "loop_status": "closed"}
    replay_session = SessionEventStore(
        tmp_path,
        project_id=request.candidate.project_id,
    ).rebuild(execution_scope(request))
    assert replay_session is not None
    assert replay_session.state == "consumed"
    assert first_outcome.status == "completed"


def test_committed_close_recovery_does_not_require_live_codex_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request, prepared, decision = _interrupted_committed_close(tmp_path)
    monkeypatch.setattr(
        codex_review_runtime,
        "resolve_codex_runtime_prerequisites",
        lambda: None,
    )
    monkeypatch.setattr(
        SessionOptimizationCoordinator,
        "bind_start",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("committed close rebound the current optimization snapshot")
        ),
    )

    result = _enforce_recovered_close(
        tmp_path,
        prepared,
        decision,
        request,
    )

    assert result == {"status": "ready", "loop_status": "closed"}
    replay_session = SessionEventStore(
        tmp_path,
        project_id=request.candidate.project_id,
    ).rebuild(execution_scope(request))
    assert replay_session is not None
    assert replay_session.state == "consumed"
    observations = OptimizationObservationStore(
        tmp_path,
        project_id=request.candidate.project_id,
    ).read_session(request.candidate.review_session_id)
    assert tuple(item.observation_kind for item in observations) == (
        "created",
        "consumed",
    )


def test_recovered_close_rejects_rehashed_session_receipt(
    tmp_path: Path,
) -> None:
    rig, prepared, decision, runtime, sessions = _closed_product(tmp_path)
    _forge_consumed_session_receipt(sessions, execution_scope(rig.request))

    with pytest.raises(
        StageCloseGateUnavailableError,
        match="review-runtime-integrity-failure",
    ):
        codex_review_runtime.CodexStageReviewExecutor(tmp_path).enforce_close(
            prepared,
            decision,
            ShadowPlanningPreflight(
                candidate=rig.request.candidate,
                source_snapshot=rig.request.source_snapshot,
                risk_profile=rig.request.proposal.risk_profile,
                failure=None,
            ),
            lambda: (_ for _ in ()).throw(
                AssertionError("forged recovery reran product writer")
            ),
        )
    assert recover_product_stage_close(
        prepared,
        decision,
        runtime.planned.candidate,
    ) is not None


def test_recovered_product_runtime_rejects_rehashed_session_receipt(
    tmp_path: Path,
) -> None:
    rig, prepared, decision, runtime, sessions = _closed_product(tmp_path)
    _forge_consumed_session_receipt(sessions, execution_scope(rig.request))

    with pytest.raises(ValueError, match="recovered product close session diverged"):
        authorize_product_stage_close(
            prepared,
            decision,
            runtime,
            sessions,
            lambda: (_ for _ in ()).throw(
                AssertionError("forged recovery reran product writer")
            ),
        )


def test_two_recoverers_return_the_same_committed_close(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request, prepared, decision = _interrupted_committed_close(tmp_path)
    runtime = _recover_stage_review_plan(
        prepared,
        decision,
        request.candidate,
        request.source_snapshot,
    )
    entry = threading.Barrier(2)
    winner_done = threading.Event()
    original_state = codex_review_runtime._recovered_review_session_state

    def synchronized_state(*args):
        state = original_state(*args)
        entry.wait(timeout=30)
        return state

    def build_executor(*_args, **kwargs):
        inner = _executor_for_request(
            tmp_path,
            runtime.execution_request(mode="enforce"),
            on_authorized=kwargs.get("on_authorized"),
        )
        if threading.current_thread().name == "recovery-loser":
            return _WaitForWinnerExecutor(inner, winner_done)
        return inner

    monkeypatch.setattr(
        codex_review_runtime,
        "_recovered_review_session_state",
        synchronized_state,
    )
    monkeypatch.setattr(
        codex_review_runtime,
        "resolve_codex_runtime_prerequisites",
        lambda: ("deterministic-provider", object()),
    )
    monkeypatch.setattr(codex_review_runtime, "_build_executor", build_executor)
    monkeypatch.setattr(
        codex_review_runtime,
        "_recover_stage_review_plan",
        lambda *_args: runtime,
    )
    monkeypatch.setattr(
        canonical_stage_review_executor,
        "build_session_optimization_coordinator",
        lambda *_args, **_kwargs: None,
    )
    results: dict[str, object] = {}
    errors: dict[str, BaseException] = {}

    def recover(label: str) -> None:
        try:
            results[label] = _enforce_recovered_close(
                tmp_path, prepared, decision, request
            )
        except BaseException as exc:
            errors[label] = exc
        finally:
            if label == "winner":
                winner_done.set()

    threads = (
        threading.Thread(target=recover, args=("winner",), name="recovery-winner"),
        threading.Thread(target=recover, args=("loser",), name="recovery-loser"),
    )
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)

    assert not any(thread.is_alive() for thread in threads)
    assert errors == {}
    assert results == {
        "winner": {"status": "ready", "loop_status": "closed"},
        "loser": {"status": "ready", "loop_status": "closed"},
    }
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


def _closed_product(
    root: Path,
) -> tuple[
    _ExecutorRig,
    PreparedStageClose,
    GateApplicabilityDecision,
    HeldStageReviewPlan,
    StageReviewSessionService,
]:
    sessions: list[StageReviewSessionService] = []
    rig = _executor_rig(
        root,
        transport_available=True,
        on_authorized=sessions.append,
    )
    outcome = rig.executor.execute(rig.request)
    prepared = _prepared_close(root)
    decision = _enforce_decision(root, prepared)
    runtime = HeldStageReviewPlan(
        planned=rig.request.proposal,
        held=_held_plan(rig.request),
        source_snapshot=rig.request.source_snapshot,
        refs={},
    )

    def writer() -> dict[str, str]:
        path = root / prepared.close_artifact_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{"status":"closed"}\n', encoding="utf-8")
        return {"status": "ready", "loop_status": "closed"}

    def record_closed(authorization: StageCloseAuthorization) -> None:
        record_enforced_activation_session(
            root,
            candidate=rig.request.candidate,
            panel_plan_digest=rig.request.plan.plan_digest,
            risk_level=rig.request.proposal.risk_profile.risk_level,
            review_outcome=outcome,
            authorization=authorization,
        )

    authorize_product_stage_close(
        prepared,
        decision,
        runtime,
        sessions[0],
        writer,
        on_closed=record_closed,
    )
    return rig, prepared, decision, runtime, sessions[0]


def _forge_consumed_session_receipt(
    sessions: StageReviewSessionService,
    scope: FindingScope,
) -> None:
    projection_path = sessions.projection_path(scope)
    event_paths = tuple((projection_path.parent / "events").glob("*.json"))
    event_path = next(
        path
        for path in event_paths
        if json.loads(path.read_text(encoding="utf-8"))["event_kind"]
        == "close_receipt_committed"
    )
    payload = json.loads(event_path.read_text(encoding="utf-8"))
    receipt_id = "stage-close-receipt.forged"
    receipt_digest = "sha256:" + ("f" * 64)
    payload["projection_after"]["close_consumption_receipt_id"] = receipt_id
    payload["projection_after"]["close_consumption_receipt_digest"] = receipt_digest
    payload["artifact_refs"] = [
        {"artifact_id": receipt_id, "artifact_digest": receipt_digest}
    ]
    payload["event_digest"] = ""
    forged = SessionEvent.model_validate(payload)
    event_path.write_text(forged.model_dump_json(), encoding="utf-8")
    projection_path.unlink()


def _interrupted_committed_close(
    root: Path,
) -> tuple[
    StageReviewExecutionRequest,
    PreparedStageClose,
    GateApplicabilityDecision,
]:
    context = multiprocessing.get_context("spawn")
    result_queue = context.Queue()
    interrupted = context.Process(
        target=_hard_exit_after_close_commit_worker,
        args=(str(root), result_queue),
    )
    interrupted.start()
    interrupted.join(timeout=60)
    assert not interrupted.is_alive()
    assert interrupted.exitcode == 88
    request, prepared, decision, _ = result_queue.get(timeout=5)
    result_queue.close()
    result_queue.join_thread()
    return request, prepared, decision


def _enforce_recovered_close(
    root: Path,
    prepared: PreparedStageClose,
    decision: GateApplicabilityDecision,
    request: StageReviewExecutionRequest,
) -> object:
    return codex_review_runtime.CodexStageReviewExecutor(root).enforce_close(
        prepared,
        decision,
        ShadowPlanningPreflight(
            candidate=request.candidate,
            source_snapshot=request.source_snapshot,
            risk_profile=request.proposal.risk_profile,
            failure=None,
        ),
        lambda: (_ for _ in ()).throw(
            AssertionError("committed close reran product writer")
        ),
    )


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


def _hard_exit_after_close_commit_worker(
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
    persist_shadow_plan(
        root,
        rig.request.proposal,
        rig.request.plan,
        rig.request.source_snapshot,
    )
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
    original_checkpoint = StageCloseAuthorizer._checkpoint

    def hard_exit_after_materialize(
        self: StageCloseAuthorizer,
        phase: str,
    ) -> None:
        if phase == "state_materialized":
            os._exit(88)
        original_checkpoint(self, phase)

    StageCloseAuthorizer._checkpoint = hard_exit_after_materialize

    def writer() -> dict[str, str]:
        path = root / prepared.close_artifact_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{"status":"closed"}\n', encoding="utf-8")
        return {"status": "ready", "loop_status": "closed"}

    authorize_product_stage_close(
        prepared,
        decision,
        runtime,
        sessions[0],
        writer,
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
