from __future__ import annotations

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
from ai_sdlc.core.stage_review.optimization.defaults import (
    _baseline_usage_estimate_policy as baseline_usage_estimate_policy,
)
from ai_sdlc.core.stage_review.optimization.shadow import (
    OptimizationShadowAssignment,
    OptimizationShadowAssignmentStore,
    ProspectiveShadowService,
    ShadowProviderSpec,
    ShadowSessionInput,
)
from ai_sdlc.core.stage_review.optimization.shadow_observations import (
    OptimizationShadowObservationStore,
    ShadowOutcome,
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
    )

    assert first.invocation_result.result_code == "committed"
    assert repeated.late_critical_event_digest == "sha256:late-critical-event"
    assert first.late_critical_event_digest == repeated.late_critical_event_digest
    assert len(recorded) == 1
    assert driver.bill_count == 1
    invocation = first.invocation_result.invocation
    assert invocation is not None
    observation = OptimizationShadowObservationStore(
        tmp_path, project_id="project.shared"
    ).record_committed(
        first.assignment,
        journal=controller.provider_journal,
        provider_invocation_id=invocation.invocation_id,
        baseline=ShadowOutcome(terminal_outcome="consumed"),
        challenger=ShadowOutcome(
            critical_detected=True,
            terminal_outcome="consumed",
        ),
        evaluation_binding_id="evaluation-binding.shadow-independent",
        label_source_digests=(first.late_critical_event_digest,),
        observed_at="2026-07-23T00:00:00Z",
    )
    assert observation.provider_submission_digest
    assert observation.validation_digest == invocation.validation_digest
    assert observation.resource_settlement_event_digest == (
        invocation.resource_settlement_event_digest
    )


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
