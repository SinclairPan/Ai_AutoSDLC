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
    "record sequence collided",
    "snapshot control sequence collided",
    "snapshot_control_safety_pending",
)


class SnapshotControlBusyError(SharedStateIntegrityError):
    """SnapshotControl 在有界竞争窗口内未能取得提交权。"""


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
        for attempt in range(1, self.policy.maximum_attempts + 1):
            try:
                return action(attempt)
            except (ResourceLockUnavailableError, SharedStateIntegrityError) as exc:
                if not _is_retryable(exc):
                    raise
                if attempt == self.policy.maximum_attempts:
                    break
                remaining = self.policy.maximum_active_seconds - (
                    self.monotonic() - started
                )
                delay = _deterministic_backoff(operation_id, attempt)
                if remaining <= 0 or delay > remaining:
                    break
                self.sleeper(delay)
        raise SnapshotControlBusyError("snapshot_control_busy")


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
