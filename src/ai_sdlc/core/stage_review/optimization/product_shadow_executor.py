"""把 Product Shadow Assignment 接到统一 Journal 与 Authority 标签。"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from ai_sdlc.core.stage_review.artifacts import (
    SharedStateIntegrityError,
    resolve_canonical_shared_state,
)
from ai_sdlc.core.stage_review.isolation_backend_identity import (
    TrustedBackendReleaseManifest,
)
from ai_sdlc.core.stage_review.optimization.controller_models import OptimizationEpoch
from ai_sdlc.core.stage_review.optimization.finding_lineage import (
    FindingEventLineageReader,
)
from ai_sdlc.core.stage_review.optimization.models import OptimizationCandidate
from ai_sdlc.core.stage_review.optimization.observations import (
    OptimizationObservationStore,
    OptimizationSessionObservation,
)
from ai_sdlc.core.stage_review.optimization.shadow import (
    OptimizationShadowAssignment,
    OptimizationShadowAssignmentStore,
    ProspectiveShadowService,
    ShadowSessionInput,
)
from ai_sdlc.core.stage_review.optimization.shadow_execution import (
    ShadowAssignmentExecutor,
    ShadowExecutionNoChangeError,
    ShadowExecutionUnrecoverableError,
)
from ai_sdlc.core.stage_review.optimization.shadow_labels import (
    labeled_shadow_outcomes,
)
from ai_sdlc.core.stage_review.optimization.shadow_observations import (
    OptimizationShadowObservationStore,
)
from ai_sdlc.core.stage_review.optimization.shadow_provider import (
    CodexOptimizationShadowDriver,
    OptimizationShadowProviderOutput,
    build_shadow_provider_payload,
    shadow_provider_spec,
    validate_shadow_provider_output,
)
from ai_sdlc.core.stage_review.provider_execution_registry import (
    FrozenProviderExecutionRegistry,
    ProviderExecutionAdapterRegistry,
)
from ai_sdlc.core.stage_review.provider_journal import (
    ProviderInvocationJournal,
    ProviderSubmission,
)
from ai_sdlc.core.stage_review.provider_transport import TrustedProviderTransport
from ai_sdlc.core.stage_review.provider_usage_models import ProviderUsageEstimatePolicy
from ai_sdlc.core.stage_review.resources import ResourceGovernor
from ai_sdlc.core.stage_review.review_input_packet import load_review_input_packets

_RETRYABLE_RESULTS = frozenset({"lock_unavailable", "retry_wait"})


class ProductShadowAssignmentExecutor:
    def __init__(
        self,
        *,
        root: Path,
        project_id: str,
        assignments: OptimizationShadowAssignmentStore,
        observations: OptimizationObservationStore,
        shadow_observations: OptimizationShadowObservationStore,
        journal: ProviderInvocationJournal,
        resources: ResourceGovernor,
        transport_source: Callable[
            [OptimizationShadowAssignment], FrozenProviderExecutionRegistry
        ],
        clock: Callable[[], str],
    ) -> None:
        self.root = root
        self.project_id = project_id
        self.assignments = assignments
        self.observations = observations
        self.shadow_observations = shadow_observations
        self.journal = journal
        self.resources = resources
        self.transport_source = transport_source
        self.clock = clock
        self.findings = FindingEventLineageReader(root, project_id=project_id)
        self.service = ProspectiveShadowService(
            store=assignments,
            journal=journal,
            resource_governor=resources,
            late_critical_recorder=self._existing_late_critical,
        )

    def execute(
        self,
        epoch: OptimizationEpoch,
        candidate: OptimizationCandidate,
        assignment: OptimizationShadowAssignment,
        authorize_effect: Callable[[], None],
    ) -> bool:
        self._verify_lineage(epoch, candidate, assignment)
        loaded = load_review_input_packets(
            self.root,
            project_id=self.project_id,
            review_session_id=assignment.session_id,
        )
        if loaded is None:
            raise ShadowExecutionUnrecoverableError("shadow_input_packet_unavailable")
        payload = build_shadow_provider_payload(
            assignment, candidate, loaded[0], loaded[1]
        )
        if payload is None:
            raise ShadowExecutionNoChangeError("shadow_input_budget_exceeded")
        reservation = self.resources.get_reservation(epoch.reservation_id)
        driver = CodexOptimizationShadowDriver(
            payload=payload,
            executions=self.transport_source(assignment),
        )
        authorize_effect()
        result = self.service.evaluate(
            epoch_id=epoch.epoch_id,
            finalist_candidate_digest=candidate.candidate_digest,
            session=_shadow_input(assignment),
            epoch_session_sequence_high_watermark=epoch.session_sequence_high_watermark,
            provider=shadow_provider_spec(payload),
            driver=driver,
            validator=validate_shadow_provider_output,
            reservation_id=reservation.reservation_id,
            lease_owner=reservation.lease_owner,
        )
        if result.invocation_result.result_code in _RETRYABLE_RESULTS:
            return False
        if result.invocation_result.result_code != "committed":
            raise ShadowExecutionUnrecoverableError(
                f"shadow_provider_{result.invocation_result.result_code}"
            )
        return self._record_observation(
            candidate, assignment, result.invocation_result.submission, authorize_effect
        )

    def _record_observation(
        self,
        candidate: OptimizationCandidate,
        assignment: OptimizationShadowAssignment,
        submission: ProviderSubmission | None,
        authorize_effect: Callable[[], None],
    ) -> bool:
        if submission is None:
            raise ShadowExecutionUnrecoverableError("shadow_submission_missing")
        output = OptimizationShadowProviderOutput.model_validate(
            submission.output_payload
        )
        baseline = _baseline_observation(self.observations, assignment)
        outcomes = labeled_shadow_outcomes(
            candidate=candidate,
            baseline_observation=baseline,
            review=output.review,
            finding_events=self.findings.events(assignment.session_id),
        )
        authorize_effect()
        self.shadow_observations.record_committed(
            assignment,
            journal=self.journal,
            provider_invocation_id=submission.invocation_id,
            baseline=outcomes[0],
            challenger=outcomes[1],
            evaluation_binding_id=f"evaluation-binding.{assignment.assignment_id}",
            label_source_digests=outcomes[2],
            observed_at=self.clock(),
        )
        return True

    def _verify_lineage(
        self,
        epoch: OptimizationEpoch,
        candidate: OptimizationCandidate,
        assignment: OptimizationShadowAssignment,
    ) -> None:
        if not all(
            (
                epoch.project_id == self.project_id == assignment.project_id,
                assignment.epoch_id == epoch.epoch_id,
                assignment.finalist_candidate_digest == candidate.candidate_digest,
                epoch.reservation_id,
            )
        ):
            raise SharedStateIntegrityError("product shadow lineage diverged")

    def _existing_late_critical(
        self,
        assignment: OptimizationShadowAssignment,
        submission: ProviderSubmission,
    ) -> str:
        del submission
        events = self.findings.events(assignment.session_id)
        match = next(
            (item.event_digest for item in events if item.late_critical_finding),
            "",
        )
        if not match:
            raise SharedStateIntegrityError("shadow critical lacks Finding Authority")
        return match


def build_product_shadow_executor(
    root: Path,
    *,
    project_id: str,
    assignments: OptimizationShadowAssignmentStore,
    observations: OptimizationObservationStore,
    shadow_observations: OptimizationShadowObservationStore,
    journal: ProviderInvocationJournal,
    resources: ResourceGovernor,
    snapshot_source: Callable[[str], object],
    clock: Callable[[], str],
) -> ShadowAssignmentExecutor | None:
    # 延迟加载上层产品运行时，避免 optimization.runtime 的模块初始化形成依赖环。
    from ai_sdlc.core.stage_review.codex_review_runtime import (
        build_codex_review_transport,
        resolve_codex_runtime_prerequisites,
    )

    resolved = resolve_codex_runtime_prerequisites()
    if resolved is None:
        return None
    executable, release = resolved
    shared = resolve_canonical_shared_state(root, project_id)

    return ProductShadowAssignmentExecutor(
        root=root,
        project_id=project_id,
        assignments=assignments,
        observations=observations,
        shadow_observations=shadow_observations,
        journal=journal,
        resources=resources,
        transport_source=_shadow_transport_source(
            root,
            project_id,
            shared,
            executable,
            release,
            snapshot_source,
            build_codex_review_transport,
        ),
        clock=clock,
    )


def _shadow_transport_source(
    root: Path,
    project_id: str,
    shared: Path,
    executable: str,
    release: TrustedBackendReleaseManifest,
    snapshot_source: Callable[[str], object],
    transport_builder: Callable[..., TrustedProviderTransport],
) -> Callable[[OptimizationShadowAssignment], FrozenProviderExecutionRegistry]:
    def transport_source(
        assignment: OptimizationShadowAssignment,
    ) -> FrozenProviderExecutionRegistry:
        snapshot = snapshot_source(assignment.baseline_snapshot_digest)
        payload = getattr(snapshot, "policy_payload", {}).get("usage_estimation_policy")
        policy = ProviderUsageEstimatePolicy.model_validate(payload)
        if (
            policy.version != assignment.usage_estimation_policy_version
            or policy.policy_digest != assignment.usage_estimation_policy_digest
        ):
            raise SharedStateIntegrityError(
                "shadow usage estimation policy lineage diverged"
            )
        transport = transport_builder(
            root,
            project_id,
            shared,
            executable,
            release,
            estimate_policy=policy,
            execution_scope="optimization_shadow",
        )
        registry = ProviderExecutionAdapterRegistry()
        registry.register_shadow(transport)
        return registry.freeze()

    return transport_source


def _shadow_input(assignment: OptimizationShadowAssignment) -> ShadowSessionInput:
    return ShadowSessionInput(
        session_id=assignment.session_id,
        session_sequence=assignment.session_sequence,
        initial_candidate_digest=assignment.initial_candidate_digest,
        risk_profile_digest=assignment.risk_profile_digest,
        visible_evidence_digest=assignment.visible_evidence_digest,
        active_baseline_result_digest=assignment.active_baseline_result_digest,
        baseline_snapshot_digest=assignment.baseline_snapshot_digest,
        usage_estimation_policy_version=(assignment.usage_estimation_policy_version),
        usage_estimation_policy_digest=assignment.usage_estimation_policy_digest,
    )


def _baseline_observation(
    store: OptimizationObservationStore,
    assignment: OptimizationShadowAssignment,
) -> OptimizationSessionObservation:
    matches = tuple(
        item
        for item in store.read_session(assignment.session_id)
        if item.observation_digest == assignment.active_baseline_result_digest
    )
    if len(matches) != 1:
        raise SharedStateIntegrityError("shadow baseline result is unavailable")
    return matches[0]


__all__ = ["ProductShadowAssignmentExecutor", "build_product_shadow_executor"]
