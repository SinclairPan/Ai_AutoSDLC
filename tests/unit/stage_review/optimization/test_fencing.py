from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ai_sdlc.core.stage_review.artifacts import (
    ResourceLockUnavailableError,
    SharedStateIntegrityError,
)
from ai_sdlc.core.stage_review.optimization.commit_fencing import (
    OptimizationCommitLeaseStore,
)


def test_claims_are_strictly_monotonic_and_projection_is_rebuildable(
    tmp_path: Path,
) -> None:
    store = OptimizationCommitLeaseStore(
        tmp_path, project_id="project.shared", lock_timeout_seconds=1
    )
    with store.acquire(
        owner_id="writer.one",
        scope="snapshot_control",
        expected_head="sha256:head-one",
        now=_now(),
    ) as first:
        first.assert_current(now=_now())
    store.projection_path.unlink()
    with store.acquire(
        owner_id="writer.two",
        scope="query_commitment",
        expected_head="sha256:head-two",
        now=_now() + timedelta(seconds=1),
    ) as second:
        second.assert_current(now=_now() + timedelta(seconds=1))

    assert first.claim.fencing_epoch == 1
    assert second.claim.fencing_epoch == 2
    assert second.claim.previous_claim_digest == first.claim.claim_digest
    assert store.high_watermark() == (2, second.claim.claim_digest)


def test_released_or_expired_writer_cannot_commit(tmp_path: Path) -> None:
    store = OptimizationCommitLeaseStore(
        tmp_path, project_id="project.shared", lock_timeout_seconds=1
    )
    with store.acquire(
        owner_id="writer.one",
        scope="snapshot_control",
        expected_head="sha256:head",
        now=_now(),
        lease_seconds=1,
    ) as handle, pytest.raises(SharedStateIntegrityError, match="expired"):
        handle.assert_current(now=_now() + timedelta(seconds=2))

    with pytest.raises(SharedStateIntegrityError, match="mutex"):
        handle.assert_current(now=_now())


def test_second_writer_cannot_mint_claim_until_mutex_is_released(
    tmp_path: Path,
) -> None:
    owner = OptimizationCommitLeaseStore(
        tmp_path, project_id="project.shared", lock_timeout_seconds=1
    )
    contender = OptimizationCommitLeaseStore(
        tmp_path, project_id="project.shared", lock_timeout_seconds=0.05
    )
    attempted = threading.Event()
    result: list[str] = []

    def compete() -> None:
        attempted.set()
        try:
            with contender.acquire(
                owner_id="writer.two",
                scope="compaction",
                expected_head="sha256:head",
                now=_now(),
            ):
                result.append("acquired")
        except ResourceLockUnavailableError:
            result.append("busy")

    with owner.acquire(
        owner_id="writer.one",
        scope="snapshot_control",
        expected_head="sha256:head",
        now=_now(),
    ):
        thread = threading.Thread(target=compete)
        thread.start()
        attempted.wait(timeout=1)
        thread.join(timeout=1)
        assert result == ["busy"]
        assert owner.high_watermark()[0] == 1

    with contender.acquire(
        owner_id="writer.two",
        scope="compaction",
        expected_head="sha256:head",
        now=_now() + timedelta(seconds=1),
    ) as second:
        assert second.claim.fencing_epoch == 2


def test_missing_or_forked_claim_chain_fails_closed(tmp_path: Path) -> None:
    store = OptimizationCommitLeaseStore(
        tmp_path, project_id="project.shared", lock_timeout_seconds=1
    )
    for index in range(2):
        with store.acquire(
            owner_id=f"writer.{index}",
            scope="snapshot_control",
            expected_head=f"sha256:head-{index}",
            now=_now() + timedelta(seconds=index),
        ):
            pass
    (store.claim_root / "00000000000000000001.json").unlink()

    with pytest.raises(SharedStateIntegrityError, match="claim chain"):
        store.high_watermark()


def test_commit_claim_history_is_checkpointed_and_bounded(tmp_path: Path) -> None:
    store = OptimizationCommitLeaseStore(
        tmp_path,
        project_id="project.shared",
        lock_timeout_seconds=1,
    )
    for index in range(300):
        with store.acquire(
            owner_id=f"writer.{index}",
            scope="snapshot_control",
            expected_head=f"sha256:head-{index}",
            now=_now() + timedelta(seconds=index),
        ):
            pass

    assert store.high_watermark()[0] == 300
    assert store.checkpoint_path.is_file()
    assert len(tuple(store.claim_root.glob("*.json"))) <= 128


def _now() -> datetime:
    return datetime(2026, 7, 22, tzinfo=UTC)
