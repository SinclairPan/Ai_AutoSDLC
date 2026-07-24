"""产品级 Stage Review Executor 组合入口测试。"""

from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest

from ai_sdlc.core.config import save_project_config
from ai_sdlc.core.stage_review import codex_review_runtime
from ai_sdlc.core.stage_review.close_gate_models import (
    GateApplicabilityDecision,
    PreparedStageClose,
)
from ai_sdlc.core.stage_review.codex_review_runtime import CodexStageReviewExecutor
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


def test_codex_enforce_fails_before_writer_without_trusted_prerequisites(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        codex_review_runtime,
        "resolve_codex_runtime_prerequisites",
        lambda: None,
    )
    unavailable = object()

    with pytest.raises(
        StageCloseGateUnavailableError,
        match="review-isolation-unproven",
    ):
        CodexStageReviewExecutor(tmp_path).enforce_close(
            cast(PreparedStageClose, unavailable),
            cast(GateApplicabilityDecision, unavailable),
            cast(ShadowPlanningPreflight, unavailable),
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
