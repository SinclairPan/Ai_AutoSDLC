from __future__ import annotations

import json
import shutil
import threading
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError
from tests.unit.stage_review.test_resources import (
    _OWNER,
    _final_reservation,
    _governor,
    _now,
    _provider_anticipated,
)

import ai_sdlc.core.stage_review.provider_journal_store as journal_store_module
from ai_sdlc.core.stage_review.artifacts import (
    ResourceLockUnavailableError,
    SharedStateIntegrityError,
)
from ai_sdlc.core.stage_review.certificate_receipt_store import (
    FilesystemReviewReceiptArtifactStore,
)
from ai_sdlc.core.stage_review.provider_journal import (
    ProviderInvocationDriver,
    ProviderInvocationJournal,
    ProviderInvocationRequest,
    ProviderQueryResult,
    ProviderRecoveryCapabilities,
    ProviderSubmission,
    build_provider_invocation_request,
    build_provider_submission,
)
from ai_sdlc.core.stage_review.provider_journal_driver import ProviderDriverRefused
from ai_sdlc.core.stage_review.provider_journal_models import (
    ProviderInvocation,
    event_digest,
)
from ai_sdlc.core.stage_review.provider_journal_reducer import (
    rebuild_provider_invocation,
)
from ai_sdlc.core.stage_review.provider_usage_models import metered_provider_usage
from ai_sdlc.core.stage_review.resource_models import ResourceAmounts
from ai_sdlc.core.stage_review.resources import ResourceGovernor


class FakeProviderDriver(ProviderInvocationDriver):
    def __init__(self, capabilities: ProviderRecoveryCapabilities) -> None:
        self.provider_id = "provider.test"
        self.capabilities = capabilities
        self.invoke_count = 0
        self.bill_count = 0
        self.raise_after_call = False
        self._submissions: dict[str, ProviderSubmission] = {}

    def invoke(self, request: ProviderInvocationRequest) -> ProviderSubmission:
        invocation = request
        self.invoke_count += 1
        key = invocation.idempotency_key
        if key not in self._submissions:
            self.bill_count += 1
            self._submissions[key] = build_provider_submission(
                invocation,
                provider_call_id=f"provider-call.{self.bill_count}",
                output_payload={"decision": "PASS"},
                accounted_usage=metered_provider_usage(_actual_usage()),
            )
        if self.raise_after_call:
            self.raise_after_call = False
            raise RuntimeError("injected provider return crash")
        return self._submissions[key]

    def query(self, request: ProviderInvocationRequest) -> ProviderQueryResult:
        key = request.idempotency_key
        submission = self._submissions.get(key)
        if submission is None:
            return ProviderQueryResult(query_status="not_found")
        return ProviderQueryResult(query_status="submitted", submission=submission)


def test_prepare_preauthorizes_resource_and_persists_one_event(tmp_path: Path) -> None:
    journal, governor, request = _journal_setup(tmp_path)

    result = journal.prepare(request, lease_owner=_OWNER, now=_now())
    reservation = governor.get_reservation(request.reservation_id)

    assert result.result_code == "prepared"
    assert result.invocation is not None
    assert result.invocation.state == "prepared"
    assert reservation.authorized_pending == request.anticipated_usage
    assert reservation.provider_permits[0].invocation_id == request.invocation_id
    assert len(journal.events(request.invocation_id)) == 1


def test_happy_path_commits_once_and_settles_exact_actual(tmp_path: Path) -> None:
    journal, governor, request = _journal_setup(tmp_path)
    driver = FakeProviderDriver(request.capabilities)
    journal.prepare(request, lease_owner=_OWNER, now=_now())

    result = journal.resume(
        request.invocation_id,
        driver=driver,
        validator=_validator,
        lease_owner=_OWNER,
        now=_now(),
    )
    reservation = governor.get_reservation(request.reservation_id)

    assert result.result_code == "committed"
    assert result.invocation is not None
    assert result.invocation.state == "committed"
    assert [event.state for event in journal.events(request.invocation_id)] == [
        "prepared",
        "dispatched",
        "submitted",
        "validated",
        "committed",
    ]
    assert driver.invoke_count == driver.bill_count == 1
    assert reservation.usage == _actual_usage()
    assert not reservation.authorized_pending.any_positive()
    authority = FilesystemReviewReceiptArtifactStore(
        tmp_path,
        project_id=request.project_id,
    )
    assert authority.resolve_invocation(request.invocation_id) == result.invocation


def test_successful_provider_overrun_commits_observed_usage(tmp_path: Path) -> None:
    journal, governor, request = _journal_setup(tmp_path)
    actual = _actual_usage().model_copy(update={"active_wall_clock": 13})

    class OverrunDriver(FakeProviderDriver):
        def invoke(self, target: ProviderInvocationRequest) -> ProviderSubmission:
            self.invoke_count += 1
            return build_provider_submission(
                target,
                provider_call_id="provider-call.overrun",
                output_payload={"decision": "PASS"},
                accounted_usage=metered_provider_usage(actual),
            )

    driver = OverrunDriver(request.capabilities)
    journal.prepare(request, lease_owner=_OWNER, now=_now())
    result = journal.resume(
        request.invocation_id,
        driver=driver,
        validator=_validator,
        lease_owner=_OWNER,
        now=_now(),
    )

    reservation = governor.get_reservation(request.reservation_id)
    assert result.result_code == "committed"
    assert reservation.usage == actual
    assert reservation.observed_overrun.active_wall_clock == 3


