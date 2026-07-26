from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
from tests.unit.stage_review.optimization.test_shadow_provider import (
    _executions,
    _RemoteShadowBroker,
    _transport,
)

from ai_sdlc.core.stage_review import codex_review_runtime
from ai_sdlc.core.stage_review.artifacts import (
    SharedStateIntegrityError,
    create_json_exclusive,
    resolve_canonical_shared_state,
)
from ai_sdlc.core.stage_review.optimization.controller_models import OptimizationEpoch
from ai_sdlc.core.stage_review.optimization.controller_store import (
    OptimizationControllerStore,
)
from ai_sdlc.core.stage_review.optimization.defaults import (
    _baseline_foreground_capacity as baseline_foreground_capacity,
)
from ai_sdlc.core.stage_review.optimization.defaults import (
    _baseline_usage_estimate_policy as baseline_usage_estimate_policy,
)
from ai_sdlc.core.stage_review.optimization.defaults import (
    baseline_epoch_budget_policy,
    baseline_offline_capacity,
)
from ai_sdlc.core.stage_review.optimization.maintenance_window import (
    EpochLeaseGuard,
)
from ai_sdlc.core.stage_review.optimization.maintenance_window import (
    _optimization_resource_session_id as optimization_resource_session_id,
)
from ai_sdlc.core.stage_review.optimization.models import (
    OptimizationCandidate,
    OptimizationPatchOperation,
)
from ai_sdlc.core.stage_review.optimization.observations import (
    CommittedSessionBinding,
    OptimizationObservationStore,
)
from ai_sdlc.core.stage_review.optimization.observations import (
    _build_terminal_observation as build_terminal_observation,
)
from ai_sdlc.core.stage_review.optimization.pipeline_effects import (
    EpochRuntimeAuthorizer,
    allow_effect,
)
from ai_sdlc.core.stage_review.optimization.product_shadow_executor import (
    ProductShadowAssignmentExecutor,
    build_product_shadow_executor,
)
from ai_sdlc.core.stage_review.optimization.shadow import (
    OptimizationShadowAssignment,
    OptimizationShadowAssignmentStore,
    ShadowSessionInput,
)
from ai_sdlc.core.stage_review.optimization.shadow_execution import (
    ShadowExecutionUnrecoverableError,
)
from ai_sdlc.core.stage_review.optimization.shadow_observations import (
    OptimizationShadowObservationStore,
)
from ai_sdlc.core.stage_review.optimization.snapshot_models import OptimizationSnapshot
from ai_sdlc.core.stage_review.provider_journal import (
    ProviderInvocation,
    ProviderInvocationJournal,
)
from ai_sdlc.core.stage_review.provider_transport import TrustedProviderTransport
from ai_sdlc.core.stage_review.provider_usage_models import build_usage_estimate_policy
from ai_sdlc.core.stage_review.resource_builders import build_budget_envelope
from ai_sdlc.core.stage_review.resource_ledger_models import ResourceReservation
from ai_sdlc.core.stage_review.resources import ResourceGovernor
from ai_sdlc.core.stage_review.review_input_packet import (
    ReviewInputPacket,
    ReviewInputPacketSet,
    ReviewPathChange,
    ReviewPathState,
)


class _RejectingRuntimeAuthorizer:
    epoch_fencing_epoch = 1
    epoch_claim_digest = "sha256:test-rejecting-epoch-claim"

    def __init__(self, reject_at: int) -> None:
        self.reject_at = reject_at
        self.authorizations = 0

    def __call__(self) -> None:
        self.authorizations += 1
        if self.authorizations == self.reject_at:
            raise SharedStateIntegrityError("runtime bundle drifted")

    def commit(self, operation: Callable[[], object]) -> object:
        self()
        return operation()


class _CommitRejectingRuntimeAuthorizer:
    epoch_fencing_epoch = 1
    epoch_claim_digest = "sha256:test-commit-rejecting-epoch-claim"

    def __call__(self) -> None:
        raise SharedStateIntegrityError("runtime bundle drifted")

    def commit(self, _operation: Callable[[], object]) -> object:
        raise SharedStateIntegrityError("runtime bundle drifted")


class _ToggleRuntimeAuthorizer:
    epoch_fencing_epoch = 1
    epoch_claim_digest = "sha256:test-toggle-epoch-claim"

    def __init__(self) -> None:
        self.blocked = False

    def __call__(self) -> None:
        if self.blocked:
            raise SharedStateIntegrityError("runtime bundle drifted")

    def commit(self, operation: Callable[[], object]) -> object:
        self()
        return operation()


