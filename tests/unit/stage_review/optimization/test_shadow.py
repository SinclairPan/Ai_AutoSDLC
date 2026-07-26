from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
from tests.unit.stage_review.optimization.resource_fixture import offline_reservation
from tests.unit.stage_review.optimization.test_controller import (
    _controller,
    _maintenance_budget,
    _record_threshold,
)
from tests.unit.stage_review.test_provider_journal import FakeProviderDriver
from tests.unit.stage_review.test_resources import _provider_anticipated

from ai_sdlc.core.stage_review.artifacts import SharedStateIntegrityError
from ai_sdlc.core.stage_review.optimization.controller_models import (
    OptimizationEpoch,
)
from ai_sdlc.core.stage_review.optimization.defaults import (
    _baseline_usage_estimate_policy as baseline_usage_estimate_policy,
)
from ai_sdlc.core.stage_review.optimization.pipeline_effects import (
    EpochRuntimeAuthorizer,
    allow_effect,
)
from ai_sdlc.core.stage_review.optimization.shadow import (
    OptimizationShadowAssignment,
    OptimizationShadowAssignmentStore,
    ProspectiveShadowService,
    ShadowProviderSpec,
    ShadowSessionInput,
)
from ai_sdlc.core.stage_review.provider_journal import (
    ProviderInvocationRequest,
    ProviderRecoveryCapabilities,
    ProviderSubmission,
    build_provider_submission,
)
from ai_sdlc.core.stage_review.provider_usage_models import metered_provider_usage
from ai_sdlc.core.stage_review.resource_models import ResourceAmounts


class _CriticalDriver(FakeProviderDriver):
    def invoke(self, request: ProviderInvocationRequest) -> ProviderSubmission:
        self.invoke_count += 1
        key = request.idempotency_key
        if key not in self._submissions:
            self.bill_count += 1
            self._submissions[key] = build_provider_submission(
                request,
                provider_call_id=f"provider-call.{self.bill_count}",
                output_payload={
                    "severity": "P1",
                    "evidence_confirmed": True,
                    "finding_authority_digest": "sha256:finding-authority",
                },
                accounted_usage=metered_provider_usage(
                    ResourceAmounts(
                        provider_calls=1,
                        tokens=80,
                        cost=0.8,
                        active_wall_clock=8,
                    )
                ),
                egress_receipt_digests=("sha256:shadow-egress-receipt",),
            )
        return self._submissions[key]


class _CallableLease:
    epoch_fencing_epoch = 1
    epoch_claim_digest = "sha256:test-shadow-epoch-claim"

    def __init__(self, gate: Callable[[], None]) -> None:
        self._gate = gate

    def __call__(self) -> None:
        self._gate()

    def commit(self, operation: Callable[[], object]) -> object:
        self._gate()
        return operation()


class _BlockAfterSettlementLease(_CallableLease):
    def __init__(self) -> None:
        super().__init__(lambda: None)
        self.commits = 0
        self.blocked = False

    def __call__(self) -> None:
        if self.blocked:
            raise SharedStateIntegrityError("runtime bundle drifted")

    def commit(self, operation: Callable[[], object]) -> object:
        self()
        result = operation()
        self.commits += 1
        if self.commits == 4:
            self.blocked = True
        return result


def _epoch_authorizer(
    gate: Callable[[], None],
    epoch: OptimizationEpoch,
) -> EpochRuntimeAuthorizer:
    lease = (
        gate
        if callable(getattr(gate, "commit", None))
        else _CallableLease(gate)
    )
    return EpochRuntimeAuthorizer.for_epoch(lease, lambda: None, epoch)


