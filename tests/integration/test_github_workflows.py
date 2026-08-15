"""Regression checks for repository GitHub Actions workflows."""

from __future__ import annotations

import json
import os
import re
import runpy
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[2]
_WORKFLOWS_DIR = _REPO_ROOT / ".github" / "workflows"


def test_github_workflows_are_valid_yaml() -> None:
    workflow_paths = sorted(_WORKFLOWS_DIR.glob("*.yml"))

    assert workflow_paths

    for workflow_path in workflow_paths:
        yaml.safe_load(workflow_path.read_text(encoding="utf-8"))

    pr_checks = (_WORKFLOWS_DIR / "pr-checks.yml").read_text(encoding="utf-8")
    required = (
        "fetch-depth: 0",
        "persist-credentials: false",
        "git branch --force main HEAD^1",
        'git switch --create "$GITHUB_HEAD_REF" HEAD^2',
    )
    assert all(token in pr_checks for token in required) and pr_checks.index(
        "Pytest smoke"
    ) < pr_checks.index(required[2]) < pr_checks.index(required[3]) < pr_checks.index(
        "uv run ai-sdlc verify constraints"
    )


def test_cross_platform_core_runs_minimal_review_smoke_on_three_platforms() -> None:
    workflow_path = _WORKFLOWS_DIR / "cross-platform-core.yml"
    workflow = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))

    matrix = workflow["jobs"]["core-smoke"]["strategy"]["matrix"]
    assert matrix["os"] == ["ubuntu-latest", "macos-latest", "windows-latest"]
    smoke = workflow_path.read_text(encoding="utf-8")
    assert "tests/unit/test_review_kernel.py" in smoke
    assert "tests/unit/test_slimming_advice.py" in smoke
    assert "stage_review" not in smoke


def test_windows_offline_smoke_workflow_covers_bundle_build_install_and_cli_checks() -> (
    None
):
    workflow_path = _WORKFLOWS_DIR / "windows-offline-smoke.yml"

    assert workflow_path.is_file()

    workflow = workflow_path.read_text(encoding="utf-8")

    assert "workflow_dispatch:" in workflow
    assert "pull_request:" in workflow
    assert "windows-latest" in workflow
    assert "astral-sh/setup-uv@v7" in workflow
    assert "uv python install 3.11" in workflow
    assert "uv python find --managed-python 3.11" in workflow
    assert "AI_SDLC_OFFLINE_PYTHON_RUNTIME" in workflow
    assert 'AI_SDLC_OFFLINE_PYTHON_VERSIONS="3.11,3.12"' in workflow
    assert 'AI_SDLC_OFFLINE_TARGET_PLATFORM="win_amd64"' in workflow
    assert "build_offline_bundle.sh" in workflow
    assert "install_offline.ps1" in workflow
    assert "old-user-upgrade:" not in workflow
    assert "git+https://" not in workflow
    assert "ai-sdlc init . --agent-target codex --shell powershell" in workflow
    assert "当前结果 / Result" in workflow
    assert "下一步 / Next" in workflow
    assert "OPENAI_CODEX" in workflow
    assert "AI_SDLC_ADAPTER_CANONICAL_SHA256" in workflow
    assert "adapter status" in workflow
    assert "run --dry-run" in workflow
    assert "actions/upload-artifact@v7" in workflow
    assert "PYTHONUTF8" in workflow
    assert "PYTHONIOENCODING" in workflow
    assert "Console]::OutputEncoding" in workflow
    assert "UTF8Encoding" in workflow
    assert "verify_offline_bundle.py" in workflow
    assert "--require-bundled-runtime" in workflow
    assert "--install-log" in workflow
    assert "WindowsPowerShell\\v1.0\\powershell.exe" in workflow
    assert (
        "-NoProfile -ExecutionPolicy Bypass -File .\\install_offline.ps1 -AddToPath"
        in workflow
    )
    assert '$cliDir = Join-Path $bundleDir.FullName ".venv\\Scripts"' in workflow
    assert "$env:Path = $cliDir + [IO.Path]::PathSeparator + $env:Path" in workflow
    assert "Get-Command ai-sdlc" in workflow
    assert "ai-sdlc --help" in workflow
    assert "Existing Artifact Probe" in workflow
    assert "recover --reconcile" in workflow


def test_posix_offline_smoke_workflow_covers_macos_linux_bundle_install_and_cli_checks() -> (
    None
):
    workflow_path = _WORKFLOWS_DIR / "posix-offline-smoke.yml"

    assert workflow_path.is_file()

    workflow = workflow_path.read_text(encoding="utf-8")

    assert "workflow_dispatch:" in workflow
    assert "pull_request:" in workflow
    assert "macos-latest" in workflow
    assert "ubuntu-latest" in workflow
    assert "astral-sh/setup-uv@v7" in workflow
    assert "uv python install 3.11" in workflow
    assert "uv python find --managed-python 3.11" in workflow
    assert "build_offline_bundle.sh" in workflow
    assert "install_offline.sh" in workflow
    assert "install_offline.sh --add-to-path" in workflow
    assert "command -v ai-sdlc" in workflow
    assert "ai-sdlc --help" in workflow
    assert "OPENAI_CODEX" in workflow
    assert "AI_SDLC_ADAPTER_CANONICAL_SHA256" in workflow
    assert "adapter status" in workflow
    assert "run --dry-run" in workflow
    assert "posix-offline-smoke-evidence" in workflow
    assert "install.log" in workflow
    assert "help.txt" in workflow
    assert "adapter-status.txt" in workflow
    assert "run-dry-run.txt" in workflow
    assert "bundle-manifest.json" in workflow
    assert "upload-artifact" in workflow
    assert "PYTHONUTF8" in workflow
    assert "PYTHONIOENCODING" in workflow
    assert "verify_offline_bundle.py" in workflow
    assert "--require-bundled-runtime" in workflow
    assert "--install-log" in workflow


def test_loop_e2e_release_gate_covers_browser_probe_runner_changes() -> None:
    workflow_path = _WORKFLOWS_DIR / "loop-e2e-release-gate.yml"

    assert workflow_path.is_file()

    workflow = workflow_path.read_text(encoding="utf-8")

    assert "scripts/loop_e2e_release_gate.py" in workflow
    assert "scripts/frontend_browser_gate_probe_runner.mjs" in workflow


def test_release_artifact_smoke_workflow_installs_published_assets() -> None:
    workflow_path = _WORKFLOWS_DIR / "release-artifact-smoke.yml"

    assert workflow_path.is_file()

    workflow = workflow_path.read_text(encoding="utf-8")

    assert "workflow_dispatch:" in workflow
    assert "release:" in workflow
    assert "default: v1.0.5" in workflow
    assert "gh release download" in workflow
    assert "windows-latest" in workflow
    assert "macos-latest" in workflow
    assert "ubuntu-latest" in workflow
    assert "ref: ${{ github.event.release.tag_name || inputs.tag }}" in workflow
    assert (
        "ai-sdlc-offline-$releaseVersion-windows-$env:RELEASE_ASSET_MACHINE.zip"
        in workflow
    )
    assert (
        "ai-sdlc-offline-${release_version}-${RELEASE_ASSET_OS}-${RELEASE_ASSET_MACHINE}.tar.gz"
        in workflow
    )
    assert "RELEASE_ASSET_OS" in workflow
    assert "RELEASE_ASSET_MACHINE" in workflow
    assert ".sha256" in workflow
    assert "install_offline.ps1" in workflow
    assert "./install_offline.sh" in workflow
    assert "actions/setup-python@v6" in workflow
    assert "verify_offline_bundle.py" in workflow
    assert "--require-bundled-runtime" in workflow
    assert "--require-checksums" in workflow
    assert "--expected-package-version" in workflow
    assert "--archive-checksum" in workflow
    assert "--install-log" in workflow
    assert "verify_offline_bundle.py failed with exit code" in workflow
    assert "adapter status" in workflow
    assert "run --dry-run" in workflow
    assert "actions/upload-artifact@v7" in workflow
    assert "WindowsPowerShell\\v1.0\\powershell.exe" in workflow
    assert (
        "-NoProfile -ExecutionPolicy Bypass -File .\\install_offline.ps1 -AddToPath"
        in workflow
    )
    assert '$cliDir = Join-Path $bundleDir.FullName ".venv\\Scripts"' in workflow
    assert "$env:Path = $cliDir + [IO.Path]::PathSeparator + $env:Path" in workflow
    assert "Get-Command ai-sdlc" in workflow
    assert "ai-sdlc --help" in workflow
    assert "ai-sdlc --version" in workflow
    assert "install_offline.sh --add-to-path" in workflow
    assert "command -v ai-sdlc" in workflow
    windows_hash = workflow.index(
        "Get-FileHash -Algorithm SHA256 -LiteralPath $archive.FullName"
    )
    windows_extract = workflow.index("Expand-Archive -LiteralPath $archive.FullName")
    windows_verify = workflow.index("--require-checksums", windows_extract)
    windows_install = workflow.index("install_offline.ps1 -AddToPath", windows_extract)
    assert windows_hash < windows_extract < windows_verify < windows_install
    posix_hash = workflow.index('actual_archive_hash="$(')
    posix_extract = workflow.index('tar xzf "${archive}"')
    posix_verify = workflow.index("--require-checksums", posix_extract)
    posix_install = workflow.index("./install_offline.sh --add-to-path", posix_extract)
    assert posix_hash < posix_extract < posix_verify < posix_install


