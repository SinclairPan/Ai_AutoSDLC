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
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

CASE_NAMESPACE = "pytest-nodeid-v1"
MANIFEST_SCHEMA = "ci-test-manifest-v1"
BASELINE_SCHEMA = "ci-baseline-v1"
LINEAGE_SCHEMA = "ci-test-lineage-v1"
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
) -> dict[str, object]:
    """把实际 collection 投影为受保护 baseline 候选。"""
    baseline: dict[str, object] = {
        "schema_version": BASELINE_SCHEMA,
        "namespace": manifest.get("namespace"),
        "source_commit": manifest.get("source_commit"),
        "previous_baseline_digest": previous_baseline_digest,
        "collection_command": manifest.get("collection_command"),
        "cells": list(manifest.get("cells", [])),
        "case_ids": list(manifest.get("case_ids", [])),
        "case_nodeids": dict(manifest.get("case_nodeids", {})),
        "execution_member_ids": list(manifest.get("execution_member_ids", [])),
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
    trusted_members = list(trusted.get("execution_member_ids", []))
    candidate_members = list(candidate.get("execution_member_ids", []))
    if (
        len(trusted_members) != len(set(trusted_members))
        or len(candidate_members) != len(set(candidate_members))
        or len(trusted_members) != len(trusted_cases) * len(trusted_cells)
        or len(candidate_members) != len(candidate_cases) * len(candidate_cells)
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
    baseline.add_argument("--pytest-arg", action="append", default=[])
    baseline.add_argument("--output", type=Path, required=True)

    transition = subparsers.add_parser("verify-transition")
    transition.add_argument("--trusted", type=Path, required=True)
    transition.add_argument("--candidate", type=Path, required=True)
    transition.add_argument("--lineage", type=Path, required=True)
    transition.add_argument("--output", type=Path)
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
                )
            )
            _write_json(args.output, payload)
            return 0

        result = verify_baseline_transition(
            _read_json(args.trusted),
            _read_json(args.candidate),
            _read_json(args.lineage),
        )
        if args.output:
            _write_json(args.output, result)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0 if result["status"] == "success" else 1
    except AssuranceError as exc:
        print(f"ci static assurance failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

