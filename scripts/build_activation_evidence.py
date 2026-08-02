"""从受保护工作流的真实测试工件构建阶段门禁激活证据包。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

from ai_sdlc.core.stage_review.activation_models import (
    ActivationProbeEvidence,
    IsolationPlatformEvidence,
)
from ai_sdlc.core.stage_review.activation_source_models import (
    ActivationEvidencePackage,
)

_PLATFORMS = {"Linux": "linux", "macOS": "macos", "Windows": "windows"}
_EXPECTED_MODES = {
    "linux": ("ordinary-fail-closed", "required-enforced", "detected-only"),
    "macos": ("ordinary-fail-closed", "required-enforced", "detected-only"),
    "windows": ("ordinary-fail-closed", "required-unavailable", "detected-only"),
}
_MODES = {mode for modes in _EXPECTED_MODES.values() for mode in modes}
_DENIED_ACTIONS = {
    "candidate-read-only",
    "peer-output-denied",
    "real-home-denied",
    "network-denied",
}
_PROBE_METRICS = {
    "canonical_plan_replay",
    "certificate_integrity",
    "provider_billing_integrity",
    "crash_recovery",
    "hard_budget_integrity",
    "clean_user_e2e",
    "planner_latency",
    "work_item_fencing",
    "hard_constraint_integrity",
    "non_waivable_integrity",
}


def _digest(path: Path) -> str:
    return f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"


def _read_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"activation source must be a JSON object: {path}")
    return payload


def _is_clean_boundary(item: object) -> bool:
    if not isinstance(item, dict):
        return False
    return (
        item.get("expected") == "denied"
        and item.get("observed") == "denied"
        and item.get("blocked_before_side_effect") is True
        and item.get("before_digest") == item.get("after_digest")
    )


def _validate_isolation_sources(
    root: Path,
    tested_commit: str,
) -> tuple[tuple[IsolationPlatformEvidence, ...], tuple[Path, ...]]:
    cells, files = _load_isolation_cells(root, tested_commit)
    evidence = []
    for platform in ("linux", "macos"):
        payload, path = cells[(platform, "required-enforced")]
        boundaries = {
            str(item.get("action", "")): item
            for item in payload.get("boundary_results", [])
            if isinstance(item, dict)
        }
        if not _DENIED_ACTIONS.issubset(boundaries) or any(
            not _is_clean_boundary(boundaries[action]) for action in _DENIED_ACTIONS
        ):
            raise ValueError(f"activation isolation boundary is not enforced: {platform}")
        evidence.append(
            IsolationPlatformEvidence(
                platform_id=platform,
                isolation_level="enforced",
                candidate_write_blocked=True,
                sibling_write_blocked=True,
                home_write_blocked=True,
                network_blocked=True,
                evidence_digest=_digest(path),
            )
        )
    unavailable, unavailable_path = cells[("windows", "required-unavailable")]
    if "isolation.backend-unproven" not in unavailable.get(
        "isolation_receipt_reason_ids",
        [],
    ):
        raise ValueError("activation Windows isolation unavailability is unproven")
    evidence.append(
        IsolationPlatformEvidence(
            platform_id="windows",
            isolation_level="unproven",
            candidate_write_blocked=False,
            sibling_write_blocked=False,
            home_write_blocked=False,
            network_blocked=False,
            provider_command_blocked=True,
            evidence_digest=_digest(unavailable_path),
        )
    )
    return tuple(evidence), files


def _load_isolation_cells(
    root: Path,
    tested_commit: str,
) -> tuple[dict[tuple[str, str], tuple[dict[str, Any], Path]], tuple[Path, ...]]:
    files = tuple(sorted(root.rglob("reviewer-isolation-evidence.json")))
    expected = {
        (platform, mode)
        for platform, modes in _EXPECTED_MODES.items()
        for mode in modes
    }
    if len(files) != len(expected):
        raise ValueError(
            "activation evidence requires the complete platform isolation matrix"
        )
    cells: dict[tuple[str, str], tuple[dict[str, Any], Path]] = {}
    for path in files:
        payload = _read_object(path)
        platform = _PLATFORMS.get(str(payload.get("runner_os", "")))
        mode = str(payload.get("mode", ""))
        key = (platform or "", mode)
        if (
            platform is None
            or mode not in _MODES
            or key in cells
            or payload.get("artifact_kind") != "reviewer-isolation-ci-evidence"
            or payload.get("tested_commit") != tested_commit
            or payload.get("evidence_valid") is not True
            or payload.get("validation_errors") not in ([], ())
        ):
            raise ValueError(f"activation isolation source is invalid: {path}")
        cells[key] = (payload, path)
    if set(cells) != expected:
        raise ValueError("activation isolation matrix identity is incomplete")
    return cells, files


def _validate_probe_sources(
    root: Path,
    tested_commit: str,
) -> tuple[tuple[Path, ...], ActivationProbeEvidence]:
    files = tuple(sorted(root.rglob("activation-probe-evidence.json")))
    if len(files) != len(_PLATFORMS):
        raise ValueError("activation evidence requires three probe platform cells")
    platforms = set()
    cells: list[dict[str, Any]] = []
    for path in files:
        payload = _read_object(path)
        platform = _PLATFORMS.get(str(payload.get("runner_os", "")))
        metrics = payload.get("metrics")
        if (
            platform is None
            or platform in platforms
            or payload.get("tested_commit") != tested_commit
            or payload.get("evidence_valid") is not True
            or int(payload.get("tests", 0)) < 1
            or any(int(payload.get(key, -1)) != 0 for key in ("failures", "errors", "skipped"))
        ):
            raise ValueError(f"activation probe source is invalid: {path}")
        if not isinstance(metrics, dict) or set(metrics) != _PROBE_METRICS:
            raise ValueError("activation probe metrics are incomplete")
        for metric, value in metrics.items():
            if (
                not isinstance(value, dict)
                or int(value.get("trials", 0)) < 1
                or int(value.get("failures", -1)) < 0
                or int(value.get("failures", -1)) > int(value.get("trials", 0))
                or not isinstance(value.get("testcase_ids"), list)
                or not value["testcase_ids"]
                or (
                    metric == "planner_latency"
                    and float(value.get("p95_seconds", -1)) < 0
                )
            ):
                raise ValueError(f"activation probe metric is invalid: {metric}")
        platforms.add(platform)
        cells.append(payload)
    if platforms != set(_PLATFORMS.values()):
        raise ValueError("activation probe platform identity is incomplete")
    return files, _aggregate_probes(cells)


def _metric_totals(
    cells: list[dict[str, Any]],
    metric: str,
) -> tuple[int, int]:
    values = [payload["metrics"][metric] for payload in cells]
    return (
        sum(int(value["trials"]) for value in values),
        sum(int(value["failures"]) for value in values),
    )


def _probe_passed(cells: list[dict[str, Any]], metric: str) -> bool:
    _trials, failures = _metric_totals(cells, metric)
    return failures == 0


def _aggregate_probes(
    cells: list[dict[str, Any]],
) -> ActivationProbeEvidence:
    return ActivationProbeEvidence(
        canonical_plan_replay_passed=_probe_passed(
            cells, "canonical_plan_replay"
        ),
        certificate_integrity_passed=_probe_passed(
            cells, "certificate_integrity"
        ),
        provider_billing_integrity_passed=_probe_passed(
            cells, "provider_billing_integrity"
        ),
        crash_recovery_passed=_probe_passed(cells, "crash_recovery"),
        hard_budget_integrity_passed=_probe_passed(
            cells, "hard_budget_integrity"
        ),
        clean_user_e2e_passed=_probe_passed(cells, "clean_user_e2e"),
        planner_benchmark_p95_seconds=max(
            float(payload["metrics"]["planner_latency"]["p95_seconds"])
            for payload in cells
        ),
        work_item_fencing_passed=_probe_passed(cells, "work_item_fencing"),
        hard_constraint_integrity_passed=_probe_passed(
            cells,
            "hard_constraint_integrity",
        ),
        non_waivable_integrity_passed=_probe_passed(
            cells,
            "non_waivable_integrity",
        ),
        platform_count=len(cells),
        probe_trial_count=sum(
            _metric_totals(cells, metric)[0] for metric in _PROBE_METRICS
        ),
    )


def _build_package(args: argparse.Namespace) -> ActivationEvidencePackage:
    purpose = os.environ.get("AI_SDLC_ACTIVATION_EVIDENCE_PURPOSE", "")
    predicate = os.environ.get("AI_SDLC_ACTIVATION_PREDICATE_TYPE", "")
    if purpose != "stage-gate-activation":
        raise ValueError("activation evidence purpose is not the protected value")
    if predicate != "https://slsa.dev/provenance/v1":
        raise ValueError("activation evidence predicate type is not protected")
    if re.fullmatch(r"[0-9a-f]{40}", args.tested_commit) is None:
        raise ValueError("activation tested commit must be a full SHA")
    isolation, isolation_files = _validate_isolation_sources(
        args.evidence_root,
        args.tested_commit,
    )
    probe_files, probes = _validate_probe_sources(
        args.evidence_root,
        args.tested_commit,
    )
    sources = tuple(sorted((*isolation_files, *probe_files)))
    return ActivationEvidencePackage(
        project_id=args.project_id,
        repository=args.repository,
        tested_commit=args.tested_commit,
        signer_workflow=(
            f"{args.repository}/.github/workflows/activation-evidence.yml"
        ),
        evidence_purpose=purpose,
        isolation_matrix=isolation,
        probes=probes,
        source_artifact_digests=tuple(_digest(path) for path in sources),
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--tested-commit", required=True)
    parser.add_argument("--project-id", required=True)
    args = parser.parse_args()
    package = _build_package(args)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(package.model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
