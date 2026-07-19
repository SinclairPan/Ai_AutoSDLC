"""Lean Code schema、兼容 marker 与轮次模型测试。"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from ai_sdlc.core.implementation_models import ImplementationInput
from ai_sdlc.core.lean_code_artifacts import LeanFindingsArtifact
from ai_sdlc.core.lean_code_models import (
    FileClassification,
    LeanFinding,
    LeanPolicy,
    MetricCapability,
    stable_finding_signature,
)
from ai_sdlc.core.lean_code_policy import stable_artifact_digest
from ai_sdlc.core.loop_models import LoopRound
from ai_sdlc.models.work import WorkType


def test_legacy_implementation_input_defaults_to_disabled_lean_profile() -> None:
    payload = {
        "artifact_kind": "implementation-input",
        "loop_id": "impl-legacy",
        "work_item_id": "WI-LEGACY",
        "work_item_path": ".ai-sdlc/work-items/WI-LEGACY",
        "spec_path": "specs/WI-LEGACY/spec.md",
        "plan_path": "specs/WI-LEGACY/plan.md",
        "tasks_path": "specs/WI-LEGACY/tasks.md",
        "design_contract_loop_id": "design-legacy",
    }

    model = ImplementationInput.model_validate(payload)

    assert model.quality_profiles == []
    assert model.work_type == WorkType.UNCERTAIN
    assert model.declared_scope == []


def test_loop_round_separates_execution_and_lean_evaluation_rounds() -> None:
    execution = LoopRound(round_number=1)
    evaluation = LoopRound(round_number=2, round_kind="lean-evaluation")

    assert execution.round_kind == "execution"
    assert evaluation.round_kind == "lean-evaluation"


def test_lean_policy_rejects_more_than_two_rounds() -> None:
    with pytest.raises(ValidationError):
        LeanPolicy(max_rounds=3)


def test_stable_signature_ignores_line_severity_and_measurement() -> None:
    first = stable_finding_signature(
        rule_id="lean.function-budget",
        classification=FileClassification.HANDWRITTEN_PRODUCT,
        path=Path("src/app.py"),
        symbol="app.process",
        evidence_locator="function:process",
    )
    finding = LeanFinding(
        finding_id="lean-1",
        stable_signature=first,
        rule_id="lean.function-budget",
        severity="ADVISORY",
        path="src/app.py",
        symbol="app.process",
        claim="51 logical lines",
        evidence=["line:10-60"],
        measured_value=51,
        configured_budget=50,
        risk="maintainability",
        suggested_fix="keep behavior and simplify if useful",
        required_verification=["pytest"],
        round_number=1,
    )

    second = stable_finding_signature(
        rule_id=finding.rule_id,
        classification=FileClassification.HANDWRITTEN_PRODUCT,
        path=Path(finding.path),
        symbol=finding.symbol,
        evidence_locator="function:process",
    )
    assert first == second
    assert MetricCapability.EXACT.value == "exact"


def test_stable_artifact_digest_ignores_nested_provenance_timestamps() -> None:
    finding = LeanFinding(
        finding_id="lean-1",
        stable_signature="sha256:stable",
        rule_id="lean.function-budget",
        severity="ADVISORY",
        path="src/app.py",
        claim="size signal",
        evidence=["function:app.process"],
        measured_value=51,
        configured_budget=50,
        risk="maintainability",
        suggested_fix="keep the direct implementation",
        required_verification=["pytest"],
        round_number=1,
        created_at="2026-01-01T00:00:00Z",
    )
    first = LeanFindingsArtifact(
        loop_id="impl-stable", evaluation_round=1, findings=[finding]
    )
    second = first.model_copy(
        update={
            "created_at": "2026-02-01T00:00:00Z",
            "findings": [
                finding.model_copy(update={"created_at": "2026-02-01T00:00:01Z"})
            ],
        }
    )

    assert stable_artifact_digest(first) == stable_artifact_digest(second)