def test_release_artifact_smoke_records_receipt_before_incident_projection() -> None:
    """捕获后验失败先投影事故、后写 Receipt 或出现第二条撤销状态。"""
    workflow_path = _WORKFLOWS_DIR / "release-artifact-smoke.yml"
    workflow_text = workflow_path.read_text(encoding="utf-8")
    workflow = yaml.safe_load(workflow_text)
    revocation_job = workflow["jobs"]["record-revocation"]
    assert revocation_job["permissions"] == {
        "contents": "read",
    }
    assert revocation_job["if"].startswith("false &&")
    attestation_steps = [
        step
        for step in revocation_job["steps"]
        if step.get("uses", "").startswith("actions/attest@")
    ]
    assert len(attestation_steps) == 1
    assert attestation_steps[0]["with"]["subject-path"] == (
        "release-revocation-receipt.json"
    )
    revocation_step_names = [
        step.get("name") for step in revocation_job["steps"]
    ]
    assert revocation_step_names.index(
        "Attest Receipt generation before publication"
    ) < revocation_step_names.index("Append immutable Receipt generation")
    jobs = workflow["jobs"]

    assert workflow["permissions"] == {"contents": "read"}
    writer_jobs = [
        job_id
        for job_id, job in jobs.items()
        if job.get("permissions", {}).get("contents") == "write"
    ]
    assert writer_jobs == []
    assert (
        "startsWith(github.event.release.tag_name || inputs.tag, 'v')"
        in jobs["windows-zip"]["if"]
    )
    assert (
        "startsWith(github.event.release.tag_name || inputs.tag, 'v')"
        in jobs["posix-tar"]["if"]
    )
    writer = jobs["record-revocation"]
    assert writer["timeout-minutes"] >= 30
    assert writer["needs"] == ["windows-zip", "posix-smoke-verdicts"]
    assert writer["environment"] == "release-publish"
    assert writer["permissions"] == {
        "contents": "read",
    }
    assert writer["env"]["GH_REPO"] == "${{ github.repository }}"
    assert writer["concurrency"] == {
        "group": "release-revocation-${{ github.event.release.tag_name || inputs.tag }}",
        "cancel-in-progress": False,
    }
    assert jobs["windows-zip"]["outputs"] == {
        "smoke-verdict": "${{ steps.smoke-verdict.outputs.smoke_verdict }}"
    }
    assert "outputs" not in jobs["posix-tar"]
    posix_verdicts = jobs["posix-smoke-verdicts"]
    assert posix_verdicts["needs"] == "posix-tar"
    assert posix_verdicts["if"] == (
        "always() && "
        "startsWith(github.event.release.tag_name || inputs.tag, 'v')"
    )
    assert posix_verdicts["outputs"] == {
        "linux-smoke-verdict": (
            "${{ steps.aggregate.outputs.linux_smoke_verdict }}"
        ),
        "macos-smoke-verdict": (
            "${{ steps.aggregate.outputs.macos_smoke_verdict }}"
        ),
    }
    windows_smoke = next(
        step
        for step in jobs["windows-zip"]["steps"]
        if step.get("name") == "Install release zip and run CLI smoke"
    )
    posix_smoke = next(
        step
        for step in jobs["posix-tar"]["steps"]
        if step.get("name") == "Install release tar and run CLI smoke"
    )
    assert windows_smoke["id"] == "smoke"
    assert windows_smoke["continue-on-error"] is True
    assert posix_smoke["id"] == "smoke"
    assert posix_smoke["continue-on-error"] is True
    assert "Copy-Item" not in windows_smoke["run"]
    assert 'cp "${bundle_dir}/bundle-manifest.json"' not in posix_smoke["run"]
    windows_collect = next(
        step
        for step in jobs["windows-zip"]["steps"]
        if step.get("name") == "Collect release zip smoke evidence"
    )
    posix_collect = next(
        step
        for step in jobs["posix-tar"]["steps"]
        if step.get("name") == "Collect release tar smoke evidence"
    )
    assert windows_collect["if"] == "always()"
    assert posix_collect["if"] == "always()"
    windows_verdict = next(
        step
        for step in jobs["windows-zip"]["steps"]
        if step.get("id") == "smoke-verdict"
    )
    posix_verdict = next(
        step
        for step in jobs["posix-tar"]["steps"]
        if step.get("id") == "smoke-verdict"
    )
    assert windows_verdict["if"] == "always()"
    assert posix_verdict["if"] == "always()"
    posix_verdict_upload = next(
        step
        for step in jobs["posix-tar"]["steps"]
        if step.get("name") == "Upload independent release tar smoke verdict"
    )
    assert posix_verdict_upload["if"] == "always()"
    assert posix_verdict_upload["with"]["name"] == (
        "release-${{ matrix.asset_os }}-tar-smoke-verdict"
    )
    posix_step_names = [
        step.get("name") for step in jobs["posix-tar"]["steps"]
    ]
    assert posix_step_names.index(
        "Enforce explicit release tar smoke failure"
    ) < posix_step_names.index("Upload independent release tar smoke verdict")
    verdict_download = next(
        step
        for step in posix_verdicts["steps"]
        if step.get("uses") == "actions/download-artifact@v7"
    )
    assert verdict_download["with"] == {
        "pattern": "release-*-tar-smoke-verdict",
        "path": "posix-smoke-verdicts",
        "merge-multiple": True,
    }
    assert "github.event_name == 'release'" in writer["if"]
    assert "needs.windows-zip.outputs.smoke-verdict == 'failed'" in writer["if"]
    assert (
        "needs.posix-smoke-verdicts.outputs.macos-smoke-verdict == 'failed'"
        in writer["if"]
    )
    assert (
        "needs.posix-smoke-verdicts.outputs.linux-smoke-verdict == 'failed'"
        in writer["if"]
    )
    assert "needs.windows-zip.result" not in writer["if"]
    assert "needs.posix-tar.result" not in writer["if"]
    assert writer["env"]["WINDOWS_SMOKE_VERDICT"] == (
        "${{ needs.windows-zip.outputs.smoke-verdict }}"
    )
    assert writer["env"]["MACOS_SMOKE_VERDICT"] == (
        "${{ needs.posix-smoke-verdicts.outputs.macos-smoke-verdict }}"
    )
    assert writer["env"]["LINUX_SMOKE_VERDICT"] == (
        "${{ needs.posix-smoke-verdicts.outputs.linux-smoke-verdict }}"
    )
    revocation_checkout = next(
        step
        for step in writer["steps"]
        if step.get("name") == "Checkout exact protected revocation writer revision"
    )
    assert revocation_checkout["with"]["ref"] == "${{ github.workflow_sha }}"
    assert "path: trusted-writer" in workflow_text
    assert "persist-credentials: false" in workflow_text
    assert "build_revocation_receipt" in workflow_text
    assert "release-revocation-receipt.json" in workflow_text
    assert (
        "release-truth/${RELEASE_TAG}/revocation/g${next_generation}" in workflow_text
    )
    assert 'gh release create "${receipt_tag}"' in workflow_text
    assert "--draft" in workflow_text
    assert "receipt-authority-pages.json" in workflow_text
    assert 'releases/tags/${receipt_tag}' not in workflow_text
    assert "scripts/release_truth.py upload-asset" in workflow_text
    assert "--authority receipt-release.json" in workflow_text
    assert 'gh release upload "${receipt_tag}"' not in workflow_text
    assert "release-revocation-receipt.json#release-revocation-receipt.json" not in workflow_text
    assert "receipt-publish-request.json" in workflow_text
    assert "receipt-publish-response.json" in workflow_text
    assert (
        '"repos/${GITHUB_REPOSITORY}/releases/${receipt_release_id}"' in workflow_text
    )
    assert "Receipt release differs from expected recovery identity" in workflow_text
    assert (
        "Receipt published release differs from expected recovery identity"
        in workflow_text
    )
    assert "create_exit" not in workflow_text
    assert "for attempt in $(seq 1 48)" in workflow_text
    assert "within twelve minutes" in workflow_text
    assert "--prerelease" in workflow_text
    assert "--latest=false" in workflow_text
    assert "immutable" in workflow_text
    assert "--clobber" not in workflow_text
    assert jobs["record-revocation"]["steps"][-1]["if"] == (
        "steps.receipt.outputs.idempotent == 'true' || "
        "steps.append.outcome == 'success'"
    )
    receipt_index = workflow_text.index('gh release create "${receipt_tag}"')
    projection_index = workflow_text.index("Project stop recommendation and incident")
    assert receipt_index < projection_index


