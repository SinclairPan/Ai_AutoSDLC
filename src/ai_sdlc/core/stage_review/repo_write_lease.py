"""Canonical Repo Write Lease 获取、续期、校验和释放。"""

from __future__ import annotations

import os
from collections.abc import Callable
from datetime import datetime, timedelta
from math import isfinite
from pathlib import Path
from types import TracebackType
from typing import Literal, Self

from ai_sdlc.core.stage_review.artifacts import (
    ResourceLockUnavailableError,
    SharedStateIntegrityError,
    ShortFileLock,
    resolve_canonical_shared_state,
)
from ai_sdlc.core.stage_review.authority_binding_artifacts import (
    ensure_shared_state_binding,
)
from ai_sdlc.core.stage_review.canonical import normalize_repo_path
from ai_sdlc.core.stage_review.project_fencing import ProjectFencingDomain
from ai_sdlc.core.stage_review.repo_write_lease_models import (
    RepoWriteLease,
    RepoWriteLeaseRequest,
    RepoWriteLeaseState,
)
from ai_sdlc.core.stage_review.repo_write_lease_store import (
    RepoWriteLeaseStore,
)
from ai_sdlc.core.stage_review.repo_write_lease_store import (
    _path_sets_overlap as path_sets_overlap,
)
from ai_sdlc.core.stage_review.resource_builders import parse_utc, stable_id, utc_iso

__all__ = [
    "canonical_worktree_identity",
    "RepoWriteLeaseAuthority",
    "RepoWriteLeaseConflictError",
    "RepoWriteLeaseRequest",
    "RepoWriteLeaseStaleError",
]


def canonical_worktree_identity(root: Path) -> str:
    normalized = os.path.normcase(str(root.resolve()))
    return stable_id("worktree", normalized)


class RepoWriteLeaseStaleError(SharedStateIntegrityError):
    """调用方持有的 Repo Write Lease 已不再是当前写权限。"""


class RepoWriteLeaseConflictError(ResourceLockUnavailableError):
    """重叠路径仍由活跃进程持有。"""