def test_shadow_assignment_only_accepts_new_post_epoch_session(tmp_path: Path) -> None:
    store = OptimizationShadowAssignmentStore(
        tmp_path, project_id="project.shared"
    )
    input_package = _input(31)
    assignment = store.assign(
        epoch_id="optimization-epoch.001",
        finalist_candidate_digest="sha256:finalist",
        session=input_package,
        epoch_session_sequence_high_watermark=30,
    )

    assert store.assign(
        epoch_id="optimization-epoch.001",
        finalist_candidate_digest="sha256:finalist",
        session=input_package,
        epoch_session_sequence_high_watermark=30,
    ) == assignment
    assert assignment.session_id == "session.031"
    assert not {
        "panel_plan_digest",
        "quorum_digest",
        "finding_ledger_digest",
        "certificate_digest",
        "session_budget_digest",
    } & set(OptimizationShadowAssignment.model_fields)
    with pytest.raises(SharedStateIntegrityError, match="post-epoch"):
        store.assign(
            epoch_id="optimization-epoch.001",
            finalist_candidate_digest="sha256:finalist",
            session=_input(30),
            epoch_session_sequence_high_watermark=30,
        )
    with pytest.raises(SharedStateIntegrityError, match="already assigned"):
        store.assign(
            epoch_id="optimization-epoch.002",
            finalist_candidate_digest="sha256:other-finalist",
            session=input_package,
            epoch_session_sequence_high_watermark=30,
        )


def test_confirmed_shadow_p1_is_forwarded_once_to_late_critical_path(
    tmp_path: Path,
) -> None:
    controller, governor = _controller(tmp_path)
    _record_threshold(controller)
    maintenance = controller.advance_optimization(
        "project.shared", _maintenance_budget(), owner_id="controller.worker"
    )
    assert maintenance.epoch is not None
    epoch = maintenance.epoch
    reservation = offline_reservation(governor, epoch.epoch_id, fencing_epoch=2)
    store = OptimizationShadowAssignmentStore(
        tmp_path, project_id="project.shared"
    )
    recorded: list[str] = []

    def record_late_critical(
        assignment: OptimizationShadowAssignment,
        submission: ProviderSubmission,
    ) -> str:
        recorded.append(submission.submission_digest)
        assert assignment.epoch_id == epoch.epoch_id
        return "sha256:late-critical-event"

    service = ProspectiveShadowService(
        store=store,
        journal=controller.provider_journal,
        resource_governor=governor,
        late_critical_recorder=record_late_critical,
    )
    capabilities = ProviderRecoveryCapabilities(
        idempotency_support=True,
        invocation_query_support=True,
        cost_metering_support=True,
    )
    driver = _CriticalDriver(capabilities)
    provider = ShadowProviderSpec(
        provider_id="provider.test",
        request_digest="sha256:shadow-provider-request",
        anticipated_usage=_provider_anticipated(),
        capabilities=capabilities,
    )

    first = service.evaluate(
        epoch_id=epoch.epoch_id,
        finalist_candidate_digest="sha256:finalist",
        session=_input(31),
        epoch_session_sequence_high_watermark=30,
        provider=provider,
        driver=driver,
        validator=lambda _: "sha256:validated-shadow-output",
        reservation_id=reservation.reservation_id,
        lease_owner=reservation.lease_owner,
        runtime_bundle_manifest_digest=epoch.runtime_bundle_manifest_digest,
        authorize_dispatch=_epoch_authorizer(allow_effect, epoch),
    )
    repeated = service.evaluate(
        epoch_id=epoch.epoch_id,
        finalist_candidate_digest="sha256:finalist",
        session=_input(31),
        epoch_session_sequence_high_watermark=30,
        provider=provider,
        driver=driver,
        validator=lambda _: "sha256:validated-shadow-output",
        reservation_id=reservation.reservation_id,
        lease_owner=reservation.lease_owner,
        runtime_bundle_manifest_digest=epoch.runtime_bundle_manifest_digest,
        authorize_dispatch=_epoch_authorizer(allow_effect, epoch),
    )

    def reject_stale_runtime() -> None:
        raise SharedStateIntegrityError("runtime bundle drifted")

    blocked_replay = service.evaluate(
        epoch_id=epoch.epoch_id,
        finalist_candidate_digest="sha256:finalist",
        session=_input(31),
        epoch_session_sequence_high_watermark=30,
        provider=provider,
        driver=driver,
        validator=lambda _: "sha256:validated-shadow-output",
        reservation_id=reservation.reservation_id,
        lease_owner=reservation.lease_owner,
        runtime_bundle_manifest_digest=epoch.runtime_bundle_manifest_digest,
        authorize_dispatch=_epoch_authorizer(reject_stale_runtime, epoch),
    )

    assert first.invocation_result.result_code == "committed"
    assert repeated.late_critical_event_digest == "sha256:late-critical-event"
    assert blocked_replay.invocation_result.result_code == "dispatch_unauthorized"
    assert blocked_replay.late_critical_event_digest == ""
    assert first.late_critical_event_digest == repeated.late_critical_event_digest
    assert len(recorded) == 1
    assert driver.bill_count == 1
    invocation = first.invocation_result.invocation
    assert invocation is not None
    assert invocation.validation_digest
    assert invocation.resource_settlement_event_digest