def test_refused_provider_call_settles_measured_usage(tmp_path: Path) -> None:
    journal, governor, request = _journal_setup(tmp_path)
    journal.prepare(request, lease_owner=_OWNER, now=_now())
    actual = _actual_usage().model_copy(update={"active_wall_clock": 3})

    class RefusingDriver(FakeProviderDriver):
        def invoke(self, target: ProviderInvocationRequest) -> ProviderSubmission:
            del target
            self.invoke_count += 1
            raise ProviderDriverRefused(
                "provider failed after execution",
                accounted_usage=metered_provider_usage(actual),
            )

    driver = RefusingDriver(request.capabilities)
    result = journal.resume(
        request.invocation_id,
        driver=driver,
        validator=_validator,
        lease_owner=_OWNER,
        now=_now(),
    )

    reservation = governor.get_reservation(request.reservation_id)
    assert result.result_code == "needs_user"
    assert reservation.usage == actual
    assert not reservation.authorized_pending.any_positive()
    assert not reservation.provider_permits
    assert journal.get(request.invocation_id).state == "refused"  # type: ignore[union-attr]

    replay = journal.resume(
        request.invocation_id,
        driver=driver,
        validator=_validator,
        lease_owner=_OWNER,
        now=_now(),
    )

    assert replay.result_code == "needs_user"
    assert driver.invoke_count == 1
    assert governor.get_reservation(request.reservation_id).usage == actual


def test_refused_provider_overrun_is_terminal_and_preserves_observed_usage(
    tmp_path: Path,
) -> None:
    journal, governor, request = _journal_setup(tmp_path)
    journal.prepare(request, lease_owner=_OWNER, now=_now())
    actual = _actual_usage().model_copy(update={"active_wall_clock": 13})

    class OverrunDriver(FakeProviderDriver):
        def invoke(self, target: ProviderInvocationRequest) -> ProviderSubmission:
            del target
            self.invoke_count += 1
            raise ProviderDriverRefused(
                "provider exceeded its active execution permit",
                accounted_usage=metered_provider_usage(actual),
            )

    driver = OverrunDriver(request.capabilities)
    result = journal.resume(
        request.invocation_id,
        driver=driver,
        validator=_validator,
        lease_owner=_OWNER,
        now=_now(),
    )

    reservation = governor.get_reservation(request.reservation_id)
    assert result.result_code == "needs_user"
    assert reservation.usage == actual
    assert reservation.observed_overrun.active_wall_clock == 3
    assert journal.get(request.invocation_id).state == "refused"  # type: ignore[union-attr]

    replay = journal.resume(
        request.invocation_id,
        driver=driver,
        validator=_validator,
        lease_owner=_OWNER,
        now=_now(),
    )
    assert replay.result_code == "needs_user"
    assert driver.invoke_count == 1