class _CallableLease:
    epoch_fencing_epoch = 1
    epoch_claim_digest = "sha256:test-callable-epoch-claim"

    def __init__(self, gate: Callable[[], None]) -> None:
        self._gate = gate

    def __call__(self) -> None:
        self._gate()

    def commit(self, operation: Callable[[], object]) -> object:
        self._gate()
        return operation()


def _epoch_authorizer(
    root: Path,
    epoch: OptimizationEpoch,
    gate: Callable[[], None],
) -> EpochRuntimeAuthorizer:
    store = OptimizationControllerStore(
        root,
        project_id=epoch.project_id,
        lock_timeout_seconds=2,
    )
    if store.epoch(epoch.epoch_id) is None:
        store.create_epoch(epoch)
    claim = store.acquire_lease(
        epoch.epoch_id,
        owner_id="controller.shadow-test",
        lease_seconds=360,
    )
    return EpochRuntimeAuthorizer.for_epoch(
        EpochLeaseGuard(store, claim),
        gate,
        epoch,
    )


def test_product_shadow_executor_commits_provider_and_label_lineage(
    tmp_path: Path,
) -> None:
    project_id = "project.shared"
    candidate = _candidate()
    governor, reservation = _offline_resources(tmp_path, project_id)
    epoch = _epoch(candidate, reservation.reservation_id, reservation.fencing_token)
    observations = OptimizationObservationStore(tmp_path, project_id=project_id)
    baseline = observations.append(
        build_terminal_observation(
            _binding(candidate),
            "consumed",
            sequence=31,
            occurred_at="2026-07-24T00:00:00Z",
            terminal_reason="consumed",
        )
    )
    assignments = OptimizationShadowAssignmentStore(tmp_path, project_id=project_id)
    assignment = assignments.assign(
        epoch_id=epoch.epoch_id,
        finalist_candidate_digest=candidate.candidate_digest,
        session=_shadow_input(baseline.observation_digest),
        epoch_session_sequence_high_watermark=30,
    )
    _persist_packet(tmp_path, project_id, assignment.session_id)
    executor, journal, results = _executor(
        tmp_path, project_id, assignments, observations, governor
    )

    assert executor.execute(
        epoch,
        candidate,
        assignment,
        _epoch_authorizer(tmp_path, epoch, allow_effect),
    ) is True
    result = results.read_assignment(assignment.assignment_id)
    assert result is not None
    assert result.challenger.unconfirmed_finding is False
    assert result.provider_submission_digest
    assert journal.get(result.provider_invocation_id).state == "committed"


def test_product_shadow_runtime_drift_leaves_prepared_outbox_without_provider_call(
    tmp_path: Path,
) -> None:
    project_id = "project.shared"
    candidate = _candidate()
    governor, reservation = _offline_resources(tmp_path, project_id)
    epoch = _epoch(candidate, reservation.reservation_id, reservation.fencing_token)
    observations = OptimizationObservationStore(tmp_path, project_id=project_id)
    baseline = observations.append(
        build_terminal_observation(
            _binding(candidate),
            "consumed",
            sequence=31,
            occurred_at="2026-07-24T00:00:00Z",
            terminal_reason="consumed",
        )
    )
    assignments = OptimizationShadowAssignmentStore(tmp_path, project_id=project_id)
    assignment = assignments.assign(
        epoch_id=epoch.epoch_id,
        finalist_candidate_digest=candidate.candidate_digest,
        session=_shadow_input(baseline.observation_digest),
        epoch_session_sequence_high_watermark=30,
    )
    _persist_packet(tmp_path, project_id, assignment.session_id)
    broker = _RemoteShadowBroker()
    executor, journal, _ = _executor(
        tmp_path,
        project_id,
        assignments,
        observations,
        governor,
        broker=broker,
    )
    authorizations = 0

    def reject_after_prepare() -> None:
        nonlocal authorizations
        authorizations += 1
        if authorizations == 2:
            raise SharedStateIntegrityError("runtime bundle drifted")

    with pytest.raises(SharedStateIntegrityError, match="runtime bundle drifted"):
        executor.execute(
            epoch,
            candidate,
            assignment,
            _epoch_authorizer(tmp_path, epoch, reject_after_prepare),
        )

    invocation = next(
        item
        for item in (
            journal.get(path.name)
            for path in journal._store.root.iterdir()  # noqa: SLF001
            if path.is_dir()
        )
        if item is not None
    )
    assert invocation.state == "prepared"
    assert (
        invocation.request.runtime_bundle_manifest_digest
        == epoch.runtime_bundle_manifest_digest
    )
    assert broker.envelope is None


