"""Regression checks for repository GitHub Actions workflows."""

from __future__ import annotations

import ast
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


def test_cross_platform_core_runs_clean_user_stage_gate_on_three_platforms() -> None:
    workflow_path = _WORKFLOWS_DIR / "cross-platform-core.yml"
    workflow = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))

    matrix = workflow["jobs"]["core-smoke"]["strategy"]["matrix"]
    assert matrix["os"] == ["ubuntu-latest", "macos-latest", "windows-latest"]
    smoke = workflow_path.read_text(encoding="utf-8")
    assert "tests/e2e/test_clean_user_stage_gate.py" in smoke


def test_ci_certificate_workflow_is_read_only_and_cross_platform() -> None:
    workflow_path = _WORKFLOWS_DIR / "ci-certificate.yml"
    workflow = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))

    matrix = workflow["jobs"]["verify"]["strategy"]["matrix"]
    assert matrix["os"] == ["ubuntu-latest", "macos-latest", "windows-latest"]
    content = workflow_path.read_text(encoding="utf-8")
    assert "fetch-depth: 0" in content
    assert "persist-credentials: false" in content
    assert "test_stage_review_attestation.py" in content
    assert "CI Certificate Gate" in content
    assert "contents: read" in content
    assert "codex" not in content.lower()
    assert "\n    paths:" not in content
    assert "pull_request_target:" in content
    assert "\n  pull_request:\n" not in content
    assert "workflow_dispatch:" not in content
    assert "Checkout trusted verifier from protected base" in content
    assert "Checkout untrusted Candidate as data only" in content
    assert "path: trusted-verifier" in content
    assert "path: candidate" in content
    assert "working-directory: trusted-verifier" in content
    assert "working-directory: candidate" not in content


def test_ci_certificate_workflow_verifies_the_exact_pr_head_bundle() -> None:
    content = (_WORKFLOWS_DIR / "ci-certificate.yml").read_text(encoding="utf-8")

    assert "repository: ${{ github.event.pull_request.head.repo.full_name" in content
    assert "ref: ${{ github.event.pull_request.head.sha || github.sha }}" in content
    assert "ref: ${{ github.event.pull_request.base.sha || github.sha }}" in content
    assert "$candidateRoot" in content
    assert "--root $candidateRoot" in content
    assert 'Get-ChildItem ".ai-sdlc/state/stage-review"' not in content
    assert ".ai-sdlc/attestations/ci-certificate-bundle.json" in content
    assert "verify stage-certificate-policy" in content
    assert "certificate_required" in content
    assert "Current Candidate certificate bundle verification failed" in content
    assert "Certificate is not required for this Shadow Candidate" in content
    assert "verify stage-certificate" in content
    assert "--tested-commit $testedCommit" in content
    assert (
        "git -C $candidateRoot status --porcelain=v1 "
        "--untracked-files=all --ignored=matching" in content
    )
    assert "CI certificate verification changed the Candidate checkout" in content
    assert content.count("uv sync --locked") == 1
    assert "uv sync --locked --project" not in content


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
    assert "default: v1.0.2" in workflow
    assert "gh release download" in workflow
    assert "windows-latest" in workflow
    assert "macos-latest" in workflow
    assert "ubuntu-latest" in workflow
    assert "ref: ${{ github.event.release.tag_name || inputs.tag }}" in workflow
    assert "ai-sdlc-offline-$releaseVersion-windows-$env:RELEASE_ASSET_MACHINE.zip" in workflow
    assert "ai-sdlc-offline-${release_version}-${RELEASE_ASSET_OS}-${RELEASE_ASSET_MACHINE}.tar.gz" in workflow
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


def test_release_build_workflow_matrix_builds_smokes_and_uploads_assets() -> None:
    workflow_path = _WORKFLOWS_DIR / "release-build.yml"

    assert workflow_path.is_file()

    workflow = workflow_path.read_text(encoding="utf-8")

    assert "workflow_dispatch:" in workflow
    assert "default: v1.0.2" in workflow
    assert "ref: ${{ inputs.tag }}" in workflow
    assert 'git rev-parse "${RELEASE_TAG}^{commit}"' in workflow
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
    assert "actions/upload-artifact@v7" in workflow
    assert "name: release-candidate-${{ github.run_id }}-${{ github.run_attempt }}" in workflow
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