def test_committed_receipt_authority_write_recovers_idempotently(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    journal, _, request = _journal_setup(tmp_path)
    driver = FakeProviderDriver(request.capabilities)
    journal.prepare(request, lease_owner=_OWNER, now=_now())
    persist = journal._receipt_artifacts.persist_invocation
    attempts = 0

    def crash_once(invocation: ProviderInvocation) -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("injected receipt authority crash")
        persist(invocation)

    monkeypatch.setattr(journal._receipt_artifacts, "persist_invocation", crash_once)
    with pytest.raises(RuntimeError, match="receipt authority crash"):
        journal.resume(
            request.invocation_id,
            driver=driver,
            validator=_validator,
            lease_owner=_OWNER,
            now=_now(),
        )

    assert journal.get(request.invocation_id).state == "committed"  # type: ignore[union-attr]
    replay = journal.resume(
        request.invocation_id,
        driver=driver,
        validator=_validator,
        lease_owner=_OWNER,
        now=_now(),
    )
    authority = FilesystemReviewReceiptArtifactStore(
        tmp_path,
        project_id=request.project_id,
    )
    assert replay.result_code == "committed"
    assert authority.resolve_invocation(request.invocation_id) == replay.invocation
    assert driver.invoke_count == 1


def test_prepared_resume_is_safe_and_does_not_duplicate_prepare(tmp_path: Path) -> None:
    journal, _, request = _journal_setup(tmp_path)
    driver = FakeProviderDriver(request.capabilities)
    first = journal.prepare(request, lease_owner=_OWNER, now=_now())
    repeated = journal.prepare(request, lease_owner=_OWNER, now=_now())

    committed = journal.resume(
        request.invocation_id,
        driver=driver,
        validator=_validator,
        lease_owner=_OWNER,
        now=_now(),
    )

    assert repeated.invocation == first.invocation
    assert committed.result_code == "committed"
    assert driver.bill_count == 1


def test_projection_rebuild_reuses_event_runtime_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    journal, _, request = _journal_setup(tmp_path)
    journal.prepare(request, lease_owner=_OWNER, now=_now())
    events = journal.events(request.invocation_id)
    timestamps = iter(("2026-07-20T00:00:00Z", "2026-07-20T00:00:01Z"))
    monkeypatch.setattr(
        ProviderInvocation.model_fields["created_at"],
        "default_factory",
        lambda: next(timestamps),
    )

    first = rebuild_provider_invocation(events)
    repeated = rebuild_provider_invocation(events)

    assert first == repeated
    assert first.created_at == events[-1].created_at


def test_dispatched_idempotent_retry_uses_same_key_without_duplicate_bill(
    tmp_path: Path,
) -> None:
    capabilities = ProviderRecoveryCapabilities(
        idempotency_support=True,
        invocation_query_support=False,
        cost_metering_support=True,
    )
    journal, _, request = _journal_setup(tmp_path, capabilities=capabilities)
    driver = FakeProviderDriver(capabilities)
    journal.prepare(request, lease_owner=_OWNER, now=_now())
    driver.raise_after_call = True
    with pytest.raises(RuntimeError, match="provider return crash"):
        journal.resume(
            request.invocation_id,
            driver=driver,
            validator=_validator,
            lease_owner=_OWNER,
            now=_now(),
        )

    recovered = journal.resume(
        request.invocation_id,
        driver=driver,
        validator=_validator,
        lease_owner=_OWNER,
        now=_now(),
    )

    assert recovered.result_code == "committed"
    assert driver.invoke_count == 2
    assert driver.bill_count == 1


def test_dispatched_query_recovers_existing_submission_without_reinvoke(
    tmp_path: Path,
) -> None:
    capabilities = ProviderRecoveryCapabilities(
        idempotency_support=False,
        invocation_query_support=True,
        cost_metering_support=True,
    )
    journal, _, request = _journal_setup(tmp_path, capabilities=capabilities)
    driver = FakeProviderDriver(capabilities)
    journal.prepare(request, lease_owner=_OWNER, now=_now())
    driver.raise_after_call = True
    with pytest.raises(RuntimeError):
        journal.resume(
            request.invocation_id,
            driver=driver,
            validator=_validator,
            lease_owner=_OWNER,
            now=_now(),
        )

    recovered = journal.resume(
        request.invocation_id,
        driver=driver,
        validator=_validator,
        lease_owner=_OWNER,
        now=_now(),
    )

    assert recovered.result_code == "committed"
    assert driver.invoke_count == 1
    assert driver.bill_count == 1


def test_dispatched_without_query_or_idempotency_requires_user(
    tmp_path: Path,
) -> None:
    capabilities = ProviderRecoveryCapabilities(
        idempotency_support=False,
        invocation_query_support=False,
        cost_metering_support=True,
    )
    journal, _, request = _journal_setup(tmp_path, capabilities=capabilities)
    driver = FakeProviderDriver(capabilities)
    journal.prepare(request, lease_owner=_OWNER, now=_now())
    driver.raise_after_call = True
    with pytest.raises(RuntimeError):
        journal.resume(
            request.invocation_id,
            driver=driver,
            validator=_validator,
            lease_owner=_OWNER,
            now=_now(),
        )

    recovered = journal.resume(
        request.invocation_id,
        driver=driver,
        validator=_validator,
        lease_owner=_OWNER,
        now=_now(),
    )

    assert recovered.result_code == "needs_user"
    assert recovered.invocation is not None
    assert recovered.invocation.state == "dispatched"
    assert driver.invoke_count == 1


def test_parallel_resume_claims_one_dispatch_before_provider_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capabilities = ProviderRecoveryCapabilities(
        idempotency_support=False,
        invocation_query_support=False,
        cost_metering_support=True,
    )
    journal, _, request = _journal_setup(tmp_path, capabilities=capabilities)
    driver = FakeProviderDriver(capabilities)
    journal.prepare(request, lease_owner=_OWNER, now=_now())
    ready = threading.Barrier(2)
    dispatches_ready = threading.Barrier(2)
    release_provider = threading.Event()
    provider_entered = threading.Event()
    original_resource_ready = journal._resource_ready
    original_advance = journal._store.advance
    original_invoke = driver.invoke

    def synchronized_resource_check(*args: object, **kwargs: object) -> bool:
        result = original_resource_ready(*args, **kwargs)  # type: ignore[arg-type]
        ready.wait(timeout=2)
        return result

    def synchronized_dispatch(*args: object, **kwargs: object) -> object:
        result = original_advance(*args, **kwargs)  # type: ignore[arg-type]
        if len(args) > 1 and args[1] == "dispatched":
            dispatches_ready.wait(timeout=2)
        return result

    def blocked_invoke(target: ProviderInvocationRequest) -> ProviderSubmission:
        provider_entered.set()
        assert release_provider.wait(timeout=2)
        return original_invoke(target)

    monkeypatch.setattr(journal, "_resource_ready", synchronized_resource_check)
    monkeypatch.setattr(journal._store, "advance", synchronized_dispatch)
    monkeypatch.setattr(driver, "invoke", blocked_invoke)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(
                journal.resume,
                request.invocation_id,
                driver=driver,
                validator=_validator,
                lease_owner=_OWNER,
                now=_now(),
            )
            for _ in range(2)
        ]
        assert provider_entered.wait(timeout=2)
        completed, _ = wait(futures, timeout=1, return_when=FIRST_COMPLETED)
        assert len(completed) == 1
        assert next(iter(completed)).result().result_code in {
            "needs_user",
            "retry_wait",
        }
        release_provider.set()
        results = [future.result(timeout=2) for future in futures]

    assert driver.invoke_count == 1
    codes = [result.result_code for result in results]
    assert codes.count("committed") == 1
    assert set(codes) <= {"committed", "needs_user", "retry_wait"}


def test_provider_call_claim_preserves_body_lock_error_and_releases(
    tmp_path: Path,
) -> None:
    journal, _, request = _journal_setup(tmp_path)

    with (
        pytest.raises(ResourceLockUnavailableError, match="driver lock failure"),
        journal._store.provider_call_claim(request.invocation_id) as owns_call,
    ):
        assert owns_call
        raise ResourceLockUnavailableError("driver lock failure")

    with journal._store.provider_call_claim(request.invocation_id) as owns_call:
        assert owns_call


def test_provider_lock_error_maps_to_result_and_resume_can_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    journal, _, request = _journal_setup(tmp_path)
    driver = FakeProviderDriver(request.capabilities)
    journal.prepare(request, lease_owner=_OWNER, now=_now())
    original_invoke = driver.invoke
    attempts = 0

    def lock_once(target: ProviderInvocationRequest) -> ProviderSubmission:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise ResourceLockUnavailableError("provider lock failure")
        return original_invoke(target)

    monkeypatch.setattr(driver, "invoke", lock_once)

    failed = journal.resume(
        request.invocation_id,
        driver=driver,
        validator=_validator,
        lease_owner=_OWNER,
        now=_now(),
    )
    recovered = journal.resume(
        request.invocation_id,
        driver=driver,
        validator=_validator,
        lease_owner=_OWNER,
        now=_now(),
    )

    assert failed.result_code == "lock_unavailable"
    assert recovered.result_code == "committed"
    assert driver.bill_count == 1


