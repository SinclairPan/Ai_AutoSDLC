from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


def _load_builder():
    path = (
        Path(__file__).resolve().parents[2]
        / "scripts"
        / "build_activation_quality_cell.py"
    )
    spec = importlib.util.spec_from_file_location(
        "build_activation_quality_cell",
        path,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _junit(testcases: tuple[tuple[str, float], ...]) -> str:
    rows = "".join(
        f'<testcase classname="tests.activation" name="{name}" time="{duration}" />'
        for name, duration in testcases
    )
    return (
        '<testsuites tests="1" failures="0" errors="0" skipped="0">'
        f'<testsuite tests="{len(testcases)}" failures="0" errors="0" skipped="0">'
        f"{rows}</testsuite></testsuites>"
    )


def test_quality_cell_derives_each_metric_from_named_testcases(
    tmp_path: Path,
) -> None:
    builder = _load_builder()
    testcases = tuple(
        (names[0], 0.25 if metric == "planner_latency" else 0.01)
        for metric, names in builder._PROBE_TEST_NAMES.items()
    )
    junit = tmp_path / "junit.xml"
    junit.write_text(_junit(testcases), encoding="utf-8")

    payload = builder._build_cell(
        junit,
        runner_os="Linux",
        tested_commit="a" * 40,
    )

    assert payload["evidence_valid"] is True
    assert set(payload["metrics"]) == set(builder._PROBE_TEST_NAMES)
    assert payload["metrics"]["planner_latency"]["p95_seconds"] == 0.25
    assert all(
        metric["trials"] >= 1 and metric["failures"] == 0
        for metric in payload["metrics"].values()
    )


def test_quality_cell_rejects_an_unexercised_metric(tmp_path: Path) -> None:
    builder = _load_builder()
    junit = tmp_path / "junit.xml"
    junit.write_text(
        _junit((("test_canonical_executor_authorizes_and_replays_without_provider_recall", 0.01),)),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="unexercised"):
        builder._build_cell(
            junit,
            runner_os="Linux",
            tested_commit="a" * 40,
        )
