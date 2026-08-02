"""隔离 Manifest、Permit、执行结果与 Provider 收据绑定校验。"""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta

from ai_sdlc.core.stage_review.isolation_execution import (
    build_isolation_execution_permit,
)
from ai_sdlc.core.stage_review.isolation_launch_models import (
    IsolationLaunchContext,
    IsolationProcessResult,
)
from ai_sdlc.core.stage_review.isolation_models import (
    IsolationEvidenceManifest,
    IsolationExecutionPermit,
)
from ai_sdlc.core.stage_review.provider_journal_builders import (
    _bind_submission_isolation_receipt as bind_submission_isolation_receipt,
)
from ai_sdlc.core.stage_review.provider_journal_models import (
    ProviderQueryResult,
    ProviderSubmission,
)
from ai_sdlc.core.stage_review.resource_builders import parse_utc, utc_iso


def _build_permit(
    context: IsolationLaunchContext,
    manifest: IsolationEvidenceManifest,
    now: datetime,
) -> IsolationExecutionPermit:
    expiry = min(
        parse_utc(manifest.expires_at),
        parse_utc(context.host_snapshot.expires_at),
        now + timedelta(seconds=30),
    )
    return build_isolation_execution_permit(
        allocation_digest=context.allocation_digest,
        assignment_digest=context.assignment_digest,
        candidate_digest=context.candidate_digest,
        host_snapshot_digest=context.host_snapshot.snapshot_digest,
        backend_id=manifest.backend_id,
        contract_version=manifest.contract_version,
        backend_version=manifest.backend_version,
        backend_instance_id=manifest.backend_instance_id,
        backend_epoch=manifest.backend_epoch,
        normalized_run_root=context.normalized_run_root,
        layout_digest=context.layout_digest,
        filesystem_policy_digest=manifest.filesystem_policy_digest,
        network_policy_digest=manifest.network_policy_digest,
        manifest_digest=manifest.manifest_digest,
        release_manifest_digest=manifest.release_manifest_digest,
        runtime_identity_digest=manifest.runtime_identity_digest,
        issued_at=utc_iso(now),
        expires_at=utc_iso(expiry),
        nonce=secrets.token_hex(16),
    )


def _permit_identity(
    context: IsolationLaunchContext,
    manifest: IsolationEvidenceManifest,
) -> dict[str, str]:
    return {
        "allocation_digest": context.allocation_digest,
        "assignment_digest": context.assignment_digest,
        "candidate_digest": context.candidate_digest,
        "host_snapshot_digest": context.host_snapshot.snapshot_digest,
        "backend_instance_id": manifest.backend_instance_id,
        "backend_epoch": manifest.backend_epoch,
        "layout_digest": context.layout_digest,
    }


def _trusted_manifest_copy(
    value: IsolationEvidenceManifest,
) -> IsolationEvidenceManifest:
    return IsolationEvidenceManifest.model_validate(value.model_dump(mode="json"))


def _manifest_matches_context(
    manifest: IsolationEvidenceManifest,
    context: IsolationLaunchContext,
) -> bool:
    trusted_identity = (
        context.selected_backend_id,
        context.selected_contract_version,
        context.release_manifest_digest,
        context.runtime_identity_digest,
    )
    if any(not value for value in trusted_identity):
        return False
    return all(
        (
            manifest.allocation_digest == context.allocation_digest,
            manifest.assignment_digest == context.assignment_digest,
            manifest.candidate_digest == context.candidate_digest,
            manifest.layout_digest == context.layout_digest,
            manifest.host_snapshot_digest == context.host_snapshot.snapshot_digest,
            manifest.backend_id == context.selected_backend_id,
            manifest.contract_version == context.selected_contract_version,
            manifest.release_manifest_digest == context.release_manifest_digest,
            manifest.runtime_identity_digest == context.runtime_identity_digest,
        )
    )


def _execution_refusal_reason(
    manifest: IsolationEvidenceManifest,
    result: IsolationProcessResult,
) -> str:
    if not result.cleanup_succeeded:
        return "isolation.execution-cleanup-failed"
    if result.before_digest != result.after_digest:
        return "isolation.protected-state-changed"
    lineage = (
        result.return_code == 0,
        result.process_id > 0,
        result.parent_process_id == manifest.parent_process_id,
        result.boundary_results == manifest.boundary_results,
        result.os_native_denials == manifest.os_native_denials,
    )
    return "" if all(lineage) else "isolation.execution-lineage-mismatch"


def _bind_decoded_receipt(
    decoded: ProviderSubmission | ProviderQueryResult,
    receipt_digest: str,
) -> ProviderSubmission | ProviderQueryResult:
    if isinstance(decoded, ProviderSubmission):
        return bind_submission_isolation_receipt(decoded, receipt_digest)
    if decoded.submission is None:
        return decoded
    return ProviderQueryResult(
        query_status="submitted",
        submission=bind_submission_isolation_receipt(
            decoded.submission,
            receipt_digest,
        ),
    )
