"""五类既有关闭路径共享的 Stage Close Gateway。"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import suppress
from dataclasses import replace
from pathlib import Path
from typing import TypeVar, cast

from ai_sdlc.core.loop_models import utc_now_iso
from ai_sdlc.core.stage_review import close_gate_routes as _routes
from ai_sdlc.core.stage_review.activation_evidence_runtime import (
    _refresh_activation_policy_from_local_evidence as refresh_activation_policy_from_local_evidence,
)
from ai_sdlc.core.stage_review.activation_fence import (
    activation_safety_mutation_fence,
    activation_safety_read_lease,
)
from ai_sdlc.core.stage_review.activation_models import ActivationSafetyHold
from ai_sdlc.core.stage_review.activation_policy_store import (
    current_activation_policy,
)
from ai_sdlc.core.stage_review.activation_safety import (
    active_activation_safety_holds_for_lineage,
    affected_activation_safety_holds,
    build_activation_safety_recovery_sample,
    record_activation_safety_recovery,
)
from ai_sdlc.core.stage_review.activation_store import (
    _record_activation_session as record_activation_session,
)
from ai_sdlc.core.stage_review.artifacts import resolve_repository_project_id
from ai_sdlc.core.stage_review.candidate import CandidateManifest
from ai_sdlc.core.stage_review.canonical import CanonicalizationPolicy, canonical_digest
from ai_sdlc.core.stage_review.close_gate_models import (
    GateApplicabilityDecision,
    PreparedStageClose,
    StageCloseGateAttestation,
    StageCloseGateOperation,
)
from ai_sdlc.core.stage_review.close_gate_observation import (
    _build_reobservation_operation as build_reobservation_operation,
)
from ai_sdlc.core.stage_review.close_gate_observation import (
    _observation_result as observation_result,
)
from ai_sdlc.core.stage_review.close_gate_observation import (
    _reconciled_result as reconciled_result,
)
from ai_sdlc.core.stage_review.close_gate_observation import (
    _stage_close_result_payload as stage_close_result_payload,
)
from ai_sdlc.core.stage_review.close_gate_observation import (
    stage_close_operation_id,
)
from ai_sdlc.core.stage_review.close_gate_policy import shadow_applicability
from ai_sdlc.core.stage_review.close_gate_store import (
    _file_digest as file_digest,
)
from ai_sdlc.core.stage_review.close_gate_store import (
    _gate_attestation_is_current as gate_attestation_is_current,
)
from ai_sdlc.core.stage_review.close_gate_store import (
    _gate_execution_lock as gate_execution_lock,
)
from ai_sdlc.core.stage_review.close_gate_store import (
    _latest_gate_attestation_id as latest_gate_attestation_id,
)
from ai_sdlc.core.stage_review.close_gate_store import (
    _persist_gate_attestation as persist_gate_attestation,
)
from ai_sdlc.core.stage_review.close_gate_store import (
    _prepare_gate_operation as prepare_gate_operation,
)
from ai_sdlc.core.stage_review.close_gate_store import (
    _read_gate_attestations as read_gate_attestations,
)
from ai_sdlc.core.stage_review.close_gate_store import (
    _read_gate_operation as read_gate_operation,
)
from ai_sdlc.core.stage_review.close_gate_store import (
    advance_gate_operation,
)
from ai_sdlc.core.stage_review.resource_builders import stable_id
from ai_sdlc.core.stage_review.shadow_planning_runtime import (
    ShadowPlanningOutcome,
    ShadowPlanningPreflight,
)
from ai_sdlc.core.stage_review.shadow_planning_runtime import (
    _observe_shadow_planning as observe_shadow_planning,
)
from ai_sdlc.core.stage_review.shadow_planning_runtime import (
    _preflight_shadow_planning as preflight_shadow_planning,
)
from ai_sdlc.core.stage_review.stage_review_execution import (
    StageCloseGateUnavailableError,
    StageReviewExecutionOutcome,
    StageReviewExecutor,
)
from ai_sdlc.core.stage_review.stage_review_runtime import (
    StageReviewRuntime,
    build_stage_review_executor,
)

_RESULT = TypeVar("_RESULT")
prepare_loop_stage_close = _routes.prepare_loop_stage_close
prepare_local_pr_stage_close = _routes.prepare_local_pr_stage_close


class StageCloseGateway:
    """在一个入口内解析适用性、执行原 writer 并记录统一证明。"""

    def __init__(
        self,
        resolver: Callable[[PreparedStageClose], GateApplicabilityDecision]
        | None = None,
        review_executor: StageReviewExecutor | None = None,
        close_enforcer: StageReviewRuntime | None = None,
    ) -> None:
        self._uses_canonical_resolver = resolver is None
        self._resolver = resolver or shadow_applicability
        self._review_executor = review_executor
        self._close_enforcer = close_enforcer

    def execute(
        self,
        prepared: PreparedStageClose,
        writer: Callable[[], _RESULT],
    ) -> _RESULT:
        _refresh_activation_before_close(prepared.root)
        preflight = preflight_shadow_planning(prepared)
        risk_level = (
            preflight.risk_profile.risk_level
            if preflight.risk_profile is not None
            else "unclassified"
        )
        frozen = replace(prepared, risk_level=risk_level)
        decision = self._resolver(frozen)
        _enforce_activation_safety_before_writer(
            frozen,
            decision,
            preflight,
            self._review_executor,
        )
        def safe_writer() -> _RESULT:
            return _execute_writer_under_activation_safety_fence(
                frozen,
                decision,
                writer,
                require_canonical_policy=self._uses_canonical_resolver,
            )

        result = self._execute_selected_mode(
            frozen,
            decision,
            safe_writer,
            preflight,
        )
        _refresh_activation_safely(frozen.root)
        return result

    def _execute_selected_mode(
        self,
        prepared: PreparedStageClose,
        decision: GateApplicabilityDecision,
        writer: Callable[[], _RESULT],
        preflight: ShadowPlanningPreflight,
    ) -> _RESULT:
        if decision.mode == "enforce":
            if self._close_enforcer is None:
                raise StageCloseGateUnavailableError(
                    "enforce stage close requires the canonical StageCloseAuthorizer"
                )
            return cast(
                _RESULT,
                self._close_enforcer.enforce_close(
                    prepared,
                    decision,
                    preflight,
                    writer,
                ),
            )
        operation_id = stage_close_operation_id(prepared)
        try:
            lock = gate_execution_lock(prepared.root, operation_id)
            lock.__enter__()
        except Exception:
            return writer()
        try:
            return self._execute_shadow(
                prepared,
                decision,
                writer,
                preflight,
            )
        finally:
            with suppress(Exception):
                lock.__exit__(None, None, None)

    def _execute_shadow(
        self,
        prepared: PreparedStageClose,
        decision: GateApplicabilityDecision,
        writer: Callable[[], _RESULT],
        preflight: ShadowPlanningPreflight,
    ) -> _RESULT:
        artifact_path = prepared.root / prepared.close_artifact_path
        existed_before = artifact_path.is_file()
        project_id = resolve_repository_project_id(prepared.root)
        with activation_safety_mutation_fence(prepared.root, project_id):
            current_policy = current_activation_policy(prepared.root)
            if (
                current_policy.compatibility_mode != "strict"
                or (
                    self._uses_canonical_resolver
                    and current_policy.policy_digest != decision.policy_digest
                )
            ):
                raise StageCloseGateUnavailableError(
                    "activation-policy-changed-before-operation-prepare"
                )
            operation = _prepare_operation(prepared, existed_before)
        result = writer()
        try:
            with activation_safety_mutation_fence(prepared.root, project_id):
                current_policy = current_activation_policy(prepared.root)
                if (
                    current_policy.compatibility_mode != "strict"
                    or (
                        self._uses_canonical_resolver
                        and current_policy.policy_digest != decision.policy_digest
                    )
                ):
                    raise StageCloseGateUnavailableError(
                        "activation-policy-changed-before-operation-reconcile"
                    )
                operation = (
                    read_gate_operation(prepared.root, operation.operation_id)
                    or operation
                )
                if (
                    operation.state == "shadow_observed"
                    and gate_attestation_is_current(
                        prepared.root,
                        operation,
                        artifact_path,
                    )
                ):
                    return result
            _complete_shadow_observation(
                prepared,
                decision,
                operation,
                result,
                preflight,
                self._review_executor,
                require_canonical_policy=self._uses_canonical_resolver,
            )
        except Exception as exc:  # Shadow 诊断不能改变原命令结果。
            _record_pending_observation(operation, prepared, exc)
        return result


def execute_stage_close(
    prepared: PreparedStageClose,
    writer: Callable[[], _RESULT],
) -> _RESULT:
    executor = build_stage_review_executor(prepared.root)
    return StageCloseGateway(
        review_executor=executor,
        close_enforcer=executor,
    ).execute(prepared, writer)


def _read_stage_close_gate_attestations(
    root: Path,
) -> tuple[StageCloseGateAttestation, ...]:
    """读取项目本地统一关闭证明，供诊断与集成验收。"""

    return read_gate_attestations(root)


def _prepare_operation(
    prepared: PreparedStageClose,
    artifact_existed_before: bool,
) -> StageCloseGateOperation:
    operation = StageCloseGateOperation(
        operation_id=stage_close_operation_id(prepared),
        stage_key=prepared.stage_key,
        loop_id=prepared.loop_id,
        close_kind=prepared.close_kind,
        state="prepared",
        stage_input_digest=prepared.stage_input_digest,
        artifact_existed_before=artifact_existed_before,
    )
    try:
        return prepare_gate_operation(prepared.root, operation)
    except Exception:
        return operation


def _complete_shadow_observation(
    prepared: PreparedStageClose,
    decision: GateApplicabilityDecision,
    operation: StageCloseGateOperation,
    result: object,
    preflight: ShadowPlanningPreflight,
    executor: StageReviewExecutor | None,
    *,
    require_canonical_policy: bool,
) -> None:
    project_id = resolve_repository_project_id(prepared.root)
    with activation_safety_mutation_fence(prepared.root, project_id):
        current_policy = current_activation_policy(prepared.root)
        if (
            current_policy.compatibility_mode != "strict"
            or (
                require_canonical_policy
                and current_policy.policy_digest != decision.policy_digest
            )
        ):
            raise StageCloseGateUnavailableError(
                "activation-policy-changed-before-original-completion"
            )
        completed = _complete_original_operation(prepared, operation, result)
    if completed is None:
        return
    planning = observe_shadow_planning(
        prepared,
        decision,
        preflight,
        executor,
    )
    attestation = _build_attestation(
        prepared,
        decision,
        completed,
        planning,
        preflight.candidate,
    )
    with activation_safety_mutation_fence(prepared.root, project_id):
        current_policy = current_activation_policy(prepared.root)
        if (
            current_policy.compatibility_mode != "strict"
            or (
                require_canonical_policy
                and current_policy.policy_digest != decision.policy_digest
            )
        ):
            raise StageCloseGateUnavailableError(
                "activation-policy-changed-before-derived-commit"
            )
        _persist_attestation(prepared, attestation)
        advance_gate_operation(
            prepared.root,
            completed.model_copy(
                update={
                    "state": "shadow_observed",
                    "attestation_id": attestation.attestation_id,
                    "attestation_digest": attestation.attestation_digest,
                    "last_error_code": "",
                }
            ),
        )


def _complete_original_operation(
    prepared: PreparedStageClose,
    operation: StageCloseGateOperation,
    result: object,
) -> StageCloseGateOperation | None:
    artifact_path = prepared.root / prepared.close_artifact_path
    if not artifact_path.is_file():
        return None
    artifact_digest = file_digest(artifact_path)
    if operation.artifact_existed_before:
        result_payload = reconciled_result(prepared, artifact_digest)
    else:
        result_payload = observation_result(
            prepared,
            stage_close_result_payload(result),
            artifact_digest,
        )
    if result_payload is None:
        return None
    if operation.state in {"original_completed", "shadow_observed"}:
        if operation.close_artifact_digest == artifact_digest:
            return operation
        return prepare_gate_operation(
            prepared.root,
            build_reobservation_operation(
                prepared,
                operation,
                result_payload,
                artifact_digest,
                supersedes_attestation_id=latest_gate_attestation_id(
                    prepared.root,
                    operation.attestation_id,
                ),
            ),
        )
    completed = operation.model_copy(
        update={
            "state": "original_completed",
            "result_digest": canonical_digest(
                result_payload,
                CanonicalizationPolicy(),
            ),
            "result_status": str(result_payload.get("status", "unknown")),
            "result_loop_status": str(result_payload.get("loop_status", "")),
            "close_artifact_digest": artifact_digest,
            "last_error_code": "",
        }
    )
    return advance_gate_operation(prepared.root, completed)


def _build_attestation(
    prepared: PreparedStageClose,
    decision: GateApplicabilityDecision,
    operation: StageCloseGateOperation,
    planning: ShadowPlanningOutcome,
    candidate: CandidateManifest | None,
) -> StageCloseGateAttestation:
    values: dict[str, object] = {
        "operation_id": operation.operation_id,
        "gate_id": decision.gate_id,
        "adapter_id": prepared.adapter_id,
        "adapter_version": prepared.adapter_version,
        "adapter_contract_digest": prepared.adapter_contract_digest,
        "stage_key": prepared.stage_key,
        "loop_id": prepared.loop_id,
        "loop_round_number": prepared.loop_round_number,
        "stage_instance_id": prepared.stage_instance_id,
        "work_item_id": prepared.work_item_id,
        "close_kind": prepared.close_kind,
        "target_status": prepared.target_status,
        "close_artifact_path": prepared.close_artifact_path,
        "close_artifact_digest": operation.close_artifact_digest,
        "stage_input_digest": operation.stage_input_digest,
        "result_digest": operation.result_digest,
        "result_status": operation.result_status,
        "result_loop_status": operation.result_loop_status,
        "applicability": decision,
        "candidate": planning.candidate,
        "planning": planning.planning,
        "review_status": planning.review_status,
        "review_reason_code": planning.review_reason_code,
        "review_session_digest": planning.review_session_digest,
        "review_completion_digest": planning.review_completion_digest,
        "review_scope": (
            {
                "project_id": candidate.project_id,
                "work_item_id": candidate.work_item_id,
                "stage_instance_id": candidate.stage_instance_id,
                "session_id": candidate.review_session_id,
            }
            if planning.review_status == "completed" and candidate is not None
            else None
        ),
        "certificate_required": False,
        "observation_origin": (
            "closed_reconciliation"
            if operation.artifact_existed_before
            else "close_execution"
        ),
        "supersedes_attestation_id": operation.supersedes_attestation_id,
    }
    semantic_digest = canonical_digest(values, CanonicalizationPolicy())
    return StageCloseGateAttestation.model_validate(
        {
            "attestation_id": stable_id(
                "stage-close-gate-attestation",
                semantic_digest,
            ),
            **values,
        }
    )


def _record_pending_observation(
    operation: StageCloseGateOperation,
    prepared: PreparedStageClose,
    error: Exception,
) -> None:
    try:
        project_id = resolve_repository_project_id(prepared.root)
        with activation_safety_mutation_fence(prepared.root, project_id):
            if current_activation_policy(prepared.root).compatibility_mode != "strict":
                return
            current = (
                read_gate_operation(prepared.root, operation.operation_id)
                or operation
            )
            advance_gate_operation(
                prepared.root,
                current.model_copy(
                    update={"last_error_code": type(error).__name__.lower()}
                ),
            )
    except Exception:
        pass


def _persist_attestation(
    prepared: PreparedStageClose,
    attestation: StageCloseGateAttestation,
) -> None:
    persist_gate_attestation(prepared.root, attestation)
    record_activation_session(prepared.root, attestation)


def _refresh_activation_safely(root: Path) -> None:
    try:
        refresh_activation_policy_from_local_evidence(root)
    except Exception:
        # 激活评估是派生维护，不能反向撤销已经提交的 Stage Close。
        return


def _refresh_activation_before_close(root: Path) -> None:
    policy = current_activation_policy(root)
    if policy.active_phase == 1:
        return
    try:
        refresh_activation_policy_from_local_evidence(root)
    except Exception as exc:
        raise StageCloseGateUnavailableError(
            "activation-safety-evaluation-unavailable"
        ) from exc


def _enforce_activation_safety_before_writer(
    prepared: PreparedStageClose,
    decision: GateApplicabilityDecision,
    preflight: ShadowPlanningPreflight,
    executor: StageReviewExecutor | None,
) -> None:
    assessed_at = utc_now_iso()
    holds = _current_affected_activation_safety_holds(
        prepared.root,
        stage_key=prepared.stage_key,
        risk_level=prepared.risk_level,
        assessed_at=assessed_at,
    )
    if not holds:
        return
    if (
        executor is None
        or preflight.candidate is None
        or preflight.source_snapshot is None
    ):
        raise StageCloseGateUnavailableError(
            "activation-safety-hold-recovery-review-unavailable"
        )
    recovery = observe_shadow_planning(
        prepared,
        decision,
        preflight,
        executor,
    )
    if recovery.review_status != "completed":
        raise StageCloseGateUnavailableError(
            "activation-safety-hold-recovery-review-incomplete"
        )
    outcome = StageReviewExecutionOutcome(
        status="completed",
        review_session_digest=recovery.review_session_digest,
        review_completion_digest=recovery.review_completion_digest,
    )
    recovery_observed_at = utc_now_iso()
    project_id = resolve_repository_project_id(prepared.root)
    with activation_safety_mutation_fence(prepared.root, project_id):
        current_policy = current_activation_policy(prepared.root)
        if (
            current_policy.compatibility_mode != "strict"
            or current_policy.policy_digest != decision.policy_digest
        ):
            raise StageCloseGateUnavailableError(
                "activation-policy-changed-before-recovery-commit"
            )
        active = {
            (item.hold_id, item.hold_digest)
            for item in active_activation_safety_holds_for_lineage(
                prepared.root,
                policy=current_policy,
            )
        }
        if any((hold.hold_id, hold.hold_digest) not in active for hold in holds):
            raise StageCloseGateUnavailableError(
                "activation-safety-hold-changed-before-recovery-commit"
            )
        for hold in holds:
            try:
                sample = build_activation_safety_recovery_sample(
                    prepared.root,
                    hold,
                    candidate=preflight.candidate,
                    outcome=outcome,
                    risk_level=prepared.risk_level,
                    observed_at=recovery_observed_at,
                )
                record_activation_safety_recovery(prepared.root, sample)
            except Exception as exc:
                raise StageCloseGateUnavailableError(
                    "activation-safety-hold-recovery-evidence-invalid"
                ) from exc
    remaining = _current_affected_activation_safety_holds(
        prepared.root,
        stage_key=prepared.stage_key,
        risk_level=prepared.risk_level,
        assessed_at=utc_now_iso(),
    )
    if remaining:
        raise StageCloseGateUnavailableError(
            "activation-safety-hold-recovery-evidence-pending"
        )


def _current_affected_activation_safety_holds(
    root: Path,
    *,
    stage_key: str,
    risk_level: str,
    assessed_at: str,
) -> tuple[ActivationSafetyHold, ...]:
    project_id = resolve_repository_project_id(root)
    with activation_safety_mutation_fence(root, project_id):
        policy = current_activation_policy(root)
        if policy.compatibility_mode != "strict":
            raise StageCloseGateUnavailableError(
                "activation-safety-policy-is-read-only"
            )
        return affected_activation_safety_holds(
            root,
            policy=policy,
            stage_key=stage_key,
            risk_level=risk_level,
            assessed_at=assessed_at,
        )


def _execute_writer_under_activation_safety_fence(
    prepared: PreparedStageClose,
    decision: GateApplicabilityDecision,
    writer: Callable[[], _RESULT],
    *,
    require_canonical_policy: bool,
) -> _RESULT:
    project_id = resolve_repository_project_id(prepared.root)
    initial_policy = current_activation_policy(prepared.root)
    if require_canonical_policy and initial_policy.active_phase > 1:
        try:
            refresh_activation_policy_from_local_evidence(prepared.root)
        except Exception as exc:
            raise StageCloseGateUnavailableError(
                "activation-safety-evaluation-unavailable"
            ) from exc
    with activation_safety_read_lease(prepared.root, project_id):
        policy = current_activation_policy(prepared.root)
        if policy.compatibility_mode != "strict":
            raise StageCloseGateUnavailableError(
                "activation-policy-read-only-before-product-writer"
            )
        if require_canonical_policy and policy.policy_digest != decision.policy_digest:
            raise StageCloseGateUnavailableError(
                "activation-policy-changed-before-product-writer"
            )
        if policy.active_phase > 1:
            holds = tuple(
                hold
                for hold in active_activation_safety_holds_for_lineage(
                    prepared.root,
                    policy=policy,
                )
                if (prepared.stage_key, prepared.risk_level)
                in {
                    (item.stage_key, item.risk_level)
                    for item in hold.affected_combinations
                }
            )
            if holds:
                raise StageCloseGateUnavailableError(
                    "activation-safety-hold-blocked-product-writer"
                )
        return writer()