def test_public_paths_reject_noncanonical_invocation_without_side_effect(
    tmp_path: Path,
) -> None:
    journal, _, _ = _journal_setup(tmp_path)
    malicious = "provider-invocation.safe/../../escaped"
    escaped = journal._store.root.parent / "escaped"

    for access in (journal.get, journal.events, journal.submission_path):
        with pytest.raises(ValueError, match="identity is invalid"):
            access(malicious)

    assert not escaped.exists()


def test_resume_rejects_noncanonical_invocation_without_side_effect(
    tmp_path: Path,
) -> None:
    journal, _, request = _journal_setup(tmp_path)
    malicious = "provider-invocation.safe/../../escaped"
    escaped = journal._store.root.parent / "escaped"
    driver = FakeProviderDriver(request.capabilities)

    result = journal.resume(
        malicious,
        driver=driver,
        validator=_validator,
        lease_owner=_OWNER,
        now=_now(),
    )

    assert result.result_code == "invalid_request"
    assert not escaped.exists()


def test_query_only_parallel_resume_waits_for_active_dispatch_owner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capabilities = ProviderRecoveryCapabilities(
        idempotency_support=False,
        invocation_query_support=True,
        cost_metering_support=True,
    )
    journal, _, request = _journal_setup(tmp_path, capabilities=capabilities)
    driver = FakeProviderDriver(capabilities)
    journal.prepare(request, lease_owner=_OWNER, now=_now())
    ready = threading.Barrier(2)
    dispatches_ready = threading.Barrier(2)
    provider_entered = threading.Event()
    release_provider = threading.Event()
    original_ready = journal._resource_ready
    original_advance = journal._store.advance
    original_invoke = driver.invoke

    def synchronized_resource_check(*args: object, **kwargs: object) -> bool:
        result = original_ready(*args, **kwargs)  # type: ignore[arg-type]
        ready.wait(timeout=2)
        return result

    def synchronized_dispatch(*args: object, **kwargs: object) -> object:
        result = original_advance(*args, **kwargs)  # type: ignore[arg-type]
        if len(args) > 1 and args[1] == "dispatched":
            dispatches_ready.wait(timeout=2)
        return result

    def blocked_invoke(target: ProviderInvocationRequest) -> ProviderSubmission:
        provider_entered.set()
        assert release_provider.wait(timeout=2)
        return original_invoke(target)

    monkeypatch.setattr(journal, "_resource_ready", synchronized_resource_check)
    monkeypatch.setattr(journal._store, "advance", synchronized_dispatch)
    monkeypatch.setattr(driver, "invoke", blocked_invoke)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(
                journal.resume,
                request.invocation_id,
                driver=driver,
                validator=_validator,
                lease_owner=_OWNER,
                now=_now(),
            )
            for _ in range(2)
        ]
        assert provider_entered.wait(timeout=2)
        completed, _ = wait(futures, timeout=1, return_when=FIRST_COMPLETED)
        assert len(completed) == 1
        assert next(iter(completed)).result().result_code == "retry_wait"
        release_provider.set()
        results = [future.result(timeout=2) for future in futures]

    assert driver.invoke_count == driver.bill_count == 1
    assert sorted(result.result_code for result in results) == [
        "committed",
        "retry_wait",
    ]