def test_release_build_emergency_freeze_removes_release_write_authority() -> None:
    workflow = (_WORKFLOWS_DIR / "release-build.yml").read_text(encoding="utf-8")

    assert "permissions:\n  contents: read" in workflow
    assert "upload_to_release:" not in workflow
    assert "GH_TOKEN:" not in workflow
    assert "gh release " not in workflow
    assert "--clobber" not in workflow
    assert (
        "name: release-build-${{ github.run_id }}-${{ github.run_attempt }}-"
        "${{ matrix.asset_os }}-${{ matrix.archive }}-evidence"
    ) in workflow
    assert "name: release-candidate-${{ github.run_id }}-${{ github.run_attempt }}" in workflow
    assert "publish_blocked" in workflow
    assert "writer_isolation_unavailable" in workflow


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
    assert "ai-sdlc-offline-1.0.2-windows-amd64" in workflow
    assert "releases/download/v1.0.2" in workflow
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
    assert 'dist-offline/${PACKAGE_NAME}' in workflow
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
    assert "shutil.which(\"codex\")" in driver
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
        'git ls-remote https://github.com/SinclairPan/Ai_AutoSDLC.git '
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


def test_windows_online_job_runs_real_installed_lean_user_flow() -> None:
    workflow = (_WORKFLOWS_DIR / "windows-user-guide-e2e.yml").read_text(
        encoding="utf-8"
    )
    driver_path = _REPO_ROOT / "scripts" / "windows_lean_code_e2e.py"

    assert driver_path.is_file()
    driver = driver_path.read_text(encoding="utf-8")
    assert "Run the installed Lean Code user journey" in workflow
    assert "windows_lean_code_e2e.py" in workflow
    assert '      - "scripts/windows_lean_code_e2e_support.py"' in workflow
    assert "windows-clean-online-user-e2e-evidence" in workflow
    adjacent_cli_tokens: set[tuple[str, str]] = set()
    for node in ast.walk(ast.parse(driver)):
        if not isinstance(node, (ast.List, ast.Tuple)):
            continue
        values = [
            item.value
            if isinstance(item, ast.Constant) and isinstance(item.value, str)
            else ""
            for item in node.elts
        ]
        adjacent_cli_tokens.update(zip(values, values[1:], strict=False))
    assert {
        ("requirement", "start"),
        ("requirement", "freeze"),
        ("design-contract", "check"),
        ("design-contract", "close"),
        ("implementation", "start"),
        ("implementation", "record"),
        ("implementation", "lean-verify"),
        ("implementation", "lean-regression"),
        ("implementation", "lean-check"),
        ("implementation", "close"),
    } <= adjacent_cli_tokens
    assert "src/订单.py" in driver
    assert "ai_sdlc.core" not in driver


def test_posix_offline_smoke_matrix_concurrency_is_job_scoped() -> None:
    workflow_path = _WORKFLOWS_DIR / "posix-offline-smoke.yml"

    workflow = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))

    assert "concurrency" not in workflow
    assert workflow["jobs"]["smoke"]["concurrency"] == {
        "group": "posix-offline-smoke-${{ github.event.pull_request.number || github.ref }}-${{ matrix.os }}",
        "cancel-in-progress": True,
    }


