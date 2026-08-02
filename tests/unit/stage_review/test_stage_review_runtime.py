"""产品级 Stage Review Executor 组合入口测试。"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from ai_sdlc.core.config import save_project_config
from ai_sdlc.core.stage_review import (
    codex_review_runtime,
    stage_review_plan_runtime,
)
from ai_sdlc.core.stage_review.close_gate_models import (
    GateApplicabilityDecision,
    PreparedStageClose,
)
from ai_sdlc.core.stage_review.codex_review_runtime import CodexStageReviewExecutor
from ai_sdlc.core.stage_review.session import SessionIntegrityError
from ai_sdlc.core.stage_review.shadow_planning_runtime import (
    ShadowPlanningPreflight,
)
from ai_sdlc.core.stage_review.stage_review_execution import (
    StageCloseGateUnavailableError,
    StageReviewExecutionRequest,
)
from ai_sdlc.core.stage_review.stage_review_runtime import (
    UnavailableStageReviewExecutor,
    build_stage_review_executor,
)
from ai_sdlc.models.project import ProjectConfig


def test_product_composition_fails_closed_for_non_codex_target(
    tmp_path: Path,
) -> None:
    save_project_config(tmp_path, ProjectConfig(agent_target="cursor"))

    executor = build_stage_review_executor(tmp_path)

    assert isinstance(executor, UnavailableStageReviewExecutor)


def test_product_composition_selects_codex_runtime(tmp_path: Path) -> None:
    save_project_config(tmp_path, ProjectConfig(agent_target="codex"))

    executor = build_stage_review_executor(tmp_path)

    assert isinstance(executor, CodexStageReviewExecutor)


def test_codex_runtime_fails_closed_without_trusted_prerequisites(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        codex_review_runtime,
        "resolve_codex_runtime_prerequisites",
        lambda: None,
    )
    request = cast(StageReviewExecutionRequest, object())

    outcome = CodexStageReviewExecutor(tmp_path).execute(request)

    assert outcome.status == "needs_user"
    assert outcome.reason_code == "review-isolation-unproven"


def test_codex_runtime_blocks_protocol_integrity_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FailingExecutor:
        def execute(self, _request: StageReviewExecutionRequest) -> object:
            raise ValueError("review completion lineage fork")

    monkeypatch.setattr(
        codex_review_runtime,
        "resolve_codex_runtime_prerequisites",
        lambda: ("codex", object()),
    )
    monkeypatch.setattr(
        codex_review_runtime,
        "_build_executor",
        lambda *_args, **_kwargs: _FailingExecutor(),
    )
    request = cast(StageReviewExecutionRequest, object())

    outcome = CodexStageReviewExecutor(tmp_path).execute(request)

    assert outcome.status == "blocked"
    assert outcome.reason_code == "review-runtime-integrity-failure"


def test_codex_runtime_blocks_session_integrity_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FailingExecutor:
        def execute(self, _request: StageReviewExecutionRequest) -> object:
            raise SessionIntegrityError("review session lineage fork")

    monkeypatch.setattr(
        codex_review_runtime,
        "resolve_codex_runtime_prerequisites",
        lambda: ("codex", object()),
    )
    monkeypatch.setattr(
        codex_review_runtime,
        "_build_executor",
        lambda *_args, **_kwargs: _FailingExecutor(),
    )
    request = cast(StageReviewExecutionRequest, object())

    outcome = CodexStageReviewExecutor(tmp_path).execute(request)

    assert outcome.status == "blocked"
    assert outcome.reason_code == "review-runtime-integrity-failure"


def test_codex_enforce_fails_before_writer_without_trusted_prerequisites(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        codex_review_runtime,
        "resolve_codex_runtime_prerequisites",
        lambda: None,
    )
    monkeypatch.setattr(
        codex_review_runtime,
        "recover_product_stage_close",
        lambda *_args: None,
    )
    unavailable = object()

    with pytest.raises(
        StageCloseGateUnavailableError,
        match="review-isolation-unproven",
    ):
        CodexStageReviewExecutor(tmp_path).enforce_close(
            cast(PreparedStageClose, unavailable),
            cast(GateApplicabilityDecision, unavailable),
            ShadowPlanningPreflight(
                candidate=cast(object, unavailable),
                source_snapshot=cast(object, unavailable),
                risk_profile=None,
                failure=None,
            ),
            lambda: (_ for _ in ()).throw(
                AssertionError("untrusted enforce route called the writer")
            ),
        )


def test_codex_enforce_maps_plan_acquisition_failure_before_writer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        codex_review_runtime,
        "resolve_codex_runtime_prerequisites",
        lambda: ("codex", object()),
    )
    monkeypatch.setattr(
        codex_review_runtime,
        "recover_product_stage_close",
        lambda *_args: None,
    )
    monkeypatch.setattr(
        codex_review_runtime,
        "hold_stage_review_plan",
        lambda *_args: (_ for _ in ()).throw(ValueError("snapshot unavailable")),
    )
    available = object()

    with pytest.raises(
        StageCloseGateUnavailableError,
        match="review-runtime-integrity-failure",
    ):
        CodexStageReviewExecutor(tmp_path).enforce_close(
            cast(PreparedStageClose, available),
            cast(GateApplicabilityDecision, available),
            ShadowPlanningPreflight(
                candidate=cast(object, available),
                source_snapshot=cast(object, available),
                risk_profile=None,
                failure=None,
            ),
            lambda: (_ for _ in ()).throw(
                AssertionError("failed planning called the writer")
            ),
        )


def test_codex_enforce_replays_recovered_close_through_session_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        codex_review_runtime,
        "recover_product_stage_close",
        lambda *_args: (object(), "recovered"),
    )
    monkeypatch.setattr(
        codex_review_runtime,
        "_recovered_review_session_state",
        lambda *_args: "consuming",
    )
    monkeypatch.setattr(
        codex_review_runtime,
        "resolve_codex_runtime_prerequisites",
        lambda: ("codex", object()),
    )
    runtime = object()
    monkeypatch.setattr(
        codex_review_runtime,
        "_recover_stage_review_plan",
        lambda *_args: runtime,
    )
    executed = []
    monkeypatch.setattr(
        codex_review_runtime,
        "_execute_enforced_close",
        lambda *_args: executed.append("session-replayed") or "recovered",
    )
    monkeypatch.setattr(
        codex_review_runtime,
        "release_stage_review_plan",
        lambda held: executed.append(held),
    )
    available = object()

    result = CodexStageReviewExecutor(tmp_path).enforce_close(
        cast(PreparedStageClose, available),
        cast(GateApplicabilityDecision, available),
        ShadowPlanningPreflight(
            candidate=cast(object, available),
            source_snapshot=cast(object, available),
            risk_profile=cast(object, available),
            failure=None,
        ),
        lambda: (_ for _ in ()).throw(
            AssertionError("recovered close called the writer")
        ),
    )

    assert result == "recovered"
    assert executed == ["session-replayed"]


def test_codex_enforce_maps_recovered_session_integrity_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        codex_review_runtime,
        "recover_product_stage_close",
        lambda *_args: (object(), "recovered"),
    )

    def fail_recovered_session(*_args: object) -> str:
        raise SessionIntegrityError("session projection digest fork")

    monkeypatch.setattr(
        codex_review_runtime,
        "_recovered_review_session_state",
        fail_recovered_session,
    )
    available = object()

    with pytest.raises(
        StageCloseGateUnavailableError,
        match="review-runtime-integrity-failure",
    ):
        CodexStageReviewExecutor(tmp_path).enforce_close(
            cast(PreparedStageClose, available),
            cast(GateApplicabilityDecision, available),
            ShadowPlanningPreflight(
                candidate=cast(object, available),
                source_snapshot=cast(object, available),
                risk_profile=cast(object, available),
                failure=None,
            ),
            lambda: (_ for _ in ()).throw(
                AssertionError("failed recovery called the writer")
            ),
        )


def test_codex_enforce_maps_consuming_replay_integrity_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        codex_review_runtime,
        "recover_product_stage_close",
        lambda *_args: (object(), "recovered"),
    )
    monkeypatch.setattr(
        codex_review_runtime,
        "_recovered_review_session_state",
        lambda *_args: "consuming",
    )
    monkeypatch.setattr(
        codex_review_runtime,
        "resolve_codex_runtime_prerequisites",
        lambda: ("codex", object()),
    )
    monkeypatch.setattr(
        codex_review_runtime,
        "_recover_stage_review_plan",
        lambda *_args: object(),
    )

    def fail_consuming_replay(*_args: object) -> object:
        raise SessionIntegrityError("completion lineage diverged")

    monkeypatch.setattr(
        codex_review_runtime,
        "_execute_enforced_close",
        fail_consuming_replay,
    )
    available = object()

    with pytest.raises(
        StageCloseGateUnavailableError,
        match="review-runtime-integrity-failure",
    ):
        CodexStageReviewExecutor(tmp_path).enforce_close(
            cast(PreparedStageClose, available),
            cast(GateApplicabilityDecision, available),
            ShadowPlanningPreflight(
                candidate=cast(object, available),
                source_snapshot=cast(object, available),
                risk_profile=cast(object, available),
                failure=None,
            ),
            lambda: (_ for _ in ()).throw(
                AssertionError("failed replay called the writer")
            ),
        )


def test_codex_enforce_refresh_failure_blocks_writer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _CompletedExecutor:
        def execute(self, _request: object) -> SimpleNamespace:
            return SimpleNamespace(status="completed")

    def build_executor(
        _root: Path,
        _request: object,
        *,
        executable: str,
        release: object,
        on_authorized,
    ) -> _CompletedExecutor:
        del executable, release
        on_authorized(object())
        return _CompletedExecutor()

    runtime = SimpleNamespace(
        execution_request=lambda *, mode: SimpleNamespace(mode=mode),
        planned=SimpleNamespace(
            candidate=SimpleNamespace(project_id="project.refresh-failure")
        ),
    )
    monkeypatch.setattr(codex_review_runtime, "_build_executor", build_executor)
    monkeypatch.setattr(
        codex_review_runtime,
        "current_activation_policy",
        lambda _root: SimpleNamespace(active_phase=2),
    )
    monkeypatch.setattr(
        codex_review_runtime,
        "refresh_activation_policy_from_local_evidence",
        lambda _root: (_ for _ in ()).throw(RuntimeError("refresh failed")),
    )
    writer_calls = 0

    def writer() -> object:
        nonlocal writer_calls
        writer_calls += 1
        return object()

    with pytest.raises(
        StageCloseGateUnavailableError,
        match="^activation-safety-evaluation-unavailable$",
    ):
        codex_review_runtime._execute_enforced_close(
            tmp_path,
            cast(PreparedStageClose, object()),
            cast(GateApplicabilityDecision, object()),
            runtime,
            writer,
            executable="codex",
            release=cast(object, object()),
        )

    assert writer_calls == 0


def test_codex_enforce_surfaces_release_failure_after_writer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        codex_review_runtime,
        "resolve_codex_runtime_prerequisites",
        lambda: ("codex", object()),
    )
    monkeypatch.setattr(
        codex_review_runtime,
        "recover_product_stage_close",
        lambda *_args: None,
    )
    runtime = object()
    monkeypatch.setattr(
        codex_review_runtime,
        "hold_stage_review_plan",
        lambda *_args: runtime,
    )
    writer_calls = []

    def execute_close(*args):
        writer = args[4]
        return writer()

    monkeypatch.setattr(
        codex_review_runtime,
        "_execute_enforced_close",
        execute_close,
    )
    monkeypatch.setattr(
        codex_review_runtime,
        "release_stage_review_plan",
        lambda _runtime: (_ for _ in ()).throw(ValueError("release failed")),
    )
    available = object()

    with pytest.raises(
        StageCloseGateUnavailableError,
        match="review-runtime-integrity-failure",
    ):
        CodexStageReviewExecutor(tmp_path).enforce_close(
            cast(PreparedStageClose, available),
            cast(GateApplicabilityDecision, available),
            ShadowPlanningPreflight(
                candidate=cast(object, available),
                source_snapshot=cast(object, available),
                risk_profile=None,
                failure=None,
            ),
            lambda: writer_calls.append("called") or "closed",
        )

    assert writer_calls == ["called"]


@pytest.mark.parametrize(
    "release_error",
    [ValueError("release failed"), KeyboardInterrupt("release interrupted")],
)
def test_plan_hold_preserves_persistence_failure_when_release_also_fails(
    monkeypatch: pytest.MonkeyPatch,
    release_error: BaseException,
) -> None:
    planned = SimpleNamespace(resolution=SimpleNamespace(proposal=object()))
    held = SimpleNamespace(plan=object())
    primary_error = RuntimeError("plan persistence failed")
    monkeypatch.setattr(
        stage_review_plan_runtime,
        "resolve_active_optimization_snapshot",
        lambda *_args, **_kwargs: object(),
    )
    monkeypatch.setattr(
        stage_review_plan_runtime,
        "_policy_from_decision",
        lambda *_args, **_kwargs: object(),
    )
    monkeypatch.setattr(
        stage_review_plan_runtime,
        "build_shadow_panel_proposal",
        lambda **_kwargs: planned,
    )
    monkeypatch.setattr(
        stage_review_plan_runtime,
        "hold_shadow_panel_plan",
        lambda *_args: held,
    )
    monkeypatch.setattr(
        stage_review_plan_runtime,
        "persist_shadow_plan",
        lambda *_args: (_ for _ in ()).throw(primary_error),
    )
    monkeypatch.setattr(
        stage_review_plan_runtime,
        "release_shadow_panel_plan",
        lambda _held: (_ for _ in ()).throw(release_error),
    )
    available = object()
    prepared = SimpleNamespace(root=Path("/unused"))
    candidate = SimpleNamespace(project_id="project.persistence-failure")
    decision = SimpleNamespace(mode=available)

    with pytest.raises(RuntimeError, match="plan persistence failed") as caught:
        stage_review_plan_runtime.hold_stage_review_plan(
            cast(PreparedStageClose, prepared),
            cast(GateApplicabilityDecision, decision),
            cast(object, candidate),
            cast(object, available),
        )

    assert caught.value is primary_error
    assert any(
        f"{type(release_error).__name__}: {release_error}" in note
        for note in caught.value.__notes__
    )


@pytest.mark.parametrize(
    "release_error",
    [ValueError("release failed"), KeyboardInterrupt("release interrupted")],
)
def test_codex_enforce_preserves_primary_failure_when_plan_release_also_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    release_error: BaseException,
) -> None:
    monkeypatch.setattr(
        codex_review_runtime,
        "resolve_codex_runtime_prerequisites",
        lambda: ("codex", object()),
    )
    monkeypatch.setattr(
        codex_review_runtime,
        "recover_product_stage_close",
        lambda *_args: None,
    )
    runtime = object()
    monkeypatch.setattr(
        codex_review_runtime,
        "hold_stage_review_plan",
        lambda *_args: runtime,
    )
    monkeypatch.setattr(
        codex_review_runtime,
        "_execute_enforced_close",
        lambda *_args: (_ for _ in ()).throw(
            StageCloseGateUnavailableError("primary-close-failure")
        ),
    )
    monkeypatch.setattr(
        codex_review_runtime,
        "release_stage_review_plan",
        lambda _runtime: (_ for _ in ()).throw(release_error),
    )
    available = object()

    with pytest.raises(
        StageCloseGateUnavailableError,
        match="primary-close-failure",
    ) as caught:
        CodexStageReviewExecutor(tmp_path).enforce_close(
            cast(PreparedStageClose, available),
            cast(GateApplicabilityDecision, available),
            ShadowPlanningPreflight(
                candidate=cast(object, available),
                source_snapshot=cast(object, available),
                risk_profile=None,
                failure=None,
            ),
            lambda: (_ for _ in ()).throw(
                AssertionError("failed close called the writer")
            ),
        )

    assert any(str(release_error) in note for note in caught.value.__notes__)