def test_prepare_recovers_after_resource_authorization_before_journal_event(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    journal, governor, request = _journal_setup(tmp_path)
    original_advance = journal._store.advance

    def crash_before_prepared(*args: object, **kwargs: object) -> object:
        raise RuntimeError("injected prepared event crash")

    monkeypatch.setattr(journal._store, "advance", crash_before_prepared)
    with pytest.raises(RuntimeError, match="prepared event crash"):
        journal.prepare(request, lease_owner=_OWNER, now=_now())
    monkeypatch.setattr(journal._store, "advance", original_advance)

    recovered = journal.prepare(request, lease_owner=_OWNER, now=_now())
    reservation = governor.get_reservation(request.reservation_id)

    assert recovered.result_code == "prepared"
    assert len(reservation.provider_permits) == 1
    assert reservation.authorized_pending == request.anticipated_usage
    assert len(journal.events(request.invocation_id)) == 1


def test_submission_artifact_recovers_before_submitted_event_without_reinvoke(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    journal, _, request = _journal_setup(tmp_path)
    driver = FakeProviderDriver(request.capabilities)
    journal.prepare(request, lease_owner=_OWNER, now=_now())
    original_advance = journal._store.advance

    def crash_before_submitted_event(*args: object, **kwargs: object) -> object:
        if len(args) > 1 and args[1] == "submitted":
            raise RuntimeError("injected submitted event crash")
        return original_advance(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(journal._store, "advance", crash_before_submitted_event)
    with pytest.raises(RuntimeError, match="submitted event crash"):
        journal.resume(
            request.invocation_id,
            driver=driver,
            validator=_validator,
            lease_owner=_OWNER,
            now=_now(),
        )
    assert journal.submission_path(request.invocation_id).exists()
    monkeypatch.setattr(journal._store, "advance", original_advance)

    recovered = journal.resume(
        request.invocation_id,
        driver=driver,
        validator=_validator,
        lease_owner=_OWNER,
        now=_now(),
    )

    assert recovered.result_code == "committed"
    assert driver.invoke_count == driver.bill_count == 1


def test_event_commit_projection_crash_rebuilds_without_provider_reinvoke(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    journal, _, request = _journal_setup(tmp_path)
    driver = FakeProviderDriver(request.capabilities)
    journal.prepare(request, lease_owner=_OWNER, now=_now())
    original_write = journal_store_module.__dict__["atomic_write_json"]
    injected = False

    def crash_after_dispatched_event(path: Path, payload: dict[str, object]) -> None:
        nonlocal injected
        if payload.get("state") == "dispatched" and not injected:
            injected = True
            raise RuntimeError("injected projection crash")
        original_write(path, payload)

    monkeypatch.setattr(
        journal_store_module, "atomic_write_json", crash_after_dispatched_event
    )
    with pytest.raises(RuntimeError, match="projection crash"):
        journal.resume(
            request.invocation_id,
            driver=driver,
            validator=_validator,
            lease_owner=_OWNER,
            now=_now(),
        )

    recovered = journal.resume(
        request.invocation_id,
        driver=driver,
        validator=_validator,
        lease_owner=_OWNER,
        now=_now(),
    )

    assert recovered.result_code == "committed"
    assert driver.invoke_count == driver.bill_count == 1


def test_deleted_projection_is_materialized_from_immutable_events(
    tmp_path: Path,
) -> None:
    journal, _, request = _journal_setup(tmp_path)
    journal.prepare(request, lease_owner=_OWNER, now=_now())
    projection_path = journal._store._projection_path(request.invocation_id)
    projection_path.unlink()

    rebuilt = journal.get(request.invocation_id)

    assert rebuilt is not None
    assert rebuilt.state == "prepared"
    assert projection_path.exists()


def test_dispatched_query_in_progress_waits_without_reinvoke(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    journal, _, request = _journal_setup(tmp_path)
    driver = FakeProviderDriver(request.capabilities)
    journal.prepare(request, lease_owner=_OWNER, now=_now())
    driver.raise_after_call = True
    with pytest.raises(RuntimeError):
        journal.resume(
            request.invocation_id,
            driver=driver,
            validator=_validator,
            lease_owner=_OWNER,
            now=_now(),
        )
    driver._submissions.clear()
    monkeypatch.setattr(
        driver,
        "query",
        lambda _: ProviderQueryResult(query_status="in_progress"),
    )

    result = journal.resume(
        request.invocation_id,
        driver=driver,
        validator=_validator,
        lease_owner=_OWNER,
        now=_now(),
    )

    assert result.result_code == "retry_wait"
    assert driver.invoke_count == 1


def test_driver_capability_drift_is_rejected_before_provider_access(
    tmp_path: Path,
) -> None:
    journal, _, request = _journal_setup(tmp_path)
    journal.prepare(request, lease_owner=_OWNER, now=_now())
    changed = ProviderRecoveryCapabilities(
        idempotency_support=False,
        invocation_query_support=False,
        cost_metering_support=True,
    )
    driver = FakeProviderDriver(changed)

    result = journal.resume(
        request.invocation_id,
        driver=driver,
        validator=_validator,
        lease_owner=_OWNER,
        now=_now(),
    )

    assert result.result_code == "invalid_request"
    assert driver.invoke_count == 0


@pytest.mark.parametrize(
    ("work_item_id", "owner_scope_id"),
    [
        ("work-item.other", "provider.role.delivery"),
        ("work-item.one", "provider.role.unreserved"),
    ],
)
def test_prepare_rejects_resource_identity_or_scope_mismatch_before_permit(
    tmp_path: Path,
    work_item_id: str,
    owner_scope_id: str,
) -> None:
    journal, governor, request = _journal_setup(tmp_path)
    changed = build_provider_invocation_request(
        project_id=request.project_id,
        work_item_id=work_item_id,
        stage_review_session_id=request.stage_review_session_id,
        owner_scope_id=owner_scope_id,
        candidate_digest=request.candidate_digest,
        assignment_digest=request.assignment_digest,
        epoch_id=request.epoch_id,
        provider_id=request.provider_id,
        request_digest=request.request_digest,
        reservation_id=request.reservation_id,
        expected_reservation_digest=request.expected_reservation_digest,
        expected_fencing_token=request.expected_fencing_token,
        anticipated_usage=request.anticipated_usage,
        capabilities=request.capabilities,
        command_id=f"{request.command_id}.mismatch",
        idempotency_key=f"{request.idempotency_key}.mismatch.{owner_scope_id}",
    )

    result = journal.prepare(changed, lease_owner=_OWNER, now=_now())
    reservation = governor.get_reservation(request.reservation_id)

    assert result.result_code == "invalid_resource_binding"
    assert not reservation.provider_permits


def test_prepare_rejects_unknown_reservation_ancestor_digest(tmp_path: Path) -> None:
    journal, governor, request = _journal_setup(tmp_path)
    changed = build_provider_invocation_request(
        project_id=request.project_id,
        work_item_id=request.work_item_id,
        stage_review_session_id=request.stage_review_session_id,
        owner_scope_id=request.owner_scope_id,
        candidate_digest=request.candidate_digest,
        assignment_digest=request.assignment_digest,
        epoch_id=request.epoch_id,
        provider_id=request.provider_id,
        request_digest=request.request_digest,
        reservation_id=request.reservation_id,
        expected_reservation_digest="sha256:unknown-reservation",
        expected_fencing_token=request.expected_fencing_token,
        anticipated_usage=request.anticipated_usage,
        capabilities=request.capabilities,
        command_id="command.provider.unknown-ancestor",
        idempotency_key="provider-idempotency.unknown-ancestor",
    )

    result = journal.prepare(changed, lease_owner=_OWNER, now=_now())

    assert result.result_code == "invalid_resource_binding"
    assert not governor.get_reservation(request.reservation_id).provider_permits


def test_provider_observes_durable_dispatched_event_before_invocation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    journal, _, request = _journal_setup(tmp_path)
    driver = FakeProviderDriver(request.capabilities)
    journal.prepare(request, lease_owner=_OWNER, now=_now())
    original_invoke = driver.invoke

    def assert_dispatched(target: ProviderInvocationRequest) -> ProviderSubmission:
        current = journal.get(target.invocation_id)
        assert current is not None
        assert current.state == "dispatched"
        return original_invoke(target)

    monkeypatch.setattr(driver, "invoke", assert_dispatched)

    result = journal.resume(
        request.invocation_id,
        driver=driver,
        validator=_validator,
        lease_owner=_OWNER,
        now=_now(),
    )

    assert result.result_code == "committed"


def test_submitted_resume_validates_existing_output_without_reinvoke(
    tmp_path: Path,
) -> None:
    journal, _, request = _journal_setup(tmp_path)
    driver = FakeProviderDriver(request.capabilities)
    journal.prepare(request, lease_owner=_OWNER, now=_now())

    def crash_during_validation(_: ProviderSubmission) -> str:
        raise RuntimeError("injected validation crash")

    with pytest.raises(RuntimeError, match="validation crash"):
        journal.resume(
            request.invocation_id,
            driver=driver,
            validator=crash_during_validation,
            lease_owner=_OWNER,
            now=_now(),
        )
    recovered = journal.resume(
        request.invocation_id,
        driver=driver,
        validator=_validator,
        lease_owner=_OWNER,
        now=_now(),
    )

    assert recovered.result_code == "committed"
    assert driver.invoke_count == 1


def test_validated_resume_reuses_resource_settlement_operation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    journal, governor, request = _journal_setup(tmp_path)
    driver = FakeProviderDriver(request.capabilities)
    journal.prepare(request, lease_owner=_OWNER, now=_now())
    original_settle = governor.settle_provider_call

    def settle_then_crash(*args: object, **kwargs: object) -> object:
        original_settle(*args, **kwargs)  # type: ignore[arg-type]
        raise RuntimeError("injected settlement return crash")

    monkeypatch.setattr(governor, "settle_provider_call", settle_then_crash)
    with pytest.raises(RuntimeError, match="settlement return crash"):
        journal.resume(
            request.invocation_id,
            driver=driver,
            validator=_validator,
            lease_owner=_OWNER,
            now=_now(),
        )
    monkeypatch.setattr(governor, "settle_provider_call", original_settle)

    recovered = journal.resume(
        request.invocation_id,
        driver=driver,
        validator=_validator,
        lease_owner=_OWNER,
        now=_now(),
    )

    assert recovered.result_code == "committed"
    assert driver.invoke_count == 1
    assert governor.get_reservation(request.reservation_id).usage == _actual_usage()


def test_committed_resume_returns_existing_result_without_driver_call(
    tmp_path: Path,
) -> None:
    journal, _, request = _journal_setup(tmp_path)
    first_driver = FakeProviderDriver(request.capabilities)
    journal.prepare(request, lease_owner=_OWNER, now=_now())
    committed = journal.resume(
        request.invocation_id,
        driver=first_driver,
        validator=_validator,
        lease_owner=_OWNER,
        now=_now(),
    )
    unused_driver = FakeProviderDriver(request.capabilities)

    repeated = journal.resume(
        request.invocation_id,
        driver=unused_driver,
        validator=_validator,
        lease_owner=_OWNER,
        now=_now(),
    )

    assert repeated.invocation == committed.invocation
    assert unused_driver.invoke_count == 0


def test_same_idempotency_key_cannot_change_request_lineage(tmp_path: Path) -> None:
    journal, _, request = _journal_setup(tmp_path)
    prepared = journal.prepare(request, lease_owner=_OWNER, now=_now())
    changed = build_provider_invocation_request(
        project_id=request.project_id,
        work_item_id=request.work_item_id,
        stage_review_session_id=request.stage_review_session_id,
        owner_scope_id=request.owner_scope_id,
        candidate_digest=request.candidate_digest,
        assignment_digest=request.assignment_digest,
        epoch_id=request.epoch_id,
        provider_id=request.provider_id,
        request_digest="sha256:changed-request",
        reservation_id=request.reservation_id,
        expected_reservation_digest=request.expected_reservation_digest,
        expected_fencing_token=request.expected_fencing_token,
        anticipated_usage=request.anticipated_usage,
        capabilities=request.capabilities,
        command_id=request.command_id,
        idempotency_key=request.idempotency_key,
    )

    conflict = journal.prepare(changed, lease_owner=_OWNER, now=_now())

    assert prepared.result_code == "prepared"
    assert changed.invocation_id == request.invocation_id
    assert conflict.result_code == "state_corrupt"
    assert len(journal.events(request.invocation_id)) == 1


def test_tampered_submission_blocks_recovery_without_reinvoke(tmp_path: Path) -> None:
    journal, _, request = _journal_setup(tmp_path)
    driver = FakeProviderDriver(request.capabilities)
    journal.prepare(request, lease_owner=_OWNER, now=_now())

    def stop_at_submitted(_: ProviderSubmission) -> str:
        raise RuntimeError("stop at submitted")

    with pytest.raises(RuntimeError):
        journal.resume(
            request.invocation_id,
            driver=driver,
            validator=stop_at_submitted,
            lease_owner=_OWNER,
            now=_now(),
        )
    path = journal.submission_path(request.invocation_id)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["output_payload"] = {"decision": "FAIL"}
    path.write_text(json.dumps(payload), encoding="utf-8")

    blocked = journal.resume(
        request.invocation_id,
        driver=driver,
        validator=_validator,
        lease_owner=_OWNER,
        now=_now(),
    )

    assert blocked.result_code == "state_corrupt"
    assert driver.invoke_count == 1


def test_submission_rejects_incomplete_accounted_usage(tmp_path: Path) -> None:
    _, _, request = _journal_setup(tmp_path)

    with pytest.raises(ValidationError, match="accounted usage"):
        build_provider_submission(
            request,
            provider_call_id="provider-call.invalid",
            output_payload={"decision": "PASS"},
            accounted_usage=metered_provider_usage(ResourceAmounts()),
        )


def test_parallel_prepared_invocations_do_not_invalidate_earlier_permit(
    tmp_path: Path,
) -> None:
    journal, _, first = _journal_setup(tmp_path)
    second = _request_variant(
        first,
        command_id="command.provider.two",
        idempotency_key="provider-idempotency.two",
        owner_scope_id="provider.role.evolution",
    )
    first_prepared = journal.prepare(first, lease_owner=_OWNER, now=_now())
    second_prepared = journal.prepare(second, lease_owner=_OWNER, now=_now())

    committed = journal.resume(
        first.invocation_id,
        driver=FakeProviderDriver(first.capabilities),
        validator=_validator,
        lease_owner=_OWNER,
        now=_now(),
    )

    assert first_prepared.result_code == second_prepared.result_code == "prepared"
    assert committed.result_code == "committed"


def test_submitted_projection_requires_matching_immutable_artifact(
    tmp_path: Path,
) -> None:
    journal, _, request = _journal_setup(tmp_path)
    driver = FakeProviderDriver(request.capabilities)
    journal.prepare(request, lease_owner=_OWNER, now=_now())

    def stop_at_submitted(_: ProviderSubmission) -> str:
        raise RuntimeError("stop at submitted")

    with pytest.raises(RuntimeError):
        journal.resume(
            request.invocation_id,
            driver=driver,
            validator=stop_at_submitted,
            lease_owner=_OWNER,
            now=_now(),
        )
    journal.submission_path(request.invocation_id).unlink()

    with pytest.raises(SharedStateIntegrityError, match="submission"):
        journal.get(request.invocation_id)


def test_reducer_rejects_submission_digest_change_after_submitted(
    tmp_path: Path,
) -> None:
    journal, governor, request = _journal_setup(tmp_path)
    driver = FakeProviderDriver(request.capabilities)
    journal.prepare(request, lease_owner=_OWNER, now=_now())
    original_settle = governor.settle_provider_call
    governor.settle_provider_call = lambda *args, **kwargs: (_ for _ in ()).throw(  # type: ignore[method-assign]
        RuntimeError("stop at validated")
    )
    with pytest.raises(RuntimeError):
        journal.resume(
            request.invocation_id,
            driver=driver,
            validator=_validator,
            lease_owner=_OWNER,
            now=_now(),
        )
    governor.settle_provider_call = original_settle  # type: ignore[method-assign]
    event_path = journal._store._event_path(request.invocation_id, 4)
    payload = json.loads(event_path.read_text(encoding="utf-8"))
    payload["submission_digest"] = "sha256:changed-submission"
    payload["event_digest"] = ""
    payload["event_digest"] = event_digest(payload)
    event_path.write_text(json.dumps(payload), encoding="utf-8")
    journal._store._projection_path(request.invocation_id).unlink()

    with pytest.raises(SharedStateIntegrityError, match="submission"):
        journal.get(request.invocation_id)


def test_event_directory_cannot_relabel_another_invocation(tmp_path: Path) -> None:
    journal, _, request = _journal_setup(tmp_path)
    other = _request_variant(
        request,
        command_id="command.provider.relabelled",
        idempotency_key="provider-idempotency.relabelled",
        owner_scope_id=request.owner_scope_id,
    )
    journal.prepare(request, lease_owner=_OWNER, now=_now())
    source = journal._store._events_dir(request.invocation_id)
    target = journal._store._events_dir(other.invocation_id)
    shutil.copytree(source, target)

    with pytest.raises(SharedStateIntegrityError, match="directory"):
        journal.get(other.invocation_id)


def test_committed_resume_rechecks_resource_settlement_truth(tmp_path: Path) -> None:
    journal, governor, request = _journal_setup(tmp_path)
    driver = FakeProviderDriver(request.capabilities)
    journal.prepare(request, lease_owner=_OWNER, now=_now())
    committed = journal.resume(
        request.invocation_id,
        driver=driver,
        validator=_validator,
        lease_owner=_OWNER,
        now=_now(),
    )
    assert committed.result_code == "committed"
    resource_event = governor._store.events_dir / "00000000000000000004.json"
    resource_event.unlink()

    recovered = journal.resume(
        request.invocation_id,
        driver=FakeProviderDriver(request.capabilities),
        validator=_validator,
        lease_owner=_OWNER,
        now=_now(),
    )

    assert recovered.result_code == "state_corrupt"


def test_validated_after_lease_expiry_reconciles_known_actual_usage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    journal, governor, request = _journal_setup(tmp_path)
    driver = FakeProviderDriver(request.capabilities)
    journal.prepare(request, lease_owner=_OWNER, now=_now())
    original_settle = governor.settle_provider_call

    def stop_before_settlement(*args: object, **kwargs: object) -> object:
        raise RuntimeError("stop before resource settlement")

    monkeypatch.setattr(governor, "settle_provider_call", stop_before_settlement)
    with pytest.raises(RuntimeError, match="before resource settlement"):
        journal.resume(
            request.invocation_id,
            driver=driver,
            validator=_validator,
            lease_owner=_OWNER,
            now=_now(),
        )
    monkeypatch.setattr(governor, "settle_provider_call", original_settle)
    later = _now() + timedelta(minutes=5)
    governor.record_usage(
        request.reservation_id,
        delta=ResourceAmounts(review_passes=1),
        lease_owner=_OWNER,
        expected_fencing_token=request.expected_fencing_token,
        operation_id="operation.trigger-expiry",
        now=later,
    )

    recovered = journal.resume(
        request.invocation_id,
        driver=driver,
        validator=_validator,
        lease_owner=_OWNER,
        now=later,
    )
    reservation = governor.get_reservation(request.reservation_id)

    assert recovered.result_code == "committed"
    assert reservation.usage == _actual_usage()
    assert recovered.invocation is not None
    settlement_event = governor.get_operation_event(
        recovered.invocation.resource_settlement_operation_id
    )
    assert settlement_event is not None
    assert settlement_event.target_reservation_digest == (
        recovered.invocation.settlement_reservation_digest
    )
    assert settlement_event.actual_usage == _actual_usage()


def test_provider_output_above_permit_is_persisted_as_observed_overrun(
    tmp_path: Path,
) -> None:
    journal, governor, request = _journal_setup(tmp_path)
    driver = FakeProviderDriver(request.capabilities)
    driver._submissions[request.idempotency_key] = build_provider_submission(
        request,
        provider_call_id="provider-call.excess",
        output_payload={"decision": "PASS"},
        accounted_usage=metered_provider_usage(
            _actual_usage().model_copy(update={"tokens": 200})
        ),
    )
    journal.prepare(request, lease_owner=_OWNER, now=_now())

    result = journal.resume(
        request.invocation_id,
        driver=driver,
        validator=_validator,
        lease_owner=_OWNER,
        now=_now(),
    )

    assert result.result_code == "committed"
    assert journal.submission_path(request.invocation_id).exists()
    reservation = governor.get_reservation(request.reservation_id)
    assert reservation.usage.tokens == 200
    assert reservation.observed_overrun.tokens == 100


def test_provider_submission_from_other_invocation_is_output_error_not_state_error(
    tmp_path: Path,
) -> None:
    journal, _, request = _journal_setup(tmp_path)
    other = _request_variant(
        request,
        command_id="command.provider.other",
        idempotency_key="provider-idempotency.other",
        owner_scope_id=request.owner_scope_id,
    )
    driver = FakeProviderDriver(request.capabilities)
    driver._submissions[request.idempotency_key] = build_provider_submission(
        other,
        provider_call_id="provider-call.wrong-lineage",
        output_payload={"decision": "PASS"},
        accounted_usage=metered_provider_usage(_actual_usage()),
    )
    journal.prepare(request, lease_owner=_OWNER, now=_now())

    result = journal.resume(
        request.invocation_id,
        driver=driver,
        validator=_validator,
        lease_owner=_OWNER,
        now=_now(),
    )

    assert result.result_code == "provider_output_invalid"
    assert not journal.submission_path(request.invocation_id).exists()
    assert journal.get(request.invocation_id).state == "dispatched"  # type: ignore[union-attr]


def test_validator_rejection_settles_usage_and_becomes_terminal(
    tmp_path: Path,
) -> None:
    journal, governor, request = _journal_setup(tmp_path)
    driver = FakeProviderDriver(request.capabilities)
    journal.prepare(request, lease_owner=_OWNER, now=_now())

    def reject(_: ProviderSubmission) -> str:
        raise ValueError("provider output does not satisfy schema")

    result = journal.resume(
        request.invocation_id,
        driver=driver,
        validator=reject,
        lease_owner=_OWNER,
        now=_now(),
    )

    assert result.result_code == "provider_output_invalid"
    assert result.invocation is not None
    assert result.invocation.state == "executed_invalid"
    reservation = governor.get_reservation(request.reservation_id)
    assert reservation.usage == _actual_usage()
    assert not reservation.authorized_pending.any_positive()
    assert not reservation.provider_permits

    replay = journal.resume(
        request.invocation_id,
        driver=driver,
        validator=reject,
        lease_owner=_OWNER,
        now=_now(),
    )

    assert replay.result_code == "provider_output_invalid"
    assert driver.invoke_count == 1


def _journal_setup(
    tmp_path: Path,
    *,
    capabilities: ProviderRecoveryCapabilities | None = None,
) -> tuple[ProviderInvocationJournal, ResourceGovernor, ProviderInvocationRequest]:
    governor = _governor(tmp_path)
    final = _final_reservation(governor)
    selected = capabilities or ProviderRecoveryCapabilities(
        idempotency_support=True,
        invocation_query_support=True,
        cost_metering_support=True,
    )
    request = build_provider_invocation_request(
        project_id=final.project_id,
        work_item_id=final.work_item_id,
        stage_review_session_id=final.stage_review_session_id,
        owner_scope_id=final.provider_scope_ids[0],
        candidate_digest="sha256:candidate",
        assignment_digest="sha256:assignment",
        epoch_id="",
        provider_id="provider.test",
        request_digest="sha256:provider-request",
        reservation_id=final.reservation_id,
        expected_reservation_digest=final.reservation_digest,
        expected_fencing_token=final.fencing_token,
        anticipated_usage=_provider_anticipated(),
        capabilities=selected,
        command_id="command.provider.one",
        idempotency_key="provider-idempotency.one",
    )
    journal = ProviderInvocationJournal(
        tmp_path,
        project_id=final.project_id,
        resource_governor=governor,
    )
    return journal, governor, request


def _actual_usage() -> ResourceAmounts:
    return ResourceAmounts(
        provider_calls=1,
        tokens=80,
        cost=0.8,
        active_wall_clock=8,
    )


def _validator(submission: ProviderSubmission) -> str:
    assert submission.output_payload == {"decision": "PASS"}
    return "sha256:validated-provider-output"


def _request_variant(
    request: ProviderInvocationRequest,
    *,
    command_id: str,
    idempotency_key: str,
    owner_scope_id: str,
) -> ProviderInvocationRequest:
    return build_provider_invocation_request(
        project_id=request.project_id,
        work_item_id=request.work_item_id,
        stage_review_session_id=request.stage_review_session_id,
        owner_scope_id=owner_scope_id,
        candidate_digest=request.candidate_digest,
        assignment_digest=request.assignment_digest,
        epoch_id=request.epoch_id,
        provider_id=request.provider_id,
        request_digest=request.request_digest,
        reservation_id=request.reservation_id,
        expected_reservation_digest=request.expected_reservation_digest,
        expected_fencing_token=request.expected_fencing_token,
        anticipated_usage=request.anticipated_usage,
        capabilities=request.capabilities,
        command_id=command_id,
        idempotency_key=idempotency_key,
    )