def test_product_shadow_journal_authorization_rejection_keeps_dispatch_retryable(
    tmp_path: Path,
) -> None:
    project_id = "project.shared"
    candidate, epoch, assignments, assignment, observations, governor = (
        _prepared_product_shadow(tmp_path, project_id)
    )
    broker = _RemoteShadowBroker()
    executor, journal, _ = _executor(
        tmp_path,
        project_id,
        assignments,
        observations,
        governor,
        broker=broker,
    )
    reject_at_journal_dispatch = _RejectingRuntimeAuthorizer(3)

    with pytest.raises(
        ShadowExecutionUnrecoverableError,
        match="shadow_provider_dispatch_unauthorized",
    ):
        executor.execute(
            epoch,
            candidate,
            assignment,
            _epoch_authorizer(
                tmp_path,
                epoch,
                reject_at_journal_dispatch,
            ),
        )

    invocation = _only_invocation(journal)
    assert invocation.state == "prepared"
    assert broker.envelope is None


def test_product_shadow_dispatch_commit_rejection_keeps_prepared_without_call(
    tmp_path: Path,
) -> None:
    project_id = "project.shared"
    candidate, epoch, assignments, assignment, observations, governor = (
        _prepared_product_shadow(tmp_path, project_id)
    )
    broker = _RemoteShadowBroker()
    executor, journal, _ = _executor(
        tmp_path,
        project_id,
        assignments,
        observations,
        governor,
        broker=broker,
    )

    with pytest.raises(
        ShadowExecutionUnrecoverableError,
        match="shadow_provider_dispatch_unauthorized",
    ):
        executor.execute(
            epoch,
            candidate,
            assignment,
            _epoch_authorizer(
                tmp_path,
                epoch,
                _RejectingRuntimeAuthorizer(3),
            ),
        )

    assert _only_invocation(journal).state == "prepared"
    assert broker.envelope is None


def test_product_shadow_post_call_runtime_drift_quarantines_submission(
    tmp_path: Path,
) -> None:
    project_id = "project.shared"
    candidate, epoch, assignments, assignment, observations, governor = (
        _prepared_product_shadow(tmp_path, project_id)
    )
    broker = _RemoteShadowBroker()
    executor, journal, results = _executor(
        tmp_path,
        project_id,
        assignments,
        observations,
        governor,
        broker=broker,
    )
    reject_provider_return = _RejectingRuntimeAuthorizer(5)

    with pytest.raises(
        ShadowExecutionUnrecoverableError,
        match="shadow_provider_dispatch_unauthorized",
    ):
        executor.execute(
            epoch,
            candidate,
            assignment,
            _epoch_authorizer(tmp_path, epoch, reject_provider_return),
        )

    invocation = _only_invocation(journal)
    assert invocation.state == "dispatched"
    assert journal.submission_path(invocation.invocation_id).is_file()
    assert results.read_assignment(assignment.assignment_id) is None
    assert broker.envelope is not None


