"""Tests for the minimal, read-only dynamic expert review kernel."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from ai_sdlc.core.review_kernel import (
    ReviewExecution,
    ReviewFinding,
    ReviewInput,
    build_review_input,
    merge_expert_findings,
)


def _finding(*, role: str = "API compatibility reviewer") -> ReviewFinding:
    return ReviewFinding(
        severity="important",
        role=role,
        location="spec.md:42",
        summary="The response contract removes a required field.",
        recommendation="Restore the field or update the accepted contract.",
    )


def test_review_models_expose_only_ephemeral_review_values() -> None:
    review_input = ReviewInput(
        loop_id="loop-1",
        loop_type="requirement",
        round_number=1,
        input_digest="a" * 64,
        artifact_paths=[
            ".ai-sdlc/loops/requirement/loop-1/requirement-brief.md"
        ],
        upstream_context_paths=[],
        risk_signals=["public-api"],
        role_brief="Choose one primary expert and at most one cross-risk expert.",
    )
    execution = ReviewExecution(
        status="completed",
        roles=["API compatibility reviewer"],
        role_reasons={
            "API compatibility reviewer": "The result changes a public schema."
        },
        findings=[],
    )

    assert review_input.input_digest == "a" * 64
    assert execution.status == "completed"
    assert "verdict" not in execution.model_dump()
    assert "passed" not in execution.model_dump()
    assert "closed" not in execution.model_dump()


@pytest.mark.parametrize(
    "payload",
    [
        {
            "status": "completed",
            "roles": ["one", "two", "three"],
            "role_reasons": {"one": "a", "two": "b", "three": "c"},
        },
        {
            "status": "completed",
            "roles": ["one", "one"],
            "role_reasons": {"one": "a"},
        },
        {"status": "completed", "roles": [], "role_reasons": {}},
        {
            "status": "completed",
            "roles": ["one"],
            "role_reasons": {"one": "a"},
            "findings": [_finding(role="absent")],
        },
        {
            "status": "failed",
            "roles": ["one"],
            "role_reasons": {"one": "a"},
        },
    ],
)
def test_review_execution_rejects_invalid_ephemeral_state(payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        ReviewExecution.model_validate(payload)


@pytest.mark.parametrize(
    "field",
    [
        "input_digest",
        "verdict",
        "passed",
        "closed",
        "certificate",
        "session_id",
        "quorum",
        "score",
    ],
)
def test_review_execution_rejects_authority_and_persistence_fields(field: str) -> None:
    payload: dict[str, object] = {
        "status": "completed",
        "roles": ["one"],
        "role_reasons": {"one": "reason"},
        field: "forbidden",
    }

    with pytest.raises(ValidationError):
        ReviewExecution.model_validate(payload)


def test_build_review_input_is_stable_read_only_and_detects_drift(
    tmp_path: Path,
) -> None:
    artifact = tmp_path / "spec.md"
    context = tmp_path / "acceptance.md"
    artifact.write_text("contract v1\n", encoding="utf-8")
    context.write_text("acceptance v1\n", encoding="utf-8")
    before = sorted(path.relative_to(tmp_path) for path in tmp_path.rglob("*"))

    first = build_review_input(
        tmp_path,
        loop_id="loop-1",
        loop_type="design-contract",
        round_number=2,
        artifact_paths=[artifact],
        upstream_context_paths=[context],
        risk_signals=["public-api"],
    )
    second = build_review_input(
        tmp_path,
        loop_id="loop-1",
        loop_type="design-contract",
        round_number=2,
        artifact_paths=[artifact],
        upstream_context_paths=[context],
        risk_signals=["public-api"],
    )

    assert first == second
    assert first.artifact_paths == ["spec.md"]
    assert first.upstream_context_paths == ["acceptance.md"]
    assert len(first.input_digest) == 64
    assert sorted(path.relative_to(tmp_path) for path in tmp_path.rglob("*")) == before

    artifact.write_text("contract v2\n", encoding="utf-8")
    changed = build_review_input(
        tmp_path,
        loop_id="loop-1",
        loop_type="design-contract",
        round_number=2,
        artifact_paths=[artifact],
        upstream_context_paths=[context],
        risk_signals=["public-api"],
    )
    assert changed.input_digest != first.input_digest


@pytest.mark.parametrize("unsafe", ["missing.md", "../outside.md"])
def test_build_review_input_rejects_missing_or_escaping_path(
    tmp_path: Path,
    unsafe: str,
) -> None:
    with pytest.raises(ValueError):
        build_review_input(
            tmp_path,
            loop_id="loop-1",
            loop_type="implementation",
            round_number=1,
            artifact_paths=[unsafe],
            upstream_context_paths=[],
            risk_signals=[],
        )


def test_merge_expert_findings_deduplicates_without_deciding_close() -> None:
    finding = _finding()
    merged = merge_expert_findings(
        [
            ReviewExecution(
                status="completed",
                roles=["API compatibility reviewer"],
                role_reasons={"API compatibility reviewer": "public schema"},
                findings=[finding],
            ),
            ReviewExecution(
                status="completed",
                roles=["API compatibility reviewer"],
                role_reasons={"API compatibility reviewer": "public schema"},
                findings=[finding],
            ),
        ]
    )

    assert merged.status == "completed"
    assert merged.findings == [finding]
    assert set(merged.model_dump()) == {
        "status",
        "roles",
        "role_reasons",
        "findings",
        "failure_kind",
        "failure_reason",
    }
