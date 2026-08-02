"""Reviewer 最终 Provider 调用的核心隔离包装器。"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from ai_sdlc.core.stage_review.artifacts import resolve_canonical_shared_state
from ai_sdlc.core.stage_review.certificate_receipt_store import (
    FilesystemReviewReceiptArtifactStore,
)
from ai_sdlc.core.stage_review.isolation_detected_only import (
    DetectedOnlySentinelEvidence,
    DetectedOnlySentinelStore,
)
from ai_sdlc.core.stage_review.isolation_driver_wrapper import (
    _IsolatedDriver,
    _PermitExecutionRecorder,
)
from ai_sdlc.core.stage_review.isolation_execution import (
    IsolationPermitStore,
    TrustedIsolationBackendRegistry,
    build_refusal_receipt,
)
from ai_sdlc.core.stage_review.isolation_launch_models import (
    CommandKind,
    IsolatedCommandProviderDriver,
    IsolatedProviderCommand,
    IsolationBackendBundle,
    IsolationExecutionRecorder,
    IsolationLaunchContext,
    IsolationProcessResult,
    JournaledIsolationBackend,
)
from ai_sdlc.core.stage_review.isolation_launch_validation import (
    _bind_decoded_receipt as bind_decoded_receipt,
)
from ai_sdlc.core.stage_review.isolation_launch_validation import (
    _build_permit as build_permit,
)
from ai_sdlc.core.stage_review.isolation_launch_validation import (
    _execution_refusal_reason as execution_refusal_reason,
)
from ai_sdlc.core.stage_review.isolation_launch_validation import (
    _manifest_matches_context as manifest_matches_context,
)
from ai_sdlc.core.stage_review.isolation_launch_validation import (
    _permit_identity as permit_identity,
)
from ai_sdlc.core.stage_review.isolation_launch_validation import (
    _trusted_manifest_copy as trusted_manifest_copy,
)
from ai_sdlc.core.stage_review.isolation_models import (
    IsolationEvidenceManifest,
    IsolationExecutionObservation,
    IsolationExecutionPermit,
    IsolationExecutionReceipt,
)
from ai_sdlc.core.stage_review.isolation_receipts import (
    build_execution_receipt,
    build_preflight_receipt,
)
from ai_sdlc.core.stage_review.provider_execution_evidence import (
    ProviderExecutionOutcome,
    build_provider_execution_outcome,
    merge_provider_execution_outcomes,
)
from ai_sdlc.core.stage_review.provider_journal_driver import (
    ProviderDriverRefused,
    ProviderInvocationDriver,
)
from ai_sdlc.core.stage_review.provider_journal_models import (
    ProviderInvocationRequest,
    ProviderQueryResult,
    ProviderSubmission,
)


class IsolationCommandRefused(ProviderDriverRefused):  # noqa: N818
    pass


class ReviewerIsolationLauncher:
    def __init__(
        self,
        root: Path,
        *,
        registry: TrustedIsolationBackendRegistry,
        backend: IsolationBackendBundle,
        project_id: str = "",
    ) -> None:
        self._registry = registry
        self._backend = backend
        state_root = (
            resolve_canonical_shared_state(root, project_id) if project_id else root
        )
        self._permits = IsolationPermitStore(state_root)
        self._detected_only = DetectedOnlySentinelStore(state_root)
        self._root = root
        self._receipt_artifacts: FilesystemReviewReceiptArtifactStore | None = None

    def prepare_driver(
        self,
        driver: ProviderInvocationDriver,
        *,
        context: IsolationLaunchContext,
        now: datetime,
    ) -> ProviderInvocationDriver | None:
        manifest = trusted_manifest_copy(self._backend.probe(context, now))
        self._permits.persist_manifest(manifest)
        if self._permits.has_incomplete_execution(context.assignment_digest):
            self._persist_preflight(
                context,
                manifest,
                "isolation.execution-recovery-required",
                now,
            )
            return None
        if not manifest_matches_context(manifest, context):
            self._persist_preflight(
                context,
                manifest,
                "isolation.manifest-lineage-mismatch",
                now,
            )
            return None
        grade = self._registry.derive_grade(
            manifest,
            context.host_snapshot,
            adapter_grade=context.adapter_grade,
            now=now,
        )
        if grade == "detected_only":
            self._record_detected_only(context, manifest, now)
            return None
        if grade != "enforced":
            self._persist_preflight(
                context, manifest, "isolation.backend-unproven", now
            )
            return None
        if not isinstance(driver, IsolatedCommandProviderDriver):
            self._persist_preflight(
                context, manifest, "isolation.driver-incompatible", now
            )
            return None
        return _IsolatedDriver(self, driver, context, now)

    def receipts(self) -> tuple[IsolationExecutionReceipt, ...]:
        return self._permits.receipts()

    def detected_only_evidence(self) -> tuple[DetectedOnlySentinelEvidence, ...]:
        return self._detected_only.evidences()

    def execution_observations(self) -> tuple[IsolationExecutionObservation, ...]:
        return self._permits.observations()

    def _record_detected_only(
        self,
        context: IsolationLaunchContext,
        manifest: IsolationEvidenceManifest,
        now: datetime,
    ) -> None:
        reason = "isolation.backend-detected-only"
        try:
            evidence = self._detected_only.run(context, manifest, now)
            if not evidence[-1].cleanup_succeeded:
                reason = "isolation.detected-only-cleanup-failed"
        except (OSError, ValueError):
            reason = "isolation.detected-only-sentinel-failed"
        self._persist_preflight(context, manifest, reason, now)
        return None

    def _execute(
        self,
        driver: IsolatedCommandProviderDriver,
        request: ProviderInvocationRequest,
        context: IsolationLaunchContext,
        command_kind: CommandKind,
        now: datetime,
    ) -> ProviderSubmission | ProviderQueryResult:
        receipt_artifacts = self._receipt_store(request.project_id)
        manifest = self._active_manifest(context, now)
        permit = build_permit(context, manifest, now)
        self._permits.consume(permit, **permit_identity(context, manifest), now=now)
        receipt_artifacts.persist_isolation_permit(permit)
        command = self._build_command(
            driver,
            request,
            permit,
            command_kind,
            receipt_artifacts,
            now,
        )
        receipt: IsolationExecutionReceipt | None = None
        try:
            result = self._run_backend(command, permit, receipt_artifacts, now)
            receipt = self._record_execution_receipt(
                manifest, permit, command_kind, result, receipt_artifacts, now
            )
            decoded = driver.decode_isolated_result(request, command_kind, result)
        except ProviderDriverRefused as exc:
            outcome = merge_provider_execution_outcomes(
                _driver_execution_outcome(driver), exc.outcome
            )
            if receipt is not None:
                outcome = merge_provider_execution_outcomes(
                    outcome,
                    build_provider_execution_outcome(
                        isolation_receipt_digests=(receipt.receipt_digest,)
                    ),
                )
            raise IsolationCommandRefused(
                str(exc),
                outcome=outcome,
            ) from exc
        return bind_decoded_receipt(decoded, receipt.receipt_digest)

    def _build_command(
        self,
        driver: IsolatedCommandProviderDriver,
        request: ProviderInvocationRequest,
        permit: IsolationExecutionPermit,
        command_kind: CommandKind,
        receipt_artifacts: FilesystemReviewReceiptArtifactStore,
        now: datetime,
    ) -> IsolatedProviderCommand:
        try:
            command = driver.build_isolated_command(request, permit, command_kind)
        except ProviderDriverRefused as exc:
            receipt = self._persist_refusal(
                permit, receipt_artifacts, "isolation.command-build-refused", now
            )
            outcome = merge_provider_execution_outcomes(
                _driver_execution_outcome(driver),
                build_provider_execution_outcome(
                    exc.accounted_usage,
                    isolation_receipt_digests=(receipt.receipt_digest,),
                    egress_receipt_digests=exc.outcome.egress_receipt_digests,
                ),
            )
            raise IsolationCommandRefused(
                "isolated provider command build was refused",
                outcome=outcome,
            ) from exc
        if command.command_kind != command_kind or not command.argv:
            self._persist_refusal(
                permit, receipt_artifacts, "isolation.command-invalid", now
            )
            raise IsolationCommandRefused("isolated provider command is invalid")
        return command

    def _persist_refusal(
        self,
        permit: IsolationExecutionPermit,
        receipt_artifacts: FilesystemReviewReceiptArtifactStore,
        reason: str,
        now: datetime,
    ) -> IsolationExecutionReceipt:
        receipt = build_refusal_receipt(permit, reason=reason, now=now)
        self._permits.persist_receipt(receipt)
        receipt_artifacts.persist_isolation_receipt(receipt)
        return receipt

    def _active_manifest(
        self,
        context: IsolationLaunchContext,
        now: datetime,
    ) -> IsolationEvidenceManifest:
        manifest = trusted_manifest_copy(self._backend.probe(context, now))
        self._permits.persist_manifest(manifest)
        if self._permits.has_incomplete_execution(context.assignment_digest):
            self._persist_preflight(
                context, manifest, "isolation.execution-recovery-required", now
            )
            raise IsolationCommandRefused("isolation execution recovery is required")
        if not manifest_matches_context(manifest, context):
            self._persist_preflight(
                context, manifest, "isolation.manifest-lineage-mismatch", now
            )
            raise IsolationCommandRefused("isolation manifest lineage changed")
        grade = self._registry.derive_grade(
            manifest,
            context.host_snapshot,
            adapter_grade=context.adapter_grade,
            now=now,
        )
        if grade != "enforced":
            self._persist_preflight(context, manifest, "isolation.backend-stale", now)
            raise IsolationCommandRefused("isolation backend is no longer enforced")
        return manifest

    def _run_backend(
        self,
        command: IsolatedProviderCommand,
        permit: IsolationExecutionPermit,
        receipt_artifacts: FilesystemReviewReceiptArtifactStore,
        now: datetime,
    ) -> IsolationProcessResult:
        try:
            if isinstance(self._backend, JournaledIsolationBackend):
                return self._backend.execute_journaled(
                    command,
                    permit,
                    _PermitExecutionRecorder(self._permits, permit, now),
                )
            return self._backend.execute(command, permit)
        except (OSError, RuntimeError) as exc:
            receipt = build_refusal_receipt(
                permit,
                reason="isolation.backend-execution-refused",
                now=now,
            )
            self._permits.persist_receipt(receipt)
            receipt_artifacts.persist_isolation_receipt(receipt)
            raise IsolationCommandRefused(
                "isolation backend refused command",
                outcome=build_provider_execution_outcome(
                    isolation_receipt_digests=(receipt.receipt_digest,)
                ),
            ) from exc

    def _record_execution_receipt(
        self,
        manifest: IsolationEvidenceManifest,
        permit: IsolationExecutionPermit,
        command_kind: CommandKind,
        result: IsolationProcessResult,
        receipt_artifacts: FilesystemReviewReceiptArtifactStore,
        now: datetime,
    ) -> IsolationExecutionReceipt:
        reason = execution_refusal_reason(manifest, result)
        receipt = build_execution_receipt(
            permit,
            command_kind,
            result,
            now,
            reason=reason or "isolation.command-completed",
        )
        self._permits.persist_receipt(receipt)
        receipt_artifacts.persist_isolation_receipt(receipt)
        if reason:
            raise IsolationCommandRefused(
                "isolated provider command failed",
                outcome=build_provider_execution_outcome(
                    isolation_receipt_digests=(receipt.receipt_digest,)
                ),
            )
        return receipt

    def _receipt_store(
        self,
        project_id: str,
    ) -> FilesystemReviewReceiptArtifactStore:
        if self._receipt_artifacts is None:
            self._receipt_artifacts = FilesystemReviewReceiptArtifactStore(
                self._root,
                project_id=project_id,
            )
        if self._receipt_artifacts.project_id != project_id:
            raise IsolationCommandRefused("isolation project authority changed")
        return self._receipt_artifacts

    def _persist_preflight(
        self,
        context: IsolationLaunchContext,
        manifest: IsolationEvidenceManifest,
        reason: str,
        now: datetime,
    ) -> None:
        self._permits.persist_receipt(
            build_preflight_receipt(context, manifest, reason, now)
        )


def _driver_execution_outcome(
    driver: IsolatedCommandProviderDriver,
) -> ProviderExecutionOutcome:
    outcome = getattr(driver, "executed_outcome", None)
    if isinstance(outcome, ProviderExecutionOutcome):
        return outcome
    return build_provider_execution_outcome(
        getattr(driver, "executed_accounted_usage", None)
    )


__all__ = [
    "IsolatedProviderCommand",
    "IsolationBackendBundle",
    "IsolationCommandRefused",
    "IsolationExecutionRecorder",
    "IsolationLaunchContext",
    "IsolationProcessResult",
    "ReviewerIsolationLauncher",
]
