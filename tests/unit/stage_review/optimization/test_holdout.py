from __future__ import annotations

from pathlib import Path

import pytest
from tests.unit.stage_review.optimization.resource_fixture import offline_reservation
from tests.unit.stage_review.optimization.test_controller import _controller
from tests.unit.stage_review.test_provider_journal import (
    FakeProviderDriver,
    _validator,
)
from tests.unit.stage_review.test_resources import _provider_anticipated

from ai_sdlc.core.stage_review.artifacts import SharedStateIntegrityError
from ai_sdlc.core.stage_review.optimization.holdout import (
    HoldoutCommitmentStore,
    HoldoutEvaluationService,
    HoldoutProviderSpec,
    HoldoutQueryRequest,
)
from ai_sdlc.core.stage_review.provider_journal import ProviderRecoveryCapabilities
from ai_sdlc.core.stage_review.resource_models import ResourceAmounts
from ai_sdlc.core.stage_review.resources import ResourceGovernor


def test_commitment_consumes_monotonic_test_sequence_and_alpha(tmp_path: Path) -> None:
    store = HoldoutCommitmentStore(
        tmp_path, project_id="project.shared", familywise_alpha=0.05
    )

    first = store.commit(_request(1))
    second = store.commit(_request(2))

    assert first.test_sequence == 1
    assert first.alpha_i == pytest.approx(0.025)
    assert second.test_sequence == 2
    assert second.alpha_i == pytest.approx(0.05 / 6)
    assert second.previous_commitment_digest == first.commitment_digest
    assert first.commit_fencing_epoch == 1
    assert second.commit_fencing_epoch == 2
    assert first.commit_claim_digest != second.commit_claim_digest
    assert first.epoch_lease_fencing_epoch == 1
    assert first.epoch_lease_claim_digest == "sha256:epoch-claim.1"
    assert store.cumulative_alpha == pytest.approx(0.05 / 2 + 0.05 / 6)


def test_commitment_recovers_from_sealed_segment_and_keeps_sequence(
    tmp_path: Path,
) -> None:
    store = HoldoutCommitmentStore(
        tmp_path, project_id="project.shared", familywise_alpha=0.05
    )
    first = store.commit(_request(1))
    second = store.commit(_request(2))
    prepared = store.storage._prepare_compaction("query-commitments")
    assert prepared is not None

    governor = ResourceGovernor(
        tmp_path,
        project_id="project.shared",
        foreground_capacity=ResourceAmounts(),
        offline_optimization_capacity=ResourceAmounts(),
        lock_timeout_seconds=1,
    )
    with governor.storage_bundle(
        bundle_class="reclamation",
        bundle_bytes=prepared.required_bundle_bytes,
        net_reclaim_bytes=prepared.net_reclaim_bytes,
        policy=store.storage.policy,
        operation_id="compactor.holdout.bundle",
    ) as bundle, store.commit_leases.acquire(
        owner_id="compactor.holdout",
        scope="compaction",
        expected_head=second.commitment_digest,
    ) as lease:
        store.storage._commit_compaction(
            prepared, lease=lease, resource_bundle=bundle
        )

    assert store.commitments() == (first, second)
    assert store.commit(_request(1)) == first
    third = store.commit(_request(3))
    assert third.test_sequence == 3
    assert third.previous_commitment_digest == second.commitment_digest


def test_same_holdout_query_is_idempotent_and_generation_is_single_use(
    tmp_path: Path,
) -> None:
    store = HoldoutCommitmentStore(
        tmp_path, project_id="project.shared", familywise_alpha=0.05
    )
    request = _request(1)
    committed = store.commit(request)

    assert store.commit(request) == committed
    with pytest.raises(SharedStateIntegrityError, match="generation"):
        store.commit(
            request.model_copy(
                update={
                    "finalist_candidate_digest": "sha256:other-finalist",
                    "provider_query_idempotency_key": "holdout-query.other-finalist",
                }
            )
        )
    with pytest.raises(SharedStateIntegrityError, match="session"):
        store.commit(
            _request(2).model_copy(update={"holdout_session_ids": ("session.001",)})
        )


def test_committed_query_survives_crash_without_requery_or_alpha_refund(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller, governor = _controller(tmp_path)
    reservation = offline_reservation(governor, "optimization-epoch.001")
    store = HoldoutCommitmentStore(
        tmp_path, project_id="project.shared", familywise_alpha=0.05
    )
    service = HoldoutEvaluationService(
        store=store,
        journal=controller.provider_journal,
        resource_governor=governor,
    )
    capabilities = ProviderRecoveryCapabilities(
        idempotency_support=True,
        invocation_query_support=True,
        cost_metering_support=True,
    )
    driver = FakeProviderDriver(capabilities)
    prepare = controller.provider_journal.prepare
    preparation_codes: list[str] = []

    def capture_prepare(*args: object, **kwargs: object) -> object:
        result = prepare(*args, **kwargs)  # type: ignore[arg-type]
        preparation_codes.append(result.result_code)
        return result

    monkeypatch.setattr(controller.provider_journal, "prepare", capture_prepare)
    request = _request(1)
    provider = HoldoutProviderSpec(
        provider_id="provider.test",
        request_digest="sha256:holdout-provider-request",
        anticipated_usage=_provider_anticipated(),
        capabilities=capabilities,
    )

    with pytest.raises(RuntimeError, match="commitment crash"):
        service.evaluate(
            request,
            provider=provider,
            driver=driver,
            validator=_validator,
            reservation_id=reservation.reservation_id,
            lease_owner=reservation.lease_owner,
            crash_after_commit=True,
        )
    recovered = service.evaluate(
        request,
        provider=provider,
        driver=driver,
        validator=_validator,
        reservation_id=reservation.reservation_id,
        lease_owner=reservation.lease_owner,
    )
    repeated = service.evaluate(
        request,
        provider=provider,
        driver=driver,
        validator=_validator,
        reservation_id=reservation.reservation_id,
        lease_owner=reservation.lease_owner,
    )

    assert recovered.invocation_result.result_code == "committed"
    assert repeated.invocation_result.invocation == recovered.invocation_result.invocation
    assert driver.bill_count == 1
    assert preparation_codes == ["prepared", "committed"]
    assert len(store.commitments()) == 1
    assert store.cumulative_alpha == pytest.approx(0.025)


def _request(index: int) -> HoldoutQueryRequest:
    return HoldoutQueryRequest(
        epoch_id=f"optimization-epoch.{index:03d}",
        hypothesis_digest=f"sha256:hypothesis-{index}",
        holdout_generation_id=f"holdout-generation.{index:03d}",
        baseline_snapshot_digest="sha256:baseline",
        finalist_candidate_digest=f"sha256:finalist-{index}",
        holdout_session_ids=(f"session.{index:03d}",),
        provider_query_idempotency_key=f"holdout-query.{index:03d}",
        epoch_lease_fencing_epoch=1,
        epoch_lease_claim_digest="sha256:epoch-claim.1",
    )
