from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path

import pytest


def _load_builder():
    path = Path(__file__).resolve().parents[2] / "scripts/build_activation_evidence.py"
    spec = importlib.util.spec_from_file_location("build_activation_evidence", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _boundary(action: str) -> dict[str, object]:
    return {
        "action": action,
        "expected": "denied",
        "observed": "denied",
        "blocked_before_side_effect": True,
        "before_digest": "before",
        "after_digest": "before",
    }


def _write_complete_sources(root: Path, commit: str) -> None:
    runner_names = {"linux": "Linux", "macos": "macOS", "windows": "Windows"}
    actions = (
        "candidate-read-only",
        "peer-output-denied",
        "real-home-denied",
        "network-denied",
    )
    for platform, runner in runner_names.items():
        modes = (
            ("ordinary-fail-closed", "required-unavailable", "detected-only")
            if platform == "windows"
            else ("ordinary-fail-closed", "required-enforced", "detected-only")
        )
        for mode in modes:
            _write_json(
                root / "isolation" / platform / mode / "reviewer-isolation-evidence.json",
                {
                    "artifact_kind": "reviewer-isolation-ci-evidence",
                    "runner_os": runner,
                    "mode": mode,
                    "tested_commit": commit,
                    "evidence_valid": True,
                    "validation_errors": [],
                    "boundary_results": [_boundary(action) for action in actions],
                    "isolation_receipt_reason_ids": (
                        ["isolation.backend-unproven"]
                        if mode == "required-unavailable"
                        else []
                    ),
                },
            )
        _write_json(
            root / "probes" / platform / "activation-probe-evidence.json",
            {
                "runner_os": runner,
                "tested_commit": commit,
                "tests": 12,
                "failures": 0,
                "errors": 0,
                "skipped": 0,
                "metrics": {
                    metric: {
                        "trials": 4,
                        "failures": 0,
                        "p95_seconds": 0.25 if metric == "planner_latency" else 0.0,
                        "testcase_ids": [f"tests.activation::{metric}"],
                    }
                    for metric in (
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
                    )
                },
                "evidence_valid": True,
            },
        )


def test_builder_requires_complete_real_matrix(tmp_path: Path) -> None:
    builder = _load_builder()

    with pytest.raises(ValueError, match="complete platform isolation matrix"):
        builder._validate_isolation_sources(tmp_path, "a" * 40)


def test_builder_binds_package_to_protected_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    builder = _load_builder()
    commit = "a" * 40
    _write_complete_sources(tmp_path, commit)
    monkeypatch.setenv(
        "AI_SDLC_ACTIVATION_EVIDENCE_PURPOSE",
        "stage-gate-activation",
    )
    monkeypatch.setenv(
        "AI_SDLC_ACTIVATION_PREDICATE_TYPE",
        "https://slsa.dev/provenance/v1",
    )

    package = builder._build_package(
        argparse.Namespace(
            evidence_root=tmp_path,
            repository="SinclairPan/Ai_AutoSDLC",
            tested_commit=commit,
            project_id="project.activation-evidence",
        )
    )

    assert package.signer_workflow == (
        "SinclairPan/Ai_AutoSDLC/.github/workflows/activation-evidence.yml"
    )
    assert package.evidence_purpose == "stage-gate-activation"
    assert {item.platform_id for item in package.isolation_matrix} == {
        "linux",
        "macos",
        "windows",
    }
    windows = next(
        item for item in package.isolation_matrix if item.platform_id == "windows"
    )
    assert windows.isolation_level == "unproven"
    assert windows.provider_command_blocked is True
    assert package.probes.canonical_plan_replay_passed is True
    assert package.probes.planner_benchmark_p95_seconds == 0.25
    assert package.probes.platform_count == 3
    assert package.probes.probe_trial_count == 120
    assert len(package.source_artifact_digests) == 12


def test_builder_rejects_quality_cell_without_measured_metrics(
    tmp_path: Path,
) -> None:
    builder = _load_builder()
    commit = "a" * 40
    _write_complete_sources(tmp_path, commit)
    path = next(tmp_path.rglob("activation-probe-evidence.json"))
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["metrics"].pop("certificate_integrity")
    _write_json(path, payload)

    with pytest.raises(ValueError, match="probe metrics are incomplete"):
        builder._validate_probe_sources(tmp_path, commit)


def test_builder_rejects_candidate_selected_purpose(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    builder = _load_builder()
    commit = "a" * 40
    _write_complete_sources(tmp_path, commit)
    monkeypatch.setenv("AI_SDLC_ACTIVATION_EVIDENCE_PURPOSE", "candidate-selected")
    monkeypatch.setenv(
        "AI_SDLC_ACTIVATION_PREDICATE_TYPE",
        "https://slsa.dev/provenance/v1",
    )

    with pytest.raises(ValueError, match="purpose is not the protected value"):
        builder._build_package(
            argparse.Namespace(
                evidence_root=tmp_path,
                repository="SinclairPan/Ai_AutoSDLC",
                tested_commit=commit,
                project_id="project.activation-evidence",
            )
        )
