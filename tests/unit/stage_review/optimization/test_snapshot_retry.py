from __future__ import annotations

from contextlib import contextmanager

import pytest

from ai_sdlc.core.stage_review.artifacts import (
    ResourceLockUnavailableError,
    SharedStateIntegrityError,
)
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


def test_retry_does_not_replay_real_integrity_failure() -> None:
    attempts: list[int] = []
    sleeps: list[float] = []

    def fail_integrity(attempt: int) -> None:
        attempts.append(attempt)
        raise SharedStateIntegrityError("event digest chain diverged")

    with pytest.raises(SharedStateIntegrityError, match="digest chain diverged"):
        SnapshotControlRetryExecutor(sleeper=sleeps.append).run(
            "operation.integrity",
            fail_integrity,
        )

    assert attempts == [1]
    assert sleeps == []


def test_expired_commit_lease_retries_the_idempotent_transaction() -> None:
    attempts: list[int] = []

    def expire_once(attempt: int) -> str:
        attempts.append(attempt)
        if attempt == 1:
            raise SharedStateIntegrityError("optimization commit lease expired")
        return "committed"

    result = SnapshotControlRetryExecutor(sleeper=lambda _delay: None).run(
        "operation.lease-expired",
        expire_once,
    )

    assert result == "committed"
    assert attempts == [1, 2]


def test_expired_commit_lease_gets_one_recovery_after_contention_window() -> None:
    attempts: list[int] = []
    monotonic_values = iter((0.0, 2.001))

    def expire_after_full_lease(attempt: int) -> str:
        attempts.append(attempt)
        if attempt == 1:
            raise SharedStateIntegrityError("optimization commit lease expired")
        return "committed"

    result = SnapshotControlRetryExecutor(
        monotonic=lambda: next(monotonic_values),
        sleeper=lambda _delay: None,
    ).run("operation.real-lease-expiry", expire_after_full_lease)

    assert result == "committed"
    assert attempts == [1, 2]
