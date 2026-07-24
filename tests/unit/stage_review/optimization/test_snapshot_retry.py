from __future__ import annotations

from contextlib import contextmanager

import pytest

from ai_sdlc.core.stage_review.artifacts import ResourceLockUnavailableError
from ai_sdlc.core.stage_review.optimization.snapshot_retry import (
    SnapshotControlBusyError,
    SnapshotControlRetryExecutor,
    SnapshotControlRetryPolicy,
)
from ai_sdlc.core.stage_review.optimization.snapshot_retry import (
    _deterministic_backoff as deterministic_backoff,
)


def test_retry_releases_attempt_scope_before_deterministic_backoff() -> None:
    held = False
    sleeps: list[float] = []
    attempts: list[int] = []

    @contextmanager
    def lease():
        nonlocal held
        held = True
        try:
            yield
        finally:
            held = False

    def action(attempt: int) -> str:
        attempts.append(attempt)
        with lease():
            if attempt < 3:
                raise ResourceLockUnavailableError("contended")
            return "committed"

    def sleep(delay: float) -> None:
        assert not held
        sleeps.append(delay)

    result = SnapshotControlRetryExecutor(sleeper=sleep).run("operation.retry", action)

    assert result == "committed"
    assert attempts == [1, 2, 3]
    assert sleeps == [
        deterministic_backoff("operation.retry", 1),
        deterministic_backoff("operation.retry", 2),
    ]


def test_retry_is_bounded_by_attempts_and_active_wall_clock() -> None:
    now = 0.0
    sleeps: list[float] = []

    def monotonic() -> float:
        return now

    def sleep(delay: float) -> None:
        nonlocal now
        sleeps.append(delay)
        now += delay

    retry = SnapshotControlRetryExecutor(
        SnapshotControlRetryPolicy(maximum_attempts=8, maximum_active_seconds=0.025),
        monotonic=monotonic,
        sleeper=sleep,
    )

    with pytest.raises(SnapshotControlBusyError, match="snapshot_control_busy"):
        retry.run(
            "operation.busy",
            lambda _: (_ for _ in ()).throw(ResourceLockUnavailableError("busy")),
        )

    assert len(sleeps) <= 2
    assert sum(sleeps) <= 0.025
