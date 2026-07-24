"""把可信 Codex 运行时组合成 canonical Stage Review Executor。"""

from __future__ import annotations

import json
import os
import shutil
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from ai_sdlc.core.stage_review.activation_fence import activation_safety_read_lease
from ai_sdlc.core.stage_review.activation_policy_store import (
    current_activation_policy,
)
from ai_sdlc.core.stage_review.activation_safety import (
    active_activation_safety_holds_for_lineage,
)
from ai_sdlc.core.stage_review.activation_store import (
    _record_enforced_activation_session as record_enforced_activation_session,
)
from ai_sdlc.core.stage_review.artifacts import resolve_canonical_shared_state
from ai_sdlc.core.stage_review.binding_invocations import ReviewerInvocationCoordinator
from ai_sdlc.core.stage_review.binding_models import (
    BindingAuthoritySnapshot,
)
from ai_sdlc.core.stage_review.bindings import ReviewerBindingService
from ai_sdlc.core.stage_review.canonical_stage_review_executor import (
    CanonicalStageReviewExecutor,
)
from ai_sdlc.core.stage_review.canonical_stage_review_support import blocked, needs_user
from ai_sdlc.core.stage_review.close_gate_models import (
    GateApplicabilityDecision,
    PreparedStageClose,
)
from ai_sdlc.core.stage_review.close_models import StageCloseAuthorization
from ai_sdlc.core.stage_review.codex_isolation_backend import (
    CodexPermissionProfileBackend,
)
from ai_sdlc.core.stage_review.codex_provider_execution import (
    codex_reviewer_execution_route,
)
from ai_sdlc.core.stage_review.codex_provider_transport import (
    build_codex_review_transport,
)
from ai_sdlc.core.stage_review.codex_review_binding_runtime import (
    build_codex_binding_service,
)
from ai_sdlc.core.stage_review.codex_trusted_releases import (
    _trusted_published_codex_release as trusted_published_codex_release,
)
from ai_sdlc.core.stage_review.isolation_backend_identity import (
    TrustedBackendReleaseManifest,
)
from ai_sdlc.core.stage_review.isolation_execution import (
    TrustedIsolationBackendRegistry,
)
from ai_sdlc.core.stage_review.isolation_launcher import ReviewerIsolationLauncher
from ai_sdlc.core.stage_review.isolation_runtime_layout import (
    FilesystemAllocationPathResolver,
)
from ai_sdlc.core.stage_review.provider_execution_registry import (
    ProviderAdapterFactoryRegistry,
    _build_reviewer_execution_registry,
)
from ai_sdlc.core.stage_review.provider_journal import ProviderInvocationJournal
from ai_sdlc.core.stage_review.provider_usage_models import (
    ProviderUsageEstimatePolicy,
)
from ai_sdlc.core.stage_review.remote_review_driver_factory import (
    RemoteReviewDriverFactory,
)
from ai_sdlc.core.stage_review.session import StageReviewSessionService
from ai_sdlc.core.stage_review.shadow_planning_runtime import (
    ShadowPlanningPreflight,
)
from ai_sdlc.core.stage_review.stage_close_product_runtime import (
    authorize_product_stage_close,
)
from ai_sdlc.core.stage_review.stage_review_execution import (
    StageCloseGateUnavailableError,
    StageReviewExecutionOutcome,
    StageReviewExecutionRequest,
)
from ai_sdlc.core.stage_review.stage_review_plan_runtime import (
    HeldStageReviewPlan,
    hold_stage_review_plan,
    release_stage_review_plan,
)


class CodexStageReviewExecutor:
    def __init__(self, root: Path) -> None:
        self._root = root.resolve()

    def execute(
        self,
        request: StageReviewExecutionRequest,
    ) -> StageReviewExecutionOutcome:
        resolved = resolve_codex_runtime_prerequisites()
        if resolved is None:
            return needs_user("review-isolation-unproven")
        executable, release = resolved
        try:
            executor = _build_executor(
                self._root,
                request,
                executable=executable,
                release=release,
            )
        except (OSError, ValueError):
            return needs_user("review-isolation-unproven")
        try:
            return executor.execute(request)
        except (OSError, ValueError):
            return blocked("review-runtime-integrity-failure")

    def enforce_close(
        self,
        prepared: PreparedStageClose,
        decision: GateApplicabilityDecision,
        preflight: ShadowPlanningPreflight,
        writer: Callable[[], object],
    ) -> object:
        resolved = resolve_codex_runtime_prerequisites()
        if resolved is None:
            raise StageCloseGateUnavailableError("review-isolation-unproven")
        if preflight.candidate is None or preflight.source_snapshot is None:
            raise StageCloseGateUnavailableError("review-candidate-unavailable")
        executable, release = resolved
        try:
            runtime = hold_stage_review_plan(
                prepared,
                decision,
                preflight.candidate,
                preflight.source_snapshot,
            )
            try:
                return _execute_enforced_close(
                    self._root,
                    prepared,
                    decision,
                    runtime,
                    writer,
                    executable,
                    release,
                )
            finally:
                release_stage_review_plan(runtime)
        except StageCloseGateUnavailableError:
            raise
        except (OSError, ValueError) as exc:
            raise StageCloseGateUnavailableError(
                "review-runtime-integrity-failure"
            ) from exc


