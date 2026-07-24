from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from threading import Barrier, Event
from unittest.mock import patch

import pytest

import ai_sdlc.core.requirement_loop as requirement_loop_module
from ai_sdlc.core.frontend_evidence_loop import (
    FrontendEvidenceSkipOptions,
)
from ai_sdlc.core.loop_models import LoopRound, LoopRun, LoopStatus, LoopType
from ai_sdlc.core.requirement_loop import (
    RequirementFreezeOptions,
    RequirementStartOptions,
    freeze_requirement_loop,
    start_requirement_loop,
)
from ai_sdlc.core.stage_review.adapters import RequirementStageAdapter
from ai_sdlc.core.stage_review.close_gate import (
    GateApplicabilityDecision,
    StageCloseGateUnavailableError,
    StageCloseGateway,
    prepare_loop_stage_close,
)
from ai_sdlc.core.stage_review.close_gate import (
    _read_stage_close_gate_attestations as read_stage_close_gate_attestations,
)
from ai_sdlc.core.stage_review.close_gate_models import PreparedStageClose
from ai_sdlc.core.stage_review.close_gate_store import (
    _file_digest as file_digest,
)
from ai_sdlc.core.stage_review.close_gate_store import (
    _gate_attestation_is_current as gate_attestation_is_current,
)
from ai_sdlc.core.stage_review.close_gate_store import (
    _read_gate_operation as read_gate_operation,
)


def test_concurrent_shadow_observation_converges_on_one_attestation(
    tmp_path: Path,
) -> None:
    loop_run = LoopRun(
        loop_id="req-concurrent",
        loop_type=LoopType.REQUIREMENT,
        status=LoopStatus.PASSED,
        work_item_id="concurrent-work-item",
        current_round=1,
        rounds=[LoopRound(round_number=1, status=LoopStatus.PASSED)],
    )
    close_path = tmp_path / "requirement-freeze.json"
    prepared = prepare_loop_stage_close(
        root=tmp_path,
        adapter=RequirementStageAdapter(),
        loop_run=loop_run,
        close_kind="requirement-freeze",
        target_status="closed",
        close_artifact_path=close_path,
    )

    def execute() -> dict[str, str]:
        def writer() -> dict[str, str]:
            close_path.write_text('{"status":"closed"}\n', encoding="utf-8")
            return {"status": "ready", "loop_status": "closed"}

        return StageCloseGateway().execute(prepared, writer)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(executor.map(lambda _index: execute(), range(2)))

    assert all(result["status"] == "ready" for result in results)
    attestations = read_stage_close_gate_attestations(tmp_path)
    assert len(attestations) == 1
    operation = read_gate_operation(tmp_path, attestations[0].operation_id)
    assert operation is not None
    assert operation.state == "shadow_observed"
    assert gate_attestation_is_current(tmp_path, operation, close_path) is True


@pytest.mark.parametrize("same_name", [False, True])
def test_stage_close_rejects_unregistered_adapter_before_preparation(
    tmp_path: Path,
    same_name: bool,
) -> None:
    adapter_type = type(
        "RequirementStageAdapter" if same_name else "UnregisteredAdapter",
        (),
        {
            "loop_type": LoopType.REQUIREMENT,
            "stage_key": LoopType.REQUIREMENT.value,
        },
    )
    loop_run = LoopRun(
        loop_id="req-unregistered-adapter",
        loop_type=LoopType.REQUIREMENT,
        status=LoopStatus.PASSED,
        work_item_id="adapter-registry-work-item",
        current_round=1,
        rounds=[LoopRound(round_number=1, status=LoopStatus.PASSED)],
    )

    with pytest.raises(ValueError, match="stage candidate adapter is not registered"):
        prepare_loop_stage_close(
            root=tmp_path,
            adapter=adapter_type(),
            loop_run=loop_run,
            close_kind="requirement-freeze",
            target_status="closed",
            close_artifact_path=tmp_path / "requirement-freeze.json",
        )


def test_prepared_stage_close_binds_versioned_adapter_contract(
    tmp_path: Path,
) -> None:
    prepared = _prepare_requirement_close(tmp_path, next_action="bound-adapter")

    assert prepared.adapter_id == "stage-candidate.requirement"
    assert prepared.adapter_version == "1.0.0"
    assert prepared.adapter_contract_digest.startswith("sha256:")


