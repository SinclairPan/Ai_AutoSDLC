from __future__ import annotations

import json
import multiprocessing
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from tests.unit.stage_review.test_resources import (
    _OWNER,
    _envelope,
    _governor,
    _now,
    _policy,
)

from ai_sdlc.core.stage_review import repo_write_lease_store
from ai_sdlc.core.stage_review.artifacts import (
    ResourceLockUnavailableError,
    SharedStateIntegrityError,
)
from ai_sdlc.core.stage_review.repo_write_lease import (
    RepoWriteLeaseAuthority,
    RepoWriteLeaseConflictError,
    RepoWriteLeaseRequest,
    RepoWriteLeaseStaleError,
    canonical_worktree_identity,
)

_NOW = "2026-07-21T15:00:00Z"
_PROJECT = "project.shared"


def _request(
    root: Path,
    *,
    owner: str,
    path: str = "close/implementation.json",
) -> RepoWriteLeaseRequest:
    return RepoWriteLeaseRequest(
        worktree_identity=canonical_worktree_identity(root),
        stage_review_session_id="session.one",
        protected_path_set=(path,),
        lease_owner=owner,
        idempotency_key=f"lease.{owner}",
        lease_seconds=60,
    )


def test_overlapping_writer_requires_newer_fencing_after_release(
    tmp_path: Path,
) -> None:
    primary = RepoWriteLeaseAuthority(
        tmp_path,
        project_id=_PROJECT,
        clock=lambda: _NOW,
        lock_timeout_seconds=1,
    )
    competitor = RepoWriteLeaseAuthority(
        tmp_path,
        project_id=_PROJECT,
        clock=lambda: _NOW,
        lock_timeout_seconds=0.02,
    )

    with primary.acquire(_request(tmp_path, owner="owner.primary")) as first_guard:
        first = first_guard.lease
        assert first.expected_revision == 0
        assert primary.require_current(first) == first
        with (
            pytest.raises(ResourceLockUnavailableError),
            competitor.acquire(_request(tmp_path, owner="owner.competitor")),
        ):
            raise AssertionError("overlapping writer acquired a held lease")

    with competitor.acquire(
        _request(tmp_path, owner="owner.competitor")
    ) as second_guard:
        second = second_guard.lease
        assert second.fencing_epoch > first.fencing_epoch
        assert second.expected_revision == 2
        assert competitor.require_current(second) == second

    with pytest.raises(RepoWriteLeaseStaleError):
        primary.require_current(first)


def test_disjoint_paths_can_hold_write_leases_concurrently(tmp_path: Path) -> None:
    first = RepoWriteLeaseAuthority(
        tmp_path,
        project_id=_PROJECT,
        clock=lambda: _NOW,
        lock_timeout_seconds=0.02,
    )
    second = RepoWriteLeaseAuthority(
        tmp_path,
        project_id=_PROJECT,
        clock=lambda: _NOW,
        lock_timeout_seconds=0.02,
    )

    with (
        first.acquire(_request(tmp_path, owner="owner.first", path="src/first.py"))
        as held,
        second.acquire(
            _request(tmp_path, owner="owner.second", path="src/second.py")
        )
        as disjoint,
    ):
        assert first.require_current(held.lease) == held.lease
        assert second.require_current(disjoint.lease) == disjoint.lease
        assert disjoint.lease.fencing_epoch > held.lease.fencing_epoch


def test_other_authority_cannot_validate_the_held_lease(tmp_path: Path) -> None:
    holder = RepoWriteLeaseAuthority(
        tmp_path,
        project_id=_PROJECT,
        clock=lambda: _NOW,
    )
    observer = RepoWriteLeaseAuthority(
        tmp_path,
        project_id=_PROJECT,
        clock=lambda: _NOW,
    )

    with holder.acquire(_request(tmp_path, owner="owner.holder")) as guard:
        assert holder.require_current(guard.lease) == guard.lease
        with pytest.raises(RepoWriteLeaseStaleError, match="holder|current"):
            observer.require_current(guard.lease)


@pytest.mark.parametrize(
    ("held_path", "competing_path"),
    (("src", "src/app.py"), ("Src/App.py", "src/app.py")),
)
def test_path_aliases_are_treated_as_overlapping(
    tmp_path: Path,
    held_path: str,
    competing_path: str,
) -> None:
    parent = RepoWriteLeaseAuthority(
        tmp_path,
        project_id=_PROJECT,
        clock=lambda: _NOW,
    )
    child = RepoWriteLeaseAuthority(
        tmp_path,
        project_id=_PROJECT,
        clock=lambda: _NOW,
    )

    with (
        parent.acquire(_request(tmp_path, owner="owner.parent", path=held_path)),
        pytest.raises(RepoWriteLeaseConflictError, match="overlapping"),
        child.acquire(
            _request(tmp_path, owner="owner.child", path=competing_path)
        ),
    ):
        raise AssertionError("child path acquired an overlapping lease")


