"""Repo Write Lease 的追加事件存储和可修复投影。"""

from __future__ import annotations

from pathlib import Path

from pydantic import ValidationError

from ai_sdlc.core.stage_review.artifacts import (
    SharedStateIntegrityError,
    ShortFileLock,
    atomic_write_json,
    bind_repository_project,
    create_json_exclusive,
    read_json_object,
)
from ai_sdlc.core.stage_review.repo_write_lease_models import (
    RepoWriteLease,
    RepoWriteLeaseEvent,
    RepoWriteLeaseEventKind,
    RepoWriteLeaseState,
)
from ai_sdlc.core.stage_review.resource_builders import stable_id
from ai_sdlc.core.stage_review.transaction_artifact_codec import (
    decode_transaction_artifact,
)


class RepoWriteLeaseStore:
    def __init__(
        self,
        shared_root: Path,
        *,
        project_id: str,
        lock_timeout_seconds: float,
    ) -> None:
        self.shared_root = shared_root
        self.project_id = project_id
        self.root = shared_root / "repo-write-leases"
        self.events_dir = self.root / "events"
        self.projection_path = self.root / "state.json"
        self.lock_path = self.root / "lease.lock"
        self.lock_timeout_seconds = lock_timeout_seconds

    def lock(self) -> ShortFileLock:
        return ShortFileLock(
            self.lock_path,
            timeout_seconds=self.lock_timeout_seconds,
        )

    def prepare_locked(self) -> RepoWriteLeaseState:
        bind_repository_project(self.shared_root, self.project_id)
        state = rebuild_repo_write_lease_state(self._read_events(), self.project_id)
        self._verify_projection(state)
        return state

    def append_locked(
        self,
        state: RepoWriteLeaseState,
        event_kind: RepoWriteLeaseEventKind,
        lease: RepoWriteLease,
        *,
        occurred_at: str,
    ) -> RepoWriteLeaseState:
        if any(
            event.compatibility_mode != "strict" for event in self._read_events()
        ):
            raise SharedStateIntegrityError("previous repo lease schema is read-only")
        event = RepoWriteLeaseEvent(
            sequence=state.head_sequence + 1,
            event_id=stable_id(
                "repo-write-lease-event",
                lease.lease_id,
                str(lease.revision),
                event_kind,
            ),
            event_kind=event_kind,
            previous_event_digest=state.head_digest,
            occurred_at=occurred_at,
            lease=lease,
        )
        path = self.events_dir / f"{event.sequence:020d}.json"
        if not create_json_exclusive(path, event.model_dump(mode="json")):
            existing = _read_event(path)
            if existing != event:
                raise SharedStateIntegrityError("repo write lease event fork")
        prospective = rebuild_repo_write_lease_state(
            self._read_events(),
            self.project_id,
        )
        atomic_write_json(
            self.projection_path,
            prospective.model_dump(mode="json"),
        )
        return prospective

    def current(self) -> RepoWriteLeaseState:
        with self.lock():
            return self.prepare_locked()

    def _read_events(self) -> tuple[RepoWriteLeaseEvent, ...]:
        if not self.events_dir.exists():
            return ()
        return tuple(_read_event(path) for path in sorted(self.events_dir.glob("*.json")))

    def _verify_projection(self, rebuilt: RepoWriteLeaseState) -> None:
        if not self.projection_path.exists():
            return
        try:
            projected = RepoWriteLeaseState.model_validate(
                read_json_object(self.projection_path)
            )
        except (ValidationError, ValueError):
            return
        if projected.head_sequence > rebuilt.head_sequence:
            raise SharedStateIntegrityError("repo write lease projection is ahead")
        if (
            projected.head_sequence == rebuilt.head_sequence
            and projected.state_digest != rebuilt.state_digest
        ):
            raise SharedStateIntegrityError("repo write lease projection fork")


def _read_event(path: Path) -> RepoWriteLeaseEvent:
    try:
        return decode_transaction_artifact(
            RepoWriteLeaseEvent,
            read_json_object(path),
        )
    except (ValidationError, ValueError) as exc:
        raise SharedStateIntegrityError(
            f"repo write lease event is invalid: {path}"
        ) from exc


def rebuild_repo_write_lease_state(
    events: tuple[RepoWriteLeaseEvent, ...],
    project_id: str,
) -> RepoWriteLeaseState:
    head = ""
    leases: dict[str, RepoWriteLease] = {}
    maximum = 0
    for sequence, event in enumerate(events, start=1):
        if event.sequence != sequence or event.previous_event_digest != head:
            raise SharedStateIntegrityError("repo write lease event chain diverged")
        _verify_transition(leases, event, maximum, project_id)
        target = event.lease
        if target.state == "active":
            leases[target.lease_id] = target
        else:
            leases.pop(target.lease_id, None)
        maximum = max(maximum, target.fencing_epoch)
        head = event.event_digest
    return RepoWriteLeaseState(
        head_sequence=len(events),
        head_digest=head,
        max_fencing_epoch=maximum,
        active_leases=tuple(sorted(leases.values(), key=lambda item: item.lease_id)),
    )


def _verify_transition(
    leases: dict[str, RepoWriteLease],
    event: RepoWriteLeaseEvent,
    maximum: int,
    project_id: str,
) -> None:
    target = event.lease
    current = leases.get(target.lease_id)
    if event.event_kind == "acquired":
        if current is not None or any(
            _path_sets_overlap(
                target.protected_path_set,
                lease.protected_path_set,
            )
            for lease in leases.values()
        ):
            raise SharedStateIntegrityError("overlapping repo write lease was acquired")
        if (
            target.state != "active"
            or target.revision != 1
            or target.project_id != project_id
            or target.fencing_epoch <= maximum
            or target.expected_revision != event.sequence - 1
        ):
            raise SharedStateIntegrityError("repo write lease acquisition is invalid")
        return
    if current is None:
        raise SharedStateIntegrityError("repo write lease update has no predecessor")
    expected_state = {
        "renewed": "active",
        "released": "released",
        "expired": "expired",
        "reconciled": "reconciled",
    }[event.event_kind]
    checks = (
        current.state == "active",
        target.lease_id == current.lease_id,
        target.fencing_epoch == current.fencing_epoch,
        target.expected_revision == event.sequence - 1,
        target.revision == current.revision + 1,
        target.previous_lease_digest == current.lease_digest,
        target.state == expected_state,
        _same_lease_identity(current, target),
    )
    if not all(checks):
        raise SharedStateIntegrityError("repo write lease transition is invalid")


def _same_lease_identity(
    current: RepoWriteLease,
    target: RepoWriteLease,
) -> bool:
    return all(
        (
            target.project_id == current.project_id,
            target.worktree_identity == current.worktree_identity,
            target.stage_review_session_id == current.stage_review_session_id,
            target.protected_path_set == current.protected_path_set,
            target.lease_owner == current.lease_owner,
            target.acquired_at == current.acquired_at,
            target.idempotency_key == current.idempotency_key,
        )
    )


def _path_sets_overlap(first: tuple[str, ...], second: tuple[str, ...]) -> bool:
    return any(
        left.casefold() == right.casefold()
        or left.casefold().startswith(f"{right.casefold()}/")
        or right.casefold().startswith(f"{left.casefold()}/")
        for left in first
        for right in second
    )
