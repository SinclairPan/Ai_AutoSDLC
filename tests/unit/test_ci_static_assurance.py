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

