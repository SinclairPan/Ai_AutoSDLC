"""Release smoke verdict transport and Receipt tag regressions."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[2]
_WORKFLOW_PATH = _REPO_ROOT / ".github" / "workflows" / "release-artifact-smoke.yml"


def test_receipt_writer_ignores_noncanonical_generation_tags(tmp_path: Path) -> None:
    """捕获非规范公开 tag 阻断受保护 Receipt writer。"""
    workflow = yaml.safe_load(_WORKFLOW_PATH.read_text(encoding="utf-8"))
    resolve_step = next(
        step
        for step in workflow["jobs"]["record-revocation"]["steps"]
        if step.get("name") == "Resolve Certificate and current Receipt generation"
    )
    marker = "uv run --project trusted-writer python - <<'PY'\n"
    parser_script = resolve_step["run"].split(marker, 1)[1].split("\nPY\n", 1)[0]

    receipt_input = tmp_path / "release-truth-input"
    receipt_input.mkdir()
    prefix = "release-truth/v9.9.9/revocation/g"
    malformed = ["mistyped", "0", "01", "１", "1" * 20]
    valid_tag = f"{prefix}1"
    pages = [
        [{"tag_name": f"{prefix}{suffix}"} for suffix in malformed]
        + [
            {
                "tag_name": valid_tag,
                "draft": False,
                "prerelease": True,
                "immutable": True,
                "assets": [{"name": "release-revocation-receipt.json"}],
            }
        ]
    ]
    (receipt_input / "release-pages.json").write_text(
        json.dumps(pages), encoding="utf-8"
    )
    env = os.environ.copy()
    env["RELEASE_TAG"] = "v9.9.9"
    env["PYTHONPATH"] = str(_REPO_ROOT / "src")

    completed = subprocess.run(
        [sys.executable, "-c", parser_script],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert json.loads(
        (receipt_input / "receipt-tags.json").read_text(encoding="utf-8")
    ) == [{"generation": 1, "tag": valid_tag}]


def test_posix_verdict_aggregator_recovers_failed_smoke_without_artifact(
    tmp_path: Path,
) -> None:
    """捕获 verdict artifact 传输失败后已证实 smoke 失败被静默丢失。"""
    artifact_root = tmp_path / "verdicts"
    artifact_root.mkdir()
    (artifact_root / "macos.txt").write_text("passed\n", encoding="utf-8")
    jobs_path = tmp_path / "jobs.json"
    jobs_path.write_text(
        json.dumps(
            [
                {
                    "jobs": [
                        {
                            "name": "linux tar.gz",
                            "steps": [
                                {
                                    "name": "Install release tar and run CLI smoke",
                                    "conclusion": "success",
                                },
                                {
                                    "name": "Record explicit release tar smoke verdict",
                                    "conclusion": "success",
                                },
                                {
                                    "name": "Enforce explicit release tar smoke failure",
                                    "conclusion": "failure",
                                },
                            ],
                        }
                    ]
                }
            ]
        ),
        encoding="utf-8",
    )
    output_path = tmp_path / "github-output.txt"

    completed = subprocess.run(
        [
            sys.executable,
            str(_REPO_ROOT / "scripts" / "aggregate_posix_smoke_verdicts.py"),
            "--artifact-root",
            str(artifact_root),
            "--jobs-json",
            str(jobs_path),
            "--github-output",
            str(output_path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert output_path.read_text(encoding="utf-8").splitlines() == [
        "macos_smoke_verdict=passed",
        "linux_smoke_verdict=failed",
    ]


def test_release_smoke_workflow_uses_protected_jobs_fallback() -> None:
    """捕获聚合器脚本存在但工作流仍只依赖 artifact 传输。"""
    workflow = yaml.safe_load(_WORKFLOW_PATH.read_text(encoding="utf-8"))
    job = workflow["jobs"]["posix-smoke-verdicts"]

    assert job["permissions"] == {"actions": "read", "contents": "read"}
    checkout = next(
        step for step in job["steps"] if step.get("uses") == "actions/checkout@v6"
    )
    assert checkout["with"] == {
        "ref": "${{ github.workflow_sha }}",
        "persist-credentials": False,
        "path": "trusted-verdict",
    }
    download = next(
        step
        for step in job["steps"]
        if step.get("uses") == "actions/download-artifact@v7"
    )
    assert download["continue-on-error"] is True
    jobs_query = next(
        step for step in job["steps"] if step.get("id") == "authoritative-jobs"
    )
    assert jobs_query["continue-on-error"] is True
    assert "/attempts/${GITHUB_RUN_ATTEMPT}/jobs?per_page=100" in jobs_query["run"]
    aggregate = next(step for step in job["steps"] if step.get("id") == "aggregate")
    assert aggregate["if"] == "always()"
    assert (
        "trusted-verdict/scripts/aggregate_posix_smoke_verdicts.py" in aggregate["run"]
    )
