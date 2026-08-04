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
    return _execution_member_projection_by_cell(
        {cell: set(case_ids) for cell in cells}
    )


def _execution_member_projection_by_cell(
    case_ids_by_cell: Mapping[str, set[str]],
) -> tuple[int, str]:
    members = sorted(
        _stable_execution_member_id(case_id, cell)
        for cell, case_ids in case_ids_by_cell.items()
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
    allowed_skip_case_ids_by_cell: Mapping[str, Sequence[str]] | None = None,
    cell_case_omissions_by_cell: Mapping[str, Sequence[str]] | None = None,
    cell_case_additions_by_cell: Mapping[str, Sequence[str]] | None = None,
) -> dict[str, object]:
    """把实际 collection 投影为受保护 baseline 候选。"""
    case_ids = _unique(
        [str(value) for value in manifest.get("case_ids", [])],
        label="baseline case identity",
    )
    cells = _unique(
        [str(value) for value in manifest.get("cells", [])],
        label="baseline execution cell",
    )
    omission_input = cell_case_omissions_by_cell or {}
    addition_input = cell_case_additions_by_cell or {}
    if not set(omission_input).issubset(cells) or not set(addition_input).issubset(
        cells
    ):
        raise AssuranceError("invalid cell case delta contract")
    omissions = {
        cell: sorted(
            str(case_id)
            for case_id in omission_input.get(cell, [])
        )
        for cell in cells
    }
    additions = {
        cell: sorted(
            str(case_id)
            for case_id in addition_input.get(cell, [])
        )
        for cell in cells
    }
    base_cases = set(case_ids)
    if any(
        len(omissions[cell]) != len(set(omissions[cell]))
        or len(additions[cell]) != len(set(additions[cell]))
        or not set(omissions[cell]).issubset(base_cases)
        or bool(set(additions[cell]) & base_cases)
        for cell in cells
    ):
        raise AssuranceError("invalid cell case delta contract")
    cell_cases = {
        cell: (base_cases - set(omissions[cell])) | set(additions[cell])
        for cell in cells
    }
    member_count, member_digest = _execution_member_projection_by_cell(cell_cases)
    allowed_input = allowed_skip_case_ids_by_cell or {}
    if not set(allowed_input).issubset(cells):
        raise AssuranceError("invalid allowed skip contract")
    allowed_skips = {
        cell: sorted(
            str(case_id)
            for case_id in allowed_input.get(cell, [])
        )
        for cell in cells
    }
    if any(
        len(case_ids_for_cell) != len(set(case_ids_for_cell))
        or not set(case_ids_for_cell).issubset(cell_cases[cell])
        for cell, case_ids_for_cell in allowed_skips.items()
    ):
        raise AssuranceError("invalid allowed skip contract")
    baseline: dict[str, object] = {
        "schema_version": BASELINE_SCHEMA,
        "namespace": manifest.get("namespace"),
        "source_commit": manifest.get("source_commit"),
        "genesis_base_commit": genesis_base_commit,
        "previous_baseline_digest": previous_baseline_digest,
        "collection_command": manifest.get("collection_command"),
        "cells": cells,
        "case_ids": case_ids,
        "cell_case_omissions_by_cell": omissions,
        "cell_case_additions_by_cell": additions,
        "execution_member_count": member_count,
        "execution_member_digest": member_digest,
        "allowed_skip_case_ids_by_cell": allowed_skips,
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


def _allowed_skip_contract(
    baseline: Mapping[str, object],
    case_ids_by_cell: Mapping[str, set[str]],
) -> dict[str, set[str]] | None:
    raw = baseline.get("allowed_skip_case_ids_by_cell")
    cells = set(case_ids_by_cell)
    if not isinstance(raw, Mapping) or set(raw) != cells:
        return None
    normalized: dict[str, set[str]] = {}
    for cell in cells:
        values = raw.get(cell)
        if not isinstance(values, list):
            return None
        skip_ids = {str(value) for value in values}
        if len(skip_ids) != len(values) or not skip_ids.issubset(
            case_ids_by_cell[cell]
        ):
            return None
        normalized[cell] = skip_ids
    return normalized


def _baseline_cell_cases(
    baseline: Mapping[str, object],
) -> dict[str, set[str]] | None:
    cells = list(baseline.get("cells", []))
    base_cases = list(baseline.get("case_ids", []))
    if (
        not cells
        or len(cells) != len(set(cells))
        or not base_cases
        or len(base_cases) != len(set(base_cases))
    ):
        return None
    raw_omissions = baseline.get("cell_case_omissions_by_cell")
    raw_additions = baseline.get("cell_case_additions_by_cell")
    if (
        not isinstance(raw_omissions, Mapping)
        or not isinstance(raw_additions, Mapping)
        or set(raw_omissions) != set(cells)
        or set(raw_additions) != set(cells)
    ):
        return None
    base_set = set(str(value) for value in base_cases)
    if any(not value for value in base_set):
        return None
    result: dict[str, set[str]] = {}
    for cell in cells:
        omissions = list(raw_omissions.get(cell, []))
        additions = list(raw_additions.get(cell, []))
        omission_set = {str(value) for value in omissions}
        addition_set = {str(value) for value in additions}
        if (
            len(omission_set) != len(omissions)
            or len(addition_set) != len(additions)
            or any(not value for value in omission_set | addition_set)
            or not omission_set.issubset(base_set)
            or bool(addition_set & base_set)
        ):
            return None
        result[cell] = (base_set - omission_set) | addition_set
    return result


def _protected_lineage_mapping(
    protected_lineage: Mapping[str, object],
) -> dict[str, str] | None:
    if protected_lineage.get("schema_version") != LINEAGE_SCHEMA:
        return None
    mappings = protected_lineage.get("mappings")
    if not isinstance(mappings, list) or any(
        not isinstance(item, Mapping) for item in mappings
    ):
        return None
    from_ids = [str(item.get("from_case_id", "")) for item in mappings]
    to_ids = [str(item.get("to_case_id", "")) for item in mappings]
    if (
        any(not value for value in from_ids + to_ids)
        or any(source == target for source, target in zip(from_ids, to_ids, strict=True))
        or len(from_ids) != len(set(from_ids))
        or len(to_ids) != len(set(to_ids))
    ):
        return None
    return dict(zip(from_ids, to_ids, strict=True))


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

    trusted_cell_cases = _baseline_cell_cases(trusted)
    candidate_cell_cases = _baseline_cell_cases(candidate)
    if trusted_cell_cases is None or candidate_cell_cases is None:
        return _transition_failure("cell_case_contract_invalid")
    trusted_projection = _execution_member_projection_by_cell(trusted_cell_cases)
    candidate_projection = _execution_member_projection_by_cell(candidate_cell_cases)
    if trusted_projection != (
        trusted.get("execution_member_count"),
        trusted.get("execution_member_digest"),
    ) or candidate_projection != (
        candidate.get("execution_member_count"),
        candidate.get("execution_member_digest"),
    ):
        return _transition_failure("execution_member_set_invalid")
    trusted_skips = _allowed_skip_contract(trusted, trusted_cell_cases)
    candidate_skips = _allowed_skip_contract(candidate, candidate_cell_cases)
    if trusted_skips is None or candidate_skips is None:
        return _transition_failure("allowed_skip_contract_invalid")
    if any(
        not candidate_skips[cell].issubset(trusted_skips[cell])
        for cell in trusted_cells
    ):
        return _transition_failure("allowed_skip_expanded")

    if protected_lineage.get("schema_version") != LINEAGE_SCHEMA:
        return _transition_failure("lineage_schema_invalid")
    lineage_by_source = _protected_lineage_mapping(protected_lineage)
    if lineage_by_source is None:
        return _transition_failure("lineage_not_one_to_one")
    # 单份 lineage 可覆盖多个 cell，但每个 cell 都必须独立证明成员守恒。
    all_missing = set().union(
        *(
            trusted_cell_cases[cell] - candidate_cell_cases[cell]
            for cell in trusted_cells
        )
    )
    all_added = set().union(
        *(
            candidate_cell_cases[cell] - trusted_cell_cases[cell]
            for cell in trusted_cells
        )
    )
    accepted_pairs: set[tuple[str, str]] = set()
    for cell in trusted_cells:
        trusted_set = trusted_cell_cases[cell]
        candidate_set = candidate_cell_cases[cell]
        missing = trusted_set - candidate_set
        added = candidate_set - trusted_set
        for source in sorted(missing):
            target = lineage_by_source.get(source)
            if not target or target not in added:
                return _transition_failure(
                    "unauthorized_negative_delta", removed_case_ids=all_missing
                )
            accepted_pairs.add((source, target))

    accepted_renames = [
        {"from_case_id": source, "to_case_id": target}
        for source, target in sorted(accepted_pairs)
    ]
    renamed_targets = {target for _, target in accepted_pairs}
    return {
        "status": "success",
        "reason": "monotonic_transition",
        "added_case_ids": sorted(all_added - renamed_targets),
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
    protected_lineage: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """证明每个 cell 都覆盖受保护成员，同时允许候选正向新增。"""
    baseline_cells = list(candidate_baseline.get("cells", []))
    baseline_cell_cases = _baseline_cell_cases(candidate_baseline)
    baseline_member_count = candidate_baseline.get("execution_member_count")
    baseline_member_digest = candidate_baseline.get("execution_member_digest")
    expected_projection = (
        _execution_member_projection_by_cell(baseline_cell_cases)
        if baseline_cell_cases is not None
        else None
    )
    allowed_skips = (
        _allowed_skip_contract(candidate_baseline, baseline_cell_cases)
        if baseline_cell_cases is not None
        else None
    )
    if (
        not baseline_cells
        or len(baseline_cells) != len(set(baseline_cells))
        or baseline_cell_cases is None
        or expected_projection != (baseline_member_count, baseline_member_digest)
        or allowed_skips is None
    ):
        return _coverage_failure("candidate_baseline_invalid")

    lineage = protected_lineage or {
        "schema_version": LINEAGE_SCHEMA,
        "mappings": [],
    }
    lineage_by_source = _protected_lineage_mapping(lineage)
    if lineage_by_source is None:
        return _coverage_failure("lineage_contract_invalid")

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
        cell = str(list(manifest.get("cells", []))[0])
        raw_cases = list(manifest.get("case_ids", []))
        runtime_cases = {str(value) for value in raw_cases}
        if not runtime_cases or len(runtime_cases) != len(raw_cases):
            return _coverage_failure("runtime_case_identity_invalid")
        protected_cases = baseline_cell_cases[cell]
        missing = protected_cases - runtime_cases
        for source in missing:
            target = lineage_by_source.get(source)
            if not target or target not in runtime_cases - protected_cases:
                return _coverage_failure("protected_case_missing")

        raw_members = list(manifest.get("execution_member_ids", []))
        members = {str(value) for value in raw_members}
        if len(members) != len(raw_members):
            return _coverage_failure("duplicate_execution_member")
        if seen_members & members:
            return _coverage_failure("execution_member_overlap")
        expected_members = {
            _stable_execution_member_id(case_id, cell)
            for case_id in runtime_cases
        }
        if members != expected_members:
            return _coverage_failure("execution_member_set_invalid")

        raw_nodeids = manifest.get("case_nodeids")
        if not isinstance(raw_nodeids, Mapping) or set(raw_nodeids) != runtime_cases:
            return _coverage_failure("runtime_case_identity_invalid")
        try:
            normalized_nodeids = {
                str(case_id): _normalize_nodeid(str(nodeid))
                for case_id, nodeid in raw_nodeids.items()
            }
        except AssuranceError:
            return _coverage_failure("runtime_case_identity_invalid")
        if any(
            _stable_case_id(nodeid) != case_id
            for case_id, nodeid in normalized_nodeids.items()
        ):
            return _coverage_failure("runtime_case_identity_invalid")
        if manifest.get("manifest_digest") != _canonical_digest(
            manifest, "manifest_digest"
        ):
            return _coverage_failure("manifest_digest_invalid")
        seen_members.update(members)

    return {
        "status": "success",
        "reason": "protected_collection_coverage",
        "cells": sorted(manifest_cells),
        "protected_case_count": len(
            set().union(*baseline_cell_cases.values())
        ),
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
        "skipped_case_ids": [],
        "duplicate_testcases": [],
        "status": "failed",
        "reason": "unknown",
        "started_at": started_at,
        "finished_at": finished_at,
        "duration_seconds": _duration_seconds(started_at, finished_at),
    }


def _junit_case_lookup(manifest: Mapping[str, object]) -> dict[tuple[str, str], str]:
    lookup: dict[tuple[str, str], str] = {}
    raw_nodeids = manifest.get("case_nodeids")
    if not isinstance(raw_nodeids, Mapping):
        raise AssuranceError("manifest case nodeids are missing")
    for raw_case_id, raw_nodeid in raw_nodeids.items():
        parts = _normalize_nodeid(str(raw_nodeid)).split("::")
        module_name = parts[0].removesuffix(".py").replace("/", ".")
        classname = ".".join([module_name, *parts[1:-1]])
        key = (classname, parts[-1])
        if key in lookup:
            raise AssuranceError("ambiguous JUnit case identity")
        lookup[key] = str(raw_case_id)
    return lookup


def build_cell_evidence(
    manifest: Mapping[str, object],
    junit_path: Path,
    *,
    cell: str,
    source_commit: str,
    started_at: str,
    finished_at: str,
    allowed_skip_case_ids: Sequence[str] = (),
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
            testcase.get("classname", ""),
            testcase.get("name", "").replace("\\", "/"),
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
    if any((evidence["failures"], evidence["errors"])):
        evidence["reason"] = "non_success_terminal_state"
        return evidence
    if evidence["executed_count"] != evidence["collected_count"]:
        evidence["reason"] = "execution_count_mismatch"
        return evidence
    if declared_tests != evidence["executed_count"]:
        evidence["reason"] = "junit_declared_count_mismatch"
        return evidence

    manifest_case_ids = set(str(value) for value in manifest.get("case_ids", []))
    allowed_skip_ids = {str(value) for value in allowed_skip_case_ids}
    if len(allowed_skip_ids) != len(allowed_skip_case_ids) or not allowed_skip_ids.issubset(
        manifest_case_ids
    ):
        evidence["reason"] = "allowed_skip_contract_invalid"
        return evidence
    if evidence["skipped"] and not allowed_skip_ids:
        evidence["reason"] = "non_success_terminal_state"
        return evidence
    try:
        lookup = _junit_case_lookup(manifest)
        executed_case_ids = [lookup[key] for key in testcase_keys]
    except (AssuranceError, KeyError):
        evidence["reason"] = "junit_case_identity_mismatch"
        return evidence
    if set(executed_case_ids) != manifest_case_ids:
        evidence["reason"] = "junit_case_set_mismatch"
        return evidence

    skipped_case_ids = sorted(
        lookup[key]
        for key, testcase in zip(testcase_keys, testcases, strict=True)
        if testcase.find("skipped") is not None
    )
    evidence["skipped_case_ids"] = skipped_case_ids
    if not set(skipped_case_ids).issubset(allowed_skip_ids):
        evidence["reason"] = "unexpected_skip_identity"
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
    baseline.add_argument("--allowed-skips", type=Path)
    baseline.add_argument("--cell-case-omissions", type=Path)
    baseline.add_argument("--cell-case-additions", type=Path)
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
    cell_evidence.add_argument("--baseline", type=Path, required=True)
    cell_evidence.add_argument("--junit", type=Path, required=True)
    cell_evidence.add_argument("--cell", required=True)
    cell_evidence.add_argument("--source-commit", required=True)
    cell_evidence.add_argument("--started-at", required=True)
    cell_evidence.add_argument("--finished-at", required=True)
    cell_evidence.add_argument("--output", type=Path, required=True)

    aggregate = subparsers.add_parser("aggregate")
    aggregate.add_argument("--evidence-root", type=Path, required=True)
    aggregate.add_argument("--candidate-baseline", type=Path, required=True)
    aggregate.add_argument("--lineage", type=Path, required=True)
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
                    allowed_skip_case_ids_by_cell=(
                        _read_json(args.allowed_skips) if args.allowed_skips else None
                    ),
                    cell_case_omissions_by_cell=(
                        _read_json(args.cell_case_omissions)
                        if args.cell_case_omissions
                        else None
                    ),
                    cell_case_additions_by_cell=(
                        _read_json(args.cell_case_additions)
                        if args.cell_case_additions
                        else None
                    ),
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
            baseline = _read_json(args.baseline)
            allowed_by_cell = baseline.get("allowed_skip_case_ids_by_cell", {})
            if not isinstance(allowed_by_cell, Mapping):
                raise AssuranceError("baseline allowed skip contract is invalid")
            result = build_cell_evidence(
                _read_json(args.manifest),
                args.junit,
                cell=args.cell,
                source_commit=args.source_commit,
                started_at=args.started_at,
                finished_at=args.finished_at,
                allowed_skip_case_ids=list(allowed_by_cell.get(args.cell, [])),
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
                protected_lineage=_read_json(args.lineage),
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
