from __future__ import annotations

import os
import stat
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from ai_sdlc.benefit_directional_demo import (
    build_directional_provider_profile,
    directional_protected_roots,
    load_directional_manifest,
    read_attempt_ledger,
    run_fake_rehearsal,
    verify_prepared_directional_arm,
)


def test_real_15_workspace_fake_provider_rehearsal(tmp_path: Path) -> None:
    manifest = load_directional_manifest()
    result = run_fake_rehearsal(
        manifest,
        workspace_root=tmp_path / "workspaces",
        output_root=tmp_path / "output",
        materialize_arms=True,
    )
    assert result.prepared_workspaces == 15
    assert result.simulated_sessions == 19
    assert result.external_provider_calls == 0
    assert len(result.prepared_arms) == 15
    assert result.input_tokens is None
    assert result.output_tokens is None
    assert result.currency_cost is None
    rows = read_attempt_ledger(result.ledger_path)
    assert len([row for row in rows if row["kind"] == "reservation"]) == 19
    assert len([row for row in rows if row["kind"] == "expert-finding"]) == 4
    assert len([row for row in rows if row["kind"] == "writer-resume"]) == 3
    for prepared in result.prepared_arms:
        verify_prepared_directional_arm(prepared)
        assert stat.S_IMODE(prepared.root.lstat().st_mode) == 0o700
        assert (prepared.root / ".git").is_dir()
        assert prepared.provider_cwd == prepared.root / "benchmark-task"
        assert Path(prepared.subprocess_cwd).samefile(prepared.provider_cwd)
        assert prepared.environment.provider_attempts_started == 0
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=prepared.root,
            env=prepared.environment.environment,
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        assert status == ""
        assert Path(prepared.environment.environment["HOME"]).is_dir()
        assert Path(prepared.environment.environment["CODEX_HOME"]).is_dir()
        assert ".venv" not in prepared.environment.environment["PATH"]
        assert prepared.environment.environment["GIT_CONFIG_GLOBAL"] == "/dev/null"
        assert prepared.environment.environment["GIT_CONFIG_NOSYSTEM"] == "1"
    by_fixture: dict[str, set[str]] = {}
    for prepared in result.prepared_arms:
        by_fixture.setdefault(prepared.fixture_id, set()).add(prepared.prompt_sha256)
    assert {fixture: len(digests) for fixture, digests in by_fixture.items()} == {
        fixture: 1 for fixture in manifest.fixture_ids
    }
    assert len({item.base_global_sha256 for item in result.prepared_arms}) == 1
    assert (
        sum(
            item.framework_init.provider_attempts_started
            for item in result.prepared_arms
        )
        == 0
    )
    assert "OPENAI_API_KEY" not in os.environ or result.external_provider_calls == 0
    actual_protected = directional_protected_roots()
    for prepared in result.prepared_arms:
        other_runs = tuple(
            item.root for item in result.prepared_arms if item.root != prepared.root
        )
        profile = build_directional_provider_profile(
            prepared,
            output_root=result.ledger_path.parent,
            other_run_roots=other_runs,
        )
        assert profile.issues == ()
        assert profile.preserve_environment is True
        assert profile.environment == prepared.environment.environment
        assert all(str(item.path) in profile.sandbox_text for item in actual_protected)
        assert all(str(root) in profile.sandbox_text for root in other_runs)
        assert str(prepared.root / ".git") in profile.sandbox_text
    attacked = result.prepared_arms[0]
    with pytest.raises(ValueError, match="cwd"):
        verify_prepared_directional_arm(
            replace(attacked, subprocess_cwd=str(attacked.root))
        )
    dirty = attacked.provider_cwd / ".directional-dirty-attack"
    dirty.write_text("attack", encoding="utf-8")
    try:
        with pytest.raises(ValueError):
            build_directional_provider_profile(
                attacked, output_root=result.ledger_path.parent
            )
    finally:
        dirty.unlink()
    plugin = Path(attacked.environment.environment["CODEX_HOME"]) / "plugins"
    plugin.mkdir()
    try:
        with pytest.raises(ValueError, match="contaminated"):
            verify_prepared_directional_arm(attacked)
    finally:
        plugin.rmdir()
    verify_prepared_directional_arm(attacked)