def test_lease_request_cannot_spoof_worktree_identity(tmp_path: Path) -> None:
    authority = RepoWriteLeaseAuthority(
        tmp_path,
        project_id=_PROJECT,
        clock=lambda: _NOW,
    )
    forged = _request(tmp_path, owner="owner.forged").model_copy(
        update={"worktree_identity": "worktree.forged"}
    )

    with pytest.raises(ValueError, match="worktree identity"):
        authority.acquire(forged)


@pytest.mark.skipif(os.name == "nt", reason="Windows symlink needs host privilege")
def test_symlink_alias_cannot_bypass_path_overlap(tmp_path: Path) -> None:
    (tmp_path / "real").mkdir()
    (tmp_path / "alias").symlink_to(tmp_path / "real", target_is_directory=True)
    first = RepoWriteLeaseAuthority(
        tmp_path,
        project_id=_PROJECT,
        clock=lambda: _NOW,
    )
    second = RepoWriteLeaseAuthority(
        tmp_path,
        project_id=_PROJECT,
        clock=lambda: _NOW,
    )

    with (
        first.acquire(_request(tmp_path, owner="owner.real", path="real/out.json")),
        pytest.raises(RepoWriteLeaseConflictError, match="overlapping"),
        second.acquire(_request(tmp_path, owner="owner.alias", path="alias/out.json")),
    ):
        raise AssertionError("symlink alias acquired an overlapping lease")


def test_resource_and_repo_lease_share_project_fencing_domain(
    tmp_path: Path,
) -> None:
    governor = _governor(tmp_path)
    admission = governor.reserve_admission(
        _envelope(),
        budget_policy=_policy(),
        lease_owner=_OWNER,
        operation_id="operation.shared-fencing",
        lease_seconds=60,
        now=_now(),
    )
    assert admission.reservation is not None
    authority = RepoWriteLeaseAuthority(
        tmp_path,
        project_id=_PROJECT,
        clock=lambda: _NOW,
    )

    with authority.acquire(_request(tmp_path, owner="owner.repo")) as guard:
        repo_epoch = guard.lease.fencing_epoch
        assert repo_epoch > admission.reservation.fencing_token

    released = governor.release_reservation(
        admission.reservation.reservation_id,
        lease_owner=_OWNER,
        expected_fencing_token=admission.reservation.fencing_token,
        operation_id="operation.shared-fencing-release",
        now=_now(),
    )
    assert released.reservation is not None
    assert released.reservation.fencing_token > repo_epoch


def test_renewal_keeps_identity_and_advances_revision(tmp_path: Path) -> None:
    authority = RepoWriteLeaseAuthority(
        tmp_path,
        project_id=_PROJECT,
        clock=lambda: _NOW,
    )

    with authority.acquire(_request(tmp_path, owner="owner.primary")) as guard:
        original = guard.lease
        renewed = guard.renew(lease_seconds=120)

        assert renewed.lease_id == original.lease_id
        assert renewed.fencing_epoch == original.fencing_epoch
        assert renewed.revision == original.revision + 1
        assert renewed.expected_revision == 1
        assert authority.require_current(renewed) == renewed

    with pytest.raises(RepoWriteLeaseStaleError):
        authority.require_current(renewed)


def test_expired_holder_cannot_validate_its_old_fencing(tmp_path: Path) -> None:
    current = datetime(2026, 7, 21, 15, tzinfo=UTC)
    authority = RepoWriteLeaseAuthority(
        tmp_path,
        project_id=_PROJECT,
        clock=lambda: current.isoformat().replace("+00:00", "Z"),
    )

    with authority.acquire(_request(tmp_path, owner="owner.primary")) as guard:
        lease = guard.lease
        current += timedelta(seconds=61)
        with pytest.raises(RepoWriteLeaseStaleError):
            authority.require_current(lease)

    assert tuple(
        event.event_kind for event in authority._store._read_events()
    ) == ("acquired", "expired")


