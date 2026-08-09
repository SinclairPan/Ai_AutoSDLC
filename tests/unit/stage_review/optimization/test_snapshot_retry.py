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
    first_error = ResourceLockUnavailableError(
        "shared state lock unavailable: /private/secret/snapshot-control.lock"
    )
    final_error = ResourceLockUnavailableError(
        "shared state lock unavailable: /private/secret/snapshot-control.lock"
    )

    def contend(attempt: int) -> None:
        raise (first_error, final_error)[attempt - 1]

    with pytest.raises(SnapshotControlBusyError, match="snapshot_control_busy") as caught:
        retry.run("operation.busy", contend)

    error = caught.value
    assert error.operation_id == "operation.busy"
    assert error.attempts == 2
    assert error.last_error_type == "ResourceLockUnavailableError"
    assert error.last_error_message == "resource_lock_unavailable"
    assert "/private/secret/snapshot-control.lock" not in error.last_error_message
    assert "/private/secret/snapshot-control.lock" not in str(error)
    assert error.elapsed_active_seconds >= 0.0
    assert error.__cause__ is final_error
    assert len(sleeps) <= 2
    assert sum(sleeps) <= 0.025

    attempt_budget_clock = iter((5.0, 5.125))
    attempt_budget_error = ResourceLockUnavailableError("attempt budget exhausted")

    def exhaust_attempt_budget(_: int) -> None:
        raise attempt_budget_error

    with pytest.raises(SnapshotControlBusyError) as caught:
        SnapshotControlRetryExecutor(
            SnapshotControlRetryPolicy(maximum_attempts=1),
            monotonic=lambda: next(attempt_budget_clock),
            sleeper=lambda _delay: None,
        ).run("operation.attempt-budget", exhaust_attempt_budget)

    error = caught.value
    assert error.attempts == 1
    assert error.elapsed_active_seconds == pytest.approx(0.125)
    assert error.__cause__ is attempt_budget_error


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

    exhausted_clock = iter((0.0, 2.001, 2.001))
    first_expiry = SharedStateIntegrityError("optimization commit lease expired")
    final_expiry = SharedStateIntegrityError("optimization commit lease expired")

    def expire_twice(attempt: int) -> None:
        if attempt == 1:
            raise first_expiry
        if attempt == 2:
            raise final_expiry
        raise AssertionError("unexpected third lease-expiry attempt")

    with pytest.raises(SnapshotControlBusyError) as caught:
        SnapshotControlRetryExecutor(
            monotonic=lambda: next(exhausted_clock),
            sleeper=lambda _delay: None,
        ).run("operation.double-lease-expiry", expire_twice)

    error = caught.value
    assert error.operation_id == "operation.double-lease-expiry"
    assert error.attempts == 2
    assert error.last_error_type == "SharedStateIntegrityError"
    assert error.last_error_message == "optimization commit lease expired"
    assert error.lease_recovery_used is True
    assert error.__cause__ is final_expiry