def test_late_critical_publication_reauthorizes_after_journal_settlement(
    tmp_path: Path,
) -> None:
    controller, governor = _controller(tmp_path)
    _record_threshold(controller)
    maintenance = controller.advance_optimization(
        "project.shared", _maintenance_budget(), owner_id="controller.worker"
    )
    assert maintenance.epoch is not None
    epoch = maintenance.epoch
    reservation = offline_reservation(governor, epoch.epoch_id, fencing_epoch=2)
    store = OptimizationShadowAssignmentStore(
        tmp_path, project_id="project.shared"
    )
    recorded: list[str] = []
    service = ProspectiveShadowService(
        store=store,
        journal=controller.provider_journal,
        resource_governor=governor,
        late_critical_recorder=lambda _assignment, submission: (
            recorded.append(submission.submission_digest)
            or "sha256:late-critical-event"
        ),
    )
    capabilities = ProviderRecoveryCapabilities(
        idempotency_support=True,
        invocation_query_support=True,
        cost_metering_support=True,
    )
    prepared = service.prepare(
        epoch_id=epoch.epoch_id,
        finalist_candidate_digest="sha256:finalist",
        session=_input(31),
        epoch_session_sequence_high_watermark=30,
        provider=ShadowProviderSpec(
            provider_id="provider.test",
            request_digest="sha256:shadow-provider-request",
            anticipated_usage=_provider_anticipated(),
            capabilities=capabilities,
        ),
        reservation_id=reservation.reservation_id,
        lease_owner=reservation.lease_owner,
        runtime_bundle_manifest_digest=epoch.runtime_bundle_manifest_digest,
    )
    lease = _BlockAfterSettlementLease()

    with pytest.raises(SharedStateIntegrityError, match="runtime bundle drifted"):
        service.resume(
            prepared,
            driver=_CriticalDriver(capabilities),
            validator=lambda _: "sha256:validated-shadow-output",
            lease_owner=reservation.lease_owner,
            expected_runtime_bundle_manifest_digest=(
                epoch.runtime_bundle_manifest_digest
            ),
            authorize_dispatch=EpochRuntimeAuthorizer.for_epoch(
                lease,
                lambda: None,
                epoch,
            ),
        )

    invocation = controller.provider_journal.get(
        prepared.invocation.invocation_id
    )
    assert invocation is not None and invocation.state == "committed"
    assert store.late_critical_signal(prepared.assignment.assignment_id) is None
    assert recorded == []


def _input(sequence: int) -> ShadowSessionInput:
    policy = baseline_usage_estimate_policy()
    return ShadowSessionInput(
        session_id=f"session.{sequence:03d}",
        session_sequence=sequence,
        initial_candidate_digest=f"sha256:candidate-{sequence}",
        risk_profile_digest=f"sha256:risk-{sequence}",
        visible_evidence_digest=f"sha256:evidence-{sequence}",
        active_baseline_result_digest=f"sha256:baseline-result-{sequence}",
        baseline_snapshot_digest="sha256:baseline-snapshot",
        usage_estimation_policy_version=policy.version,
        usage_estimation_policy_digest=policy.policy_digest,
    )
