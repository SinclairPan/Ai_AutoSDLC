from __future__ import annotations

import sys
from pathlib import Path

import pytest

from ai_sdlc.benefit_benchmark_arms import prepare_arm
from ai_sdlc.benefit_benchmark_fixtures import prepare_fixture
from ai_sdlc.benefit_directional_demo import (
    build_directional_provider_profile,
    run_directional_system_isolation_canary,
)


@pytest.mark.skipif(sys.platform != "darwin", reason="requires macOS Seatbelt")
def test_exact_directional_profile_denies_actual_surfaces(tmp_path: Path) -> None:
    fixture = prepare_fixture("multi-tenant-security-review", tmp_path / "fixture")
    prepared = prepare_arm(
        "A11",
        fixture,
        tmp_path / "run",
        shared_runtime_root=tmp_path / "shared-runtime",
        environment_root=tmp_path / "environment",
    )
    prepared.root.chmod(0o700)
    output = tmp_path / "output"
    output.mkdir(mode=0o700)
    profile = build_directional_provider_profile(prepared, output_root=output)
    result = run_directional_system_isolation_canary(prepared, profile)
    assert result.passed is True