def test_installed_runtime_declares_embedded_sigstore_verifier() -> None:
    pyproject = (_REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")

    assert '"sigstore==4.5.0"' in pyproject


def test_release_build_workflow_matrix_builds_smokes_and_uploads_assets(
    tmp_path: Path,
) -> None:
    workflow_path = _WORKFLOWS_DIR / "release-build.yml"

    assert workflow_path.is_file()

    workflow = workflow_path.read_text(encoding="utf-8")
    workflow_data = yaml.safe_load(workflow)
    jobs = workflow_data["jobs"]

    # 单个 run 标量超过表达式限制时，GitHub 会在创建任何 job 前拒绝工作流。
    # 保留安全余量，确保受保护 writer 不只是在本地 YAML 解析器中可用。
    for job in jobs.values():
        for step in job.get("steps", []):
            run = step.get("run")
            if not isinstance(run, str):
                continue
            assert len(run) <= 18_000, step.get("name")
            assert len(run.encode("utf-8")) <= 18_000, step.get("name")

    dispatch_inputs = workflow_data[True]["workflow_dispatch"]["inputs"]
    assert dispatch_inputs["mode"] == {
        "description": "Read-only load probe or the one-shot release generation.",
        "required": True,
        "type": "choice",
        "default": "load_probe",
        "options": ["load_probe", "release"],
    }
    assert dispatch_inputs["expected_candidate_sha"] == {
        "description": "Exact approved protected-main candidate merge SHA.",
        "required": True,
        "type": "string",
    }
    assert dispatch_inputs["load_probe_run_id"] == {
        "description": (
            "Exact successful read-only load-probe run ID; required only for release mode."
        ),
        "required": False,
        "type": "string",
        "default": "",
    }
    assert workflow_data["run-name"] == (
        "${{ inputs.mode == 'release' && "
        "format('release-admission|{0}|g0', inputs.tag) || "
        "format('release-load-probe|{0}|{1}', inputs.tag, "
        "inputs.expected_candidate_sha) }}"
    )
    assert workflow_data["concurrency"] == {
        "group": (
            "release-build-${{ inputs.mode }}-"
            "${{ inputs.expected_candidate_sha }}"
        ),
        "cancel-in-progress": False,
    }

    probe_job = jobs["release-load-probe"]
    assert probe_job["if"] == "inputs.mode == 'load_probe'"
    assert probe_job["permissions"] == {"contents": "read"}
    assert "environment" not in probe_job
    probe_contract = json.dumps(probe_job, sort_keys=True)
    assert "--method POST" not in probe_contract
    assert "--method PATCH" not in probe_contract
    assert "contents: write" not in probe_contract
    assert "release-publish" not in probe_contract
    assert "Load probe reruns are forbidden" in probe_contract
    assert "Load probe mode must not receive a prior load-probe run ID" in probe_contract
    assert "load_probe_run_id=%s" in probe_contract
    assert jobs["release-assurance-policy"]["if"] == "inputs.mode == 'release'"
    assert jobs["release-assurance-policy"]["permissions"] == {
        "actions": "read",
        "contents": "read",
    }
    policy_steps = jobs["release-assurance-policy"]["steps"]
    candidate_step_index = next(
        index
        for index, step in enumerate(policy_steps)
        if step.get("name") == "Require exact reviewed candidate"
    )
    load_probe_step_index = next(
        index
        for index, step in enumerate(policy_steps)
        if step.get("name")
        == "Require exact successful read-only load-probe authority"
    )
    assert load_probe_step_index == candidate_step_index + 1
    load_probe_step = policy_steps[load_probe_step_index]
    assert load_probe_step["env"] == {
        "EXPECTED_CANDIDATE_SHA": "${{ inputs.expected_candidate_sha }}",
        "GH_TOKEN": "${{ github.token }}",
        "LOAD_PROBE_RUN_ID": "${{ inputs.load_probe_run_id }}",
        "RELEASE_TAG": "${{ inputs.tag }}",
    }
    load_probe_gate = load_probe_step["run"]
    assert (
        '"repos/${GITHUB_REPOSITORY}/actions/runs/${GITHUB_RUN_ID}"'
        in load_probe_gate
    )
    assert (
        '"repos/${GITHUB_REPOSITORY}/actions/runs/${LOAD_PROBE_RUN_ID}/attempts/1"'
        in load_probe_gate
    )
    assert 'actions/runs/${LOAD_PROBE_RUN_ID}"' not in load_probe_gate
    assert "filter=latest" not in load_probe_gate
    assert '"release-load-probe|${RELEASE_TAG}|${EXPECTED_CANDIDATE_SHA}"' in (
        load_probe_gate
    )
    for required_run_field in (
        "workflow_id",
        "head_sha",
        "head_branch",
        "path",
        "display_title",
        "status",
        "conclusion",
        "run_attempt",
        "updated_at",
    ):
        assert required_run_field in load_probe_gate
    assert 'probe_run.get("id") != int(os.environ["LOAD_PROBE_RUN_ID"])' in (
        load_probe_gate
    )
    assert 'actions/runs/${probe_run_id}/artifacts?per_page=100' in load_probe_gate
    assert 'actions/runs/${probe_run_id}/attempts/1/jobs?per_page=100' in (
        load_probe_gate
    )
    assert 'artifacts.get("total_count") != 0' in load_probe_gate
    assert '"Read-only Release Workflow Load Probe"' in load_probe_gate
    assert '"Read-only Release Workflow Load Probe": "success"' in load_probe_gate
    assert load_probe_gate.count(': "skipped"') == 6
    assert 'jobs_document.get("total_count") != 7' in load_probe_gate
    writer_steps = jobs["publish-release"]["steps"]
    writer_probe_step_index = next(
        index
        for index, step in enumerate(writer_steps)
        if step.get("name")
        == "Revalidate exact load-probe authority before mutation"
    )
    writer_duplicate_step_index = next(
        index
        for index, step in enumerate(writer_steps)
        if step.get("name")
        == "Revalidate trusted Actions duplicate-run detector before mutation"
    )
    writer_tag_step_index = next(
        index
        for index, step in enumerate(writer_steps)
        if step.get("name") == "Create exact annotated release tag"
    )
    assert writer_probe_step_index == writer_duplicate_step_index + 1
    assert writer_tag_step_index == writer_probe_step_index + 1
    writer_probe_gate = writer_steps[writer_probe_step_index]["run"]
    assert (
        '"repos/${GITHUB_REPOSITORY}/actions/runs/${LOAD_PROBE_RUN_ID}/attempts/1"'
        in writer_probe_gate
    )
    assert 'actions/runs/${LOAD_PROBE_RUN_ID}"' not in writer_probe_gate
    assert 'probe_run.get("id") != int(os.environ["LOAD_PROBE_RUN_ID"])' in (
        writer_probe_gate
    )
    assert "inputs.mode == 'release'" in jobs["publish-release"]["if"]
    for job in jobs.values():
        if job.get("permissions", {}).get("contents") != "write":
            continue
        assert job.get("environment") == "release-publish"
        assert "inputs.mode == 'release'" in job.get("if", "")

    assert "workflow_dispatch:" in workflow
    assert "default: v1.0.5" in workflow
    assert workflow_data["env"] == {
        "CURRENT_RELEASE_TAG": "v1.0.5",
        "RELEASE_BOOTSTRAP_ENABLED": "false",
        "RELEASE_PUBLISH_ENVIRONMENT": "release-publish",
        "RELEASE_ENVIRONMENT_PROTECTION_VERIFIED": "false",
        "RELEASE_TAG_RULESET_PROTECTION_VERIFIED": "false",
        "RELEASE_USER_AGENT": "ai-sdlc-release-writer/1.0",
    }
    assert "ref: ${{ inputs.tag }}" not in workflow
    policy_checkout = next(
        step
        for step in jobs["release-assurance-policy"]["steps"]
        if step.get("uses", "").startswith("actions/checkout@")
    )
    build_checkout = next(
        step
        for step in jobs["build-smoke-candidate"]["steps"]
        if step.get("uses", "").startswith("actions/checkout@")
    )
    assert policy_checkout["with"]["ref"] == "${{ github.sha }}"
    assert build_checkout["with"]["ref"] == "${{ github.sha }}"
    assert 'git rev-parse "${RELEASE_TAG}^{commit}"' not in workflow
    assert 'head_commit="$(git rev-parse HEAD)"' in workflow
    assert '"${head_commit}" != "${GITHUB_SHA}"' in workflow
    assert workflow.count("run-authority-check") == 2
    assert workflow.count(
        "repos/${GITHUB_REPOSITORY}/actions/runs?event=workflow_dispatch&per_page=100"
    ) == 2
    assert "actions/workflows/${workflow_id}/runs" not in workflow
    assert workflow.count(
        'repos/${GITHUB_REPOSITORY}/contents/${workflow_path}?ref=${run_sha}'
    ) == 2
    assert workflow.count("--workflow-snapshots") == 2
    assert workflow.count("--paginate --slurp") >= 2
    assert "status=" not in workflow
    writer_generation_check = workflow.index(
        "Revalidate trusted Actions duplicate-run detector before mutation"
    )
    first_tag_mutation = workflow.index(
        '"repos/${GITHUB_REPOSITORY}/git/tags"', writer_generation_check
    )
    assert writer_generation_check < first_tag_mutation
    candidate_step = next(
        (
            step
            for step in jobs["release-assurance-policy"]["steps"]
            if step.get("name") == "Require exact reviewed candidate"
        ),
        None,
    )
    assert candidate_step is not None
    assert candidate_step["env"] == {
        "EXPECTED_CANDIDATE_SHA": "${{ inputs.expected_candidate_sha }}",
        "GITHUB_WORKFLOW_REF": "${{ github.workflow_ref }}",
        "LOAD_PROBE_RUN_ID": "${{ inputs.load_probe_run_id }}",
    }
    if os.name != "nt":
        candidate_bash = shutil.which("bash")
        assert candidate_bash is not None
        valid_candidate = subprocess.run(
            [candidate_bash, "-c", candidate_step["run"]],
            env={
                **os.environ,
                "EXPECTED_CANDIDATE_SHA": "a" * 40,
                "GITHUB_SHA": "a" * 40,
                "GITHUB_WORKFLOW_REF": (
                    "SinclairPan/Ai_AutoSDLC/.github/workflows/"
                    "release-build.yml@refs/heads/main"
                ),
                "GITHUB_REPOSITORY": "SinclairPan/Ai_AutoSDLC",
                "GITHUB_RUN_ATTEMPT": "1",
                "LOAD_PROBE_RUN_ID": "11",
            },
            check=False,
            capture_output=True,
            text=True,
        )
        mismatched_candidate = subprocess.run(
            [candidate_bash, "-c", candidate_step["run"]],
            env={
                **os.environ,
                "EXPECTED_CANDIDATE_SHA": "a" * 40,
                "GITHUB_SHA": "b" * 40,
                "GITHUB_WORKFLOW_REF": (
                    "SinclairPan/Ai_AutoSDLC/.github/workflows/"
                    "release-build.yml@refs/heads/main"
                ),
                "GITHUB_REPOSITORY": "SinclairPan/Ai_AutoSDLC",
                "GITHUB_RUN_ATTEMPT": "1",
                "LOAD_PROBE_RUN_ID": "11",
            },
            check=False,
            capture_output=True,
            text=True,
        )
        assert valid_candidate.returncode == 0, valid_candidate.stderr
        assert mismatched_candidate.returncode != 0
        assert "does not match exact approved candidate" in (
            mismatched_candidate.stderr
        )
        for invalid_probe_run_id in ("", "0", "abc"):
            invalid_probe_authority = subprocess.run(
                [candidate_bash, "-c", candidate_step["run"]],
                env={
                    **os.environ,
                    "EXPECTED_CANDIDATE_SHA": "a" * 40,
                    "GITHUB_SHA": "a" * 40,
                    "GITHUB_WORKFLOW_REF": (
                        "SinclairPan/Ai_AutoSDLC/.github/workflows/"
                        "release-build.yml@refs/heads/main"
                    ),
                    "GITHUB_REPOSITORY": "SinclairPan/Ai_AutoSDLC",
                    "GITHUB_RUN_ATTEMPT": "1",
                    "LOAD_PROBE_RUN_ID": invalid_probe_run_id,
                },
                check=False,
                capture_output=True,
                text=True,
            )
            assert invalid_probe_authority.returncode != 0
            assert "requires an exact positive load-probe run ID" in (
                invalid_probe_authority.stderr
            )
    assert "windows-latest" in workflow
    assert "macos-latest" in workflow
    assert "ubuntu-latest" in workflow
    assert "AI_SDLC_OFFLINE_ASSET_SUFFIX" in workflow
    assert "AI_SDLC_OFFLINE_PYTHON_RUNTIME" in workflow
    assert "uv python install 3.11" in workflow
    assert "uv python find --managed-python 3.11" in workflow
    assert "build_offline_bundle.sh" in workflow
    assert "install_offline.ps1" in workflow
    assert "./install_offline.sh" in workflow
    assert "verify_offline_bundle.py" in workflow
    assert "--require-bundled-runtime" in workflow
    assert "--require-checksums" in workflow
    assert "--expected-package-version" in workflow
    assert "--archive-checksum" in workflow
    assert "--install-log" in workflow
    assert "verify_offline_bundle.py failed with exit code" in workflow
    assert "adapter status" in workflow
    assert "run --dry-run" in workflow
    assert "actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a" in workflow
    assert (
        "name: release-candidate-${{ github.run_id }}-${{ github.run_attempt }}"
        in workflow
    )
    assert ".${{ matrix.archive }}.sha256" in workflow
    assert "WindowsPowerShell\\v1.0\\powershell.exe" in workflow
    assert (
        "-NoProfile -ExecutionPolicy Bypass -File .\\install_offline.ps1 -AddToPath"
        in workflow
    )
    assert '$cliDir = Join-Path $bundleDir.FullName ".venv\\Scripts"' in workflow
    assert "$env:Path = $cliDir + [IO.Path]::PathSeparator + $env:Path" in workflow
    assert "Get-Command ai-sdlc" in workflow
    assert "ai-sdlc --help" in workflow
    assert "ai-sdlc --version" in workflow
    assert "install_offline.sh --add-to-path" in workflow
    assert "command -v ai-sdlc" in workflow
    windows_hash = workflow.index(
        "Get-FileHash -Algorithm SHA256 -LiteralPath $archive.FullName"
    )
    windows_extract = workflow.index("Expand-Archive -LiteralPath $archive.FullName")
    windows_verify = workflow.index("--require-checksums", windows_extract)
    windows_install = workflow.index("install_offline.ps1 -AddToPath", windows_extract)
    assert windows_hash < windows_extract < windows_verify < windows_install
    posix_hash = workflow.index('actual_archive_hash="$(')
    posix_extract = workflow.index('tar xzf "${archive}"')
    posix_verify = workflow.index("--require-checksums", posix_extract)
    posix_install = workflow.index("./install_offline.sh --add-to-path", posix_extract)
    assert posix_hash < posix_extract < posix_verify < posix_install

    if os.name == "nt":
        return
    bash = shutil.which("bash")
    assert bash is not None

    probe_candidate_sha = "a" * 40
    probe_fake_bin = tmp_path / "probe-seal-fake-bin"
    probe_fake_bin.mkdir()
    probe_fake_gh = probe_fake_bin / "gh"
    probe_fake_gh.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "request=\"$*\"\n"
        "case \"${request}\" in\n"
        "  *\"git/ref/heads/main\"*) printf '%s\\n' \"${EXPECTED_CANDIDATE_SHA}\" ;;\n"
        "  *) echo \"unexpected gh request: ${request}\" >&2; exit 2 ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    probe_fake_gh.chmod(0o755)
    probe_env = {
        **os.environ,
        "CURRENT_RELEASE_TAG": "v1.0.5",
        "EXPECTED_CANDIDATE_SHA": probe_candidate_sha,
        "GH_TOKEN": "read-only-test-token",
        "GITHUB_REPOSITORY": "SinclairPan/Ai_AutoSDLC",
        "GITHUB_RUN_ID": "10",
        "GITHUB_RUN_ATTEMPT": "1",
        "GITHUB_SHA": probe_candidate_sha,
        "GITHUB_WORKFLOW_REF": (
            "SinclairPan/Ai_AutoSDLC/.github/workflows/"
            "release-build.yml@refs/heads/main"
        ),
        "LOAD_PROBE_RUN_ID": "",
        "PATH": f"{probe_fake_bin}{os.pathsep}{os.environ['PATH']}",
        "RELEASE_TAG": "v1.0.5",
        "RELEASE_USER_AGENT": "ai-sdlc-release-writer/1.0",
    }
    probe_seal_step = probe_job["steps"][0]
    probe_before_release = subprocess.run(
        [bash, "-c", probe_seal_step["run"]],
        cwd=tmp_path,
        env=probe_env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert probe_before_release.returncode == 0, probe_before_release.stderr

    # 只读探测不具备可消费状态；重复 dispatch 必须仍是零副作用探测，
    # 实际 release 只信任操作者显式绑定的一个成功 run ID。
    duplicate_probe_dispatch = subprocess.run(
        [bash, "-c", probe_seal_step["run"]],
        cwd=tmp_path,
        env={
            **probe_env,
            "GITHUB_RUN_ID": "11",
        },
        check=False,
        capture_output=True,
        text=True,
    )
    assert duplicate_probe_dispatch.returncode == 0, duplicate_probe_dispatch.stderr
    assert "load_probe_run_id=11" in duplicate_probe_dispatch.stdout

    probe_rerun = subprocess.run(
        [bash, "-c", probe_seal_step["run"]],
        cwd=tmp_path,
        env={**probe_env, "GITHUB_RUN_ATTEMPT": "2"},
        check=False,
        capture_output=True,
        text=True,
    )
    assert probe_rerun.returncode != 0
    assert "Load probe reruns are forbidden" in probe_rerun.stderr
    probe_with_prior_id = subprocess.run(
        [bash, "-c", probe_seal_step["run"]],
        cwd=tmp_path,
        env={**probe_env, "LOAD_PROBE_RUN_ID": "9"},
        check=False,
        capture_output=True,
        text=True,
    )
    assert probe_with_prior_id.returncode != 0
    assert "must not receive a prior load-probe run ID" in probe_with_prior_id.stderr

    expected_assets = (
        "ai-sdlc-offline-1.0.5-linux-amd64.tar.gz",
        "ai-sdlc-offline-1.0.5-linux-amd64.tar.gz.sha256",
        "ai-sdlc-offline-1.0.5-macos-arm64.tar.gz",
        "ai-sdlc-offline-1.0.5-macos-arm64.tar.gz.sha256",
        "ai-sdlc-offline-1.0.5-windows-amd64.zip",
        "ai-sdlc-offline-1.0.5-windows-amd64.zip.sha256",
    )
    proof_inputs = tmp_path / "release-proof-inputs"
    candidates = proof_inputs / "candidates"
    candidates.mkdir(parents=True)
    for asset_name in expected_assets:
        (candidates / asset_name).write_text(asset_name, encoding="utf-8")
    (proof_inputs / "release-proof-inputs.json").write_text(
        json.dumps(
            {
                "assets": [
                    {"name": name, "digest": "sha256:" + "0" * 64, "size_bytes": 1}
                    for name in expected_assets
                ]
            }
        ),
        encoding="utf-8",
    )

    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    fake_gh = fake_bin / "gh"
    fake_gh.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "[[ \"${1:-}\" == api ]]\n"
        "call_count=0\n"
        "[[ ! -f \"${GH_CALL_COUNT}\" ]] || call_count=\"$(<\"${GH_CALL_COUNT}\")\"\n"
        "call_count=$((call_count + 1))\n"
        "printf '%s' \"${call_count}\" > \"${GH_CALL_COUNT}\"\n"
        "if [[ \"${call_count}\" == 1 ]]; then\n"
        "  \"${PYTHON_EXE}\" - \"${INITIAL_ASSET_NAMES_JSON:-[]}\" <<'PY'\n"
        "import json\n"
        "import sys\n"
        "names = json.loads(sys.argv[1])\n"
        "print(json.dumps({'draft': True, 'prerelease': False, 'assets': [\n"
        "    {'name': name} for name in names\n"
        "]}))\n"
        "PY\n"
        "  exit 0\n"
        "fi\n"
        "\"${PYTHON_EXE}\" - \"${UPLOAD_LOG}\" \"${FORCE_TAMPERED_LIVE:-0}\" <<'PY'\n"
        "import json\n"
        "import sys\n"
        "names = open(sys.argv[1], encoding='utf-8').read().splitlines()\n"
        "if sys.argv[2] == '1':\n"
        "    names = names[:-1]\n"
        "print(json.dumps({'draft': True, 'prerelease': False, 'assets': [\n"
        "    {'name': name} for name in names\n"
        "]}))\n"
        "PY\n",
        encoding="utf-8",
    )
    fake_uv = fake_bin / "uv"
    fake_uv.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "asset=''\n"
        "while (($#)); do\n"
        "  if [[ \"$1\" == --asset ]]; then asset=\"$2\"; break; fi\n"
        "  shift\n"
        "done\n"
        "[[ -n \"${asset}\" ]]\n"
        "printf '%s\\n' \"${asset##*/}\" >> \"${UPLOAD_LOG}\"\n",
        encoding="utf-8",
    )
    fake_gh.chmod(0o755)
    fake_uv.chmod(0o755)

    current_release_tag = workflow_data["env"]["CURRENT_RELEASE_TAG"]
    generation_step = next(
        step
        for step in workflow_data["jobs"]["release-assurance-policy"]["steps"]
        if step.get("name") == "Require current release generation"
    )
    accepted_generation = subprocess.run(
        [bash, "-c", generation_step["run"]],
        env={
            **os.environ,
            "CURRENT_RELEASE_TAG": current_release_tag,
            "RELEASE_TAG": current_release_tag,
        },
        check=False,
        capture_output=True,
        text=True,
    )
    rejected_generation = subprocess.run(
        [bash, "-c", generation_step["run"]],
        env={
            **os.environ,
            "CURRENT_RELEASE_TAG": current_release_tag,
            "RELEASE_TAG": "v1.0.3",
        },
        check=False,
        capture_output=True,
        text=True,
    )
    assert accepted_generation.returncode == 0, accepted_generation.stderr
    assert rejected_generation.returncode != 0
    assert "does not match current release generation" in rejected_generation.stderr

    bootstrap_step = next(
        step
        for step in jobs["release-assurance-policy"]["steps"]
        if step.get("name") == "Require future release generation enablement"
    )
    disabled_bootstrap = subprocess.run(
        [bash, "-c", bootstrap_step["run"]],
        env={
            **os.environ,
            "CURRENT_RELEASE_TAG": current_release_tag,
            "RELEASE_BOOTSTRAP_ENABLED": "false",
        },
        check=False,
        capture_output=True,
        text=True,
    )
    assert disabled_bootstrap.returncode != 0
    assert "release bootstrap is disabled" in disabled_bootstrap.stderr.lower()

    pre_admission = json.dumps(
        {
            job_id: jobs[job_id]
            for job_id in (
                "release-assurance-policy",
                "release-assurance",
                "build-smoke-candidate",
                "release-qualification",
            )
        },
        sort_keys=True,
    )
    assert "--method POST" not in pre_admission
    assert "--method PATCH" not in pre_admission
    assert "upload-asset" not in pre_admission
    assert "zero-asset Draft" not in pre_admission
    prequalification_step = next(
        step
        for step in jobs["release-assurance-policy"]["steps"]
        if step.get("name") == "Require admission namespaces absent before qualification"
    )
    assert "release namespace already exists before qualification" in (
        prequalification_step["run"]
    )
    assert "certificate namespace already exists before qualification" in (
        prequalification_step["run"]
    )

    upload_step = next(
        step
        for step in workflow_data["jobs"]["publish-release"]["steps"]
        if step.get("name")
        == "Upload exact candidate assets to fresh zero-asset Draft"
    )
    assert 'while IFS= read -r asset_name || [[ -n "${asset_name}" ]]; do' in (
        upload_step["run"]
    )
    upload_log = tmp_path / "uploaded-assets.txt"
    gh_call_count = tmp_path / "gh-call-count.txt"
    env = {
        **os.environ,
        "GITHUB_REPOSITORY": "SinclairPan/Ai_AutoSDLC",
            "RELEASE_ID": "123456",
            "RELEASE_USER_AGENT": "ai-sdlc-release-writer/1.0",
        "UPLOAD_LOG": str(upload_log),
        "GH_CALL_COUNT": str(gh_call_count),
        "PYTHON_EXE": sys.executable,
        "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
    }
    completed = subprocess.run(
        [bash, "-c", upload_step["run"]],
        cwd=tmp_path,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert tuple(upload_log.read_text(encoding="utf-8").splitlines()) == expected_assets
    assert (tmp_path / "assets-to-upload.txt").read_bytes().endswith(b"\n")

    upload_log.unlink()
    gh_call_count.unlink()
    tampered = subprocess.run(
        [bash, "-c", upload_step["run"]],
        cwd=tmp_path,
        env={**env, "FORCE_TAMPERED_LIVE": "1"},
        check=False,
        capture_output=True,
        text=True,
    )
    assert tampered.returncode != 0
    assert "Draft candidate asset set must contain exactly six expected assets" in (
        tampered.stderr
    )

    upload_log.unlink()
    gh_call_count.unlink()
    nonempty_draft = subprocess.run(
        [bash, "-c", upload_step["run"]],
        cwd=tmp_path,
        env={
            **env,
            "INITIAL_ASSET_NAMES_JSON": json.dumps([expected_assets[0]]),
        },
        check=False,
        capture_output=True,
        text=True,
    )
    assert nonempty_draft.returncode != 0
    assert "fresh release generation requires a zero-asset Draft" in (
        nonempty_draft.stderr
    )
    assert not upload_log.exists()
    assert workflow.count("fresh release generation requires a zero-asset Draft") == 1
    assert "created release differs from exact zero-asset Draft admission" in workflow

    step = next(
        item
        for item in workflow_data["jobs"]["release-assurance-policy"]["steps"]
        if item.get("name") == "Require exact successful read-only load-probe authority"
    )

    candidate_sha = "a" * 40
    probe_run = {
        "id": 11,
        "workflow_id": 314982030,
        "path": ".github/workflows/release-build.yml",
        "event": "workflow_dispatch",
        "display_title": f"release-load-probe|v1.0.5|{candidate_sha}",
        "head_sha": candidate_sha,
        "head_branch": "main",
        "status": "completed",
        "conclusion": "success",
        "run_attempt": 1,
        "updated_at": "2026-08-13T00:01:00Z",
    }
    current_run = {
        "id": 99,
        "workflow_id": 314982030,
        "created_at": "2026-08-13T00:02:00Z",
    }
    expected_conclusions = {
        "Read-only Release Workflow Load Probe": "success",
        "Resolve Pre-tag Release Qualification Policy": "skipped",
        "release-assurance": "skipped",
        "${{ matrix.asset_os }} ${{ matrix.archive }}": "skipped",
        "Release Qualification": "skipped",
        "Build Release Proof Inputs": "skipped",
        "Publish Proof-bound Release": "skipped",
    }

    current_path = tmp_path / "current.json"
    authority_run_path = tmp_path / "authority-run.json"
    artifacts_path = tmp_path / "artifacts.json"
    jobs_path = tmp_path / "jobs.json"
    current_path.write_text(json.dumps(current_run), encoding="utf-8")
    authority_run_path.write_text(json.dumps(probe_run), encoding="utf-8")
    artifacts_path.write_text(
        json.dumps({"total_count": 0, "artifacts": []}), encoding="utf-8"
    )
    jobs = [
        {
            "name": name,
            "status": "completed",
            "conclusion": conclusion,
            "run_attempt": 1,
        }
        for name, conclusion in expected_conclusions.items()
    ]
    jobs_path.write_text(
        json.dumps({"total_count": 7, "jobs": jobs}), encoding="utf-8"
    )

    probe_fake_bin = tmp_path / "load-probe-fake-bin"
    probe_fake_bin.mkdir()
    probe_fake_gh = probe_fake_bin / "gh"
    probe_fake_gh.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "request=\"$*\"\n"
        "case \"${request}\" in\n"
        "  *\"/actions/runs/${GITHUB_RUN_ID}\"*) cat \"${FAKE_CURRENT_RUN}\" ;;\n"
        "  *\"/actions/runs/11/artifacts\"*) cat \"${FAKE_ARTIFACTS}\" ;;\n"
        "  *\"/actions/runs/11/attempts/1/jobs\"*) cat \"${FAKE_JOBS}\" ;;\n"
        "  *\"/actions/runs/${LOAD_PROBE_RUN_ID}/attempts/1\"*) cat \"${FAKE_AUTHORITY_RUN}\" ;;\n"
        "  *) echo \"unexpected gh request: ${request}\" >&2; exit 2 ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    probe_fake_gh.chmod(0o755)
    env = {
        **os.environ,
        "EXPECTED_CANDIDATE_SHA": candidate_sha,
        "FAKE_ARTIFACTS": str(artifacts_path),
        "FAKE_AUTHORITY_RUN": str(authority_run_path),
        "FAKE_CURRENT_RUN": str(current_path),
        "FAKE_JOBS": str(jobs_path),
        "GH_TOKEN": "read-only-test-token",
        "GITHUB_REPOSITORY": "SinclairPan/Ai_AutoSDLC",
        "GITHUB_RUN_ID": "99",
        "LOAD_PROBE_RUN_ID": "11",
        "PATH": f"{probe_fake_bin}{os.pathsep}{os.environ['PATH']}",
        "RELEASE_TAG": "v1.0.5",
        "RELEASE_USER_AGENT": "ai-sdlc-release-writer/1.0",
    }

    def run_gate() -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [bash, "-c", step["run"]],
            cwd=tmp_path,
            env=env,
            check=False,
            capture_output=True,
            text=True,
        )

    accepted = run_gate()
    assert accepted.returncode == 0, accepted.stderr

    writer_probe_gate_result = subprocess.run(
        [bash, "-c", writer_probe_gate],
        cwd=tmp_path,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert writer_probe_gate_result.returncode == 0, writer_probe_gate_result.stderr

    authority_run_path.write_text(
        json.dumps({**probe_run, "updated_at": current_run["created_at"]}),
        encoding="utf-8",
    )
    same_second = run_gate()
    assert same_second.returncode == 0, same_second.stderr
    writer_same_second = subprocess.run(
        [bash, "-c", writer_probe_gate],
        cwd=tmp_path,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert writer_same_second.returncode == 0, writer_same_second.stderr
    authority_run_path.write_text(json.dumps(probe_run), encoding="utf-8")

    # 由历史列表重复项矩阵改为按不可变 run ID 验证每个身份字段。
    # queued、failure、rerun 与错误身份都不能成为绑定的 release authority。
    # 删除连续 run-number 账本：只读 probe 无法消费一次性状态，列表延迟、删除或截断
    # 反而会烧毁合法 generation。
    invalid_authorities = (
        {**probe_run, "id": 12},
        {**probe_run, "workflow_id": 999},
        {**probe_run, "display_title": f"release-load-probe|v9.9.9|{candidate_sha}"},
        {**probe_run, "head_sha": "b" * 40},
        {**probe_run, "head_branch": "feature"},
        {**probe_run, "status": "queued", "conclusion": None},
        {**probe_run, "conclusion": "failure"},
        {**probe_run, "run_attempt": 2},
        {**probe_run, "updated_at": "2026-08-13T00:03:00Z"},
    )
    for invalid_authority in invalid_authorities:
        authority_run_path.write_text(json.dumps(invalid_authority), encoding="utf-8")
        rejected = run_gate()
        assert rejected.returncode != 0
        assert "exact load-probe run ID is not" in rejected.stderr
        writer_rejected = subprocess.run(
            [bash, "-c", writer_probe_gate],
            cwd=tmp_path,
            env=env,
            check=False,
            capture_output=True,
            text=True,
        )
        assert writer_rejected.returncode != 0
        assert "load-probe run ID no longer proves" in writer_rejected.stderr

    authority_run_path.write_text(json.dumps(probe_run), encoding="utf-8")
    artifacts_path.write_text(
        json.dumps({"total_count": 1, "artifacts": [{"name": "forbidden"}]}),
        encoding="utf-8",
    )
    artifact_bearing = run_gate()
    assert artifact_bearing.returncode != 0
    assert "exactly zero artifacts" in artifact_bearing.stderr

    artifacts_path.write_text(
        json.dumps({"total_count": 0, "artifacts": []}), encoding="utf-8"
    )
    jobs[-1]["conclusion"] = "success"
    jobs_path.write_text(
        json.dumps({"total_count": 7, "jobs": jobs}), encoding="utf-8"
    )
    release_path_ran = run_gate()
    assert release_path_ran.returncode != 0
    assert "every release path is skipped" in release_path_ran.stderr


def test_release_build_emergency_freeze_removes_release_write_authority() -> None:
    """保留既有 baseline 身份，并证明临时冻结已被唯一受保护 writer 完整替代。"""
    workflow_text = (_WORKFLOWS_DIR / "release-build.yml").read_text(encoding="utf-8")

    assert "Emergency Publish Freeze" not in workflow_text
    test_release_build_has_one_proof_bound_protected_writer()


def test_release_build_has_one_proof_bound_protected_writer() -> None:
    """捕获第二个写入者、伪造 Gate 权威或资格前创建发行身份。"""
    workflow_path = _WORKFLOWS_DIR / "release-build.yml"
    workflow_text = workflow_path.read_text(encoding="utf-8")
    workflow = yaml.safe_load(workflow_text)
    jobs = workflow["jobs"]

    assert workflow["permissions"] == {"contents": "read"}
    writers = [
        job_id
        for job_id, job in jobs.items()
        if isinstance(job, dict)
        and job.get("permissions", {}).get("contents") == "write"
    ]
    assert writers == ["publish-release"]
    qualification_job = jobs["release-qualification"]
    proof_job = jobs["build-release-proof"]
    publish_job = jobs["publish-release"]
    assert qualification_job["name"] == "Release Qualification"
    assert qualification_job["needs"] == [
        "release-assurance-policy",
        "release-assurance",
        "build-smoke-candidate",
    ]
    assert "needs.release-assurance.result == 'success'" in qualification_job["if"]
    assert "needs.build-smoke-candidate.result == 'success'" in qualification_job["if"]
    assert proof_job["needs"] == ["release-qualification"]
    assert proof_job["permissions"] == {"actions": "read", "contents": "read"}
    proof_freeze_step = next(
        step
        for step in proof_job["steps"]
        if step.get("name") == "Freeze protected Gate and candidate inputs"
    )
    assert proof_freeze_step["env"] == {
        "GH_TOKEN": "${{ github.token }}",
        "GITHUB_WORKFLOW_REF": "${{ github.workflow_ref }}",
        "LOAD_PROBE_RUN_ID": "${{ inputs.load_probe_run_id }}",
        "RELEASE_TAG": "${{ inputs.tag }}",
    }
    assert publish_job["needs"] == ["build-release-proof"]
    assert publish_job["environment"] == "release-publish"
    assert publish_job["permissions"] == {
        "actions": "read",
        "artifact-metadata": "write",
        "attestations": "write",
        "contents": "write",
        "id-token": "write",
    }
    assert publish_job["env"]["GH_REPO"] == "${{ github.repository }}"
    assert publish_job["concurrency"] == {
        "group": "release-publish-${{ inputs.tag }}",
        "cancel-in-progress": False,
    }
    checkout_refs = [
        step["with"]["ref"]
        for job in jobs.values()
        if isinstance(job, dict)
        for step in job.get("steps", [])
        if step.get("uses", "").startswith("actions/checkout@")
    ]
    assert checkout_refs == ["${{ github.sha }}"] * 4
    assert "ref: ${{ github.event.repository.default_branch }}" not in workflow_text
    assert "path: trusted-writer" in workflow_text
    assert "persist-credentials: false" in workflow_text
    assert "scripts/release_truth.py proof" in workflow_text
    assert "scripts/release_truth.py publish-check" in workflow_text
    assert "scripts/release_truth.py certificate" in workflow_text
    assert "actions/attest@1e69f48acb82d1966a394da916b4c1698aa569d6" in workflow_text
    assert "subject-path: release-certificate.json" in workflow_text
    assert "GITHUB_WORKFLOW_REF" in workflow_text
    assert "github.run_id" in workflow_text
    assert "github.run_attempt" in workflow_text
    assert "actions/download-artifact@37930b1c2abaa49bbe596cd826c3c89aef350131" in workflow_text
    assert "--clobber" not in workflow_text
    assert (
        '"repos/${GITHUB_REPOSITORY}/actions/runs/${GITHUB_RUN_ID}/jobs?per_page=100"'
        in workflow_text
    )
    assert "expected exactly one successful completed Release Qualification job" in workflow_text
    assert '"workflow_job_id": qualification["id"]' in workflow_text
    assert '"completed_at": qualification["completed_at"]' in workflow_text
    assert 'job.get("run_attempt") == int(os.environ["GITHUB_RUN_ATTEMPT"])' in (
        workflow_text
    )
    assert "datetime.fromisoformat(gate[\"completed_at\"]" in workflow_text
    assert "from ai_sdlc.core.release_truth import RELEASE_TRUTH_FRESHNESS_TTL" in (
        workflow_text
    )
    assert "completed_at + RELEASE_TRUTH_FRESHNESS_TTL" in workflow_text
    assert "cutoff = datetime.now(UTC).replace(microsecond=0)" in workflow_text
    assert '"evidence_cutoff_at": cutoff.isoformat()' in workflow_text

    namespace_index = workflow_text.index("Require all release admission namespaces absent")
    create_tag_index = workflow_text.index("Create exact annotated release tag")
    create_draft_index = workflow_text.index("Create fresh zero-asset Draft admission")
    upload_index = workflow_text.index("scripts/release_truth.py upload-asset")
    assert namespace_index < create_tag_index < create_draft_index < upload_index
    namespace_step = next(
        step
        for step in publish_job["steps"]
        if step.get("name") == "Require all release admission namespaces absent"
    )
    namespace_contract = namespace_step["run"]
    assert 'target_ref = f"refs/tags/{os.environ[\'RELEASE_TAG\']}"' in namespace_contract
    assert "release-truth/${RELEASE_TAG}/certificate/g0" in namespace_contract
    assert "release namespace already exists" in namespace_contract
    assert "certificate namespace already exists" in namespace_contract
    assert "admission_id" in workflow_text
    assert "tag_object_sha" in workflow_text
    assert "numeric_release_id" in workflow_text
    assert "upload_url" in workflow_text
    assert "commit_sha" in workflow_text
    assert "tree_sha" in workflow_text
    assert "workflow_run_id" in workflow_text
    assert "workflow_run_attempt" in workflow_text
    assert '"load_probe_run_id": int(os.environ["LOAD_PROBE_RUN_ID"])' in (
        workflow_text
    )
    assert publish_job["env"]["EXPECTED_CANDIDATE_SHA"] == (
        "${{ inputs.expected_candidate_sha }}"
    )
    assert '"expected_candidate_sha": os.environ["EXPECTED_CANDIDATE_SHA"]' in (
        workflow_text
    )
    assert "RELEASE_USER_AGENT" in workflow_text
    assert publish_job["env"]["GH_HTTP_USER_AGENT"] == "ai-sdlc-release-writer/1.0"
    assert workflow["env"]["RELEASE_PUBLISH_ENVIRONMENT"] == "release-publish"
    assert workflow["env"]["RELEASE_ENVIRONMENT_PROTECTION_VERIFIED"] == "false"
    assert workflow["env"]["RELEASE_TAG_RULESET_PROTECTION_VERIFIED"] == "false"
    assert '"${RELEASE_ENVIRONMENT_PROTECTION_VERIFIED}" != "true"' in workflow_text
    assert "historical writer runs remain blocked" in workflow_text
    assert "terminal-generation-burn" in workflow_text
    assert "no cleanup, edit, reuse, or rerun" in workflow_text
    assert workflow_text.count('"${GITHUB_RUN_ATTEMPT}" != "1"') == 4
    assert "rerun is forbidden by terminal-generation-burn" in workflow_text
    assert workflow_text.count("scripts/release_truth.py ruleset-check") >= 6
    assert "repos/${GITHUB_REPOSITORY}/rulesets?includes_parents=true" in workflow_text
    assert "refs/tags/v*" in workflow_text
    assert "refs/tags/release-truth/v*/certificate/g0" in workflow_text
    assert "Release ETag does not protect Git refs" in workflow_text
    assert 'admission["admission_digest"] = admission_digest' in workflow_text
    assert '"upload_url": admission["upload_url"]' in workflow_text
    assert '"release_user_agent": admission["user_agent"]' in workflow_text

    assert workflow_text.count("scripts/release_truth.py upload-asset") == 3
    assert "gh release upload" not in workflow_text
    assert "release-certificate.json#release-certificate.json" not in workflow_text
    cas_index = workflow_text.index("scripts/release_truth.py publish-check")
    publish_index = workflow_text.index("gh api --method PATCH")
    verify_index = workflow_text.index(
        'gh release verify "${RELEASE_TAG}" --format json'
    )
    certificate_index = workflow_text.index("scripts/release_truth.py certificate")
    evidence_release_index = workflow_text.index("certificate-create-request.json")
    certificate_attestation_index = workflow_text.index(
        "actions/attest@1e69f48acb82d1966a394da916b4c1698aa569d6"
    )
    assert upload_index < cas_index < publish_index < verify_index
    assert (
        verify_index
        < certificate_index
        < certificate_attestation_index
        < evidence_release_index
    )
    assert 'gh release edit "${RELEASE_TAG}" --draft=false' not in workflow_text
    assert "gh release edit" not in workflow_text
    assert "--force" not in workflow_text
    assert "existing_proof" not in workflow_text
    assert "resolve_certificate_release" not in workflow_text
    assert '"repos/${GITHUB_REPOSITORY}/releases/${release_id}"' in workflow_text
    assert "release-before-publish.http" in workflow_text
    assert "release-publish-etag.txt" in workflow_text
    assert '-H "If-Match: ${release_etag}"' in workflow_text
    assert "--input release-publish-request.json" in workflow_text
    assert "release-publish-response.json" in workflow_text
    assert "Published release differs from Proof-bound transition" in workflow_text
    assert workflow_text.count("Validate live frozen admission authority") >= 4
    assert workflow_text.count("Validate current gate freshness") >= 3
    assert '--observed-at "${publish_observed_at}"' in workflow_text
    assert workflow_text.count("gh api") == workflow_text.count(
        'User-Agent: ${RELEASE_USER_AGENT}'
    )
    assert workflow_text.count("scripts/release_truth.py upload-asset") == (
        workflow_text.count('--user-agent "${RELEASE_USER_AGENT}"')
    )
    assert "base64.b64decode(payload, validate=True)" in workflow_text
    assert 'predicate.get("releaseId") != str(published["id"])' in workflow_text
    assert "asset_subjects != expected_assets" in workflow_text
    assert "Validate live software authority immediately after publish" in workflow_text
    assert "--release-state immutable" in workflow_text
    assert "publish_exit" not in workflow_text
    assert '"prerelease": True' in workflow_text
    assert '"make_latest": "false"' in workflow_text
    assert "release-truth/${RELEASE_TAG}/certificate/g0" in workflow_text
    assert "--authority certificate-release.json" in workflow_text
    assert "certificate-tag-ref-request.json" in workflow_text
    assert "certificate-create-request.json" in workflow_text
    assert "certificate-publish-request.json" in workflow_text
    assert "certificate-publish-response.json" in workflow_text
    assert "certificate-annotated-tag-request.json" in workflow_text
    assert workflow_text.count("scripts/release_truth.py tag-authority-check") >= 3
    assert "certificate-attestation.json" in workflow_text
    assert "Certificate Attestation content differs from frozen authority" in workflow_text
    cert_ref_index = workflow_text.index("certificate-tag-ref-request.json")
    cert_live_index = workflow_text.index("tag-authority-check", cert_ref_index)
    cert_create_index = workflow_text.index("certificate-create-request.json")
    assert cert_ref_index < cert_live_index < cert_create_index
    assert "certificate-before-publish.http" in workflow_text
    assert "certificate-publish-etag.txt" in workflow_text
    certificate_cas_index = workflow_text.index("certificate-before-publish.http")
    certificate_if_match_index = workflow_text.index(
        '-H "If-Match: ${certificate_etag}"', certificate_cas_index
    )
    certificate_wait_index = workflow_text.index("evidence_ready=false")
    assert certificate_cas_index < certificate_if_match_index < certificate_wait_index
    assert (
        '"repos/${GITHUB_REPOSITORY}/releases/${certificate_release_id}"'
        in workflow_text
    )
    assert (
        "Certificate release differs from fresh current-run identity" in workflow_text
    )
    assert "immutable" in workflow_text
    assert "release-satisfaction-proof.json" in workflow_text
    assert "release-certificate.json" in workflow_text
    assert "scripts/release_truth.py" not in (_REPO_ROOT / "pyproject.toml").read_text(
        encoding="utf-8"
    )


def test_windows_user_guide_e2e_replays_existing_project_install_path() -> None:
    workflow_path = _WORKFLOWS_DIR / "windows-user-guide-e2e.yml"

    assert workflow_path.is_file()

    workflow = workflow_path.read_text(encoding="utf-8")

    assert "workflow_dispatch:" in workflow
    assert "pull_request:" in workflow
    assert "windows-latest" in workflow
    assert "default: v1.0.2" in workflow
    assert "Build Windows offline bundle for pull request replay" in workflow
    assert "build_offline_bundle.sh" in workflow
    assert 'AI_SDLC_OFFLINE_ASSET_SUFFIX="-windows-amd64"' in workflow
    assert "pull_request_local_bundle" in workflow
    assert "USER_GUIDE.zh-CN.md Chapter 2: existing project" in workflow
    assert "my-existing-project" in workflow
    assert "v1.0.5" in workflow
    assert "ai-sdlc-offline-$releaseVersion-windows-amd64" in workflow
    assert "releases/download/$env:RELEASE_TAG" in workflow
    assert "Invoke-WebRequest" in workflow
    assert ".sha256" in workflow
    assert "Get-FileHash -Algorithm SHA256" in workflow
    assert "Expand-Archive" in workflow
    assert "-ExecutionPolicy Bypass -File .\\install_offline.ps1 -AddToPath" in workflow
    assert ".\\.venv\\Scripts\\python.exe -m ai_sdlc --help" in workflow
    assert "Direct shim" in workflow
    assert "Codex \\+ PowerShell project init" in workflow
    assert "released-package-guide-gap.txt" in workflow
    assert "& $directShim init . --agent-target vscode --shell powershell" in workflow
    assert "当前结果 / Result" in workflow
    assert "下一步 / Next" in workflow
    assert "adapter ingress|materialized|unverified|host ingress" in workflow
    assert "& $directShim adopt ." in workflow
    assert "接入已有项目" in workflow
    assert "business-file-hashes-before.txt" in workflow
    assert "business-file-hashes-after.txt" in workflow
    assert "Compare-Object" in workflow
    assert "init/adopt modified existing business files" in workflow
    assert "my-new-project" in workflow
    assert "& $directShim init . --agent-target cursor --shell powershell" in workflow
    assert "windows-empty-project-init.txt" in workflow
    assert ".cursor\\rules\\ai-sdlc.mdc" in workflow
    assert "windows-user-guide-existing-project-evidence" in workflow
    assert "actions/upload-artifact@v7" in workflow


def test_posix_user_guide_e2e_replays_published_guide_commands() -> None:
    workflow_path = _WORKFLOWS_DIR / "posix-user-guide-e2e.yml"
    driver_path = _REPO_ROOT / "scripts" / "posix_clean_user_e2e.py"

    assert workflow_path.is_file()
    assert driver_path.is_file()

    workflow = workflow_path.read_text(encoding="utf-8")
    driver = driver_path.read_text(encoding="utf-8")
    assert "workflow_dispatch:" in workflow
    assert "pull_request:" in workflow
    assert 'default: "v1.0.2"' in workflow
    assert "v1.0.5" in workflow
    for path_filter in (
        '      - "src/**"',
        '      - "pyproject.toml"',
        '      - "packaging_backend.py"',
        '      - "README.md"',
        '      - "templates/**"',
        '      - "packaging/offline/**"',
    ):
        assert path_filter in workflow
    assert "macos-latest" in workflow
    assert "ubuntu-latest" in workflow
    assert "USER_GUIDE.zh-CN.md" in workflow
    assert '- "scripts/posix_clean_user_e2e.py"' in workflow
    assert "Build POSIX offline bundle for pull request replay" in workflow
    assert "github.event_name == 'pull_request'" in workflow
    assert "bash packaging/offline/build_offline_bundle.sh" in workflow
    assert 'package_source="pull_request_local_bundle"' in workflow
    assert 'package_source="published_release"' in workflow
    assert "dist-offline/${PACKAGE_NAME}" in workflow
    assert "curl --fail --location --retry 3" in workflow
    assert "releases/download/$RELEASE_TAG/$PACKAGE_NAME" in workflow
    assert "shasum -a 256 -c" in workflow
    assert "sha256sum -c" in workflow
    assert "./install_offline.sh --add-to-path" in workflow
    assert '"$DIRECT_CLI" --version' in workflow
    assert '"$DIRECT_CLI" init .' in workflow
    assert '"$DIRECT_CLI" adopt .' in workflow
    assert "python3 scripts/posix_clean_user_e2e.py" in workflow
    assert "POSIX_INTERACTIVE_SELECTION_COMPLETED" in workflow
    assert "pty.fork()" in driver
    assert 'os.execv(str(cli_path), [str(cli_path), "init", "."])' in driver
    assert 'os.write(master_fd, b"\\x1b[A")' in driver
    assert "agent_renders > observed_agent_renders" in driver
    assert "AGENT_PROMPT" in driver
    assert "SHELL_PROMPT" in driver
    assert '"--agent-target"' not in driver
    assert '"--shell"' not in driver
    assert "import ai_sdlc" not in driver
    assert "business-before.sha256" in workflow
    assert "business-after.sha256" in workflow
    assert "diff -u" in workflow
    assert "posix-user-guide-e2e-evidence" in workflow
    assert "actions/upload-artifact@v7" in workflow


def test_windows_clean_user_e2e_uses_remote_install_and_real_interactive_init() -> None:
    workflow_path = _WORKFLOWS_DIR / "windows-user-guide-e2e.yml"
    driver_path = _REPO_ROOT / "scripts" / "windows_clean_user_e2e.py"

    assert workflow_path.is_file()
    assert driver_path.is_file()

    workflow = workflow_path.read_text(encoding="utf-8")
    driver = driver_path.read_text(encoding="utf-8")

    install_inputs = (
        '- "src/**"',
        '- "pyproject.toml"',
        '- "packaging_backend.py"',
        '- "README.md"',
        '- "templates/**"',
        '- "scripts/frontend_browser_gate_probe_runner.mjs"',
        '- "packaging/install_online.ps1"',
    )
    assert all(path_filter in workflow for path_filter in install_inputs)
    assert "clean-online-interactive-user-journey:" in workflow
    assert "remote-release-tag" in workflow
    assert (
        "raw.githubusercontent.com/$sourceRepository/"
        "$remoteSha/packaging/install_online.ps1" in workflow
    )
    assert "git+https://github.com/$sourceRepository.git@$remoteSha" in workflow
    assert "pywinpty" in workflow
    assert "windows-clean-online-user-e2e-evidence" in workflow
    assert "PtyProcess.spawn" in driver
    assert '[cli_path, "init", "."]' in driver
    assert "请选择当前实际用于聊天开发的 AI 代理入口" in driver
    assert "请选择当前项目默认使用的命令 Shell" in driver
    assert 'process.write("2\\r\\n")' in driver
    assert 'process.write("1\\r\\n")' in driver
    assert '"--agent-target"' not in driver
    assert '"--shell"' not in driver
    assert "import ai_sdlc" not in driver


def test_windows_clean_user_e2e_uses_real_codex_cli_and_archives_adapter_files() -> (
    None
):
    workflow_path = _WORKFLOWS_DIR / "windows-user-guide-e2e.yml"
    driver_path = _REPO_ROOT / "scripts" / "windows_clean_user_e2e.py"

    workflow = workflow_path.read_text(encoding="utf-8")
    driver = driver_path.read_text(encoding="utf-8")

    assert "actions/setup-node@v6" in workflow
    assert '"@openai/codex@0.138.0"' in workflow
    assert 'shutil.which("codex")' in driver
    assert "codex-cli-version.txt" in driver
    assert "codex-adapter-files" in driver
    assert "codex-adapter-manifest.json" in driver
    assert 'project_root / "AGENTS.md"' in driver
    assert 'project_root / ".ai-sdlc" / "project" / "config"' in driver
    assert '"project-config.yaml"' in driver
    assert "hashlib.sha256" in driver
    clean_upload = workflow.split("Upload clean online ordinary-user evidence", 1)[1]
    assert "include-hidden-files: true" in clean_upload


def test_windows_clean_user_e2e_pins_release_tag_before_online_install() -> None:
    workflow_path = _WORKFLOWS_DIR / "windows-user-guide-e2e.yml"

    workflow = workflow_path.read_text(encoding="utf-8").split(
        "clean-online-interactive-user-journey:", 1
    )[1]
    resolve_release_tag = (
        "git ls-remote https://github.com/SinclairPan/Ai_AutoSDLC.git "
        '"refs/tags/$env:RELEASE_TAG" "refs/tags/$env:RELEASE_TAG^{}"'
    )
    pinned_installer = (
        "raw.githubusercontent.com/$sourceRepository/"
        "$remoteSha/packaging/install_online.ps1"
    )
    pinned_package = "git+https://github.com/$sourceRepository.git@$remoteSha"

    assert resolve_release_tag in workflow
    assert '$sourceKind = "remote-release-tag"' in workflow
    assert pinned_installer in workflow
    assert pinned_package in workflow
    assert workflow.index(resolve_release_tag) < workflow.index(pinned_installer)
    assert workflow.index(pinned_installer) < workflow.index("Invoke-WebRequest")
    assert workflow.count(resolve_release_tag) == 1
    assert "$directUrl.vcs_info.requested_revision -ne $remoteSha" in workflow


def test_windows_clean_user_e2e_installs_pull_request_head_on_pr_runs() -> None:
    workflow_path = _WORKFLOWS_DIR / "windows-user-guide-e2e.yml"
    driver_path = _REPO_ROOT / "scripts" / "windows_clean_user_e2e.py"
    support_path = _REPO_ROOT / "scripts" / "windows_clean_user_e2e_support.py"

    workflow = workflow_path.read_text(encoding="utf-8").split(
        "clean-online-interactive-user-journey:", 1
    )[1]
    driver = driver_path.read_text(encoding="utf-8")
    contract = driver + support_path.read_text(encoding="utf-8")

    assert "PR_HEAD_REPOSITORY:" in workflow
    assert "github.event.pull_request.head.repo.full_name" in workflow
    assert "PR_HEAD_SHA:" in workflow
    assert "github.event.pull_request.head.sha" in workflow
    assert 'if ($env:GITHUB_EVENT_NAME -eq "pull_request")' in workflow
    assert "$sourceRepository = $env:PR_HEAD_REPOSITORY" in workflow
    assert "$remoteSha = $env:PR_HEAD_SHA" in workflow
    assert (
        "raw.githubusercontent.com/$sourceRepository/"
        "$remoteSha/packaging/install_online.ps1" in workflow
    )
    assert "git+https://github.com/$sourceRepository.git@$remoteSha" in workflow
    assert "AI_SDLC_E2E_INSTALL_SOURCE=$sourceKind" in workflow
    assert "AI_SDLC_E2E_SOURCE_REVISION=$remoteSha" in workflow
    assert 'os.environ.get("AI_SDLC_E2E_INSTALL_SOURCE", "remote-main")' in contract
    assert 'os.environ.get("AI_SDLC_E2E_SOURCE_REVISION", "")' in contract


def test_windows_clean_user_e2e_covers_solution_recommendation_and_advanced_choice() -> (
    None
):
    driver_path = _REPO_ROOT / "scripts" / "windows_clean_user_e2e.py"
    support_path = _REPO_ROOT / "scripts" / "windows_clean_user_e2e_support.py"

    assert driver_path.is_file()
    assert support_path.is_file()

    driver = driver_path.read_text(encoding="utf-8")
    contract = driver + support_path.read_text(encoding="utf-8")

    assert '"program validate: PASS"' in driver
    assert '"program", "solution-confirm", "--dry-run"' in driver
    assert '"--mode", "advanced"' in driver
    assert '"--frontend-stack",' in driver
    assert '"vue3",' in driver
    assert '"--provider-id",' in driver
    assert '"public-primevue",' in driver
    assert '"--style-pack-id",' in driver
    assert '"data-console",' in driver
    assert "PrimeVue + @primeuix/themes + primeicons" in contract
    assert "definePreset(Aura) + #1770e6 + darkModeSelector=false" in contract
    assert "enterprise-default" in contract
    assert "data-console" in contract
    assert "high-clarity" in contract
    assert "macos-glass" in contract
    assert "enterprise-vue2" in contract
    assert "--execute" not in driver
    assert '["program", "managed-delivery-apply"' not in driver


def test_windows_clean_user_e2e_uses_public_requirement_and_workitem_flow() -> None:
    driver_path = _REPO_ROOT / "scripts" / "windows_clean_user_e2e.py"
    workflow_path = _WORKFLOWS_DIR / "windows-user-guide-e2e.yml"
    driver = driver_path.read_text(encoding="utf-8")
    workflow = workflow_path.read_text(encoding="utf-8")

    assert "requirement-start.json" in driver
    assert '"--input-file"' in driver
    assert "requirement-status.json" in driver
    assert "requirement-freeze.json" in driver
    assert '"--yes"' in driver
    assert "workitem-init.txt" in driver
    assert "windows_clean_user_e2e_support.py" in workflow
    assert 'spec_root / "spec.md"' not in driver


def test_historical_update_prompt_workflow_is_not_published() -> None:
    assert not (_WORKFLOWS_DIR / "windows-update-prompt-e2e.yml").exists()


def test_windows_online_job_does_not_install_retired_lean_governance() -> None:
    workflow = (_WORKFLOWS_DIR / "windows-user-guide-e2e.yml").read_text(
        encoding="utf-8"
    )

    assert "Run the installed Lean Code user journey" not in workflow
    assert "windows_lean_code_e2e.py" not in workflow
    assert "windows_lean_code_e2e_support.py" not in workflow
    assert "windows-clean-online-user-e2e-evidence" in workflow


def test_posix_offline_smoke_matrix_concurrency_is_job_scoped() -> None:
    workflow_path = _WORKFLOWS_DIR / "posix-offline-smoke.yml"

    workflow = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))

    assert "concurrency" not in workflow
    assert workflow["jobs"]["smoke"]["concurrency"] == {
        "group": "posix-offline-smoke-${{ github.event.pull_request.number || github.ref }}-${{ matrix.os }}",
        "cancel-in-progress": True,
    }