def test_reviewer_isolation_workflow_requires_real_mode_specific_evidence() -> None:
    workflow_path = _WORKFLOWS_DIR / "reviewer-isolation.yml"

    assert workflow_path.is_file()
    workflow = workflow_path.read_text(encoding="utf-8")
    for platform in ("ubuntu-latest", "macos-latest", "windows-latest"):
        assert workflow.count(f"os: {platform}") == 3
    assert workflow.count("mode: ordinary-fail-closed") == 3
    assert workflow.count("mode: required-enforced") == 2
    assert workflow.count("mode: required-unavailable") == 1
    assert workflow.count("mode: detected-only") == 3
    assert "codex_version: 0.137.0" in workflow
    assert workflow.count("codex_version: 0.138.0") == 6
    assert "AI_SDLC_CODEX_PREFIX=$codexPrefix" in workflow
    assert '$codexPrefix = "/usr/local/share/ai-sdlc-codex-backend"' in workflow
    assert 'if ("${{ runner.os }}" -eq "Linux")' in workflow
    assert "sudo chown -R $env:USER $codexPrefix" in workflow
    assert '$codexPrefix = Join-Path $env:RUNNER_TEMP "codex-backend"' in workflow
    assert "npm install --prefix $env:AI_SDLC_CODEX_PREFIX" in workflow
    assert "npm audit signatures --prefix $env:AI_SDLC_CODEX_PREFIX --json" in workflow
    assert "codex-npm-audit-signatures.json" in workflow
    assert "codex-npm-registry-attestations.json" in workflow
    assert "verify_published_codex_npm_attestations" in workflow
    assert "AI_SDLC_CODEX_NPM_ATTESTATIONS=$registryPath" in workflow
    assert "published_codex_release; release = published_codex_release()" in workflow
    assert "print(release.package_version)" in workflow
    assert (
        "trusted_published_codex_release; release = trusted_published_codex_release()"
        not in workflow
    )
    assert "codex-npm-provenance-verification.json" in workflow
    assert "codex.npm-pinned-provenance-unverified" in workflow
    assert "npm_provenance_verified" in workflow
    assert "kernel.apparmor_restrict_unprivileged_userns=0" in workflow
    assert "sudo apt-get install --yes bubblewrap musl" in workflow
    assert "AI_SDLC_LINUX_NAMESPACE_PREPARED=1" in workflow
    assert "linux_namespace_prepared" in workflow
    assert "t601-unit-junit.xml" in workflow
    assert "t601-e2e-junit.xml" in workflow
    assert "tests/unit/stage_review/test_codex_isolation_probe.py" in workflow
    assert "Get-ChildItem $pytestRoot -Recurse -Force -File" in workflow
    assert "$document.testsuites.testsuite" in workflow
    assert "Measure-Object -Property tests -Sum" in workflow
    assert "--junitxml" in workflow
    assert "-W error" in workflow
    assert "junit.e2e.unexpected-test-count" in workflow
    assert "ordinary-mode-started-or-attested-provider" in workflow
    assert "required-mode-egress-lineage-count" in workflow
    assert "required-mode-transport-claim-invalid" in workflow
    assert "required-unavailable-started-provider-command" in workflow
    assert "required-unavailable-proof-missing" in workflow
    assert "detected-only-started-provider-command" in workflow
    assert "detected-only-stage-lineage-invalid" in workflow
    assert 'artifact_kind = "reviewer-isolation-ci-evidence"' in workflow
    assert 'expectedTestedCommit = "${{ github.sha }}"' in workflow
    assert (
        'candidateHeadCommit = "${{ github.event.pull_request.head.sha || github.sha }}"'
        in workflow
    )
    assert 'baseCommit = "${{ github.event.pull_request.base.sha }}"' in workflow
    assert "reviewed_commit = $testedCommit" in workflow
    assert "tested_commit = $testedCommit" in workflow
    assert "candidate_head_commit = $candidateHeadCommit" in workflow
    assert "base_commit = $baseCommit" in workflow
    assert "workflow.tested-commit-identity-mismatch" in workflow
    assert "execution_evidence_root_digest" in workflow
    assert "transport_contract_attested" in workflow
    assert "remote_provider_exercised" in workflow
    assert "actions/attest-build-provenance@v2" not in workflow
    assert "reviewer-isolation-gate:" in workflow
    assert "name: Reviewer Isolation Gate" in workflow
    assert "needs: isolation" in workflow
    assert '"${{ needs.isolation.result }}" -ne "success"' in workflow
    assert "--ignore" not in workflow
    assert "pytest.mark.skip" not in workflow
    assert "pytest.mark.xfail" not in workflow


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
    jobs = workflow["jobs"]
    assert jobs["fast-gate"]["runs-on"] == "ubuntu-latest"
    assert jobs["cross-platform-validation"]["strategy"]["matrix"] == {
        "os": ["ubuntu-latest", "macos-latest", "windows-latest"],
        "python-version": ["3.11", "3.12", "3.13", "3.14"],
    }
    assert (
        jobs["cross-platform-validation"]["if"]
        == "needs.authority-check.outputs.full-assurance-required == 'true'"
    )
    assert (
        jobs["windows-shell-smoke"]["if"]
        == "needs.authority-check.outputs.full-assurance-required == 'true'"
    )
    assert jobs["merge-assurance"]["if"] == "always()"
    assert jobs["compatibility-gate-result"]["name"] == "Compatibility Gate Result"