def test_event_persisted_before_projection_crash_is_reconciled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = RepoWriteLeaseAuthority(
        tmp_path,
        project_id=_PROJECT,
        clock=lambda: _NOW,
    )
    original = repo_write_lease_store.atomic_write_json
    attempts = 0

    def fail_first_projection(*args: object, **kwargs: object) -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OSError("simulated projection write failure")
        original(*args, **kwargs)

    monkeypatch.setattr(
        repo_write_lease_store,
        "atomic_write_json",
        fail_first_projection,
    )
    with (
        pytest.raises(OSError, match="projection write failure"),
        first.acquire(_request(tmp_path, owner="owner.crashed")),
    ):
        pass

    recovered = RepoWriteLeaseAuthority(
        tmp_path,
        project_id=_PROJECT,
        clock=lambda: _NOW,
    )
    with recovered.acquire(_request(tmp_path, owner="owner.recovered")) as guard:
        assert guard.lease.fencing_epoch == 2
        assert recovered.require_current(guard.lease) == guard.lease

    events = recovered._store._read_events()
    assert tuple(event.event_kind for event in events) == (
        "acquired",
        "reconciled",
        "acquired",
        "released",
    )


def test_expired_orphan_records_expiry_before_new_fencing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current = datetime(2026, 7, 21, 15, tzinfo=UTC)
    first = RepoWriteLeaseAuthority(
        tmp_path,
        project_id=_PROJECT,
        clock=lambda: current.isoformat().replace("+00:00", "Z"),
    )
    original = repo_write_lease_store.atomic_write_json
    attempts = 0

    def fail_first_projection(*args: object, **kwargs: object) -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OSError("simulated projection write failure")
        original(*args, **kwargs)

    monkeypatch.setattr(
        repo_write_lease_store,
        "atomic_write_json",
        fail_first_projection,
    )
    short = _request(tmp_path, owner="owner.expired").model_copy(
        update={"lease_seconds": 1}
    )
    with (
        pytest.raises(OSError, match="projection write failure"),
        first.acquire(short),
    ):
        pass
    current += timedelta(seconds=2)

    recovered = RepoWriteLeaseAuthority(
        tmp_path,
        project_id=_PROJECT,
        clock=lambda: current.isoformat().replace("+00:00", "Z"),
    )
    with recovered.acquire(_request(tmp_path, owner="owner.recovered")):
        pass

    events = recovered._store._read_events()
    assert tuple(event.event_kind for event in events) == (
        "acquired",
        "expired",
        "acquired",
        "released",
    )


def test_real_process_death_releases_holder_and_recovers_with_new_fencing(
    tmp_path: Path,
) -> None:
    process_context = multiprocessing.get_context("spawn")
    parent, child = process_context.Pipe(duplex=False)
    process = process_context.Process(
        target=_die_while_holding_lease,
        args=(str(tmp_path), child),
    )
    process.start()
    assert parent.poll(20)
    crashed_lease = parent.recv()
    process.join(timeout=20)
    assert process.exitcode == 23

    recovered = RepoWriteLeaseAuthority(
        tmp_path,
        project_id=_PROJECT,
        clock=lambda: _NOW,
    )
    with recovered.acquire(
        _request(tmp_path, owner="owner.after-process-death")
    ) as guard:
        assert guard.lease.fencing_epoch > crashed_lease["fencing_epoch"]

    assert tuple(
        event.event_kind for event in recovered._store._read_events()
    ) == ("acquired", "reconciled", "acquired", "released")


def test_shared_fencing_domain_binding_change_fails_closed(tmp_path: Path) -> None:
    authority = RepoWriteLeaseAuthority(
        tmp_path,
        project_id=_PROJECT,
        clock=lambda: _NOW,
    )
    binding_path = authority._store.shared_root / "shared-state-binding.json"
    payload = json.loads(binding_path.read_text(encoding="utf-8"))
    payload["binding_id"] = "canonical-shared-state.forged"
    binding_path.write_text(json.dumps(payload), encoding="utf-8")

    with (
        pytest.raises(SharedStateIntegrityError, match="binding"),
        authority.acquire(_request(tmp_path, owner="owner.primary")),
    ):
        pass


def _die_while_holding_lease(root: str, connection: object) -> None:
    authority = RepoWriteLeaseAuthority(
        Path(root),
        project_id=_PROJECT,
        clock=lambda: _NOW,
    )
    guard = authority.acquire(_request(Path(root), owner="owner.process-death"))
    guard.__enter__()
    connection.send(guard.lease.model_dump(mode="json"))  # type: ignore[attr-defined]
    connection.close()  # type: ignore[attr-defined]
    os._exit(23)
