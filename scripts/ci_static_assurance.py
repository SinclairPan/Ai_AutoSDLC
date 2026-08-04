#!/usr/bin/env python3
"""框架仓库内部 CI 的静态集合、身份与完整性验证器。

该脚本只服务 Ai_AutoSDLC 自身 GitHub Actions，不属于发布包公开 CLI。受保护
main 中的脚本和 baseline 才是验证权威；候选 checkout 只作为待验证数据。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import xml.etree.ElementTree as ET
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

CASE_NAMESPACE = "pytest-nodeid-v1"
MANIFEST_SCHEMA = "ci-test-manifest-v1"
BASELINE_SCHEMA = "ci-baseline-v1"
LINEAGE_SCHEMA = "ci-test-lineage-v1"
CELL_EVIDENCE_SCHEMA = "ci-cell-evidence-v1"
AGGREGATE_SCHEMA = "ci-assurance-report-v1"
DEFAULT_COLLECTION_COMMAND = "pytest --collect-only -q --ignore=tests/e2e/stage_review"
DEFAULT_CELLS = tuple(
    f"{os_name}-py{python_version}"
    for os_name in ("ubuntu-latest", "macos-latest", "windows-latest")
    for python_version in ("3.11", "3.12", "3.13", "3.14")
)


class AssuranceError(ValueError):
    """表示集合或权威合同不完整，调用方必须 fail-closed。"""


def _sha256(value: str) -> str:
    return f"sha256:{hashlib.sha256(value.encode('utf-8')).hexdigest()}"


def _canonical_digest(payload: Mapping[str, object], digest_field: str) -> str:
    canonical = {key: value for key, value in payload.items() if key != digest_field}
    encoded = json.dumps(
        canonical,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return _sha256(encoded)


def _normalize_nodeid(raw: str) -> str:
    nodeid = raw.strip().replace("\\", "/")
    if not nodeid or "::" not in nodeid:
        raise AssuranceError(f"invalid pytest nodeid: {raw!r}")
    return nodeid


def _stable_case_id(nodeid: str, namespace: str = CASE_NAMESPACE) -> str:
    return _sha256(f"{namespace}\0{nodeid}")


def _stable_execution_member_id(case_id: str, cell: str) -> str:
    return _sha256(f"{case_id}\0{cell}")


def _execution_member_projection(
    case_ids: Sequence[str], cells: Sequence[str]
) -> tuple[int, str]:
    members = sorted(
        _stable_execution_member_id(case_id, cell)
        for cell in cells
        for case_id in case_ids
    )
    return len(members), _sha256("\n".join(members))


def _unique(values: Sequence[str], *, label: str) -> list[str]:
    normalized = [str(value).strip() for value in values]
    if any(not value for value in normalized):
        raise AssuranceError(f"empty {label}")
    if len(normalized) != len(set(normalized)):
        raise AssuranceError(f"duplicate {label}")
    return sorted(normalized)


def build_collection_manifest(
    nodeids: Sequence[str],
    cells: Sequence[str],
    source_commit: str,
    *,
    collection_command: str = DEFAULT_COLLECTION_COMMAND,
) -> dict[str, object]:
    """为稳定 pytest nodeid 与执行 cell 生成可复算 manifest。"""
    normalized_nodeids = [_normalize_nodeid(nodeid) for nodeid in nodeids]
    if len(normalized_nodeids) != len(set(normalized_nodeids)):
        raise AssuranceError("duplicate collected nodeid")
    normalized_nodeids.sort()
    normalized_cells = _unique(cells, label="execution cell")

    case_nodeids = {
        _stable_case_id(nodeid): nodeid for nodeid in normalized_nodeids
    }
    case_ids = sorted(case_nodeids)
    execution_member_ids = sorted(
        _stable_execution_member_id(case_id, cell)
        for cell in normalized_cells
        for case_id in case_ids
    )
    manifest: dict[str, object] = {
        "schema_version": MANIFEST_SCHEMA,
        "namespace": CASE_NAMESPACE,
        "source_commit": source_commit.strip(),
        "collection_command": collection_command.strip(),
        "case_ids": case_ids,
        "case_nodeids": case_nodeids,
        "cells": normalized_cells,
        "execution_member_ids": execution_member_ids,
    }
    manifest["manifest_digest"] = _canonical_digest(manifest, "manifest_digest")
    return manifest


def build_baseline(
    manifest: Mapping[str, object],
    *,
    previous_baseline_digest: str | None = None,
    genesis_base_commit: str | None = None,
) -> dict[str, object]:
    """把实际 collection 投影为受保护 baseline 候选。"""
    case_ids = list(manifest.get("case_ids", []))
    cells = list(manifest.get("cells", []))
    member_count, member_digest = _execution_member_projection(case_ids, cells)
    baseline: dict[str, object] = {
        "schema_version": BASELINE_SCHEMA,
        "namespace": manifest.get("namespace"),
        "source_commit": manifest.get("source_commit"),
        "genesis_base_commit": genesis_base_commit,
        "previous_baseline_digest": previous_baseline_digest,
        "collection_command": manifest.get("collection_command"),
        "cells": cells,
        "case_ids": case_ids,
        "execution_member_count": member_count,
        "execution_member_digest": member_digest,
    }
    baseline["baseline_digest"] = _canonical_digest(baseline, "baseline_digest")
    return baseline


def _transition_failure(
    reason: str, *, removed_case_ids: Sequence[str] = ()
) -> dict[str, object]:
    return {
        "status": "failed",
        "reason": reason,
        "added_case_ids": [],
        "renamed_case_ids": [],
        "removed_case_ids": sorted(removed_case_ids),
    }


def verify_baseline_transition(
    trusted: Mapping[str, object],
    candidate: Mapping[str, object],
    protected_lineage: Mapping[str, object],
) -> dict[str, object]:
    """验证候选集合仅正向增长或使用受保护的一对一 rename。"""
    if trusted.get("namespace") != candidate.get("namespace"):
        return _transition_failure("namespace_mismatch")
    trusted_cells = list(trusted.get("cells", []))
    candidate_cells = list(candidate.get("cells", []))
    if trusted_cells != candidate_cells:
        return _transition_failure("cell_contract_changed")
    if candidate.get("previous_baseline_digest") != trusted.get("baseline_digest"):
        return _transition_failure("previous_baseline_digest_mismatch")

    trusted_cases = list(trusted.get("case_ids", []))
    candidate_cases = list(candidate.get("case_ids", []))
    if len(trusted_cases) != len(set(trusted_cases)) or len(candidate_cases) != len(
        set(candidate_cases)
    ):
        return _transition_failure("duplicate_case_identity")
    trusted_projection = _execution_member_projection(trusted_cases, trusted_cells)
    candidate_projection = _execution_member_projection(candidate_cases, candidate_cells)
    if trusted_projection != (
        trusted.get("execution_member_count"),
        trusted.get("execution_member_digest"),
    ) or candidate_projection != (
        candidate.get("execution_member_count"),
        candidate.get("execution_member_digest"),
    ):
        return _transition_failure("execution_member_set_invalid")

    if protected_lineage.get("schema_version") != LINEAGE_SCHEMA:
        return _transition_failure("lineage_schema_invalid")
    mappings = list(protected_lineage.get("mappings", []))
    from_ids = [str(item.get("from_case_id", "")) for item in mappings]
    to_ids = [str(item.get("to_case_id", "")) for item in mappings]
    if (
        any(not value for value in from_ids + to_ids)
        or len(from_ids) != len(set(from_ids))
        or len(to_ids) != len(set(to_ids))
    ):
        return _transition_failure("lineage_not_one_to_one")

    trusted_set = set(trusted_cases)
    candidate_set = set(candidate_cases)
    missing = trusted_set - candidate_set
    added = candidate_set - trusted_set
    lineage_by_source = {
        str(item["from_case_id"]): str(item["to_case_id"]) for item in mappings
    }
    accepted_renames: list[dict[str, str]] = []
    for source in sorted(missing):
        target = lineage_by_source.get(source)
        if not target or target not in added:
            return _transition_failure(
                "unauthorized_negative_delta", removed_case_ids=missing
            )
        accepted_renames.append({"from_case_id": source, "to_case_id": target})

    renamed_targets = {item["to_case_id"] for item in accepted_renames}
    return {
        "status": "success",
        "reason": "monotonic_transition",
        "added_case_ids": sorted(added - renamed_targets),
        "renamed_case_ids": accepted_renames,
        "removed_case_ids": [],
    }


def decide_assurance_mode(
    *,
    event_name: str,
    pull_request_draft: bool,
    authority_available: bool,
    protected_paths_changed: bool,
    force_full: bool,
) -> dict[str, object]:
    """静态决定是否运行完整层；任何未知或权威缺失都 fail-safe。"""
    if force_full:
        return {"full_assurance_required": True, "reason": "force_full"}
    if not authority_available:
        return {"full_assurance_required": True, "reason": "authority_unavailable"}
    if protected_paths_changed:
        return {"full_assurance_required": True, "reason": "protected_ci_change"}
    if event_name == "pull_request":
        if pull_request_draft:
            return {
                "full_assurance_required": False,
                "reason": "ordinary_draft_fast_gate",
            }
        return {"full_assurance_required": True, "reason": "ready_pull_request"}
    reasons = {
        "merge_group": "merge_candidate",
        "push": "protected_main_or_candidate",
        "workflow_dispatch": "protected_main_or_candidate",
        "schedule": "protected_main_or_candidate",
        "workflow_call": "release_candidate",
    }
    return {
        "full_assurance_required": True,
        "reason": reasons.get(event_name, "unknown_event_fail_closed"),
    }


def _coverage_failure(reason: str) -> dict[str, object]:
    return {"status": "failed", "reason": reason}


def verify_collection_coverage(
    candidate_baseline: Mapping[str, object],
    manifests: Sequence[Mapping[str, object]],
    *,
    expected_source_commit: str | None = None,
) -> dict[str, object]:
    """证明所有 cell 的实际 collection union 等于候选 baseline。"""
    baseline_cells = list(candidate_baseline.get("cells", []))
    baseline_cases = set(candidate_baseline.get("case_ids", []))
    baseline_member_count = candidate_baseline.get("execution_member_count")
    baseline_member_digest = candidate_baseline.get("execution_member_digest")
    expected_projection = _execution_member_projection(
        sorted(baseline_cases), baseline_cells
    )
    if (
        not baseline_cells
        or len(baseline_cells) != len(set(baseline_cells))
        or not baseline_cases
        or expected_projection != (baseline_member_count, baseline_member_digest)
    ):
        return _coverage_failure("candidate_baseline_invalid")

    manifest_cells: list[str] = []
    for manifest in manifests:
        cells = list(manifest.get("cells", []))
        if len(cells) != 1:
            return _coverage_failure("manifest_cell_cardinality_invalid")
        manifest_cells.append(str(cells[0]))
    if len(manifest_cells) != len(set(manifest_cells)):
        return _coverage_failure("duplicate_cell_manifest")
    if set(manifest_cells) != set(baseline_cells):
        return _coverage_failure("cell_set_mismatch")

    runtime_commits = {
        str(manifest.get("source_commit", "")) for manifest in manifests
    }
    if (
        "" in runtime_commits
        or len(runtime_commits) != 1
        or (
            expected_source_commit is not None
            and runtime_commits != {expected_source_commit}
        )
    ):
        return _coverage_failure("candidate_commit_mismatch")

    seen_members: set[str] = set()
    for manifest in manifests:
        if manifest.get("namespace") != candidate_baseline.get("namespace"):
            return _coverage_failure("namespace_mismatch")
        if set(manifest.get("case_ids", [])) != baseline_cases:
            return _coverage_failure("case_set_mismatch")
        members = set(manifest.get("execution_member_ids", []))
        if seen_members & members:
            return _coverage_failure("execution_member_overlap")
        seen_members.update(members)
    actual_projection = (len(seen_members), _sha256("\n".join(sorted(seen_members))))
    if actual_projection != expected_projection:
        return _coverage_failure("execution_member_set_mismatch")

    return {
        "status": "success",
        "reason": "exact_collection_coverage",
        "cells": sorted(manifest_cells),
        "case_count": len(baseline_cases),
        "execution_member_count": len(seen_members),
    }


def _duration_seconds(started_at: str, finished_at: str) -> float:
    try:
        started = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
        finished = datetime.fromisoformat(finished_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise AssuranceError("invalid evidence timestamp") from exc
    duration = (finished - started).total_seconds()
    if duration < 0:
        raise AssuranceError("evidence finish precedes start")
    return duration


def _cell_evidence_base(
    manifest: Mapping[str, object],
    *,
    cell: str,
    source_commit: str,
    started_at: str,
    finished_at: str,
) -> dict[str, object]:
    return {
        "schema_version": CELL_EVIDENCE_SCHEMA,
        "cell": cell,
        "source_commit": source_commit,
        "collection_manifest_digest": str(manifest.get("manifest_digest", "")),
        "collected_count": len(list(manifest.get("case_ids", []))),
        "executed_count": 0,
        "failures": 0,
        "errors": 0,
        "skipped": 0,
        "duplicate_testcases": [],
        "status": "failed",
        "reason": "unknown",
        "started_at": started_at,
        "finished_at": finished_at,
        "duration_seconds": _duration_seconds(started_at, finished_at),
    }


def build_cell_evidence(
    manifest: Mapping[str, object],
    junit_path: Path,
    *,
    cell: str,
    source_commit: str,
    started_at: str,
    finished_at: str,
) -> dict[str, object]:
    """把一个 cell 的 collection 与 JUnit 收敛为严格终态证据。"""
    try:
        evidence = _cell_evidence_base(
            manifest,
            cell=cell,
            source_commit=source_commit,
            started_at=started_at,
            finished_at=finished_at,
        )
    except AssuranceError:
        return {
            "schema_version": CELL_EVIDENCE_SCHEMA,
            "cell": cell,
            "source_commit": source_commit,
            "status": "failed",
            "reason": "timestamp_invalid",
            "duration_seconds": 0.0,
        }

    if source_commit != manifest.get("source_commit"):
        evidence["reason"] = "candidate_commit_mismatch"
        return evidence
    if cell not in list(manifest.get("cells", [])):
        evidence["reason"] = "cell_not_collected"
        return evidence
    if not junit_path.is_file():
        evidence["reason"] = "junit_missing"
        return evidence
    if junit_path.stat().st_size == 0:
        evidence["reason"] = "junit_empty"
        return evidence
    try:
        root = ET.parse(junit_path).getroot()
    except (ET.ParseError, OSError):
        evidence["reason"] = "junit_corrupt"
        return evidence
    if root.tag not in {"testsuite", "testsuites"}:
        evidence["reason"] = "junit_root_invalid"
        return evidence

    testcases = list(root.iter("testcase"))
    testcase_keys = [
        (
            testcase.get("file", ""),
            testcase.get("classname", ""),
            testcase.get("name", ""),
        )
        for testcase in testcases
    ]
    duplicates = sorted(
        "::".join(key)
        for key in set(testcase_keys)
        if testcase_keys.count(key) > 1
    )
    failures = sum(1 for testcase in testcases if testcase.find("failure") is not None)
    errors = sum(1 for testcase in testcases if testcase.find("error") is not None)
    skipped = sum(1 for testcase in testcases if testcase.find("skipped") is not None)
    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    declared_failures = sum(int(suite.get("failures", "0") or 0) for suite in suites)
    declared_errors = sum(int(suite.get("errors", "0") or 0) for suite in suites)
    declared_skipped = sum(int(suite.get("skipped", "0") or 0) for suite in suites)
    declared_tests = sum(int(suite.get("tests", "0") or 0) for suite in suites)

    evidence.update(
        {
            "executed_count": len(testcases),
            "failures": max(failures, declared_failures),
            "errors": max(errors, declared_errors),
            "skipped": max(skipped, declared_skipped),
            "duplicate_testcases": duplicates,
        }
    )
    if duplicates:
        evidence["reason"] = "duplicate_testcase"
        return evidence
    if any((evidence["failures"], evidence["errors"], evidence["skipped"])):
        evidence["reason"] = "non_success_terminal_state"
        return evidence
    if evidence["executed_count"] != evidence["collected_count"]:
        evidence["reason"] = "execution_count_mismatch"
        return evidence
    if declared_tests != evidence["executed_count"]:
        evidence["reason"] = "junit_declared_count_mismatch"
        return evidence

    evidence["status"] = "success"
    evidence["reason"] = "complete"
    return evidence


def _aggregate_failure(
    reason: str,
    *,
    candidate_commit: str,
    baseline_digest: str,
    runner_seconds: float,
    late_red: bool,
) -> dict[str, object]:
    return {
        "schema_version": AGGREGATE_SCHEMA,
        "status": "failed",
        "reason": reason,
        "candidate_commit": candidate_commit,
        "baseline_digest": baseline_digest,
        "runner_seconds": runner_seconds,
        "late_red": late_red,
    }


def aggregate_assurance(
    expected_cells: Sequence[str],
    evidence: Sequence[Mapping[str, object]],
    *,
    candidate_commit: str,
    baseline_digest: str,
    fast_gate_status: str,
) -> dict[str, object]:
    """验证 cell evidence 精确覆盖合同，并输出只读成本观察值。"""
    expected = list(expected_cells)
    if len(expected) != len(set(expected)) or any(not cell for cell in expected):
        return _aggregate_failure(
            "expected_cell_contract_invalid",
            candidate_commit=candidate_commit,
            baseline_digest=baseline_digest,
            runner_seconds=0.0,
            late_red=False,
        )
    cells = [str(item.get("cell", "")) for item in evidence]
    runner_seconds = sum(float(item.get("duration_seconds", 0.0)) for item in evidence)
    any_failed = any(item.get("status") != "success" for item in evidence)
    late_red = fast_gate_status == "success" and any_failed
    if len(cells) != len(set(cells)):
        return _aggregate_failure(
            "duplicate_cell_evidence",
            candidate_commit=candidate_commit,
            baseline_digest=baseline_digest,
            runner_seconds=runner_seconds,
            late_red=late_red,
        )
    if set(cells) != set(expected):
        return _aggregate_failure(
            "cell_set_mismatch",
            candidate_commit=candidate_commit,
            baseline_digest=baseline_digest,
            runner_seconds=runner_seconds,
            late_red=late_red,
        )
    if any(item.get("schema_version") != CELL_EVIDENCE_SCHEMA for item in evidence):
        return _aggregate_failure(
            "evidence_schema_invalid",
            candidate_commit=candidate_commit,
            baseline_digest=baseline_digest,
            runner_seconds=runner_seconds,
            late_red=late_red,
        )
    if any(item.get("source_commit") != candidate_commit for item in evidence):
        return _aggregate_failure(
            "candidate_commit_mismatch",
            candidate_commit=candidate_commit,
            baseline_digest=baseline_digest,
            runner_seconds=runner_seconds,
            late_red=late_red,
        )
    if any_failed:
        return _aggregate_failure(
            "non_success_cell",
            candidate_commit=candidate_commit,
            baseline_digest=baseline_digest,
            runner_seconds=runner_seconds,
            late_red=late_red,
        )
    report: dict[str, object] = {
        "schema_version": AGGREGATE_SCHEMA,
        "status": "success",
        "reason": "complete",
        "candidate_commit": candidate_commit,
        "baseline_digest": baseline_digest,
        "cells": sorted(cells),
        "runner_seconds": runner_seconds,
        "late_red": False,
    }
    report["report_digest"] = _canonical_digest(report, "report_digest")
    return report


def _collect_nodeids(root: Path, pytest_args: Sequence[str]) -> list[str]:
    command = [
        sys.executable,
        "-m",
        "pytest",
        "--collect-only",
        "-q",
        "--ignore=tests/e2e/stage_review",
        *pytest_args,
    ]
    completed = subprocess.run(
        command,
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if completed.returncode != 0:
        raise AssuranceError(
            f"pytest collection failed ({completed.returncode}): {completed.stderr.strip()}"
        )
    nodeids = [line.strip() for line in completed.stdout.splitlines() if "::" in line]
    if not nodeids:
        raise AssuranceError("pytest collection produced no test nodeids")
    return nodeids


def _git_commit(root: Path) -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if completed.returncode != 0:
        raise AssuranceError("cannot resolve source commit")
    return completed.stdout.strip()


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise AssuranceError(f"cannot read JSON artifact {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise AssuranceError(f"JSON artifact must be an object: {path}")
    return value


def _write_json(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    collect = subparsers.add_parser("collect")
    collect.add_argument("--root", type=Path, default=Path.cwd())
    collect.add_argument("--source-commit")
    collect.add_argument("--cell", action="append", required=True)
    collect.add_argument("--pytest-arg", action="append", default=[])
    collect.add_argument("--output", type=Path, required=True)

    baseline = subparsers.add_parser("baseline")
    baseline.add_argument("--root", type=Path, default=Path.cwd())
    baseline.add_argument("--source-commit")
    baseline.add_argument("--previous-baseline-digest")
    baseline.add_argument("--genesis-base-commit")
    baseline.add_argument("--pytest-arg", action="append", default=[])
    baseline.add_argument("--output", type=Path, required=True)

    transition = subparsers.add_parser("verify-transition")
    transition.add_argument("--trusted", type=Path, required=True)
    transition.add_argument("--candidate", type=Path, required=True)
    transition.add_argument("--lineage", type=Path, required=True)
    transition.add_argument("--output", type=Path)

    mode = subparsers.add_parser("decide-mode")
    mode.add_argument("--event-name", required=True)
    mode.add_argument("--pull-request-draft", default="false")
    mode.add_argument("--authority-available", default="false")
    mode.add_argument("--protected-paths-changed", default="false")
    mode.add_argument("--force-full", default="false")
    mode.add_argument("--output", type=Path)
    mode.add_argument("--github-output", type=Path)

    cell_evidence = subparsers.add_parser("cell-evidence")
    cell_evidence.add_argument("--manifest", type=Path, required=True)
    cell_evidence.add_argument("--junit", type=Path, required=True)
    cell_evidence.add_argument("--cell", required=True)
    cell_evidence.add_argument("--source-commit", required=True)
    cell_evidence.add_argument("--started-at", required=True)
    cell_evidence.add_argument("--finished-at", required=True)
    cell_evidence.add_argument("--output", type=Path, required=True)

    aggregate = subparsers.add_parser("aggregate")
    aggregate.add_argument("--evidence-root", type=Path, required=True)
    aggregate.add_argument("--candidate-baseline", type=Path, required=True)
    aggregate.add_argument("--expected-cell", action="append", required=True)
    aggregate.add_argument("--candidate-commit", required=True)
    aggregate.add_argument("--baseline-digest", required=True)
    aggregate.add_argument("--fast-gate-status", default="unknown")
    aggregate.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        if args.command in {"collect", "baseline"}:
            root = args.root.resolve()
            source_commit = args.source_commit or os.environ.get("GITHUB_SHA")
            source_commit = source_commit or _git_commit(root)
            nodeids = _collect_nodeids(root, args.pytest_arg)
            cells = args.cell if args.command == "collect" else DEFAULT_CELLS
            manifest = build_collection_manifest(nodeids, cells, source_commit)
            payload = (
                manifest
                if args.command == "collect"
                else build_baseline(
                    manifest,
                    previous_baseline_digest=args.previous_baseline_digest,
                    genesis_base_commit=args.genesis_base_commit,
                )
            )
            _write_json(args.output, payload)
            return 0

        if args.command == "decide-mode":
            result = decide_assurance_mode(
                event_name=args.event_name,
                pull_request_draft=_parse_bool(args.pull_request_draft),
                authority_available=_parse_bool(args.authority_available),
                protected_paths_changed=_parse_bool(args.protected_paths_changed),
                force_full=_parse_bool(args.force_full),
            )
            if args.output:
                _write_json(args.output, result)
            if args.github_output:
                full = str(result["full_assurance_required"]).lower()
                with args.github_output.open("a", encoding="utf-8") as output:
                    output.write(f"full_assurance_required={full}\n")
                    output.write(f"reason={result['reason']}\n")
            print(json.dumps(result, ensure_ascii=False, sort_keys=True))
            return 0

        if args.command == "verify-transition":
            result = verify_baseline_transition(
                _read_json(args.trusted),
                _read_json(args.candidate),
                _read_json(args.lineage),
            )
            if args.output:
                _write_json(args.output, result)
            print(json.dumps(result, ensure_ascii=False, sort_keys=True))
            return 0 if result["status"] == "success" else 1

        if args.command == "cell-evidence":
            result = build_cell_evidence(
                _read_json(args.manifest),
                args.junit,
                cell=args.cell,
                source_commit=args.source_commit,
                started_at=args.started_at,
                finished_at=args.finished_at,
            )
        else:
            evidence = [
                _read_json(path)
                for path in sorted(args.evidence_root.rglob("cell-evidence.json"))
            ]
            manifests = [
                _read_json(path)
                for path in sorted(
                    args.evidence_root.rglob("collection-manifest.json")
                )
            ]
            baseline = _read_json(args.candidate_baseline)
            coverage = verify_collection_coverage(
                baseline,
                manifests,
                expected_source_commit=args.candidate_commit,
            )
            result = (
                coverage
                if coverage["status"] != "success"
                else aggregate_assurance(
                    args.expected_cell,
                    evidence,
                    candidate_commit=args.candidate_commit,
                    baseline_digest=args.baseline_digest,
                    fast_gate_status=args.fast_gate_status,
                )
            )
        _write_json(args.output, result)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0 if result["status"] == "success" else 1
    except AssuranceError as exc:
        print(f"ci static assurance failed: {exc}", file=sys.stderr)
        return 1


def _parse_bool(raw: str) -> bool:
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes"}:
        return True
    if normalized in {"0", "false", "no", ""}:
        return False
    raise AssuranceError(f"invalid boolean value: {raw}")


if __name__ == "__main__":
    raise SystemExit(main())
