from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from ai_sdlc.cli.main import app
from ai_sdlc.core.source_snapshot import SourceSnapshot
from ai_sdlc.core.stage_review import shadow_planning_runtime
from ai_sdlc.core.stage_review.activation_policy_store import (
    current_activation_policy,
)
from ai_sdlc.core.stage_review.candidate import CandidateManifest
from ai_sdlc.core.stage_review.close_gate import (
    _read_stage_close_gate_attestations as read_stage_close_gate_attestations,
)
from ai_sdlc.core.stage_review.close_gate_models import (
    GateApplicabilityDecision,
    PreparedStageClose,
)
from ai_sdlc.core.stage_review.shadow_planning_runtime import ShadowPlanningOutcome
from ai_sdlc.core.stage_review.stage_review_execution import StageReviewExecutor

runner = CliRunner()


def test_clean_user_shadow_close_needs_no_internal_review_commands(
    git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _initialize_clean_project(git_repo, monkeypatch)
    start = runner.invoke(
        app,
        _requirement_start_args("clean-user-shadow"),
    )
    with (
        patch(
            "ai_sdlc.core.stage_review.codex_review_runtime."
            "resolve_codex_runtime_prerequisites",
            return_value=None,
        ),
        patch.object(
            shadow_planning_runtime,
            "_observe_candidate_plan",
            side_effect=_observe_candidate_without_swallowing,
        ),
        patch(
            "ai_sdlc.core.stage_review.close_gate._record_pending_observation",
            side_effect=_reraise_pending_observation,
        ),
    ):
        closed = runner.invoke(
            app,
            ["loop", "requirement", "freeze", "--yes", "--json"],
        )

    if closed.exception is not None and closed.exc_info is not None:
        raise closed.exception.with_traceback(closed.exc_info[2])
    assert start.exit_code == 0
    assert closed.exit_code == 0
    assert json.loads(closed.output)["frozen"] is True
    attestation = read_stage_close_gate_attestations(git_repo)[0]
    assert attestation.applicability.mode == "shadow"
    assert attestation.review_status == "needs_user", attestation.model_dump_json(
        indent=2
    )
    assert attestation.review_reason_code == "review-isolation-unproven"


def _observe_candidate_without_swallowing(
    prepared: PreparedStageClose,
    decision: GateApplicabilityDecision,
    candidate: CandidateManifest,
    source_snapshot: SourceSnapshot,
    executor: StageReviewExecutor | None,
) -> ShadowPlanningOutcome:
    runtime, execution = shadow_planning_runtime._run_candidate_review(
        prepared,
        decision,
        candidate,
        source_snapshot,
        executor,
    )
    return shadow_planning_runtime._resolved_outcome(
        candidate=shadow_planning_runtime._candidate_state(
            candidate,
            runtime.refs["candidate.json"],
        ),
        planning=shadow_planning_runtime._planning_state(runtime),
        execution=execution,
    )


def _reraise_pending_observation(
    operation: object,
    prepared: PreparedStageClose,
    error: Exception,
) -> None:
    del operation, prepared
    raise error


def test_clean_user_enforce_failure_is_actionable_and_does_not_close(
    git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _initialize_clean_project(git_repo, monkeypatch)
    assert (
        runner.invoke(
            app,
            _requirement_start_args("clean-user-enforce"),
        ).exit_code
        == 0
    )
    with (
        patch(
            "ai_sdlc.core.stage_review.close_gate.shadow_applicability",
            side_effect=_enforce_decision,
        ),
        patch(
            "ai_sdlc.core.stage_review.codex_review_runtime."
            "resolve_codex_runtime_prerequisites",
            return_value=None,
        ),
    ):
        result = runner.invoke(
            app,
            ["loop", "requirement", "freeze", "--yes", "--json"],
        )

    payload = json.loads(result.output)
    assert result.exit_code == 2
    assert payload["status"] == "needs_user"
    assert payload["reason_code"] == "review-isolation-unproven"
    assert len(payload["next_action"].splitlines()) == 1
    close_files = tuple(
        git_repo.glob(".ai-sdlc/loops/requirement/*/requirement-freeze.json")
    )
    assert close_files == ()


def _initialize_clean_project(
    root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(root)
    result = runner.invoke(
        app,
        ["init", ".", "--agent-target", "codex", "--shell", "powershell"],
    )
    assert result.exit_code == 0, result.output
    subprocess.run(
        ["git", "add", "-A", "-f"],
        cwd=root,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "initialize ai-sdlc"],
        cwd=root,
        check=True,
        capture_output=True,
    )
    root.joinpath("feature.txt").write_text(
        "candidate change\n",
        encoding="utf-8",
    )


def _requirement_start_args(loop_id: str) -> list[str]:
    return [
        "loop",
        "requirement",
        "start",
        "--idea",
        "Add a deterministic requirement close.",
        "--acceptance",
        "The requirement can be frozen.",
        "--work-item-id",
        "clean-user",
        "--loop-id",
        loop_id,
        "--json",
    ]


def _enforce_decision(prepared) -> GateApplicabilityDecision:
    policy = current_activation_policy(prepared.root)
    return GateApplicabilityDecision(
        decision_id="decision.clean-user-enforce",
        gate_id="stage-close-authorizer",
        stage_key=prepared.stage_key,
        loop_id=prepared.loop_id,
        mode="enforce",
        policy_id=policy.policy_id,
        policy_version=policy.policy_version,
        policy_digest=policy.policy_digest,
        reason_code="clean-user-enforce-fixture",
    )