def test_compatibility_gate_statically_layers_fast_and_full_assurance() -> None:
    workflow_path = _WORKFLOWS_DIR / "compatibility-gate.yml"
    workflow = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
    triggers = workflow[True]

    assert {
        "pull_request",
        "push",
        "merge_group",
        "workflow_dispatch",
        "workflow_call",
        "schedule",
    } <= set(triggers)
    assert triggers["pull_request"]["types"] == [
        "opened",
        "synchronize",
        "reopened",
        "ready_for_review",
        "converted_to_draft",
    ]
    assert triggers["workflow_call"]["inputs"]["force_full"] == {
        "description": "Force the complete OS and Python assurance matrix.",
        "required": False,
        "type": "boolean",
        "default": False,
    }
    jobs = workflow["jobs"]
    assert jobs["fast-gate"]["runs-on"] == "ubuntu-latest"
    assert "authority-check" not in jobs
    assert "baseline-preflight" not in jobs
    assert jobs["cross-platform-validation"]["strategy"]["matrix"] == {
        "os": ["ubuntu-latest", "macos-latest", "windows-latest"],
        "python-version": ["3.11", "3.12", "3.13", "3.14"],
    }
    full_condition = (
        "github.event_name != 'pull_request' || "
        "github.event.pull_request.draft == false || inputs.force_full == true"
    )
    assert jobs["cross-platform-validation"]["if"] == full_condition
    assert jobs["windows-shell-smoke"]["if"] == full_condition
    assert jobs["merge-assurance"]["if"] == "always()"
    assert jobs["merge-assurance"]["needs"] == [
        "fast-gate",
        "cross-platform-validation",
        "windows-shell-smoke",
    ]
    assert jobs["compatibility-gate-result"]["name"] == "Compatibility Gate Result"


