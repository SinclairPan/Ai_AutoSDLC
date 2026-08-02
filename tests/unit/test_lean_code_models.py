"""Lean Code schema、兼容 marker 与轮次模型测试。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from ai_sdlc.core.implementation_models import ImplementationInput
from ai_sdlc.core.implementation_store import implementation_artifacts
from ai_sdlc.core.lean_code_artifacts import (
    LeanCurrentPointer,
    LeanFindingsArtifact,
    read_current_report,
)
from ai_sdlc.core.lean_code_models import (
    FileClassification,
    FunctionMetric,
    LeanEvaluationReport,
    LeanFinding,
    LeanMetrics,
    LeanPolicy,
    MetricCapability,
    stable_finding_signature,
)
from ai_sdlc.core.lean_code_policy import stable_artifact_digest
from ai_sdlc.core.loop_models import LoopArtifactModel, LoopRound
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


def test_legacy_function_metric_roundtrip_does_not_add_extension_fields() -> None:
    legacy_payload = {
        "symbol": "module.function",
        "logical_lines": 1,
        "base_logical_lines": 0,
        "complexity": 1,
        "base_complexity": 0,
        "max_nesting": 0,
        "base_max_nesting": 0,
        "caller_count": 0,
        "public": False,
        "is_new": True,
        "capability": "exact",
        "binding_state": "disproven",
        "execution_state": "unreachable",
        "invocation_boundary": "",
        "fingerprint": "",
        "duplicate_count": 1,
    }

    metric = FunctionMetric.model_validate(legacy_payload)

    assert metric.model_dump(mode="json") == legacy_payload


def test_legacy_lean_metrics_roundtrip_preserves_schema_and_digest() -> None:
    legacy = LeanMetrics(
        schema_version="1",
        created_at="2026-01-01T00:00:00Z",
    )
    legacy_payload = legacy.model_dump(mode="json")

    restored = LeanMetrics.model_validate(legacy_payload)

    assert restored.schema_version == "1"
    assert restored.model_dump(mode="json") == legacy_payload
    assert "task_scope_matches" not in legacy_payload
    assert stable_artifact_digest(restored) == stable_artifact_digest(legacy)


def test_new_lean_reports_emit_schema_two_and_read_schema_one() -> None:
    common = {
        "loop_id": "impl-schema",
        "work_item_id": "WI-SCHEMA",
        "work_type": "new_requirement",
        "evaluation_profile": "feature",
        "evaluation_round": 1,
        "source_snapshot_digest": "sha256:source",
        "diff_hash": "sha256:diff",
        "policy_digest": "sha256:policy",
        "status": "passed",
        "created_at": "2026-01-01T00:00:00Z",
    }
    current = LeanEvaluationReport(metrics=LeanMetrics(), **common)
    legacy = LeanEvaluationReport(
        schema_version="1",
        metrics=LeanMetrics(
            schema_version="1",
            created_at="2026-01-01T00:00:00Z",
        ),
        **common,
    )
    legacy_payload = legacy.model_dump(mode="json")

    assert current.schema_version == "2"
    assert current.metrics.schema_version == "2"
    assert LeanEvaluationReport.model_validate(legacy_payload).model_dump(
        mode="json"
    ) == legacy_payload


def test_legacy_loop_reader_rejects_new_lean_schema_before_use() -> None:
    class LegacyLeanEnvelope(LoopArtifactModel):
        artifact_kind: str = "lean-code-metrics"

    with pytest.raises(ValidationError, match="unsupported schema_version: 2"):
        LegacyLeanEnvelope.model_validate(
            {
                "schema_version": "2",
                "artifact_kind": "lean-code-metrics",
            }
        )


def test_real_v1_report_pointer_replay_preserves_legacy_digest(
    tmp_path: Path,
) -> None:
    report_payload = _legacy_v1_report_payload()
    report_digest = _raw_stable_digest(report_payload)
    loop_dir = implementation_artifacts(tmp_path, "impl-v1").loop_dir
    report_path = loop_dir / "lean" / "round-001" / "report.json"
    pointer_path = loop_dir / "lean" / "current.json"
    report_path.parent.mkdir(parents=True)
    report_path.write_text(json.dumps(report_payload), encoding="utf-8")
    pointer = LeanCurrentPointer(
        loop_id="impl-v1",
        evaluation_round=1,
        report_path=report_path.relative_to(tmp_path).as_posix(),
        report_digest=report_digest,
        snapshot_path="evidence/snapshot.json",
        snapshot_digest="sha256:snapshot",
        policy_path="evidence/policy.json",
        policy_digest="sha256:policy",
        findings_path="evidence/findings.json",
        findings_digest="sha256:findings",
        input_path="evidence/input.json",
        input_digest="sha256:input",
        diff_hash="sha256:diff",
    )
    pointer_path.write_text(pointer.model_dump_json(), encoding="utf-8")

    restored = read_current_report(tmp_path, "impl-v1")

    assert restored is not None
    assert stable_artifact_digest(restored) == report_digest


def test_published_origin_main_v1_pointer_replay_preserves_fixed_digest(
    tmp_path: Path,
) -> None:
    fixture_path = (
        Path(__file__).parents[1]
        / "fixtures"
        / "lean_code"
        / "origin-main-v1-report.json"
    )
    report_payload = json.loads(fixture_path.read_text(encoding="utf-8"))
    report_digest = (
        "sha256:8e507dfa1f085867660cef8738cb5b4bcfd36cd18e8c3a6d54175bcf55ee6f03"
    )
    assert _raw_stable_digest(report_payload) == report_digest
    loop_dir = implementation_artifacts(tmp_path, "impl-origin-v1").loop_dir
    report_path = loop_dir / "lean" / "round-001" / "report.json"
    pointer_path = loop_dir / "lean" / "current.json"
    report_path.parent.mkdir(parents=True)
    report_path.write_text(json.dumps(report_payload), encoding="utf-8")
    pointer = LeanCurrentPointer(
        loop_id="impl-origin-v1",
        evaluation_round=1,
        report_path=report_path.relative_to(tmp_path).as_posix(),
        report_digest=report_digest,
        snapshot_path="evidence/snapshot.json",
        snapshot_digest="sha256:snapshot",
        policy_path="evidence/policy.json",
        policy_digest="sha256:policy",
        findings_path="evidence/findings.json",
        findings_digest="sha256:findings",
        input_path="evidence/input.json",
        input_digest="sha256:input",
        diff_hash="sha256:diff",
    )
    pointer_path.write_text(pointer.model_dump_json(), encoding="utf-8")

    restored = read_current_report(tmp_path, "impl-origin-v1")

    assert restored is not None
    assert stable_artifact_digest(restored) == report_digest


@pytest.mark.parametrize(
    ("report_version", "metrics_version"),
    (("1", "2"), ("2", "1")),
)
def test_lean_report_rejects_mixed_outer_and_nested_schema_versions(
    report_version: str,
    metrics_version: str,
) -> None:
    payload = _legacy_v1_report_payload()
    payload["schema_version"] = report_version
    payload["metrics"]["schema_version"] = metrics_version

    with pytest.raises(ValidationError, match="must match report schema_version"):
        LeanEvaluationReport.model_validate(payload)


def test_schema_one_metrics_reject_v2_function_fields() -> None:
    payload = _legacy_v1_report_payload()["metrics"]
    payload["files"][0]["functions"][0]["import_fan_out"] = 1

    with pytest.raises(ValidationError, match="v2-only FunctionMetric fields"):
        LeanMetrics.model_validate(payload)


def _legacy_v1_report_payload() -> dict:
    return {
        "schema_version": "1",
        "artifact_kind": "lean-code-report",
        "created_by": "ai-sdlc",
        "created_at": "2026-01-01T00:00:00Z",
        "ai_sdlc_version": "1.0.0",
        "loop_id": "impl-v1",
        "work_item_id": "WI-V1",
        "work_type": "new_requirement",
        "evaluation_profile": "feature",
        "evaluation_round": 1,
        "source_snapshot_digest": "sha256:source",
        "diff_hash": "sha256:diff",
        "policy_digest": "sha256:policy",
        "enforcement_mode": "warning",
        "verification_digest": "",
        "status": "passed",
        "metrics": {
            "schema_version": "1",
            "artifact_kind": "lean-code-metrics",
            "created_by": "ai-sdlc",
            "created_at": "2026-01-01T00:00:00Z",
            "ai_sdlc_version": "1.0.0",
            "product_added_lines": 1,
            "product_deleted_lines": 0,
            "product_net_lines": 1,
            "test_added_lines": 0,
            "test_deleted_lines": 0,
            "test_net_lines": 0,
            "new_file_count": 1,
            "changed_file_count": 1,
            "classification_counts": {"handwritten_product": 1},
            "unknown_files": [],
            "unsupported_semantic_files": [],
            "duplicate_candidates": [],
            "scope_drift": [],
            "task_scope_matches": {"src/app.py": ["T1"]},
            "files": [
                {
                    "path": "src/app.py",
                    "classification": "handwritten_product",
                    "language": "python",
                    "capability": "exact",
                    "base_lines": 0,
                    "head_lines": 1,
                    "added_lines": 1,
                    "deleted_lines": 0,
                    "import_fan_out": 0,
                    "base_import_fan_out": 0,
                    "functions": [
                        {
                            "symbol": "app.run",
                            "logical_lines": 1,
                            "base_logical_lines": 0,
                            "complexity": 1,
                            "base_complexity": 0,
                            "max_nesting": 0,
                            "base_max_nesting": 0,
                            "caller_count": 0,
                            "public": True,
                            "is_new": True,
                            "capability": "exact",
                            "binding_state": "disproven",
                            "execution_state": "unreachable",
                            "invocation_boundary": "",
                            "fingerprint": "sha256:function",
                            "duplicate_count": 1,
                        }
                    ],
                    "parse_errors": [],
                }
            ],
        },
        "findings": [],
        "exception_ids": [],
        "risk_accepted": False,
        "previous_signatures": [],
        "stop_reason": "",
    }


def _raw_stable_digest(payload: object) -> str:
    def without_provenance(value: object) -> object:
        if isinstance(value, dict):
            return {
                key: without_provenance(item)
                for key, item in value.items()
                if key not in {"created_at", "ai_sdlc_version"}
            }
        if isinstance(value, list):
            return [without_provenance(item) for item in value]
        return value

    encoded = json.dumps(
        without_provenance(payload),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return f"sha256:{hashlib.sha256(encoded.encode('utf-8')).hexdigest()}"
