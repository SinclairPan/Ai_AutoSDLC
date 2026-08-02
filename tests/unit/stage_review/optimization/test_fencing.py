from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ai_sdlc.core.stage_review.artifacts import (
    ResourceLockUnavailableError,
    SharedStateIntegrityError,
)
from ai_sdlc.core.stage_review.optimization import commit_fencing
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


def test_stale_preview_is_rejected_before_any_fencing_write(tmp_path: Path) -> None:
    store = OptimizationCommitLeaseStore(
        tmp_path, project_id="project.shared", lock_timeout_seconds=1
    )
    stale = store.preview_acquire(
        owner_id="writer.stale",
        scope="snapshot_control",
        expected_head="sha256:head-stale",
    )
    authorization = store._authorize_plan(stale)
    with store.acquire(
        owner_id="writer.current",
        scope="snapshot_control",
        expected_head="sha256:head-current",
        now=_now(),
    ):
        pass
    before = _tree_bytes(store.root)

    with (
        pytest.raises(SharedStateIntegrityError, match="plan is stale"),
        store.acquire(
            owner_id=stale.owner_id,
            scope=stale.scope,
            expected_head=stale.expected_head,
            lease_seconds=stale.lease_seconds,
            plan=stale,
            authorization=authorization,
            now=_now() + timedelta(seconds=1),
        ),
    ):
        pass

    assert _tree_bytes(store.root) == before
    assert store.high_watermark()[0] == 1


def test_preview_is_read_only_inside_accounting_root(tmp_path: Path) -> None:
    store = OptimizationCommitLeaseStore(
        tmp_path, project_id="project.shared", lock_timeout_seconds=1
    )
    before = _tree_entries(store.root)

    plan = store.preview_acquire(
        owner_id="writer.preview",
        scope="snapshot_control",
        expected_head="sha256:head-preview",
    )

    assert plan.max_write_bytes > 0
    assert _tree_entries(store.root) == before
    assert store.lock_path.is_file()
    assert store.lock_path.is_relative_to(store.root) is False


def test_rollover_recovers_claim_and_segment_before_checkpoint_crash(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = OptimizationCommitLeaseStore(
        tmp_path, project_id="project.shared", lock_timeout_seconds=1
    )
    _mint_claims(store, 128)
    original = commit_fencing.atomic_write_json
    failed = False

    def fail_checkpoint(path: Path, payload: dict[str, object]) -> None:
        nonlocal failed
        if path == store.checkpoint_path and not failed:
            failed = True
            raise RuntimeError("checkpoint crash")
        original(path, payload)

    monkeypatch.setattr(commit_fencing, "atomic_write_json", fail_checkpoint)
    with (
        pytest.raises(RuntimeError, match="checkpoint crash"),
        store.acquire(
            owner_id="writer.129",
            scope="snapshot_control",
            expected_head="sha256:head-129",
            now=_now() + timedelta(seconds=129),
        ),
    ):
        pass
    assert store.high_watermark()[0] == 129
    monkeypatch.setattr(commit_fencing, "atomic_write_json", original)

    def forbidden_segment_rewrite(_path: Path, _payload: bytes) -> None:
        raise AssertionError("verified orphan segment must not be rewritten")

    monkeypatch.setattr(
        commit_fencing,
        "_create_bytes_idempotent",
        forbidden_segment_rewrite,
    )

    with store.acquire(
        owner_id="writer.130",
        scope="snapshot_control",
        expected_head="sha256:head-130",
        now=_now() + timedelta(seconds=130),
    ) as recovered:
        assert recovered.claim.fencing_epoch == 130
        assert recovered.receipt.actual_write_bytes > 0

    assert store._checkpoint().compacted_through == 128
    assert [int(path.stem) for path in sorted(store.claim_root.glob("*.json"))] == [
        129,
        130,
    ]


def test_rollover_recovers_checkpointed_stale_claims(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = OptimizationCommitLeaseStore(
        tmp_path, project_id="project.shared", lock_timeout_seconds=1
    )
    _mint_claims(store, 128)
    first_path = store.claim_root / "00000000000000000001.json"
    original_unlink = Path.unlink
    failed = False

    def fail_first_cleanup(
        path: Path,
        *args: object,
        **kwargs: object,
    ) -> None:
        nonlocal failed
        if path == first_path and not failed:
            failed = True
            raise OSError("cleanup crash")
        original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_first_cleanup)
    with (
        pytest.raises(OSError, match="cleanup crash"),
        store.acquire(
            owner_id="writer.129",
            scope="snapshot_control",
            expected_head="sha256:head-129",
            now=_now() + timedelta(seconds=129),
        ),
    ):
        pass
    assert store._checkpoint().compacted_through == 128
    assert len(tuple(store.claim_root.glob("*.json"))) == 129
    monkeypatch.setattr(Path, "unlink", original_unlink)

    with store.acquire(
        owner_id="writer.130",
        scope="snapshot_control",
        expected_head="sha256:head-130",
        now=_now() + timedelta(seconds=130),
    ) as recovered:
        assert recovered.claim.fencing_epoch == 130

    assert [int(path.stem) for path in sorted(store.claim_root.glob("*.json"))] == [
        129,
        130,
    ]


def _mint_claims(store: OptimizationCommitLeaseStore, count: int) -> None:
    for index in range(1, count + 1):
        with store.acquire(
            owner_id=f"writer.{index}",
            scope="snapshot_control",
            expected_head=f"sha256:head-{index}",
            now=_now() + timedelta(seconds=index),
        ):
            pass


def _tree_bytes(root: Path) -> int:
    return sum(path.stat().st_size for path in root.rglob("*") if path.is_file())


def _tree_entries(root: Path) -> tuple[tuple[str, int], ...]:
    return tuple(
        (path.relative_to(root).as_posix(), path.stat().st_size)
        for path in sorted(root.rglob("*"))
        if path.is_file()
    ) if root.is_dir() else ()


def _now() -> datetime:
    return datetime(2026, 7, 22, tzinfo=UTC)
