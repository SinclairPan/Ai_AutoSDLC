"""Repository release identity must expose one current 1.0.1 truth."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_package_and_source_fallback_versions_are_1_0_1() -> None:
    metadata = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert metadata["project"]["version"] == "1.0.1"
    assert '__version__ = "1.0.1"' in (
        REPO_ROOT / "src" / "ai_sdlc" / "__init__.py"
    ).read_text(encoding="utf-8")
    assert '__version__ = "1.0.1"' in (
        REPO_ROOT / "ai_sdlc" / "__init__.py"
    ).read_text(encoding="utf-8")


def test_release_workflow_defaults_target_v1_0_1() -> None:
    workflows = (
        "release-artifact-smoke.yml",
        "release-build.yml",
        "windows-user-guide-e2e.yml",
    )

    for name in workflows:
        text = (REPO_ROOT / ".github" / "workflows" / name).read_text(
            encoding="utf-8"
        )
        assert "v1.0.1" in text, name


def test_stable_git_install_examples_pin_v1_0_1() -> None:
    for name in ("README.md", "USER_GUIDE.zh-CN.md"):
        text = (REPO_ROOT / name).read_text(encoding="utf-8")
        assert "git+https://github.com/SinclairPan/Ai_AutoSDLC.git@v1.0.1" in text
        assert "@main" in text
        assert "开发版" in text


def test_stable_source_checkout_examples_pin_v1_0_1() -> None:
    stable_clone = (
        "git clone --branch v1.0.1 --depth 1 "
        "https://github.com/SinclairPan/Ai_AutoSDLC.git"
    )
    for name in ("README.md", "USER_GUIDE.zh-CN.md", "packaging/offline/README.md"):
        text = (REPO_ROOT / name).read_text(encoding="utf-8")
        assert stable_clone in text, name


def test_user_guide_requires_complete_offline_integrity_verification() -> None:
    text = (REPO_ROOT / "USER_GUIDE.zh-CN.md").read_text(encoding="utf-8")

    for marker in (
        "--require-bundled-runtime",
        "--require-checksums",
        "--expected-package-version 1.0.1",
        "--archive-checksum",
    ):
        assert marker in text


def test_post_install_offline_example_allows_installer_created_files() -> None:
    text = (REPO_ROOT / "packaging/offline/README.md").read_text(encoding="utf-8")
    match = re.search(
        r"安装 smoke 后补充安装日志：\n\n```powershell\n(?P<command>.*?)\n```",
        text,
        re.DOTALL,
    )

    assert match is not None
    command = match.group("command")
    assert "--install-log" in command
    assert "--expected-package-version 1.0.1" in command
    assert "--archive-checksum" in command
    assert "--require-checksums" not in command
