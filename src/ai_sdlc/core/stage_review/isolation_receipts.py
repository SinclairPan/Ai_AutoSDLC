"""隔离执行 Receipt 的规范化构建器。"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Literal

from ai_sdlc.core.stage_review.isolation_models import (
    IsolationEvidenceManifest,
    IsolationExecutionObservation,
    IsolationExecutionPermit,
    IsolationExecutionReceipt,
)
from ai_sdlc.core.stage_review.isolation_models import (
    _observation_digest as observation_digest,
)
from ai_sdlc.core.stage_review.isolation_models import (
    _receipt_digest as receipt_digest,
)
from ai_sdlc.core.stage_review.resource_builders import stable_id, utc_iso

if TYPE_CHECKING:
    from ai_sdlc.core.stage_review.isolation_launcher import (
        IsolationLaunchContext,
        IsolationProcessResult,
    )

CommandKind = Literal["invoke", "query"]


def build_execution_receipt(
    permit: IsolationExecutionPermit,
    command_kind: CommandKind,
    result: IsolationProcessResult,
    now: datetime,
    *,
    reason: str = "isolation.command-completed",
) -> IsolationExecutionReceipt:
    values = _receipt_values(
        permit=permit,
        command_kind=command_kind,
        result=result,
        recorded_at=utc_iso(now),
        reason=reason,
    )
    return _receipt(values)


def build_preflight_receipt(
    context: IsolationLaunchContext,
    manifest: IsolationEvidenceManifest,
    reason: str,
    now: datetime,
) -> IsolationExecutionReceipt:
    values = {
        "receipt_id": stable_id(
            "isolation-receipt",
            context.assignment_digest,
            reason,
            utc_iso(now),
        ),
        "permit_digest": "unissued",
        "manifest_digest": manifest.manifest_digest,
        "release_manifest_digest": manifest.release_manifest_digest,
        "runtime_identity_digest": manifest.runtime_identity_digest,
        "allocation_digest": context.allocation_digest,
        "assignment_digest": context.assignment_digest,
        "candidate_digest": context.candidate_digest,
        "host_snapshot_digest": context.host_snapshot.snapshot_digest,
        "backend_id": manifest.backend_id,
        "backend_version": manifest.backend_version,
        "backend_instance_id": manifest.backend_instance_id,
        "backend_epoch": manifest.backend_epoch,
        "layout_digest": context.layout_digest,
        "command_kind": "refusal",
        "command_started": False,
        "process_id": 0,
        "parent_process_id": 0,
        "boundary_results": (),
        "os_native_denials": (),
        "before_digest": "",
        "after_digest": "",
        "cleanup_succeeded": True,
        "reason_id": reason,
        "recorded_at": utc_iso(now),
    }
    return _receipt(values)


def build_execution_observation(
    permit: IsolationExecutionPermit,
    result: IsolationProcessResult,
    *,
    stage: str,
    previous_observation_digest: str,
    now: datetime,
) -> IsolationExecutionObservation:
    recorded_at = utc_iso(now)
    values = {
        "observation_id": stable_id(
            "isolation-observation",
            permit.permit_digest,
            stage,
        ),
        "permit_digest": permit.permit_digest,
        "manifest_digest": permit.manifest_digest,
        "release_manifest_digest": permit.release_manifest_digest,
        "runtime_identity_digest": permit.runtime_identity_digest,
        "assignment_digest": permit.assignment_digest,
        "candidate_digest": permit.candidate_digest,
        "stage": stage,
        "previous_observation_digest": previous_observation_digest,
        "process_id": result.process_id,
        "parent_process_id": result.parent_process_id,
        "before_digest": result.before_digest,
        "after_digest": result.after_digest,
        "cleanup_succeeded": stage == "cleaned",
        "recorded_at": recorded_at,
    }
    draft = IsolationExecutionObservation.model_construct(
        **values,  # type: ignore[arg-type]
        observation_digest="",
    )
    payload = draft.model_dump(mode="json")
    payload["observation_digest"] = observation_digest(draft)
    return IsolationExecutionObservation.model_validate(payload)


def _receipt_values(
    *,
    permit: IsolationExecutionPermit,
    command_kind: CommandKind,
    result: IsolationProcessResult,
    recorded_at: str,
    reason: str,
) -> dict[str, object]:
    return {
        "receipt_id": stable_id(
            "isolation-receipt", permit.permit_digest, command_kind
        ),
        "permit_digest": permit.permit_digest,
        "manifest_digest": permit.manifest_digest,
        "release_manifest_digest": permit.release_manifest_digest,
        "runtime_identity_digest": permit.runtime_identity_digest,
        "allocation_digest": permit.allocation_digest,
        "assignment_digest": permit.assignment_digest,
        "candidate_digest": permit.candidate_digest,
        "host_snapshot_digest": permit.host_snapshot_digest,
        "backend_id": permit.backend_id,
        "backend_version": permit.backend_version,
        "backend_instance_id": permit.backend_instance_id,
        "backend_epoch": permit.backend_epoch,
        "layout_digest": permit.layout_digest,
        "command_kind": command_kind,
        "command_started": True,
        "process_id": result.process_id,
        "parent_process_id": result.parent_process_id,
        "boundary_results": tuple(
            sorted(result.boundary_results, key=lambda item: item.action)
        ),
        "os_native_denials": tuple(
            sorted(
                result.os_native_denials,
                key=lambda item: (
                    item.mechanism,
                    item.operation,
                    item.target,
                    item.observed_at,
                ),
            )
        ),
        "before_digest": result.before_digest,
        "after_digest": result.after_digest,
        "cleanup_succeeded": result.cleanup_succeeded,
        "reason_id": reason,
        "recorded_at": recorded_at,
    }


def _receipt(values: dict[str, object]) -> IsolationExecutionReceipt:
    draft = IsolationExecutionReceipt.model_construct(
        **values,  # type: ignore[arg-type]
        receipt_digest="",
    )
    payload = draft.model_dump(mode="json")
    payload["receipt_digest"] = receipt_digest(draft)
    return IsolationExecutionReceipt.model_validate(payload)


__all__ = [
    "build_execution_observation",
    "build_execution_receipt",
    "build_preflight_receipt",
]