def test_prepared_retry_rebinds_changed_input_before_any_close_artifact(
    tmp_path: Path,
) -> None:
    first = _prepare_requirement_close(tmp_path, next_action="first")
    with pytest.raises(RuntimeError, match="writer crashed"):
        StageCloseGateway().execute(
            first,
            lambda: (_ for _ in ()).throw(RuntimeError("writer crashed")),
        )

    second = _prepare_requirement_close(tmp_path, next_action="second")
    close_path = tmp_path / "requirement-freeze.json"

    def writer() -> dict[str, str]:
        close_path.write_text('{"status":"closed"}\n', encoding="utf-8")
        return {"status": "ready", "loop_status": "closed"}

    StageCloseGateway().execute(second, writer)
    attestation = read_stage_close_gate_attestations(tmp_path)[0]
    assert first.stage_input_digest != second.stage_input_digest
    assert attestation.stage_input_digest == second.stage_input_digest


def test_concurrent_prepared_inputs_keep_writer_generation_boundaries(
    tmp_path: Path,
) -> None:
    first = _prepare_requirement_close(tmp_path, next_action="first")
    second = _prepare_requirement_close(tmp_path, next_action="second")
    first_entered = Event()
    release_first = Event()

    def execute(prepared: PreparedStageClose, label: str) -> dict[str, str]:
        def writer() -> dict[str, str]:
            if label == "first":
                first_entered.set()
                assert release_first.wait(timeout=5)
            (tmp_path / "requirement-freeze.json").write_text(label, encoding="utf-8")
            return {"status": "ready", "loop_status": "closed", "value": label}

        return StageCloseGateway().execute(prepared, writer)

    with ThreadPoolExecutor(max_workers=2) as executor:
        first_future = executor.submit(execute, first, "first")
        assert first_entered.wait(timeout=5)
        second_future = executor.submit(execute, second, "second")
        release_first.set()
        first_future.result(timeout=5)
        second_future.result(timeout=5)

    attestations = read_stage_close_gate_attestations(tmp_path)
    first_attestation = next(
        item
        for item in attestations
        if item.stage_input_digest == first.stage_input_digest
    )
    second_attestation = next(
        item
        for item in attestations
        if item.stage_input_digest == second.stage_input_digest
    )
    assert (
        second_attestation.supersedes_attestation_id == first_attestation.attestation_id
    )


def test_close_artifact_change_creates_a_superseding_current_attestation(
    tmp_path: Path,
) -> None:
    _start_requirement(tmp_path, "req-shadow")
    freeze_requirement_loop(RequirementFreezeOptions(root=tmp_path, yes=True))
    original = read_stage_close_gate_attestations(tmp_path)[0]
    close_path = tmp_path / original.close_artifact_path
    close_path.write_text(
        close_path.read_text(encoding="utf-8") + "\n",
        encoding="utf-8",
    )

    freeze_requirement_loop(
        RequirementFreezeOptions(root=tmp_path, loop_id="req-shadow", yes=True)
    )
    attestations = read_stage_close_gate_attestations(tmp_path)
    replacement = next(
        item
        for item in attestations
        if item.supersedes_attestation_id == original.attestation_id
    )
    operation = read_gate_operation(tmp_path, replacement.operation_id)
    assert operation is not None
    assert replacement.close_artifact_digest == file_digest(close_path)
    assert gate_attestation_is_current(tmp_path, operation, close_path) is True


def test_concurrent_requirement_writers_leave_a_current_final_attestation(
    tmp_path: Path,
) -> None:
    _start_requirement(tmp_path, "req-concurrent-real")
    barrier = Barrier(2)
    original_writer = requirement_loop_module._write_requirement_freeze

    def synchronized_writer(*args, **kwargs):
        barrier.wait(timeout=5)
        return original_writer(*args, **kwargs)

    with (
        patch.object(
            requirement_loop_module,
            "_write_requirement_freeze",
            side_effect=synchronized_writer,
        ),
        ThreadPoolExecutor(max_workers=2) as executor,
    ):
        results = tuple(
            executor.map(
                lambda accepted_by: freeze_requirement_loop(
                    RequirementFreezeOptions(
                        root=tmp_path,
                        accepted_by=accepted_by,
                        yes=True,
                    )
                ),
                ("reviewer-a", "reviewer-b"),
            )
        )

    assert all(result.status == "ready" for result in results)
    attestations = read_stage_close_gate_attestations(tmp_path)
    close_path = tmp_path / attestations[0].close_artifact_path
    assert any(
        item.close_artifact_digest == file_digest(close_path) for item in attestations
    )


