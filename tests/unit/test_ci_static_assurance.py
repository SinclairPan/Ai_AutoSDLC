"""行为测试：框架自身 CI 的静态集合身份与完整性合同。"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "ci_static_assurance.py"


def _member_projection(case_ids: tuple[str, ...], cells: tuple[str, ...]):
    def digest(value: str) -> str:
        return f"sha256:{hashlib.sha256(value.encode('utf-8')).hexdigest()}"

    members = sorted(digest(f"{case_id}\0{cell}") for cell in cells for case_id in case_ids)
    return len(members), digest("\n".join(members))


def _load_module():
    spec = importlib.util.spec_from_file_location("ci_static_assurance", _SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _refresh_baseline_digest(baseline: dict[str, object]) -> dict[str, object]:
    canonical = {key: value for key, value in baseline.items() if key != "baseline_digest"}
    encoded = json.dumps(
        canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    baseline["baseline_digest"] = (
        f"sha256:{hashlib.sha256(encoded.encode('utf-8')).hexdigest()}"
    )
    return baseline


def _baseline(*case_ids: str) -> dict[str, object]:
    cells = ("ubuntu-latest-py3.11",)
    member_count, member_digest = _member_projection(case_ids, cells)
    return _refresh_baseline_digest({
        "schema_version": "ci-baseline-v1",
        "namespace": "pytest-nodeid-v1",
        "source_commit": "a" * 40,
        "previous_baseline_digest": None,
        "cells": list(cells),
        "case_ids": list(case_ids),
        "cell_case_omissions_by_cell": {cells[0]: []},
        "cell_case_additions_by_cell": {cells[0]: []},
        "execution_member_count": member_count,
        "execution_member_digest": member_digest,
        "allowed_skip_case_ids_by_cell": {cells[0]: []},
    })


def _candidate(
    *case_ids: str, previous_baseline_digest: str
) -> dict[str, object]:
    cells = ("ubuntu-latest-py3.11",)
    member_count, member_digest = _member_projection(case_ids, cells)
    return _refresh_baseline_digest({
        "schema_version": "ci-baseline-v1",
        "namespace": "pytest-nodeid-v1",
        "source_commit": "b" * 40,
        "previous_baseline_digest": previous_baseline_digest,
        "cells": list(cells),
        "case_ids": list(case_ids),
        "cell_case_omissions_by_cell": {cells[0]: []},
        "cell_case_additions_by_cell": {cells[0]: []},
        "execution_member_count": member_count,
        "execution_member_digest": member_digest,
        "allowed_skip_case_ids_by_cell": {cells[0]: []},
    })


def _transition_baselines(
    trusted_case_ids: tuple[str, ...], candidate_case_ids: tuple[str, ...]
) -> tuple[dict[str, object], dict[str, object]]:
    trusted = _baseline(*trusted_case_ids)
    candidate = _candidate(
        *candidate_case_ids,
        previous_baseline_digest=str(trusted["baseline_digest"]),
    )
    return trusted, candidate


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


def test_genesis_baseline_binds_protected_base_commit() -> None:
    """防止首次 baseline 只绑定候选而丢失受保护 main 的 genesis 来源。"""
    module = _load_module()
    manifest = module.build_collection_manifest(
        ["tests/a.py::test_ok"],
        ["ubuntu-latest-py3.11"],
        source_commit="b" * 40,
    )

    baseline = module.build_baseline(
        manifest,
        genesis_base_commit="a" * 40,
    )

    assert baseline["source_commit"] == "b" * 40
    assert baseline["genesis_base_commit"] == "a" * 40
    assert baseline["previous_baseline_digest"] is None
    assert baseline["baseline_digest"].startswith("sha256:")


def test_baseline_keeps_member_projection_compact() -> None:
    """防止 12 个 cell 的派生成员全部写入仓库，造成 baseline 体积膨胀。"""
    module = _load_module()
    manifest = module.build_collection_manifest(
        ["tests/a.py::test_ok", "tests/b.py::test_ok"],
        ["ubuntu-latest-py3.11", "windows-latest-py3.14"],
        source_commit="b" * 40,
    )

    baseline = module.build_baseline(manifest)

    assert "execution_member_ids" not in baseline
    assert "case_nodeids" not in baseline
    assert baseline["execution_member_count"] == 4
    assert str(baseline["execution_member_digest"]).startswith("sha256:")


def test_baseline_compacts_platform_specific_collection_as_cell_deltas() -> None:
    """平台差异只保存相对 reference 的小增量，不复制十二份完整集合。"""
    module = _load_module()
    manifest = module.build_collection_manifest(
        ["tests/a.py::test_common", "tests/a.py::test_posix"],
        ["ubuntu-latest-py3.11", "windows-latest-py3.11"],
        source_commit="a" * 40,
    )
    common, posix = manifest["case_ids"]
    windows_only = module._stable_case_id("tests/a.py::test_windows")

    baseline = module.build_baseline(
        manifest,
        cell_case_omissions_by_cell={"windows-latest-py3.11": [posix]},
        cell_case_additions_by_cell={"windows-latest-py3.11": [windows_only]},
    )

    expected_count, expected_digest = module._execution_member_projection_by_cell(
        {
            "ubuntu-latest-py3.11": {common, posix},
            "windows-latest-py3.11": {common, windows_only},
        }
    )
    assert baseline["cell_case_omissions_by_cell"] == {
        "ubuntu-latest-py3.11": [],
        "windows-latest-py3.11": [posix],
    }
    assert baseline["cell_case_additions_by_cell"] == {
        "ubuntu-latest-py3.11": [],
        "windows-latest-py3.11": [windows_only],
    }
    assert baseline["execution_member_count"] == expected_count
    assert baseline["execution_member_digest"] == expected_digest


@pytest.mark.parametrize(
    ("omissions", "additions"),
    [
        ({"windows-latest-py3.11": ["case:unknown"]}, {}),
        ({}, {"windows-latest-py3.11": ["case:reference"]}),
        ({}, {"unknown-cell": ["case:new"]}),
    ],
)
def test_baseline_rejects_invalid_platform_delta_contract(
    omissions: dict[str, list[str]], additions: dict[str, list[str]]
) -> None:
    """防止未知成员、reference 重复成员或未知 cell 污染受保护投影。"""
    module = _load_module()
    manifest = module.build_collection_manifest(
        ["tests/a.py::test_reference"],
        ["windows-latest-py3.11"],
        source_commit="a" * 40,
    )
    reference = manifest["case_ids"][0]
    additions = {
        cell: [reference if value == "case:reference" else value for value in values]
        for cell, values in additions.items()
    }

    with pytest.raises(module.AssuranceError, match="invalid cell case delta"):
        module.build_baseline(
            manifest,
            cell_case_omissions_by_cell=omissions,
            cell_case_additions_by_cell=additions,
        )


def test_baseline_transition_allows_only_monotonic_addition() -> None:
    """防止新增测试因 baseline 保守策略被误判，同时保持旧成员。"""
    module = _load_module()

    trusted, candidate = _transition_baselines(
        ("case:a", "case:b"), ("case:a", "case:b", "case:c")
    )
    result = module.verify_baseline_transition(
        trusted,
        candidate,
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

    trusted, candidate = _transition_baselines(("case:a", "case:b"), ("case:a",))
    result = module.verify_baseline_transition(
        trusted,
        candidate,
        {"schema_version": "ci-test-lineage-v1", "mappings": []},
    )

    assert result["status"] == "failed"
    assert result["reason"] == "unauthorized_negative_delta"
    assert result["removed_case_ids"] == ["case:b"]


def test_baseline_transition_rejects_stale_candidate_digest() -> None:
    """候选 baseline 被修改但未重算 digest 时不能进入后续链路。"""
    module = _load_module()
    trusted, candidate = _transition_baselines(("case:a",), ("case:a", "case:b"))
    candidate["source_commit"] = "c" * 40

    result = module.verify_baseline_transition(
        trusted,
        candidate,
        {"schema_version": "ci-test-lineage-v1", "mappings": []},
    )

    assert result["status"] == "failed"
    assert result["reason"] == "baseline_digest_mismatch"


def test_protected_one_to_one_lineage_allows_member_conserving_rename() -> None:
    """防止合法 rename 被当成删除；映射必须来自受保护 base。"""
    module = _load_module()

    trusted, candidate = _transition_baselines(("case:old",), ("case:new",))
    result = module.verify_baseline_transition(
        trusted,
        candidate,
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

    trusted, candidate = _transition_baselines(("case:a", "case:b"), ("case:new",))
    result = module.verify_baseline_transition(
        trusted,
        candidate,
        lineage,
    )

    assert result["status"] == "failed"
    assert result["reason"] == reason


def test_cell_contract_change_fails_closed() -> None:
    """防止候选通过删掉 OS/Python cell 缩小完整执行集合。"""
    module = _load_module()
    trusted, candidate = _transition_baselines(("case:a",), ("case:a",))
    candidate["cells"] = ["windows-latest-py3.14"]
    _refresh_baseline_digest(candidate)

    result = module.verify_baseline_transition(
        trusted,
        candidate,
        {"schema_version": "ci-test-lineage-v1", "mappings": []},
    )

    assert result["status"] == "failed"
    assert result["reason"] == "cell_contract_changed"


def test_baseline_transition_rejects_new_allowed_skip() -> None:
    """防止候选把新 skip 写入 baseline 后自行降低完整质量地板。"""
    module = _load_module()
    trusted, candidate = _transition_baselines(
        ("case:a", "case:b"), ("case:a", "case:b")
    )
    candidate["allowed_skip_case_ids_by_cell"] = {
        "ubuntu-latest-py3.11": ["case:b"]
    }
    _refresh_baseline_digest(candidate)

    result = module.verify_baseline_transition(
        trusted,
        candidate,
        {"schema_version": "ci-test-lineage-v1", "mappings": []},
    )

    assert result["status"] == "failed"
    assert result["reason"] == "allowed_skip_expanded"


def test_baseline_transition_rejects_platform_specific_protected_loss() -> None:
    """防止候选只在某个 OS 的 compact delta 中删除受保护测试。"""
    module = _load_module()
    trusted, candidate = _transition_baselines(
        ("case:a", "case:b"), ("case:a", "case:b")
    )
    candidate["cell_case_omissions_by_cell"] = {
        "ubuntu-latest-py3.11": ["case:b"]
    }
    count, digest = module._execution_member_projection_by_cell(
        {"ubuntu-latest-py3.11": {"case:a"}}
    )
    candidate["execution_member_count"] = count
    candidate["execution_member_digest"] = digest
    _refresh_baseline_digest(candidate)

    result = module.verify_baseline_transition(
        trusted,
        candidate,
        {"schema_version": "ci-test-lineage-v1", "mappings": []},
    )

    assert result["status"] == "failed"
    assert result["reason"] == "unauthorized_negative_delta"
    assert result["removed_case_ids"] == ["case:b"]


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


def test_supervised_pytest_rebuilds_evidence_after_candidate_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """候选退出钩子即使先伪造 JUnit，父监督进程也必须在其退出后重建证据。"""
    module = _load_module()
    manifest = _single_cell_manifest()
    junit = tmp_path / "supervised.xml"

    def fake_run(command, **kwargs):
        nonce = command[command.index("--nonce") + 1]
        _write_junit(
            junit,
            '<testcase classname="tests.a" name="test_one"/>'
            '<testcase classname="tests.a" name="test_two"/>',
        )
        reports = [
            {"nodeid": "tests/a.py::test_one", "when": "setup", "outcome": "passed"},
            {"nodeid": "tests/a.py::test_one", "when": "call", "outcome": "passed"},
            {
                "nodeid": "tests/a.py::test_one",
                "when": "teardown",
                "outcome": "passed",
            },
            {
                "nodeid": "tests/a.py::test_two",
                "when": "setup",
                "outcome": "skipped",
            },
        ]
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=(
                module._encode_pytest_report_payload(nonce, reports)
                + "\n"
                + module._encode_pytest_report_payload(nonce, [], complete=True)
                + "\n"
            ),
            stderr="",
        )

    monkeypatch.setattr(module.subprocess, "run", fake_run)

    returncode = module.run_pytest_with_trusted_evidence(
        tmp_path,
        manifest,
        junit,
        pytest_args=["-q"],
    )
    evidence = module.build_cell_evidence(
        manifest,
        junit,
        cell="ubuntu-latest-py3.11",
        source_commit="a" * 40,
        started_at="2026-08-04T10:00:00+00:00",
        finished_at="2026-08-04T10:00:05+00:00",
    )

    assert returncode == 0
    assert evidence["status"] == "failed"
    assert evidence["reason"] == "non_success_terminal_state"
    assert evidence["skipped"] == 1


def test_supervised_pytest_stream_is_not_rewritable_by_candidate_plugin(
    tmp_path: Path,
) -> None:
    """候选插件能枚举 recorder，也不能在 unconfigure 抹掉已流出的 skip。"""
    module = _load_module()
    nodeid = "test_sample.py::test_never_runs"
    (tmp_path / "test_sample.py").write_text(
        "def test_never_runs():\n    raise AssertionError('must stay skipped')\n",
        encoding="utf-8",
    )
    (tmp_path / "conftest.py").write_text(
        "import pytest\n\n"
        "def pytest_collection_modifyitems(items):\n"
        "    for item in items:\n"
        "        item.add_marker(pytest.mark.skip(reason='candidate skip'))\n\n"
        "def pytest_unconfigure(config):\n"
        "    for plugin in config.pluginmanager.get_plugins():\n"
        "        reports = getattr(plugin, 'reports', None)\n"
        "        if isinstance(reports, list):\n"
        "            reports[:] = [{\n"
        f"                'nodeid': '{nodeid}',\n"
        "                'when': 'call',\n"
        "                'outcome': 'passed',\n"
        "                'duration': 0.0,\n"
        "            }]\n",
        encoding="utf-8",
    )
    manifest = module.build_collection_manifest(
        [nodeid], ["ubuntu-latest-py3.11"], source_commit="a" * 40
    )
    junit = tmp_path / "streamed.xml"

    returncode = module.run_pytest_with_trusted_evidence(
        tmp_path,
        manifest,
        junit,
        pytest_args=["-q"],
    )
    evidence = module.build_cell_evidence(
        manifest,
        junit,
        cell="ubuntu-latest-py3.11",
        source_commit="a" * 40,
        started_at="2026-08-04T10:00:00+00:00",
        finished_at="2026-08-04T10:00:05+00:00",
    )

    assert returncode == 0
    assert evidence["status"] == "failed"
    assert evidence["reason"] == "non_success_terminal_state"
    assert evidence["skipped"] == 1


def test_baseline_digest_validation_rejects_stale_payload() -> None:
    """候选 baseline 内容变化后未重算 digest 时必须 fail closed。"""
    module = _load_module()
    baseline = _two_cell_baseline()
    baseline["source_commit"] = "c" * 40

    with pytest.raises(module.AssuranceError, match="baseline digest mismatch"):
        module._validated_baseline_digest(baseline)


def test_cell_evidence_accepts_only_protected_skip_identity(tmp_path: Path) -> None:
    """允许受保护基线已有的平台 skip，但拒绝同数量的新 skip 替换。"""
    module = _load_module()
    manifest = _single_cell_manifest()
    case_by_nodeid = {
        nodeid: case_id for case_id, nodeid in manifest["case_nodeids"].items()
    }
    allowed = case_by_nodeid["tests/a.py::test_one"]
    junit = tmp_path / "allowed.xml"
    _write_junit(
        junit,
        '<testcase classname="tests.a" name="test_one"><skipped/></testcase>'
        '<testcase classname="tests.a" name="test_two"/>',
    )

    allowed_result = module.build_cell_evidence(
        manifest,
        junit,
        cell="ubuntu-latest-py3.11",
        source_commit="a" * 40,
        started_at="2026-08-04T10:00:00+00:00",
        finished_at="2026-08-04T10:00:05+00:00",
        allowed_skip_case_ids=[allowed],
    )
    substituted = module.build_cell_evidence(
        manifest,
        junit,
        cell="ubuntu-latest-py3.11",
        source_commit="a" * 40,
        started_at="2026-08-04T10:00:00+00:00",
        finished_at="2026-08-04T10:00:05+00:00",
        allowed_skip_case_ids=[case_by_nodeid["tests/a.py::test_two"]],
    )

    assert allowed_result["status"] == "success"
    assert allowed_result["skipped_case_ids"] == [allowed]
    assert substituted["status"] == "failed"
    assert substituted["reason"] == "unexpected_skip_identity"


def test_cell_evidence_normalizes_escaped_parameter_identity(tmp_path: Path) -> None:
    """确保 collection 与 JUnit 对 Windows 路径参数使用同一稳定身份。"""
    module = _load_module()
    manifest = module.build_collection_manifest(
        [r"tests/a.py::test_one[dir\leaf]"],
        ["ubuntu-latest-py3.11"],
        source_commit="a" * 40,
    )
    junit = tmp_path / "escaped.xml"
    _write_junit(
        junit,
        r'<testcase classname="tests.a" name="test_one[dir\leaf]"/>',
        tests=1,
    )

    result = module.build_cell_evidence(
        manifest,
        junit,
        cell="ubuntu-latest-py3.11",
        source_commit="a" * 40,
        started_at="2026-08-04T10:00:00+00:00",
        finished_at="2026-08-04T10:00:05+00:00",
    )

    assert result["status"] == "success"


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
        "skipped_case_ids": [],
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
        allowed_skip_case_ids_by_cell={
            "ubuntu-latest-py3.11": [],
            "windows-latest-py3.14": [],
        },
    )
    missing = module.aggregate_assurance(
        ["ubuntu-latest-py3.11", "windows-latest-py3.14"],
        evidence[:1],
        candidate_commit="a" * 40,
        baseline_digest="sha256:baseline",
        fast_gate_status="success",
        allowed_skip_case_ids_by_cell={
            "ubuntu-latest-py3.11": [],
            "windows-latest-py3.14": [],
        },
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
        allowed_skip_case_ids_by_cell={"ubuntu-latest-py3.11": []},
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
        allowed_skip_case_ids_by_cell={"ubuntu-latest-py3.11": []},
    )

    assert late_red["status"] == "failed"
    assert late_red["reason"] == "non_success_cell"
    assert late_red["late_red"] is True
    assert duplicate["status"] == "failed"
    assert duplicate["reason"] == "duplicate_cell_evidence"


def test_aggregate_rechecks_skips_against_pristine_contract() -> None:
    """候选测试即使改写本地 baseline，也不能授权新的 skip。"""
    module = _load_module()
    cell = "ubuntu-latest-py3.11"
    evidence = _successful_evidence(cell)
    evidence["skipped"] = 1
    evidence["skipped_case_ids"] = ["sha256:candidate-authorized-skip"]

    result = module.aggregate_assurance(
        [cell],
        [evidence],
        candidate_commit="a" * 40,
        baseline_digest="sha256:baseline",
        fast_gate_status="success",
        allowed_skip_case_ids_by_cell={cell: []},
    )

    assert result["status"] == "failed"
    assert result["reason"] == "unexpected_skip_identity"


@pytest.mark.parametrize(
    ("event_name", "draft", "authority", "protected_changed", "full", "reason"),
    [
        ("pull_request", True, True, False, False, "ordinary_draft_fast_gate"),
        ("pull_request", False, True, False, True, "ready_pull_request"),
        ("pull_request", True, False, False, True, "authority_unavailable"),
        ("pull_request", True, True, True, True, "protected_ci_change"),
        ("merge_group", False, True, False, True, "merge_candidate"),
        ("push", False, True, False, True, "protected_main_or_candidate"),
        ("workflow_call", False, True, False, True, "release_candidate"),
        ("unknown", True, True, False, True, "unknown_event_fail_closed"),
    ],
)
def test_assurance_mode_only_keeps_ordinary_draft_change_fast(
    event_name: str,
    draft: bool,
    authority: bool,
    protected_changed: bool,
    full: bool,
    reason: str,
) -> None:
    """防止普通 Draft 再跑全量，也防止 ready/RC/未知事件绕过完整层。"""
    module = _load_module()

    result = module.decide_assurance_mode(
        event_name=event_name,
        pull_request_draft=draft,
        authority_available=authority,
        protected_paths_changed=protected_changed,
        force_full=False,
    )

    assert result == {"full_assurance_required": full, "reason": reason}


def _two_cell_baseline() -> dict[str, object]:
    module = _load_module()
    manifest = module.build_collection_manifest(
        ["tests/a.py::test_one"],
        ["ubuntu-latest-py3.11", "windows-latest-py3.14"],
        source_commit="a" * 40,
    )
    return module.build_baseline(manifest)


def _single_manifest(cell: str, *nodeids: str) -> dict[str, object]:
    module = _load_module()
    return module.build_collection_manifest(
        list(nodeids) or ["tests/a.py::test_one"],
        [cell],
        source_commit="a" * 40,
    )


def test_collection_coverage_matches_protected_members_in_every_cell() -> None:
    """防止单 cell 正确但跨 cell 缺少受保护成员。"""
    module = _load_module()
    manifests = [
        _single_manifest("ubuntu-latest-py3.11"),
        _single_manifest("windows-latest-py3.14"),
    ]

    result = module.verify_collection_coverage(_two_cell_baseline(), manifests)

    assert result == {
        "status": "success",
        "reason": "protected_collection_coverage",
        "cells": ["ubuntu-latest-py3.11", "windows-latest-py3.14"],
        "protected_case_count": 1,
        "execution_member_count": 2,
    }


def test_collection_coverage_rejects_unpromoted_runtime_addition() -> None:
    """新增测试必须先进入 candidate baseline，避免后续删除时失去保护。"""
    module = _load_module()
    manifests = [
        _single_manifest(
            "ubuntu-latest-py3.11",
            "tests/a.py::test_one",
            "tests/a.py::test_new",
        ),
        _single_manifest(
            "windows-latest-py3.14",
            "tests/a.py::test_one",
            "tests/a.py::test_windows_new",
        ),
    ]

    result = module.verify_collection_coverage(_two_cell_baseline(), manifests)

    assert result["status"] == "failed"
    assert result["reason"] == "unprotected_runtime_addition"


def test_collection_coverage_honors_compact_platform_delta() -> None:
    """Windows 与 POSIX 的真实 collection 差异由 cell delta 精确约束。"""
    module = _load_module()
    reference_manifest = module.build_collection_manifest(
        ["tests/a.py::test_common", "tests/a.py::test_posix"],
        ["ubuntu-latest-py3.11", "windows-latest-py3.14"],
        source_commit="a" * 40,
    )
    by_nodeid = {
        nodeid: case_id
        for case_id, nodeid in reference_manifest["case_nodeids"].items()
    }
    windows_only = module._stable_case_id("tests/a.py::test_windows")
    baseline = module.build_baseline(
        reference_manifest,
        cell_case_omissions_by_cell={
            "windows-latest-py3.14": [by_nodeid["tests/a.py::test_posix"]]
        },
        cell_case_additions_by_cell={
            "windows-latest-py3.14": [windows_only]
        },
    )
    manifests = [
        _single_manifest(
            "ubuntu-latest-py3.11",
            "tests/a.py::test_common",
            "tests/a.py::test_posix",
        ),
        _single_manifest(
            "windows-latest-py3.14",
            "tests/a.py::test_common",
            "tests/a.py::test_windows",
        ),
    ]

    result = module.verify_collection_coverage(baseline, manifests)

    assert result["status"] == "success"


def test_collection_coverage_rejects_unpromoted_runtime_rename() -> None:
    """lineage 证明 baseline 迁移，不能替代 candidate baseline 本身更新。"""
    module = _load_module()
    old_case = module._stable_case_id("tests/a.py::test_one")
    new_case = module._stable_case_id("tests/a.py::test_renamed")
    manifests = [
        _single_manifest("ubuntu-latest-py3.11", "tests/a.py::test_renamed"),
        _single_manifest("windows-latest-py3.14", "tests/a.py::test_renamed"),
    ]

    result = module.verify_collection_coverage(
        _two_cell_baseline(),
        manifests,
        protected_lineage={
            "schema_version": "ci-test-lineage-v1",
            "mappings": [{"from_case_id": old_case, "to_case_id": new_case}],
        },
    )

    assert result["status"] == "failed"
    assert result["reason"] == "protected_case_missing"


def test_collection_coverage_uses_runtime_candidate_binding_not_baseline_origin() -> None:
    """防止 tracked baseline 需要自引用尚未产生的候选 commit。"""
    module = _load_module()
    baseline = _two_cell_baseline()
    baseline["source_commit"] = "b" * 40
    manifests = [
        _single_manifest("ubuntu-latest-py3.11"),
        _single_manifest("windows-latest-py3.14"),
    ]

    result = module.verify_collection_coverage(baseline, manifests)

    assert result["status"] == "success"


def test_collection_coverage_rejects_mixed_runtime_candidate_commits() -> None:
    """防止不同候选提交的 collection manifest 被拼成一次完整证明。"""
    module = _load_module()
    manifests = [
        _single_manifest("ubuntu-latest-py3.11"),
        _single_manifest("windows-latest-py3.14"),
    ]
    manifests[1]["source_commit"] = "c" * 40

    result = module.verify_collection_coverage(_two_cell_baseline(), manifests)

    assert result["status"] == "failed"
    assert result["reason"] == "candidate_commit_mismatch"


def test_collection_coverage_binds_runtime_manifests_to_expected_commit() -> None:
    """防止同一旧提交的完整 collection 被用于证明当前候选。"""
    module = _load_module()
    manifests = [
        _single_manifest("ubuntu-latest-py3.11"),
        _single_manifest("windows-latest-py3.14"),
    ]

    result = module.verify_collection_coverage(
        _two_cell_baseline(), manifests, expected_source_commit="d" * 40
    )

    assert result["status"] == "failed"
    assert result["reason"] == "candidate_commit_mismatch"


@pytest.mark.parametrize(
    ("mutator", "reason"),
    [
        (lambda manifests: manifests[:1], "cell_set_mismatch"),
        (lambda manifests: [manifests[0], manifests[0]], "duplicate_cell_manifest"),
        (
            lambda manifests: [
                manifests[0],
                _single_manifest("windows-latest-py3.14", "tests/a.py::test_other"),
            ],
            "protected_case_missing",
        ),
    ],
)
def test_collection_coverage_faults_fail_closed(mutator, reason: str) -> None:
    """防止缺 cell、重复 cell 或受保护成员缺失通过聚合。"""
    module = _load_module()
    manifests = [
        _single_manifest("ubuntu-latest-py3.11"),
        _single_manifest("windows-latest-py3.14"),
    ]

    result = module.verify_collection_coverage(
        _two_cell_baseline(), mutator(manifests)
    )

    assert result["status"] == "failed"
    assert result["reason"] == reason


def test_collection_coverage_rejects_overlapping_execution_members() -> None:
    """防止 shard/cell 通过重复 execution member 充数。"""
    module = _load_module()
    manifests = [
        _single_manifest("ubuntu-latest-py3.11"),
        _single_manifest("windows-latest-py3.14"),
    ]
    manifests[1]["execution_member_ids"] = list(
        manifests[0]["execution_member_ids"]
    )

    result = module.verify_collection_coverage(_two_cell_baseline(), manifests)

    assert result["status"] == "failed"
    assert result["reason"] == "execution_member_overlap"