class RepoWriteLeaseAuthority:
    def __init__(
        self,
        root: Path,
        *,
        project_id: str,
        clock: Callable[[], str],
        lock_timeout_seconds: float = 2,
    ) -> None:
        self.worktree_root = root.resolve()
        shared_root = resolve_canonical_shared_state(root, project_id)
        binding = ensure_shared_state_binding(
            shared_root / "shared-state-binding.json",
            project_id,
        )
        self.project_id = project_id
        self.shared_state_binding_id = binding.binding_id
        self._clock = clock
        self._store = RepoWriteLeaseStore(
            shared_root,
            project_id=project_id,
            lock_timeout_seconds=lock_timeout_seconds,
        )
        self._fencing = ProjectFencingDomain(
            shared_root,
            project_id=project_id,
            lock_timeout_seconds=lock_timeout_seconds,
        )
        self._held_leases: dict[str, RepoWriteLease] = {}

    @property
    def _held(self) -> RepoWriteLease | None:
        return next(iter(self._held_leases.values()), None)

    def acquire(self, request: RepoWriteLeaseRequest) -> RepoWriteLeaseGuard:
        trusted = RepoWriteLeaseRequest.model_validate(request)
        if trusted.worktree_identity != canonical_worktree_identity(
            self.worktree_root
        ):
            raise ValueError("repo write lease worktree identity is invalid")
        paths = _canonical_protected_paths(
            self.worktree_root,
            trusted.protected_path_set,
        )
        return RepoWriteLeaseGuard(
            self,
            trusted.model_copy(update={"protected_path_set": paths}),
        )

    def require_shared_state_binding(self) -> str:
        binding = ensure_shared_state_binding(
            self._store.shared_root / "shared-state-binding.json",
            self.project_id,
        )
        if binding.binding_id != self.shared_state_binding_id:
            raise SharedStateIntegrityError("repo write lease fencing binding changed")
        return binding.binding_id

    def require_current(self, lease: RepoWriteLease) -> RepoWriteLease:
        self.require_shared_state_binding()
        trusted = RepoWriteLease.model_validate(lease.model_dump(mode="json"))
        state = self._store.current()
        current = _active_lease(state, trusted.lease_id)
        held = self._held_leases.get(trusted.lease_id)
        now = parse_utc(self._clock())
        if (
            current is None
            or current != trusted
            or held != trusted
            or current.state != "active"
            or parse_utc(current.expires_at) <= now
        ):
            raise RepoWriteLeaseStaleError("repo write lease is not current")
        return trusted

    def _acquire_locked(
        self,
        request: RepoWriteLeaseRequest,
    ) -> tuple[RepoWriteLease, ShortFileLock]:
        self.require_shared_state_binding()
        now_text = self._clock()
        now = parse_utc(now_text)
        state = self._store.prepare_locked()
        for active in state.active_leases:
            if path_sets_overlap(
                request.protected_path_set,
                active.protected_path_set,
            ):
                state = self._reconcile_or_reject_locked(
                    state,
                    active,
                    now_text,
                    now,
                )
        epoch = self._fencing.next_epoch_locked()
        lease = _build_new_lease(
            self.project_id,
            request,
            state,
            epoch,
            now_text,
            now,
        )
        holder = self._holder_lock(lease.lease_id)
        holder.__enter__()
        try:
            self._fencing.require_allocation_locked(
                epoch,
                ("repo-write", lease.lease_id),
            )
            self._store.append_locked(state, "acquired", lease, occurred_at=now_text)
        except BaseException:
            holder.__exit__(None, None, None)
            raise
        return lease, holder

    def _renew_locked(self, lease: RepoWriteLease, lease_seconds: float) -> RepoWriteLease:
        if not isfinite(lease_seconds) or lease_seconds <= 0:
            raise ValueError("repo write lease duration must be positive and finite")
        now_text = self._clock()
        now = parse_utc(now_text)
        state = self._store.prepare_locked()
        current = _active_lease(state, lease.lease_id)
        if (
            current != lease
            or current is None
            or parse_utc(current.expires_at) <= now
        ):
            raise RepoWriteLeaseStaleError("repo write lease changed before renewal")
        renewed = current.model_copy(
            update={
                "expected_revision": state.head_sequence,
                "revision": current.revision + 1,
                "expires_at": utc_iso(now + timedelta(seconds=lease_seconds)),
                "renewed_at": now_text,
                "previous_lease_digest": current.lease_digest,
                "lease_digest": "",
            }
        )
        renewed = RepoWriteLease.model_validate(renewed.model_dump(mode="json"))
        self._store.append_locked(state, "renewed", renewed, occurred_at=now_text)
        return renewed

    def _release_locked(self, lease: RepoWriteLease) -> None:
        state = self._store.prepare_locked()
        current = _active_lease(state, lease.lease_id)
        if current is None or current.lease_digest != lease.lease_digest:
            raise RepoWriteLeaseStaleError("repo write lease changed before release")
        now_text = self._clock()
        target: Literal["released", "expired"] = (
            "expired"
            if parse_utc(current.expires_at) <= parse_utc(now_text)
            else "released"
        )
        self._terminate_locked(state, current, target, now_text)

    def _terminate_locked(
        self,
        state: RepoWriteLeaseState,
        current: RepoWriteLease,
        target: Literal["released", "expired", "reconciled"],
        occurred_at: str,
    ) -> RepoWriteLeaseState:
        if _active_lease(state, current.lease_id) != current:
            raise RepoWriteLeaseStaleError("active repo write lease is unavailable")
        terminal = current.model_copy(
            update={
                "expected_revision": state.head_sequence,
                "revision": current.revision + 1,
                "state": target,
                "previous_lease_digest": current.lease_digest,
                "lease_digest": "",
            }
        )
        terminal = RepoWriteLease.model_validate(terminal.model_dump(mode="json"))
        return self._store.append_locked(
            state,
            target,
            terminal,
            occurred_at=occurred_at,
        )

    def _reconcile_or_reject_locked(
        self,
        state: RepoWriteLeaseState,
        active: RepoWriteLease,
        now_text: str,
        now: datetime,
    ) -> RepoWriteLeaseState:
        probe = self._holder_lock(active.lease_id, timeout_seconds=0)
        try:
            probe.__enter__()
        except ResourceLockUnavailableError as exc:
            raise RepoWriteLeaseConflictError(
                "overlapping repo write lease is still held"
            ) from exc
        try:
            target: Literal["expired", "reconciled"] = (
                "expired" if parse_utc(active.expires_at) <= now else "reconciled"
            )
            return self._terminate_locked(state, active, target, now_text)
        finally:
            probe.__exit__(None, None, None)

    def _holder_lock(
        self,
        lease_id: str,
        *,
        timeout_seconds: float | None = None,
    ) -> ShortFileLock:
        return ShortFileLock(
            self._store.root / "holders" / f"{lease_id}.lock",
            timeout_seconds=(
                self._store.lock_timeout_seconds
                if timeout_seconds is None
                else timeout_seconds
            ),
        )