def test_compatibility_gate_uses_candidate_artifacts_and_exact_results(
    tmp_path: Path,
) -> None:
    workflow = (_WORKFLOWS_DIR / "compatibility-gate.yml").read_text(encoding="utf-8")

    assert "trusted-base" not in workflow
    assert "authority_ref" not in workflow
    assert "test-baseline.json" not in workflow
    assert "test-lineage.json" not in workflow
    assert "python scripts/ci_static_assurance.py collect" in workflow
    assert "python scripts/ci_static_assurance.py cell-evidence" in workflow
    assert "python scripts/ci_static_assurance.py aggregate" in workflow
    assert "--ignore=tests/e2e/stage_review" not in workflow
    assert "--junitxml=ci-evidence/${CELL}/compatibility-results.xml" in workflow
    assert (
        "actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a"
        in workflow
    )
    assert "if-no-files-found: error" in workflow
    assert "--maxfail" not in workflow
    assert "continue-on-error" not in workflow

    parsed = yaml.safe_load(workflow)
    matrix_steps = parsed["jobs"]["cross-platform-validation"]["steps"]
    assert all("cell-evidence" not in str(step.get("run", "")) for step in matrix_steps)
    full_pytest_step = next(
        step for step in matrix_steps if step.get("name") == "Run full pytest suite"
    )
    assert "uv run pytest" in full_pytest_step["run"]
    assert (
        "--junitxml=ci-evidence/${CELL}/compatibility-results.xml"
        in full_pytest_step["run"]
    )
    step_names = [step.get("name") for step in matrix_steps]
    assert step_names.index("Doctor") < step_names.index("Run full pytest suite")
    merge_steps = parsed["jobs"]["merge-assurance"]["steps"]
    assert all("uv run python" not in str(step.get("run", "")) for step in merge_steps)
    gate_script = merge_steps[0]["run"]
    assert "needs.cross-platform-validation.result" in gate_script
    aggregate_script = next(
        step["run"]
        for step in merge_steps
        if step.get("name") == "Rebuild, verify, and aggregate full evidence"
    )
    assert "python scripts/ci_static_assurance.py cell-evidence" in aggregate_script
    assert "python scripts/ci_static_assurance.py aggregate" in aggregate_script
    assert "started-at.txt" in aggregate_script
    assert "finished-at.txt" in aggregate_script
    assert "--baseline" not in aggregate_script
    assert "--lineage" not in aggregate_script

    sentinel = runpy.run_path(
        _REPO_ROOT / "scripts" / "ci_snapshot_control_sentinel.py"
    )
    expected_command = [
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "--no-cov",
        "tests/unit/test_review_kernel.py::"
        "test_merge_expert_findings_deduplicates_without_deciding_close",
    ]
    assert sentinel["SENTINEL_NODE"] == expected_command[-1]
    assert sentinel["SENTINEL_ROUNDS"] == 5
    success_calls: list[list[str]] = []

    def successful_runner(command: list[str]) -> subprocess.CompletedProcess[str]:
        success_calls.append(command)
        return subprocess.CompletedProcess(command, 0)

    success = sentinel["run_snapshot_control_sentinel"](successful_runner)
    assert success["success"] is True
    assert success["exit_code"] == 0
    assert success["declared_rounds"] == sentinel["SENTINEL_ROUNDS"]
    assert success["executed_rounds"] == sentinel["SENTINEL_ROUNDS"]
    assert len(success_calls) == sentinel["SENTINEL_ROUNDS"]
    assert all(command == expected_command for command in success_calls)
    assert [attempt["command"] for attempt in success["attempts"]] == [
        expected_command
    ] * sentinel["SENTINEL_ROUNDS"]
    assert [attempt["returncode"] for attempt in success["attempts"]] == [0] * 5

    failure_calls: list[list[str]] = []

    def failing_runner(command: list[str]) -> subprocess.CompletedProcess[str]:
        failure_calls.append(command)
        return subprocess.CompletedProcess(command, 17 if len(failure_calls) == 2 else 0)

    failure = sentinel["run_snapshot_control_sentinel"](failing_runner)
    assert failure["success"] is False
    assert failure["exit_code"] == 17
    assert failure["declared_rounds"] == sentinel["SENTINEL_ROUNDS"]
    assert failure["executed_rounds"] == 2
    assert failure_calls == [expected_command, expected_command]
    assert [attempt["returncode"] for attempt in failure["attempts"]] == [0, 17]

    cli_success_output = tmp_path / "nested" / "success.json"
    assert sentinel["main"](["--output", str(cli_success_output)], successful_runner) == 0
    cli_success_text = cli_success_output.read_text(encoding="utf-8")
    cli_success = json.loads(cli_success_text)
    assert cli_success_output.parent.is_dir()
    assert cli_success["success"] is True
    assert cli_success["exit_code"] == 0
    assert cli_success["declared_rounds"] == 5
    assert cli_success["executed_rounds"] == 5
    assert cli_success_text == (
        json.dumps(
            cli_success,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    )

    cli_failure_calls: list[list[str]] = []

    def cli_failing_runner(command: list[str]) -> subprocess.CompletedProcess[str]:
        cli_failure_calls.append(command)
        return subprocess.CompletedProcess(command, 23 if len(cli_failure_calls) == 2 else 0)

    cli_failure_output = tmp_path / "nested" / "failure.json"
    assert sentinel["main"](["--output", str(cli_failure_output)], cli_failing_runner) == 23
    cli_failure = json.loads(cli_failure_output.read_text(encoding="utf-8"))
    assert cli_failure_calls == [expected_command, expected_command]
    assert cli_failure["success"] is False
    assert cli_failure["exit_code"] == 23
    assert cli_failure["declared_rounds"] == 5
    assert cli_failure["executed_rounds"] == 2
    assert [attempt["returncode"] for attempt in cli_failure["attempts"]] == [0, 23]

    def unavailable_runner(command: list[str]) -> subprocess.CompletedProcess[str]:
        raise OSError("secret-token=/private/ci/runner")

    runner_error_output = tmp_path / "nested" / "runner-error.json"
    assert sentinel["main"](["--output", str(runner_error_output)], unavailable_runner) == 1
    runner_error_text = runner_error_output.read_text(encoding="utf-8")
    runner_error = json.loads(runner_error_text)
    assert runner_error["success"] is False
    assert runner_error["exit_code"] == 1
    assert runner_error["declared_rounds"] == 5
    assert runner_error["executed_rounds"] == 0
    assert runner_error["attempts"] == []
    assert runner_error["runner_error"] == {
        "reason": "runner_exception",
        "type": "OSError",
    }
    assert "secret-token" not in runner_error_text
    assert "/private/ci/runner" not in runner_error_text

    sentinel_step = next(
        step
        for step in matrix_steps
        if step.get("name") == "Run fixed SnapshotControl stability sentinel"
    )
    assert sentinel_step["if"] == (
        "matrix.os == 'windows-latest' && matrix.python-version == '3.14'"
    )
    assert sentinel_step["run"] == (
        "uv run python scripts/ci_snapshot_control_sentinel.py "
        "--output ci-evidence/${{ env.CELL }}/snapshot-control-sentinel.json"
    )
    assert step_names.index("Run full pytest suite") < step_names.index(
        "Run fixed SnapshotControl stability sentinel"
    ) < step_names.index("Record raw cell completion")
    evidence_upload = next(
        step for step in matrix_steps if step.get("name") == "Upload compatibility evidence"
    )
    assert evidence_upload["if"] == "always()"


def test_compatibility_gate_uses_candidate_local_execution_evidence_only() -> None:
    """普通 CI 不得以 protected baseline/lineage 阻止有意删除废止测试。"""
    workflow_text = (_WORKFLOWS_DIR / "compatibility-gate.yml").read_text(
        encoding="utf-8"
    )
    workflow = yaml.safe_load(workflow_text)
    jobs = workflow["jobs"]

    assert "authority-check" not in jobs
    assert "baseline-preflight" not in jobs
    assert "trusted-base" not in workflow_text
    assert "test-baseline.json" not in workflow_text
    assert "test-lineage.json" not in workflow_text
    assert "baseline-preflight" not in workflow_text
    assert "verify-transition" not in workflow_text
    assert "validate-lineage" not in workflow_text
    assert "decide-mode" not in workflow_text
    assert "collect" in workflow_text
    assert "cell-evidence" in workflow_text
    assert "aggregate" in workflow_text
    assert jobs["cross-platform-validation"]["strategy"]["matrix"] == {
        "os": ["ubuntu-latest", "macos-latest", "windows-latest"],
        "python-version": ["3.11", "3.12", "3.13", "3.14"],
    }
    assert "windows-shell-smoke" in jobs
    assert "fast-gate" in jobs


def test_compatibility_gate_pull_request_executes_merge_commit() -> None:
    workflow_text = (_WORKFLOWS_DIR / "compatibility-gate.yml").read_text(
        encoding="utf-8"
    )
    workflow = yaml.safe_load(workflow_text)

    merge_candidate_ref = "inputs.candidate_ref || github.sha"
    checkout_steps = [
        step
        for job in workflow["jobs"].values()
        for step in job.get("steps", [])
        if str(step.get("uses", "")).startswith("actions/checkout@")
    ]
    assert checkout_steps
    assert all(step["with"]["ref"] == f"${{{{ {merge_candidate_ref} }}}}" for step in checkout_steps)
    assert "github.event.pull_request.head.sha" not in workflow_text


def test_release_build_preserves_legacy_tags_and_requires_future_assurance() -> None:
    workflow_path = _WORKFLOWS_DIR / "release-build.yml"
    workflow_text = workflow_path.read_text(encoding="utf-8")
    workflow = yaml.safe_load(workflow_text)
    jobs = workflow["jobs"]

    assert workflow["env"]["CURRENT_RELEASE_TAG"] == "v1.0.5"
    assert workflow["env"]["RELEASE_BOOTSTRAP_ENABLED"] == "false"
    assert "default: v1.0.5" in workflow_text
    assert "v1.0.1 v1.0.2" not in workflow_text
    assert "outputs" not in jobs["release-assurance-policy"]
    assert jobs["release-assurance"] == {
        "needs": "release-assurance-policy",
        "uses": "./.github/workflows/compatibility-gate.yml",
        "with": {
            "candidate_ref": "${{ github.sha }}",
            "force_full": True,
        },
    }
    assert "authority_ref" not in workflow_text
    build_job = jobs["build-smoke-candidate"]
    assert build_job["needs"] == [
        "release-assurance-policy",
        "release-assurance",
    ]
    assert "always()" in build_job["if"]
    assert "needs.release-assurance-policy.result == 'success'" in build_job["if"]
    assert "needs.release-assurance.result == 'success'" in build_job["if"]
    assert jobs["release-qualification"]["needs"] == [
        "release-assurance-policy",
        "release-assurance",
        "build-smoke-candidate",
    ]
    assert jobs["build-release-proof"]["needs"] == ["release-qualification"]
    assert jobs["publish-release"]["needs"] == ["build-release-proof"]
    policy_steps = [
        step.get("name") for step in jobs["release-assurance-policy"]["steps"]
    ]
    enablement_index = policy_steps.index("Require future release generation enablement")
    namespace_index = policy_steps.index(
        "Require admission namespaces absent before qualification"
    )
    detector_index = policy_steps.index(
        "Detect duplicate actual release dispatch in trusted Actions history"
    )
    assert enablement_index < namespace_index < detector_index
    convention = (
        _REPO_ROOT / "docs" / "框架自迭代开发与发布约定.md"
    ).read_text(encoding="utf-8")
    assert "Actions history duplicate-run detector" in convention
    assert "retention and no-delete trust boundary" in convention
    assert "not an immutable authority" in convention
    assert "protected tag namespace becomes the durable burn authority" in convention


def test_candidate_ci_helper_is_not_packaged_for_ordinary_users() -> None:
    pyproject = (_REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")

    assert "scripts/ci_static_assurance.py" not in pyproject
    assert (_REPO_ROOT / ".github" / "ci" / "fast-gate-tests.txt").is_file()
    assert not (_REPO_ROOT / ".github" / "ci" / "test-baseline.json").exists()
    assert not (_REPO_ROOT / ".github" / "ci" / "test-lineage.json").exists()


def test_github_workflows_use_node24_compatible_core_actions() -> None:
    legacy_actions = {
        "actions/checkout@v4",
        "actions/setup-python@v5",
    }

    for workflow_path in sorted(_WORKFLOWS_DIR.glob("*.yml")):
        workflow = workflow_path.read_text(encoding="utf-8")
        for legacy_action in legacy_actions:
            assert legacy_action not in workflow, (
                f"{workflow_path.relative_to(_REPO_ROOT)} still uses {legacy_action}"
            )

    closure_paths = (
        _WORKFLOWS_DIR / "release-build.yml",
        _WORKFLOWS_DIR / "compatibility-gate.yml",
    )
    release_build = closure_paths[0].read_text(encoding="utf-8")
    audited_pins = {
        "actions/checkout": "d23441a48e516b6c34aea4fa41551a30e30af803",
        "actions/setup-python": "ece7cb06caefa5fff74198d8649806c4678c61a1",
        "astral-sh/setup-uv": "37802adc94f370d6bfd71619e3f0bf239e1f3b78",
        "actions/upload-artifact": "043fb46d1a93c77aae656e7c1c64a875d1fc6a0a",
        "actions/download-artifact": "37930b1c2abaa49bbe596cd826c3c89aef350131",
        "actions/attest": "1e69f48acb82d1966a394da916b4c1698aa569d6",
    }
    for action, commit_sha in audited_pins.items():
        assert f"uses: {action}@{commit_sha}" in release_build
        assert not re.search(rf"uses: {re.escape(action)}@v\\d+", release_build)
    for workflow_path in closure_paths:
        workflow = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
        external_uses = [
            step["uses"]
            for job in workflow["jobs"].values()
            if isinstance(job, dict)
            for step in job.get("steps", [])
            if isinstance(step, dict)
            and isinstance(step.get("uses"), str)
            and not step["uses"].startswith("./")
        ]
        assert external_uses
        assert all(re.fullmatch(r"[^@]+@[0-9a-f]{40}", use) for use in external_uses)
