from __future__ import annotations

from pathlib import Path

import pytest

from ai_sdlc.core.stage_review.optimization.runtime import (
    _run_bounded_optimization_maintenance as run_bounded_optimization_maintenance,
)
from ai_sdlc.core.stage_review.optimization.runtime import (
    build_optimization_runtime,
)


def test_runtime_bootstraps_one_deterministic_baseline(tmp_path: Path) -> None:
    first = build_optimization_runtime(
        tmp_path,
        clock=lambda: "2026-07-22T12:00:00Z",
    )
    second = build_optimization_runtime(
        tmp_path,
        clock=lambda: "2026-07-22T13:00:00Z",
    )

    assert first.project_id == second.project_id
    assert (
        first.snapshots.resolve_snapshot().active_snapshot_digest
        == second.snapshots.resolve_snapshot().active_snapshot_digest
    )


def test_maintenance_without_session_threshold_is_non_blocking(tmp_path: Path) -> None:
    result = run_bounded_optimization_maintenance(tmp_path)

    assert result.result_code == "not_ready"
    assert result.epoch is None


def test_phase_one_never_bootstraps_offline_optimization(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden(_root: Path) -> object:
        raise AssertionError("phase one must not build optimization runtime")

    monkeypatch.setattr(
        "ai_sdlc.core.stage_review.optimization.runtime.build_optimization_runtime",
        forbidden,
    )

    result = run_bounded_optimization_maintenance(tmp_path)

    assert result.result_code == "not_ready"
    assert not (tmp_path / ".ai-sdlc").exists()


def test_idle_maintenance_compacts_existing_snapshot_control_storage(
    tmp_path: Path,
) -> None:
    runtime = build_optimization_runtime(tmp_path)
    storage = runtime.snapshots.store.storage
    for index in range(64):
        with storage.commit_leases.acquire(
            owner_id=f"writer.runtime-{index}",
            scope="snapshot_control",
            expected_head=f"head.{index}",
        ) as lease:
            storage.append(
                "snapshot-control",
                {"index": index, "padding": "x" * 200},
                keys={"operation_id": f"operation.runtime-{index}"},
                lease=lease,
            )
    assert storage.manifest().revision == 0

    result = run_bounded_optimization_maintenance(tmp_path)

    assert result.result_code == "not_ready"
    assert storage.manifest().revision == 1
    assert not tuple(storage.loose_root.rglob("*.json"))