def _execute_enforced_close(
    root: Path,
    prepared: PreparedStageClose,
    decision: GateApplicabilityDecision,
    runtime: HeldStageReviewPlan,
    writer: Callable[[], object],
    executable: str,
    release: TrustedBackendReleaseManifest,
) -> object:
    services: list[StageReviewSessionService] = []
    request = runtime.execution_request(mode="enforce")
    executor = _build_executor(
        root,
        request,
        executable=executable,
        release=release,
        on_authorized=services.append,
    )
    outcome = executor.execute(request)
    if outcome.status != "completed":
        raise StageCloseGateUnavailableError(outcome.reason_code)
    if len(services) != 1:
        raise ValueError("authorized review session service is unavailable")
    def record_closed(authorization: StageCloseAuthorization) -> None:
        record_enforced_activation_session(
            root,
            candidate=runtime.planned.candidate,
            panel_plan_digest=runtime.held.plan.plan_digest,
            risk_level=runtime.planned.risk_profile.risk_level,
            review_outcome=outcome,
            authorization=authorization,
        )

    project_id = runtime.planned.candidate.project_id
    with activation_safety_read_lease(root, project_id):
        policy = current_activation_policy(root)
        if policy.policy_digest != decision.policy_digest:
            raise StageCloseGateUnavailableError(
                "activation-policy-changed-before-product-commit"
            )
        if any(
            (prepared.stage_key, prepared.risk_level)
            in {
                (item.stage_key, item.risk_level)
                for item in hold.affected_combinations
            }
            for hold in active_activation_safety_holds_for_lineage(
                root,
                policy=policy,
            )
        ):
            raise StageCloseGateUnavailableError(
                "activation-safety-hold-blocked-product-commit"
            )
        return authorize_product_stage_close(
            prepared,
            decision,
            runtime,
            services[0],
            writer,
            on_closed=record_closed,
        )


def _build_executor(
    root: Path,
    request: StageReviewExecutionRequest,
    *,
    executable: str,
    release: TrustedBackendReleaseManifest,
    on_authorized: Callable[[StageReviewSessionService], None] | None = None,
) -> CanonicalStageReviewExecutor:
    project_id = request.candidate.project_id
    shared = resolve_canonical_shared_state(root, project_id)
    paths = FilesystemAllocationPathResolver(shared / "reviewer-runtime")
    bindings, authority = build_codex_binding_service(
        root,
        request,
        paths,
        executable=executable,
        release=release,
    )
    journal, invocations = _reviewer_invocations(
        root,
        project_id,
        request,
        paths,
        executable,
        release,
        bindings,
    )
    drivers = _review_drivers(
        root,
        project_id,
        shared,
        executable,
        release,
        bindings,
        paths,
        authority,
        request,
    )
    return CanonicalStageReviewExecutor(
        root,
        bindings=bindings,
        binding_authority=authority,
        journal=journal,
        invocations=invocations,
        drivers=drivers,
        clock=lambda: datetime.now(UTC),
        on_authorized=on_authorized,
    )


def _reviewer_invocations(
    root: Path,
    project_id: str,
    request: StageReviewExecutionRequest,
    paths: FilesystemAllocationPathResolver,
    executable: str,
    release: TrustedBackendReleaseManifest,
    bindings: ReviewerBindingService,
) -> tuple[ProviderInvocationJournal, ReviewerInvocationCoordinator]:
    journal = ProviderInvocationJournal(
        root,
        project_id=project_id,
        resource_governor=request.governor,
    )
    launcher = ReviewerIsolationLauncher(
        root,
        registry=TrustedIsolationBackendRegistry.default(),
        backend=CodexPermissionProfileBackend(executable, release_manifest=release),
        project_id=project_id,
    )
    invocations = ReviewerInvocationCoordinator(
        bindings,
        journal,
        isolation_launcher=launcher,
        allocation_path_resolver=paths,
    )
    return journal, invocations


def _review_drivers(
    root: Path,
    project_id: str,
    shared: Path,
    executable: str,
    release: TrustedBackendReleaseManifest,
    bindings: ReviewerBindingService,
    paths: FilesystemAllocationPathResolver,
    authority: BindingAuthoritySnapshot,
    request: StageReviewExecutionRequest,
) -> RemoteReviewDriverFactory:
    factories = ProviderAdapterFactoryRegistry()
    factories.register_reviewer(
        codex_reviewer_execution_route(),
        provider_id_prefixes=("provider.openai-codex.",),
        model_family_prefixes=("model.openai-codex.",),
        factory=lambda descriptor: build_codex_review_transport(
            root,
            project_id,
            shared,
            executable,
            release,
            estimate_policy=_usage_estimate_policy(request),
            execution_scope="reviewer_binding",
            descriptor=descriptor,
        ),
    )
    executions = _build_reviewer_execution_registry(
        authority.provider_descriptors,
        factories.freeze(),
    )
    return RemoteReviewDriverFactory(
        bindings=bindings,
        allocation_path_resolver=paths,
        executions=executions,
    )


def _usage_estimate_policy(
    request: StageReviewExecutionRequest,
) -> ProviderUsageEstimatePolicy:
    payload = request.proposal.optimization_snapshot.policy_payload.get(
        "usage_estimation_policy"
    )
    return ProviderUsageEstimatePolicy.model_validate(payload)


def resolve_codex_runtime_prerequisites() -> (
    tuple[str, TrustedBackendReleaseManifest] | None
):
    executable = shutil.which("codex")
    evidence_path = os.getenv("AI_SDLC_CODEX_NPM_ATTESTATIONS", "").strip()
    if not executable or not evidence_path:
        return None
    try:
        path = Path(evidence_path).resolve(strict=True)
        if path.stat().st_size > 4 * 1024 * 1024:
            return None
        evidence = json.loads(path.read_text(encoding="utf-8"))
        release = trusted_published_codex_release(registry_attestations=evidence)
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    return (executable, release) if release is not None else None


__all__ = [
    "CodexStageReviewExecutor",
    "build_codex_review_transport",
    "resolve_codex_runtime_prerequisites",
]
