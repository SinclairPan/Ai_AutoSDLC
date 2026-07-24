from __future__ import annotations

import argparse
import json
import math
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

_PROBE_TEST_NAMES = {
    "canonical_plan_replay": (
        "test_canonical_executor_authorizes_and_replays_without_provider_recall",
    ),
    "certificate_integrity": (
        "test_ci_verifier_rejects_certificate_for_another_stage_purpose",
        "test_ci_verifier_rejects_protected_change_after_review",
        "test_ci_verifier_rejects_tampered_certificate_digest",
        "test_ci_verifier_rejects_tampered_authority_evidence",
    ),
    "provider_billing_integrity": (
        "test_provider_authorization_is_pending_until_exactly_once_settlement",
        "test_two_process_provider_authorization_cannot_exceed_final_budget",
    ),
    "crash_recovery": (
        "test_partial_advance_outage_is_labeled_as_closed_reconciliation",
        "test_committed_event_recovers_when_projection_write_crashes",
        "test_pending_budget_grant_recovers_before_unrelated_accounting",
    ),
    "hard_budget_integrity": (
        "test_enforce_budget_requires_every_finite_hard_limit",
        "test_usage_above_final_reservation_is_rejected_without_mutation",
    ),
    "clean_user_e2e": (
        "test_clean_user_shadow_close_needs_no_internal_review_commands",
        "test_clean_user_enforce_failure_is_actionable_and_does_not_close",
    ),
    "planner_latency": (
        "test_planner_returns_proposal_not_final_plan_before_reservation",
        "test_planner_supports_dynamic_required_slot_counts",
        "test_planner_uses_minimum_set_and_stable_tie_break",
    ),
    "work_item_fencing": (
        "test_lease_renewal_rotates_fencing_and_rejects_old_writer",
        "test_all_resource_mutations_reject_a_fencing_token_thief",
        "test_git_worktrees_share_project_state_root",
    ),
    "hard_constraint_integrity": (
        "test_unknown_or_underfunded_requirements_fail_without_approximation",
        "test_plan_reader_ignores_runtime_time_but_rejects_quorum_downgrade",
        "test_risk_profile_hard_capability_cannot_be_removed_by_model_copy",
    ),
    "non_waivable_integrity": (
        "test_waiver_requires_governance_and_cannot_override_non_waivable",
    ),
}


def _matches(name: str, expected: str) -> bool:
    return name == expected or name.startswith(f"{expected}[")


def _testcase_id(item: ET.Element) -> str:
    classname = item.attrib.get("classname", "")
    name = item.attrib.get("name", "")
    return f"{classname}::{name}" if classname else name


def _failed(item: ET.Element) -> bool:
    return any(item.find(tag) is not None for tag in ("failure", "error", "skipped"))


def _p95(values: tuple[float, ...]) -> float:
    if not values:
        raise ValueError("planner latency probe is unexercised")
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * 0.95) - 1)]


def _build_cell(
    junit: Path,
    *,
    runner_os: str,
    tested_commit: str,
) -> dict[str, Any]:
    if re.fullmatch(r"[0-9a-f]{40}", tested_commit) is None:
        raise ValueError("activation probe tested commit must be a full SHA")
    root = ET.parse(junit).getroot()
    testcases = tuple(root.iter("testcase"))
    metrics: dict[str, dict[str, Any]] = {}
    for metric, expected_names in _PROBE_TEST_NAMES.items():
        selected = tuple(
            item
            for item in testcases
            if any(_matches(item.attrib.get("name", ""), name) for name in expected_names)
        )
        if not selected:
            raise ValueError(f"activation probe metric is unexercised: {metric}")
        durations = tuple(float(item.attrib.get("time", "0")) for item in selected)
        metrics[metric] = {
            "trials": len(selected),
            "failures": sum(_failed(item) for item in selected),
            "p95_seconds": _p95(durations) if metric == "planner_latency" else 0.0,
            "testcase_ids": sorted(_testcase_id(item) for item in selected),
        }
    failures = sum(_failed(item) for item in testcases)
    return {
        "schema_version": "activation-probe-workflow-cell.v1",
        "tested_commit": tested_commit,
        "runner_os": runner_os,
        "tests": len(testcases),
        "failures": failures,
        "errors": sum(item.find("error") is not None for item in testcases),
        "skipped": sum(item.find("skipped") is not None for item in testcases),
        "metrics": metrics,
        "evidence_valid": bool(testcases)
        and failures == 0
        and all(metric["failures"] == 0 for metric in metrics.values()),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--junit", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--runner-os", required=True)
    parser.add_argument("--tested-commit", required=True)
    args = parser.parse_args()
    payload = _build_cell(
        args.junit,
        runner_os=args.runner_os,
        tested_commit=args.tested_commit,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
