"""SnapshotControl 短租约竞争的确定性有界重试。"""

from __future__ import annotations

import hashlib
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TypeVar

from ai_sdlc.core.stage_review.artifacts import (
    ResourceLockUnavailableError,
    SharedStateIntegrityError,
)

T = TypeVar("T")

_BACKOFF_BASELINES = (0.010, 0.020, 0.040, 0.080, 0.160, 0.320, 0.500)
_RETRYABLE_INTEGRITY_MARKERS = (
    "expected head is stale",
    "commit claim collided",
    "commit fencing is stale",
    "optimization commit lease expired",
    "commit lease plan is stale",
    "record sequence collided",
    "snapshot control sequence collided",
    "snapshot_control_safety_pending",
)


class SnapshotControlBusyError(SharedStateIntegrityError):
    """SnapshotControl 在有界竞争窗口内未能取得提交权。"""

    def __init__(
        self,
        message: str = "snapshot_control_busy",
        *,
        operation_id: str | None = None,
        attempts: int = 0,
        elapsed_active_seconds: float = 0.0,
        last_error: Exception | None = None,
        lease_recovery_used: bool = False,
    ) -> None:
        super().__init__(message)
        self.operation_id = operation_id
        self.attempts = attempts
        self.elapsed_active_seconds = max(0.0, elapsed_active_seconds)
        self.last_error_type = (
            type(last_error).__name__ if last_error is not None else None
        )
        self.last_error_message = _public_error_reason(last_error)
        self.lease_recovery_used = lease_recovery_used


@dataclass(frozen=True)
class SnapshotControlRetryPolicy:
    maximum_attempts: int = 8
    maximum_active_seconds: float = 2.0

    def __post_init__(self) -> None:
        if self.maximum_attempts < 1 or self.maximum_attempts > 8:
            raise ValueError("snapshot control attempts must be within 1..8")
        if self.maximum_active_seconds <= 0 or self.maximum_active_seconds > 2:
            raise ValueError("snapshot control active window must be within two seconds")


class SnapshotControlRetryExecutor:
    def __init__(
        self,
        policy: SnapshotControlRetryPolicy | None = None,
        *,
        monotonic: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.policy = policy or SnapshotControlRetryPolicy()
        self.monotonic = monotonic
        self.sleeper = sleeper

    def run(self, operation_id: str, action: Callable[[int], T]) -> T:
        started = self.monotonic()
        lease_recovery_available = True
        lease_recovery_used = False
        elapsed_active_seconds = 0.0
        last_attempt = 0
        last_error: Exception | None = None
        for attempt in range(1, self.policy.maximum_attempts + 1):
            try:
                return action(attempt)
            except (ResourceLockUnavailableError, SharedStateIntegrityError) as exc:
                last_attempt = attempt
                last_error = exc
                if not _is_retryable(exc):
                    raise
                if attempt == self.policy.maximum_attempts:
                    break
                if _is_expired_commit_lease(exc) and lease_recovery_available:
                    lease_recovery_available = False
                    lease_recovery_used = True
                    continue
                elapsed_active_seconds = self.monotonic() - started
                remaining = self.policy.maximum_active_seconds - elapsed_active_seconds
                delay = _deterministic_backoff(operation_id, attempt)
                if remaining <= 0 or delay > remaining:
                    break
                self.sleeper(delay)
        assert last_error is not None
        elapsed_active_seconds = self.monotonic() - started
        error = SnapshotControlBusyError(
            operation_id=operation_id,
            attempts=last_attempt,
            elapsed_active_seconds=elapsed_active_seconds,
            last_error=last_error,
            lease_recovery_used=lease_recovery_used,
        )
        raise error from last_error


def _deterministic_backoff(operation_id: str, attempt: int) -> float:
    if attempt < 1 or attempt > len(_BACKOFF_BASELINES):
        raise ValueError("snapshot control backoff attempt is invalid")
    digest = hashlib.sha256(f"{operation_id}:{attempt}".encode()).digest()
    jitter = 0.9 + (int.from_bytes(digest[:2], "big") % 201) / 1000
    return _BACKOFF_BASELINES[attempt - 1] * jitter


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, ResourceLockUnavailableError):
        return True
    return any(marker in str(exc) for marker in _RETRYABLE_INTEGRITY_MARKERS)


def _is_expired_commit_lease(exc: BaseException) -> bool:
    return "optimization commit lease expired" in str(exc)


def _public_error_reason(exc: Exception | None) -> str | None:
    if exc is None:
        return None
    if isinstance(exc, ResourceLockUnavailableError):
        return "resource_lock_unavailable"
    return next(
        (
            marker
            for marker in _RETRYABLE_INTEGRITY_MARKERS
            if marker in str(exc)
        ),
        "retryable_shared_state_integrity_error",
    )
