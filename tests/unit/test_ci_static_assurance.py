"""行为测试：框架自身 CI 的静态集合身份与完整性合同。"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "ci_static_assurance.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("ci_static_assurance", _SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _baseline(*case_ids: str) -> dict[str, object]:
    return {
        "schema_version": "ci-baseline-v1",
        "namespace": "pytest-nodeid-v1",
        "source_commit": "a" * 40,
        "previous_baseline_digest": None,
        "cells": ["ubuntu-latest-py3.11"],
        "case_ids": list(case_ids),
        "execution_member_ids": [f"member:{case_id}" for case_id in case_ids],
        "baseline_digest": "sha256:trusted",
    }


def _candidate(*case_ids: str) -> dict[str, object]:
    return {
        "schema_version": "ci-baseline-v1",
        "namespace": "pytest-nodeid-v1",
        "source_commit": "b" * 40,
        "previous_baseline_digest": "sha256:trusted",
        "cells": ["ubuntu-latest-py3.11"],
        "case_ids": list(case_ids),
        "execution_member_ids": [f"member:{case_id}" for case_id in case_ids],
        "baseline_digest": "sha256:candidate",
    }


def test_case_id_survives_commit_change_while_execution_member_binds_cell() -> None:
    """防止把一次性 commit/run 身份混入稳定测试成员。"""
    module = _load_module()

    first = module.build_collection_manifest(
        ["tests/a.py::test_ok"],
        ["ubuntu-latest-py3.11"],
        source_commit="a" * 40,
    )
    second = module.build_collection_manifest(
        ["tests/a.py::test_ok"],
        ["windows-latest-py3.14"],
        source_commit="b" * 40,
    )

    assert first["case_ids"] == second["case_ids"]
    assert first["execution_member_ids"] != second["execution_member_ids"]
    assert first["case_ids"][0].startswith("sha256:")


def test_parameter_instance_changes_case_identity() -> None:
    """防止参数实例被压成同一个 case，掩盖成员缺失。"""
    module = _load_module()

    manifest = module.build_collection_manifest(
        ["tests/a.py::test_ok[one]", "tests/a.py::test_ok[two]"],
        ["ubuntu-latest-py3.11"],
        source_commit="a" * 40,
    )

    assert len(manifest["case_ids"]) == 2
    assert len(set(manifest["case_ids"])) == 2


def test_duplicate_collected_nodeid_fails_closed() -> None:
    """防止重复 shard/collection 成员被排序去重后静默通过。"""
    module = _load_module()

    with pytest.raises(module.AssuranceError, match="duplicate collected nodeid"):
        module.build_collection_manifest(
            ["tests/a.py::test_ok", "tests/a.py::test_ok"],
            ["ubuntu-latest-py3.11"],
            source_commit="a" * 40,
        )


def test_baseline_transition_allows_only_monotonic_addition() -> None:
    """防止新增测试因 baseline 保守策略被误判，同时保持旧成员。"""
    module = _load_module()

    result = module.verify_baseline_transition(
        _baseline("case:a", "case:b"),
        _candidate("case:a", "case:b", "case:c"),
        {"schema_version": "ci-test-lineage-v1", "mappings": []},
    )

    assert result == {
        "status": "success",
        "reason": "monotonic_transition",
        "added_case_ids": ["case:c"],
        "renamed_case_ids": [],
        "removed_case_ids": [],
    }


def test_baseline_transition_rejects_real_deletion() -> None:
    """防止候选通过缩小 baseline 静默删除完整质量成员。"""
    module = _load_module()

    result = module.verify_baseline_transition(
        _baseline("case:a", "case:b"),
        _candidate("case:a"),
        {"schema_version": "ci-test-lineage-v1", "mappings": []},
    )

    assert result["status"] == "failed"
    assert result["reason"] == "unauthorized_negative_delta"
    assert result["removed_case_ids"] == ["case:b"]


def test_protected_one_to_one_lineage_allows_member_conserving_rename() -> None:
    """防止合法 rename 被当成删除；映射必须来自受保护 base。"""
    module = _load_module()

    result = module.verify_baseline_transition(
        _baseline("case:old"),
        _candidate("case:new"),
        {
            "schema_version": "ci-test-lineage-v1",
            "mappings": [{"from_case_id": "case:old", "to_case_id": "case:new"}],
        },
    )

    assert result["status"] == "success"
    assert result["renamed_case_ids"] == [
        {"from_case_id": "case:old", "to_case_id": "case:new"}
    ]
    assert result["removed_case_ids"] == []


@pytest.mark.parametrize(
    ("lineage", "reason"),
    [
        (
            {
                "schema_version": "ci-test-lineage-v1",
                "mappings": [
                    {"from_case_id": "case:a", "to_case_id": "case:new"},
                    {"from_case_id": "case:b", "to_case_id": "case:new"},
                ],
            },
            "lineage_not_one_to_one",
        ),
        (
            {"schema_version": "ci-test-lineage-v1", "mappings": []},
            "unauthorized_negative_delta",
        ),
    ],
)
def test_merge_or_unapproved_rename_fails_closed(
    lineage: dict[str, object], reason: str
) -> None:
    """防止多对一合并或候选未获批 rename 冒充成员守恒。"""
    module = _load_module()

    result = module.verify_baseline_transition(
        _baseline("case:a", "case:b"),
        _candidate("case:new"),
        lineage,
    )

    assert result["status"] == "failed"
    assert result["reason"] == reason


def test_cell_contract_change_fails_closed() -> None:
    """防止候选通过删掉 OS/Python cell 缩小完整执行集合。"""
    module = _load_module()
    candidate = _candidate("case:a")
    candidate["cells"] = ["windows-latest-py3.14"]

    result = module.verify_baseline_transition(
        _baseline("case:a"),
        candidate,
        {"schema_version": "ci-test-lineage-v1", "mappings": []},
    )

    assert result["status"] == "failed"
    assert result["reason"] == "cell_contract_changed"


def _single_cell_manifest(source_commit: str = "a" * 40) -> dict[str, object]:
    module = _load_module()
    return module.build_collection_manifest(
        ["tests/a.py::test_one", "tests/a.py::test_two"],
        ["ubuntu-latest-py3.11"],
        source_commit=source_commit,
    )


def _write_junit(path: Path, body: str, *, tests: int = 2) -> None:
    path.write_text(
        f'<testsuite tests="{tests}" failures="0" errors="0" skipped="0">'
        f"{body}</testsuite>",
        encoding="utf-8",
    )


def test_cell_evidence_accepts_exact_successful_junit(tmp_path: Path) -> None:
    """防止成功 cell 因 XML 包装差异被误判，同时验证真实计数与耗时。"""
    module = _load_module()
    junit = tmp_path / "junit.xml"
    _write_junit(
        junit,
        '<testcase classname="tests.a" name="test_one"/>'
        '<testcase classname="tests.a" name="test_two"/>',
    )

    result = module.build_cell_evidence(
        _single_cell_manifest(),
        junit,
        cell="ubuntu-latest-py3.11",
        source_commit="a" * 40,
        started_at="2026-08-04T10:00:00+00:00",
        finished_at="2026-08-04T10:00:05+00:00",
    )

    assert result["status"] == "success"
    assert result["reason"] == "complete"
    assert result["collected_count"] == 2
    assert result["executed_count"] == 2
    assert result["duration_seconds"] == 5.0


@pytest.mark.parametrize(
    ("filename", "xml", "reason"),
    [
        ("missing.xml", None, "junit_missing"),
        ("empty.xml", "", "junit_empty"),
        ("broken.xml", "<testsuite>", "junit_corrupt"),
        (
            "skipped.xml",
            '<testsuite tests="2" skipped="1">'
            '<testcase classname="a" name="one"><skipped/></testcase>'
            '<testcase classname="a" name="two"/>'
            "</testsuite>",
            "non_success_terminal_state",
        ),
        (
            "failed.xml",
            '<testsuite tests="2" failures="1">'
            '<testcase classname="a" name="one"><failure/></testcase>'
            '<testcase classname="a" name="two"/>'
            "</testsuite>",
            "non_success_terminal_state",
        ),
        (
            "partial.xml",
            '<testsuite tests="1"><testcase classname="a" name="one"/></testsuite>',
            "execution_count_mismatch",
        ),
        (
            "duplicate.xml",
            '<testsuite tests="2">'
            '<testcase classname="a" name="same"/>'
            '<testcase classname="a" name="same"/>'
            "</testsuite>",
            "duplicate_testcase",
        ),
    ],
)
def test_cell_evidence_faults_fail_closed(
    tmp_path: Path, filename: str, xml: str | None, reason: str
) -> None:
    """防止缺失、损坏、skip、失败、部分或重复 JUnit 产生绿色终态。"""
    module = _load_module()
    junit = tmp_path / filename
    if xml is not None:
        junit.write_text(xml, encoding="utf-8")

    result = module.build_cell_evidence(
        _single_cell_manifest(),
        junit,
        cell="ubuntu-latest-py3.11",
        source_commit="a" * 40,
        started_at="2026-08-04T10:00:00+00:00",
        finished_at="2026-08-04T10:00:05+00:00",
    )

    assert result["status"] == "failed"
    assert result["reason"] == reason


def test_cell_evidence_rejects_wrong_candidate_or_cell(tmp_path: Path) -> None:
    """防止旧候选证据或其他 cell 的证据被当前 Assurance 复用。"""
    module = _load_module()
    junit = tmp_path / "junit.xml"
    _write_junit(
        junit,
        '<testcase classname="a" name="one"/>'
        '<testcase classname="a" name="two"/>',
    )

    wrong_commit = module.build_cell_evidence(
        _single_cell_manifest(),
        junit,
        cell="ubuntu-latest-py3.11",
        source_commit="b" * 40,
        started_at="2026-08-04T10:00:00+00:00",
        finished_at="2026-08-04T10:00:01+00:00",
    )
    wrong_cell = module.build_cell_evidence(
        _single_cell_manifest(),
        junit,
        cell="windows-latest-py3.14",
        source_commit="a" * 40,
        started_at="2026-08-04T10:00:00+00:00",
        finished_at="2026-08-04T10:00:01+00:00",
    )

    assert wrong_commit["reason"] == "candidate_commit_mismatch"
    assert wrong_cell["reason"] == "cell_not_collected"


def _successful_evidence(cell: str, *, duration: float = 3.0) -> dict[str, object]:
    return {
        "schema_version": "ci-cell-evidence-v1",
        "cell": cell,
        "source_commit": "a" * 40,
        "collection_manifest_digest": "sha256:manifest",
        "collected_count": 2,
        "executed_count": 2,
        "failures": 0,
        "errors": 0,
        "skipped": 0,
        "duplicate_testcases": [],
        "status": "success",
        "reason": "complete",
        "started_at": "2026-08-04T10:00:00+00:00",
        "finished_at": "2026-08-04T10:00:03+00:00",
        "duration_seconds": duration,
    }


def test_aggregate_requires_exact_cells_and_sums_runner_seconds() -> None:
    """防止部分矩阵被聚合为成功，并保留成本观察值。"""
    module = _load_module()
    evidence = [
        _successful_evidence("ubuntu-latest-py3.11", duration=2.5),
        _successful_evidence("windows-latest-py3.14", duration=4.5),
    ]

    success = module.aggregate_assurance(
        ["ubuntu-latest-py3.11", "windows-latest-py3.14"],
        evidence,
        candidate_commit="a" * 40,
        baseline_digest="sha256:baseline",
        fast_gate_status="success",
    )
    missing = module.aggregate_assurance(
        ["ubuntu-latest-py3.11", "windows-latest-py3.14"],
        evidence[:1],
        candidate_commit="a" * 40,
        baseline_digest="sha256:baseline",
        fast_gate_status="success",
    )

    assert success["status"] == "success"
    assert success["runner_seconds"] == 7.0
    assert success["late_red"] is False
    assert missing["status"] == "failed"
    assert missing["reason"] == "cell_set_mismatch"


def test_aggregate_marks_late_red_and_rejects_duplicate_or_failed_cell() -> None:
    """防止 Fast 绿后完整层红被吞掉，也防止重复 shard/cell 充数。"""
    module = _load_module()
    failed = _successful_evidence("ubuntu-latest-py3.11")
    failed["status"] = "failed"
    failed["reason"] = "non_success_terminal_state"

    late_red = module.aggregate_assurance(
        ["ubuntu-latest-py3.11"],
        [failed],
        candidate_commit="a" * 40,
        baseline_digest="sha256:baseline",
        fast_gate_status="success",
    )
    duplicate = module.aggregate_assurance(
        ["ubuntu-latest-py3.11"],
        [
            _successful_evidence("ubuntu-latest-py3.11"),
            _successful_evidence("ubuntu-latest-py3.11"),
        ],
        candidate_commit="a" * 40,
        baseline_digest="sha256:baseline",
        fast_gate_status="success",
    )

    assert late_red["status"] == "failed"
    assert late_red["reason"] == "non_success_cell"
    assert late_red["late_red"] is True
    assert duplicate["status"] == "failed"
    assert duplicate["reason"] == "duplicate_cell_evidence"
