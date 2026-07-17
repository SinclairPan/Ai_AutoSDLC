"""Repository release identity must expose one current 1.0.0 truth."""

from __future__ import annotations

import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_package_and_source_fallback_versions_are_1_0_0() -> None:
    metadata = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert metadata["project"]["version"] == "1.0.0"
    assert '__version__ = "1.0.0"' in (
        REPO_ROOT / "src" / "ai_sdlc" / "__init__.py"
    ).read_text(encoding="utf-8")
    assert '__version__ = "1.0.0"' in (
        REPO_ROOT / "ai_sdlc" / "__init__.py"
    ).read_text(encoding="utf-8")


def test_release_workflow_defaults_target_v1_0_0() -> None:
    workflows = (
        "release-artifact-smoke.yml",
        "release-build.yml",
        "windows-user-guide-e2e.yml",
    )

    for name in workflows:
        text = (REPO_ROOT / ".github" / "workflows" / name).read_text(
            encoding="utf-8"
        )
        assert "v1.0.0" in text, name