def test_compatibility_gate_uses_protected_base_authority_and_exact_artifacts() -> None:
    workflow = (_WORKFLOWS_DIR / "compatibility-gate.yml").read_text(
        encoding="utf-8"
    )

    assert "Checkout protected base authority" in workflow
    assert "path: trusted-base" in workflow
    assert "trusted-base/scripts/ci_static_assurance.py" in workflow
    assert "authority_unavailable" in workflow
    assert "protected_ci_change" in workflow
    assert 'assurance_script="scripts/ci_static_assurance.py"' in workflow
    assert 'assurance_lineage=".github/ci/test-lineage.json"' in workflow
    assert 'assurance_lineage="trusted-base/.github/ci/test-lineage.json"' in workflow
    assert 'assurance_baseline=".github/ci/test-baseline.json"' in workflow
    assert 'assurance_baseline="trusted-base/.github/ci/test-baseline.json"' not in workflow
    assert '"${assurance_script}" collect' in workflow
    assert '"${ASSURANCE_SCRIPT}" cell-evidence' in workflow
    assert '--baseline "${ASSURANCE_BASELINE}"' in workflow
    assert 'python "${assurance_script}" aggregate' in workflow
    assert '--lineage "${assurance_lineage}"' in workflow
    assert "verify-transition" in workflow
    assert "--ignore=tests/e2e/stage_review" in workflow
    assert "--junitxml=ci-evidence/${CELL}/compatibility-results.xml" in workflow
    assert "actions/upload-artifact@v7" in workflow
    assert "if-no-files-found: error" in workflow
    assert "--maxfail" not in workflow
    assert "continue-on-error" not in workflow

    parsed = yaml.safe_load(workflow)
    merge_steps = parsed["jobs"]["merge-assurance"]["steps"]
    assert all(
        "uv run python" not in str(step.get("run", "")) for step in merge_steps
    )
    gate_script = merge_steps[0]["run"]
    assert "needs.cross-platform-validation.result" in gate_script


def test_release_build_requires_reusable_full_release_assurance() -> None:
    workflow = yaml.safe_load(
        (_WORKFLOWS_DIR / "release-build.yml").read_text(encoding="utf-8")
    )

    assert workflow["jobs"]["release-assurance"] == {
        "uses": "./.github/workflows/compatibility-gate.yml",
        "with": {"candidate_ref": "${{ inputs.tag }}", "force_full": True},
    }
    assert workflow["jobs"]["build-smoke-candidate"]["needs"] == "release-assurance"


def test_static_ci_authority_is_not_packaged_for_ordinary_users() -> None:
    pyproject = (_REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")

    assert "scripts/ci_static_assurance.py" not in pyproject
    assert (_REPO_ROOT / ".github" / "ci" / "fast-gate-tests.txt").is_file()
    assert (_REPO_ROOT / ".github" / "ci" / "test-baseline.json").is_file()


def test_activation_evidence_workflow_owns_its_trust_root_and_real_inputs() -> None:
    workflow_path = _WORKFLOWS_DIR / "activation-evidence.yml"

    assert workflow_path.is_file()
    workflow = workflow_path.read_text(encoding="utf-8")
    assert "pull_request:" in workflow
    assert "push:" in workflow
    assert "branches: [main]" in workflow
    assert "uses: ./.github/workflows/reviewer-isolation.yml" in workflow
    assert "workflow_call:" in (_WORKFLOWS_DIR / "reviewer-isolation.yml").read_text(
        encoding="utf-8"
    )
    assert "artifact-metadata: write" in workflow
    assert "actions/download-artifact@v7" in workflow
    assert "actions/upload-artifact@v6" in workflow
    assert "actions/attest@v4" in workflow
    assert (
        "if: github.event_name == 'push' && github.ref == 'refs/heads/main'" in workflow
    )
    assert "name: Activation Evidence Required Gate" in workflow
    assert "activation-evidence-required:" in workflow
    assert "if: always()" in workflow
    assert (
        "needs: [reviewer-isolation, probe-evidence, activation-evidence-build]"
        in workflow
    )
    assert '"${{ needs.reviewer-isolation.result }}"' in workflow
    assert '"${{ needs.probe-evidence.result }}"' in workflow
    assert '"${{ needs.activation-evidence-build.result }}"' in workflow
    assert (
        "subject-path: activation-evidence/activation-evidence-package.json" in workflow
    )
    assert "AI_SDLC_ACTIVATION_EVIDENCE_PURPOSE: stage-gate-activation" in workflow
    assert (
        "AI_SDLC_ACTIVATION_PREDICATE_TYPE: https://slsa.dev/provenance/v1" in workflow
    )
    assert "scripts/build_activation_evidence.py" in workflow
    assert "scripts/build_activation_quality_cell.py" in workflow
    assert "tests/integration/test_cli_activation.py" in workflow
    assert "${{ inputs." not in workflow
    assert "activation-evidence-package.json" in workflow


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