class RepoWriteLeaseGuard:
    def __init__(
        self,
        authority: RepoWriteLeaseAuthority,
        request: RepoWriteLeaseRequest,
    ) -> None:
        self._authority = authority
        self._request = request
        self._holder: ShortFileLock
        self.lease: RepoWriteLease

    def __enter__(self) -> Self:
        with self._authority._store.lock(), self._authority._fencing.locked():
            self.lease, self._holder = self._authority._acquire_locked(
                self._request
            )
        self._authority._held_leases[self.lease.lease_id] = self.lease
        return self

    def renew(self, *, lease_seconds: float) -> RepoWriteLease:
        with self._authority._store.lock(), self._authority._fencing.locked():
            self.lease = self._authority._renew_locked(
                self.lease,
                lease_seconds,
            )
        self._authority._held_leases[self.lease.lease_id] = self.lease
        return self.lease

    def require_current(self) -> RepoWriteLease:
        return self._authority.require_current(self.lease)

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        try:
            with self._authority._store.lock(), self._authority._fencing.locked():
                self._authority._release_locked(self.lease)
        finally:
            self._authority._held_leases.pop(self.lease.lease_id, None)
            self._holder.__exit__(exc_type, exc, traceback)


def _active_lease(
    state: RepoWriteLeaseState,
    lease_id: str,
) -> RepoWriteLease | None:
    return next(
        (lease for lease in state.active_leases if lease.lease_id == lease_id),
        None,
    )


def _build_new_lease(
    project_id: str,
    request: RepoWriteLeaseRequest,
    state: RepoWriteLeaseState,
    epoch: int,
    now_text: str,
    now: datetime,
) -> RepoWriteLease:
    return RepoWriteLease(
        lease_id=stable_id(
            "repo-write-lease",
            project_id,
            str(epoch),
            request.idempotency_key,
        ),
        project_id=project_id,
        worktree_identity=request.worktree_identity,
        stage_review_session_id=request.stage_review_session_id,
        protected_path_set=request.protected_path_set,
        lease_owner=request.lease_owner,
        fencing_epoch=epoch,
        expected_revision=state.head_sequence,
        revision=1,
        state="active",
        acquired_at=now_text,
        expires_at=utc_iso(now + timedelta(seconds=request.lease_seconds)),
        renewed_at=now_text,
        idempotency_key=request.idempotency_key,
    )


def _canonical_protected_paths(
    worktree_root: Path,
    paths: tuple[str, ...],
) -> tuple[str, ...]:
    canonical: set[str] = set()
    for relative in paths:
        resolved = (worktree_root / relative).resolve()
        try:
            within_root = resolved.relative_to(worktree_root)
        except ValueError as exc:
            raise ValueError("repo write lease path escapes worktree") from exc
        canonical.add(normalize_repo_path(within_root.as_posix()))
    return tuple(sorted(canonical))