def test_product_shadow_observation_publish_reauthorizes_after_staging(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_id = "project.shared"
    candidate, epoch, assignments, assignment, observations, governor = (
        _prepared_product_shadow(tmp_path, project_id)
    )
    executor, journal, results = _executor(
        tmp_path,
        project_id,
        assignments,
        observations,
        governor,
    )
    authorizer = _ToggleRuntimeAuthorizer()
    prepare = results._canonical_publication  # noqa: SLF001

    def prepare_then_reject(*args: object, **kwargs: object) -> object:
        staged = prepare(*args, **kwargs)  # type: ignore[arg-type]
        authorizer.blocked = True
        return staged

    monkeypatch.setattr(results, "_canonical_publication", prepare_then_reject)

    with pytest.raises(SharedStateIntegrityError, match="runtime bundle drifted"):
        executor.execute(
            epoch,
            candidate,
            assignment,
            _epoch_authorizer(tmp_path, epoch, authorizer),
        )

    assert _only_invocation(journal).state == "committed"
    assert results.read_assignment(assignment.assignment_id) is None
    assert not hasattr(results, "append")
    assert not hasattr(results, "record_committed")


def test_product_shadow_rejects_callable_forged_epoch_authority(
    tmp_path: Path,
) -> None:
    project_id = "project.shared"
    candidate, epoch, assignments, assignment, observations, governor = (
        _prepared_product_shadow(tmp_path, project_id)
    )
    executor, journal, results = _executor(
        tmp_path,
        project_id,
        assignments,
        observations,
        governor,
    )
    forged = EpochRuntimeAuthorizer.for_epoch(
        _CallableLease(allow_effect),
        lambda: None,
        epoch,
    )

    with pytest.raises(TypeError, match="bound EpochLeaseGuard"):
        executor.execute(
            epoch,
            candidate,
            assignment,
            forged,
        )

    assert _only_invocation(journal).state == "committed"
    assert results.read_assignment(assignment.assignment_id) is None


def test_shadow_publication_recovers_after_envelope_before_receipt_crash(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_id = "project.shared"
    candidate, epoch, assignments, assignment, observations, governor = (
        _prepared_product_shadow(tmp_path, project_id)
    )
    executor, journal, results = _executor(
        tmp_path,
        project_id,
        assignments,
        observations,
        governor,
    )
    first_authority = _epoch_authorizer(tmp_path, epoch, allow_effect)
    def crash_before_receipt(
        _store: OptimizationControllerStore,
        _receipt: object,
    ) -> object:
        raise SharedStateIntegrityError("simulated receipt crash")

    with monkeypatch.context() as scoped:
        scoped.setattr(
            OptimizationControllerStore,
            "_persist_effect_receipt",
            crash_before_receipt,
        )
        with pytest.raises(
            SharedStateIntegrityError,
            match="simulated receipt crash",
        ):
            executor.execute(
                epoch,
                candidate,
                assignment,
                first_authority,
            )

    invocation = _only_invocation(journal)
    assert invocation.state == "committed"
    assert len(journal.events(invocation.invocation_id)) == 5
    assert results.read_assignment(assignment.assignment_id) is None
    first_authority._lease_guard.release()  # noqa: SLF001

    recovered = executor.execute(
        epoch,
        candidate,
        assignment,
        _epoch_authorizer(tmp_path, epoch, allow_effect),
    )

    assert recovered is True
    assert results.read_assignment(assignment.assignment_id) is not None
    assert len(journal.events(invocation.invocation_id)) == 5


def test_product_shadow_transport_uses_epoch_snapshot_estimate_policy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_id = "project.shared"
    policy = build_usage_estimate_policy(
        policy_id="usage-estimate.non-default",
        version="9.4.0",
        characters_per_token=7,
        estimated_cost_per_token=0.000004,
    )
    snapshot = OptimizationSnapshot(
        snapshot_id="optimization-snapshot.non-default",
        project_id=project_id,
        policy_payload={"usage_estimation_policy": policy.model_dump(mode="json")},
        created_at="2026-07-23T00:00:00Z",
        is_baseline=True,
    )
    governor, _reservation = _offline_resources(tmp_path, project_id)
    assignments = OptimizationShadowAssignmentStore(tmp_path, project_id=project_id)
    observations = OptimizationObservationStore(tmp_path, project_id=project_id)
    results = OptimizationShadowObservationStore(tmp_path, project_id=project_id)
    journal = ProviderInvocationJournal(
        tmp_path, project_id=project_id, resource_governor=governor
    )
    captured = []
    monkeypatch.setattr(
        codex_review_runtime,
        "resolve_codex_runtime_prerequisites",
        lambda: ("codex", object()),
    )

    def fake_transport(
        *_args: object,
        estimate_policy: object | None = None,
        **_kwargs: object,
    ) -> TrustedProviderTransport:
        captured.append(estimate_policy)
        return _transport(tmp_path, project_id, _RemoteShadowBroker())

    monkeypatch.setattr(
        codex_review_runtime,
        "build_codex_review_transport",
        fake_transport,
    )
    executor = build_product_shadow_executor(
        tmp_path,
        project_id=project_id,
        assignments=assignments,
        observations=observations,
        shadow_observations=results,
        journal=journal,
        resources=governor,
        snapshot_source=lambda digest: (
            snapshot if digest == snapshot.snapshot_digest else None
        ),
        clock=lambda: "2026-07-23T00:00:00Z",
    )
    assert executor is not None
    assignment = assignments.assign(
        epoch_id="optimization-epoch.non-default",
        finalist_candidate_digest="sha256:finalist",
        session=ShadowSessionInput(
            session_id="session.non-default",
            session_sequence=31,
            initial_candidate_digest="sha256:initial",
            risk_profile_digest="sha256:risk",
            visible_evidence_digest="sha256:visible",
            active_baseline_result_digest="sha256:baseline-result",
            baseline_snapshot_digest=snapshot.snapshot_digest,
            usage_estimation_policy_version=policy.version,
            usage_estimation_policy_digest=policy.policy_digest,
        ),
        epoch_session_sequence_high_watermark=30,
    )

    executor.transport_source(assignment)

    assert captured == [policy]
    stale = assignment.model_copy(
        update={"usage_estimation_policy_digest": "sha256:stale"}
    )
    with pytest.raises(SharedStateIntegrityError, match="policy lineage"):
        executor.transport_source(stale)


def _executor(
    root: Path,
    project_id: str,
    assignments: OptimizationShadowAssignmentStore,
    observations: OptimizationObservationStore,
    governor: ResourceGovernor,
    *,
    broker: _RemoteShadowBroker | None = None,
) -> tuple[
    ProductShadowAssignmentExecutor,
    ProviderInvocationJournal,
    OptimizationShadowObservationStore,
]:
    journal = ProviderInvocationJournal(
        root, project_id=project_id, resource_governor=governor
    )
    selected_broker = broker or _RemoteShadowBroker()
    results = OptimizationShadowObservationStore(
        root,
        project_id=project_id,
        journal=journal,
    )
    executor = ProductShadowAssignmentExecutor(
        root=root,
        project_id=project_id,
        assignments=assignments,
        observations=observations,
        shadow_observations=results,
        journal=journal,
        resources=governor,
        transport_source=lambda _: _executions(
            _transport(root, project_id, selected_broker)
        ),
        clock=lambda: "2026-08-06T00:00:00Z",
    )
    return executor, journal, results


def _prepared_product_shadow(
    root: Path,
    project_id: str,
) -> tuple[
    OptimizationCandidate,
    OptimizationEpoch,
    OptimizationShadowAssignmentStore,
    OptimizationShadowAssignment,
    OptimizationObservationStore,
    ResourceGovernor,
]:
    candidate = _candidate()
    governor, reservation = _offline_resources(root, project_id)
    epoch = _epoch(candidate, reservation.reservation_id, reservation.fencing_token)
    observations = OptimizationObservationStore(root, project_id=project_id)
    baseline = observations.append(
        build_terminal_observation(
            _binding(candidate),
            "consumed",
            sequence=31,
            occurred_at="2026-07-24T00:00:00Z",
            terminal_reason="consumed",
        )
    )
    assignments = OptimizationShadowAssignmentStore(root, project_id=project_id)
    assignment = assignments.assign(
        epoch_id=epoch.epoch_id,
        finalist_candidate_digest=candidate.candidate_digest,
        session=_shadow_input(baseline.observation_digest),
        epoch_session_sequence_high_watermark=30,
    )
    _persist_packet(root, project_id, assignment.session_id)
    return candidate, epoch, assignments, assignment, observations, governor


def _only_invocation(journal: ProviderInvocationJournal) -> ProviderInvocation:
    return next(
        item
        for item in (
            journal.get(path.name)
            for path in journal._store.root.iterdir()  # noqa: SLF001
            if path.is_dir()
        )
        if item is not None
    )


def _offline_resources(
    root: Path, project_id: str
) -> tuple[ResourceGovernor, ResourceReservation]:
    governor = ResourceGovernor(
        root,
        project_id=project_id,
        foreground_capacity=baseline_foreground_capacity(),
        offline_optimization_capacity=baseline_offline_capacity(),
    )
    policy = baseline_epoch_budget_policy()
    envelope = build_budget_envelope(
        project_id=project_id,
        work_item_id="offline-optimization",
        stage_review_session_id=optimization_resource_session_id(
            "optimization-epoch.shadow-product", 1
        ),
        risk_level="low",
        budget_policy=policy,
        pool="offline_optimization",
    )
    admission = governor.reserve_admission(
        envelope,
        budget_policy=policy,
        lease_owner="optimization-worker.shadow-product",
        operation_id="admission.shadow-product",
        lease_seconds=360,
    )
    assert admission.reservation is not None
    finalized = governor.finalize_offline_reservation(
        admission.reservation.reservation_id,
        lease_owner=admission.reservation.lease_owner,
        expected_fencing_token=admission.reservation.fencing_token,
        operation_id="finalize.shadow-product",
    )
    assert finalized.reservation is not None
    return governor, finalized.reservation


def _epoch(
    candidate: OptimizationCandidate,
    reservation_id: str,
    fencing_token: int,
) -> OptimizationEpoch:
    return OptimizationEpoch(
        epoch_id="optimization-epoch.shadow-product",
        project_id="project.shared",
        trigger_fingerprint="sha256:trigger",
        trigger_digest="sha256:trigger-event",
        constitution_digest="sha256:constitution",
        baseline_snapshot_digest=candidate.base_snapshot_digest,
        candidate_domain_registry_digest="sha256:registry",
        statistics_policy_digest="sha256:statistics-policy",
        evaluator_registry_digest="sha256:evaluator-registry",
        auto_promotion_policy_digest="sha256:promotion-policy",
        runtime_bundle_manifest_digest="sha256:test-runtime-bundle",
        session_sequence_high_watermark=30,
        new_session_count=30,
        state="shadow_observing",
        revision=1,
        reservation_id=reservation_id,
        reservation_fencing_token=fencing_token,
        finalist_candidate_digest=candidate.candidate_digest,
        started_at="2026-07-22T00:00:00Z",
    )


def _candidate() -> OptimizationCandidate:
    return OptimizationCandidate(
        candidate_id="candidate.shadow-product",
        candidate_domain="selection",
        base_snapshot_digest="sha256:baseline-snapshot",
        patch_operations=(
            OptimizationPatchOperation(
                operation="replace",
                field_path="selection_policy.capability_requirement_rules",
                value=[],
            ),
        ),
        expected_effect="recover confirmed critical coverage",
        rollback_target="sha256:baseline-snapshot",
        generator_identity="generator.selection",
        generator_provider_id="provider.generator",
        attribution_digests=("sha256:attribution",),
        target_stratum_ids=("implementation:high",),
        dataset_partition_refs=("train",),
        estimated_provider_calls=1,
        estimated_tokens=1000,
        estimated_cost=0.5,
        estimated_active_wall_clock=30,
        evidence_refs=("sha256:evidence",),
    )


def _binding(candidate: OptimizationCandidate) -> CommittedSessionBinding:
    return CommittedSessionBinding(
        project_id="project.shared",
        session_id="session.shadow-product",
        initial_candidate_digest="sha256:initial",
        stage_key="implementation",
        risk_level="high",
        candidate_size_bucket="medium",
        provider_ids=("provider.codex",),
        active_snapshot_digest=candidate.base_snapshot_digest,
        control_sequence=31,
        control_event_digest="sha256:control",
        committed_at="2026-07-23T00:00:00Z",
    )


def _shadow_input(baseline_digest: str) -> ShadowSessionInput:
    policy = baseline_usage_estimate_policy()
    return ShadowSessionInput(
        session_id="session.shadow-product",
        session_sequence=31,
        initial_candidate_digest="sha256:initial",
        risk_profile_digest="sha256:risk",
        visible_evidence_digest="sha256:visible",
        active_baseline_result_digest=baseline_digest,
        baseline_snapshot_digest="sha256:baseline-snapshot",
        usage_estimation_policy_version=policy.version,
        usage_estimation_policy_digest=policy.policy_digest,
    )


def _persist_packet(root: Path, project_id: str, session_id: str) -> None:
    state = ReviewPathState(
        mode="100644",
        encoding="utf-8",
        text="value = 1\n",
        content_digest="sha256:source",
        byte_count=10,
    )
    packet = ReviewInputPacket(
        candidate_manifest_digest="sha256:review-candidate",
        source_snapshot_digest="sha256:source-snapshot",
        slot_id="slot.correctness",
        role_profile_id="role.correctness",
        role_contract_digest="sha256:role-contract",
        capability_ids=("capability.correctness",),
        blocking_authorities=("capability.correctness",),
        primary_dimensions=("dimension.correctness",),
        prompt_template_digest="sha256:prompt",
        changes=(ReviewPathChange(path="src/example.py", before=state, after=state),),
    )
    packet_set = ReviewInputPacketSet(
        project_id=project_id,
        review_session_id=session_id,
        candidate_manifest_digest=packet.candidate_manifest_digest,
        source_snapshot_digest=packet.source_snapshot_digest,
        packet_digests=(packet.packet_digest,),
    )
    directory = (
        resolve_canonical_shared_state(root, project_id)
        / "review-input-packets"
        / session_id
    )
    create_json_exclusive(
        directory / f"{packet.slot_id}.json", packet.model_dump(mode="json")
    )
    create_json_exclusive(
        directory / "packet-set.json", packet_set.model_dump(mode="json")
    )
