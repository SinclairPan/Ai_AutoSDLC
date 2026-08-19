"""Installed-package contract for the intentionally small review product."""

from __future__ import annotations

import tarfile
import zipfile
from pathlib import Path

import packaging_backend
from typer.testing import CliRunner

from ai_sdlc.cli.main import app


def test_wheel_and_sdist_ship_only_the_retained_review_runtime(tmp_path: Path) -> None:
    wheel_path = tmp_path / packaging_backend.build_wheel(str(tmp_path))
    sdist_path = tmp_path / packaging_backend.build_sdist(str(tmp_path))

    with zipfile.ZipFile(wheel_path) as archive:
        wheel_names = set(archive.namelist())
    with tarfile.open(sdist_path, "r:gz") as archive:
        sdist_names = set(archive.getnames())

    for names in (wheel_names, sdist_names):
        assert any(name.endswith("/core/review_kernel.py") for name in names)
        assert any(name.endswith("/core/slimming_advice.py") for name in names)
        assert any(name.endswith("/core/pr_review_provider.py") for name in names)
        assert any(name.endswith("/adapters/codex/AI-SDLC.md") for name in names)
        assert not any("/core/stage_review/" in name for name in names)
        assert not any("/core/lean_code" in name for name in names)
        assert not any(name.endswith("/rules/lean-code.md") for name in names)
        assert not any("stage-gate-activation-policy" in name for name in names)


def test_fresh_init_installs_minimal_review_guidance_without_legacy_state(
    tmp_path: Path,
) -> None:
    result = CliRunner().invoke(
        app,
        [
            "init",
            str(tmp_path),
            "--agent-target",
            "codex",
            "--shell",
            "powershell",
        ],
    )

    assert result.exit_code == 0, result.output
    guidance = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    assert "五类结果的内置动态专家复核" in guidance
    assert "expert_roles" in guidance
    assert "每个角色启动一个全新且只读的独立上下文" in guidance
    assert "ai-sdlc loop review-record" in guidance
    assert "不得要求用户手动触发专家" in guidance
    assert "review-outcome-round-2.json" in guidance
    assert "只提供建议" in guidance
    assert "Local PR Review" in guidance
    for retired in (
        "StageReviewSession",
        "FindingLedger",
        "StageCloseCertificate",
        "lean-check",
        "lean-verify",
        "lean-regression",
        "ci-certificate",
    ):
        assert retired not in guidance

    for retired_path in (
        ".ai-sdlc/sessions",
        ".ai-sdlc/certificates",
        ".ai-sdlc/attestations",
        ".ai-sdlc/activation",
        ".ai-sdlc/policies/stage-gate-activation-policy.json",
    ):
        assert not (tmp_path / retired_path).exists()