def test_enforce_cannot_use_an_unmaterialized_shadow_route(tmp_path: Path) -> None:
    prepared = _prepare_enforce_close(tmp_path)
    called = False

    def writer() -> dict[str, str]:
        nonlocal called
        called = True
        return {"status": "ready"}

    with pytest.raises(StageCloseGateUnavailableError, match="StageCloseAuthorizer"):
        StageCloseGateway(_enforce_decision).execute(prepared, writer)

    assert called is False
    assert (tmp_path / "requirement-freeze.json").exists() is False


def test_enforce_delegates_to_the_canonical_close_enforcer(tmp_path: Path) -> None:
    prepared = _prepare_enforce_close(tmp_path)
    enforcer = _RecordingCloseEnforcer()

    result = StageCloseGateway(
        _enforce_decision,
        close_enforcer=enforcer,
    ).execute(prepared, lambda: {"status": "ready"})

    assert result == {"status": "ready"}
    assert enforcer.stage_input_digest == prepared.stage_input_digest


def test_enforce_reconciles_an_already_closed_stage_through_authorizer(
    tmp_path: Path,
) -> None:
    prepared = replace(_prepare_enforce_close(tmp_path), stage_status="closed")
    close_path = tmp_path / prepared.close_artifact_path
    close_path.write_text('{"status":"closed"}\n', encoding="utf-8")
    enforcer = _RecordingCloseEnforcer()

    result = StageCloseGateway(
        _enforce_decision,
        close_enforcer=enforcer,
    ).execute(prepared, lambda: {"status": "ready", "loop_status": "closed"})

    assert result == {"status": "ready", "loop_status": "closed"}
    assert enforcer.stage_input_digest == prepared.stage_input_digest


class _RecordingCloseEnforcer:
    stage_input_digest = ""

    def __init__(self, *, fail_if_called: bool = False) -> None:
        self._fail_if_called = fail_if_called

    def enforce_close(self, prepared, decision, preflight, writer):
        if self._fail_if_called:
            raise AssertionError("already closed stage re-entered Enforce")
        assert decision.mode == "enforce"
        assert preflight.failure is not None
        self.stage_input_digest = prepared.stage_input_digest
        return writer()


def _start_requirement(root: Path, loop_id: str) -> None:
    start_requirement_loop(
        RequirementStartOptions(
            root=root,
            loop_id=loop_id,
            idea="需要验证 Shadow 关闭观测的可恢复性。",
            acceptance=("需求仍可按原语义冻结",),
        )
    )


def _attestation_path(root: Path, attestation_id: str) -> Path:
    shared_state = root / ".ai-sdlc" / "state" / "shared"
    return next(shared_state.rglob(f"{attestation_id}.json"))


def _prepare_requirement_close(
    root: Path,
    *,
    next_action: str,
) -> PreparedStageClose:
    loop_run = LoopRun(
        loop_id="req-retry",
        loop_type=LoopType.REQUIREMENT,
        status=LoopStatus.PASSED,
        work_item_id="retry-work-item",
        current_round=1,
        rounds=[LoopRound(round_number=1, status=LoopStatus.PASSED)],
        next_action=next_action,
    )
    return prepare_loop_stage_close(
        root=root,
        adapter=RequirementStageAdapter(),
        loop_run=loop_run,
        close_kind="requirement-freeze",
        target_status="closed",
        close_artifact_path=root / "requirement-freeze.json",
    )


def _frontend_skip_options(root: Path) -> FrontendEvidenceSkipOptions:
    return FrontendEvidenceSkipOptions(
        root=root,
        work_item="specs/demo-frontend",
        loop_id="frontend-shadow-skip-recovery",
        reason="本地环境当前没有可用的浏览器控制提供方。",
        yes=True,
    )


def _prepare_enforce_close(root: Path) -> PreparedStageClose:
    loop_run = LoopRun(
        loop_id="req-enforce",
        loop_type=LoopType.REQUIREMENT,
        status=LoopStatus.PASSED,
        work_item_id="enforce-work-item",
        current_round=1,
        rounds=[LoopRound(round_number=1, status=LoopStatus.PASSED)],
    )
    return prepare_loop_stage_close(
        root=root,
        adapter=RequirementStageAdapter(),
        loop_run=loop_run,
        close_kind="requirement-freeze",
        target_status="closed",
        close_artifact_path=root / "requirement-freeze.json",
    )


def _enforce_decision(_prepared: object) -> GateApplicabilityDecision:
    return GateApplicabilityDecision(
        decision_id="decision.enforce",
        gate_id="stage-close-authorizer",
        stage_key="requirement",
        loop_id="req-enforce",
        mode="enforce",
        policy_id="test-enforce-policy",
        policy_version="1.0.0",
        policy_digest="sha256:enforce",
        reason_code="test-only-enforce",
    )
