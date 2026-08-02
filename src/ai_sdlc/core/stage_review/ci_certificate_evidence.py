"""可由 CI 离线重放的关闭证书权威输入与 Provider Receipt。"""

from __future__ import annotations

from typing import Literal, Self, TypeVar, cast

from pydantic import BaseModel, ConfigDict, model_validator

from ai_sdlc.core.stage_review.artifact_compat import (
    ArtifactCompatibility,
    JsonValue,
    fill_artifact_digest,
)
from ai_sdlc.core.stage_review.binding_models import BindingAuthoritySnapshot
from ai_sdlc.core.stage_review.binding_result_models import (
    ReviewerBindingSet,
    ReviewerDispatchAssignment,
)
from ai_sdlc.core.stage_review.certificate_builder import build_certificate
from ai_sdlc.core.stage_review.certificate_models import (
    StageCloseCertificate,
    StageCloseCertificateRequest,
)
from ai_sdlc.core.stage_review.certificate_receipt_store import (
    ReviewReceiptArtifactResolver,
)
from ai_sdlc.core.stage_review.certificate_validation import (
    CertificateInvalidError,
    validate_reconciled_certificate_inputs,
)
from ai_sdlc.core.stage_review.certificate_validation import (
    _validate_certificate_inputs as validate_certificate_inputs,
)
from ai_sdlc.core.stage_review.close_models import CloseConsumptionClaim
from ai_sdlc.core.stage_review.close_recovery_models import StageCloseRecoveryDecision
from ai_sdlc.core.stage_review.finding_models import FindingLedger
from ai_sdlc.core.stage_review.isolation_models import (
    IsolationExecutionPermit,
    IsolationExecutionReceipt,
)
from ai_sdlc.core.stage_review.panel_plan_models import ReviewerPanelPlan
from ai_sdlc.core.stage_review.provider_journal_models import ProviderInvocation
from ai_sdlc.core.stage_review.provider_transport_models import (
    ProviderEgressPermit,
    ProviderEgressReceipt,
)
from ai_sdlc.core.stage_review.resource_ledger_models import (
    ResourceReconciliation,
    ResourceReservation,
)
from ai_sdlc.core.stage_review.session_artifact_models import ReviewCohort, ReviewPass
from ai_sdlc.core.stage_review.session_certificate_inputs import (
    SessionCertificateInputs,
)
from ai_sdlc.core.stage_review.session_models import StageReviewSession

_CONFIG = ConfigDict(extra="forbid", frozen=True)
_ModelT = TypeVar("_ModelT", bound=BaseModel)


class CiReviewReceiptBundle(ArtifactCompatibility):
    """ReviewPass 引用的全部 Receipt 实体，只提供只读解析。"""

    model_config = _CONFIG
    schema_version: Literal["ci-review-receipt-bundle.v1"] = (
        "ci-review-receipt-bundle.v1"
    )
    artifact_kind: Literal["ci-review-receipt-bundle"] = "ci-review-receipt-bundle"
    invocations: tuple[ProviderInvocation, ...]
    isolation_permits: tuple[IsolationExecutionPermit, ...]
    isolation_receipts: tuple[IsolationExecutionReceipt, ...]
    egress_permits: tuple[ProviderEgressPermit, ...]
    egress_receipts: tuple[ProviderEgressReceipt, ...]
    responses: dict[str, dict[str, JsonValue]]
    receipt_bundle_digest: str = ""

    @model_validator(mode="after")
    def _validate_bundle(self) -> Self:
        _require_canonical(self.invocations, "invocation_id")
        _require_canonical(self.isolation_permits, "permit_digest")
        _require_canonical(self.isolation_receipts, "receipt_digest")
        _require_canonical(self.egress_permits, "permit_digest")
        _require_canonical(self.egress_receipts, "receipt_digest")
        if tuple(self.responses) != tuple(sorted(self.responses)):
            raise ValueError("CI receipt responses must be canonical")
        return fill_artifact_digest(self, "receipt_bundle_digest")

    def resolve_invocation(self, invocation_id: str) -> ProviderInvocation:
        return _resolve(self.invocations, "invocation_id", invocation_id)

    def resolve_isolation_permit(self, permit_digest: str) -> IsolationExecutionPermit:
        return _resolve(self.isolation_permits, "permit_digest", permit_digest)

    def resolve_isolation_receipt(
        self, receipt_digest: str
    ) -> IsolationExecutionReceipt:
        return _resolve(self.isolation_receipts, "receipt_digest", receipt_digest)

    def resolve_egress_permit(self, permit_digest: str) -> ProviderEgressPermit:
        return _resolve(self.egress_permits, "permit_digest", permit_digest)

    def resolve_egress_receipt(self, receipt_digest: str) -> ProviderEgressReceipt:
        return _resolve(self.egress_receipts, "receipt_digest", receipt_digest)

    def resolve_response(self, response_digest: str) -> dict[str, object]:
        try:
            return dict(self.responses[response_digest])
        except KeyError as exc:
            raise ValueError("CI receipt response is unavailable") from exc


class CiCertificateAuthorityEvidence(ArtifactCompatibility):
    """签发时已锁定的 Session、治理、资源与 Receipt 权威快照。"""

    model_config = _CONFIG
    schema_version: Literal["ci-certificate-authority-evidence.v1"] = (
        "ci-certificate-authority-evidence.v1"
    )
    artifact_kind: Literal["ci-certificate-authority-evidence"] = (
        "ci-certificate-authority-evidence"
    )
    session: StageReviewSession
    panel_plan: ReviewerPanelPlan
    binding_authority: BindingAuthoritySnapshot
    binding_set: ReviewerBindingSet
    cohort: ReviewCohort
    passes: tuple[ReviewPass, ...]
    assignments: tuple[ReviewerDispatchAssignment, ...]
    finding_ledger: FindingLedger
    final_reservation: ResourceReservation
    current_reservation: ResourceReservation
    reconciliation: ResourceReconciliation
    receipts: CiReviewReceiptBundle
    authority_evidence_digest: str = ""

    @model_validator(mode="after")
    def _validate_evidence(self) -> Self:
        if self.passes != tuple(sorted(self.passes, key=lambda item: item.slot_id)):
            raise ValueError("CI certificate passes must be canonical")
        if self.assignments != tuple(
            sorted(self.assignments, key=lambda item: item.slot_id)
        ):
            raise ValueError("CI certificate assignments must be canonical")
        return fill_artifact_digest(self, "authority_evidence_digest")

    def session_inputs(self) -> SessionCertificateInputs:
        return SessionCertificateInputs(
            session=self.session,
            plan=self.panel_plan,
            authority_snapshot=self.binding_authority,
            binding_set=self.binding_set,
            cohort=self.cohort,
            passes=self.passes,
            assignments=self.assignments,
            ledger=self.finding_ledger,
        )


class CiCertificateAuthorityProof(ArtifactCompatibility):
    """Certificate Authority 在签发点持久化的完整离线证明。"""

    model_config = _CONFIG
    schema_version: Literal["ci-certificate-authority-proof.v1"] = (
        "ci-certificate-authority-proof.v1"
    )
    artifact_kind: Literal["ci-certificate-authority-proof"] = (
        "ci-certificate-authority-proof"
    )
    certificate: StageCloseCertificate
    certificate_request: StageCloseCertificateRequest
    authority_evidence: CiCertificateAuthorityEvidence
    aborted_claim: CloseConsumptionClaim | None = None
    recovery_decision: StageCloseRecoveryDecision | None = None
    authority_proof_digest: str = ""

    @model_validator(mode="after")
    def _validate_proof(self) -> Self:
        if (self.aborted_claim is None) != (self.recovery_decision is None):
            raise ValueError("CI reconciled certificate proof context is incomplete")
        validate_ci_certificate_authority_evidence(
            self.certificate,
            self.certificate_request,
            self.authority_evidence,
            aborted_claim=self.aborted_claim,
        )
        return fill_artifact_digest(self, "authority_proof_digest")


def capture_ci_certificate_authority_evidence(
    inputs: SessionCertificateInputs,
    final: ResourceReservation,
    current: ResourceReservation,
    reconciliation: ResourceReconciliation,
    resolver: ReviewReceiptArtifactResolver,
) -> CiCertificateAuthorityEvidence:
    return CiCertificateAuthorityEvidence(
        session=inputs.session,
        panel_plan=inputs.plan,
        binding_authority=inputs.authority_snapshot,
        binding_set=inputs.binding_set,
        cohort=inputs.cohort,
        passes=inputs.passes,
        assignments=inputs.assignments,
        finding_ledger=inputs.ledger,
        final_reservation=final,
        current_reservation=current,
        reconciliation=reconciliation,
        receipts=_capture_receipts(inputs.passes, resolver),
    )


def validate_ci_certificate_authority_evidence(
    certificate: StageCloseCertificate,
    request: StageCloseCertificateRequest,
    evidence: CiCertificateAuthorityEvidence,
    *,
    aborted_claim: CloseConsumptionClaim | None = None,
) -> None:
    trusted = CiCertificateAuthorityEvidence.model_validate(
        evidence.model_dump(mode="json")
    )
    inputs = trusted.session_inputs()
    _validate_authority_inputs(request, inputs, trusted, aborted_claim)
    rebuilt = build_certificate(
        request,
        inputs,
        trusted.final_reservation,
        trusted.reconciliation,
        issued_at=certificate.issued_at,
    )
    if rebuilt != certificate:
        raise CertificateInvalidError("CI certificate authority proof diverged")


def _validate_authority_inputs(
    request: StageCloseCertificateRequest,
    inputs: SessionCertificateInputs,
    evidence: CiCertificateAuthorityEvidence,
    aborted_claim: CloseConsumptionClaim | None,
) -> None:
    args = (
        request,
        inputs,
        evidence.final_reservation,
        evidence.current_reservation,
        evidence.reconciliation,
    )
    if aborted_claim is None:
        validate_certificate_inputs(*args, evidence.receipts)
        return
    validate_reconciled_certificate_inputs(
        *args,
        aborted_claim,
        evidence.receipts,
    )


def _capture_receipts(
    passes: tuple[ReviewPass, ...],
    resolver: ReviewReceiptArtifactResolver,
) -> CiReviewReceiptBundle:
    invocations: dict[str, ProviderInvocation] = {}
    isolation_permits: dict[str, IsolationExecutionPermit] = {}
    isolation_receipts: dict[str, IsolationExecutionReceipt] = {}
    egress_permits: dict[str, ProviderEgressPermit] = {}
    egress_receipts: dict[str, ProviderEgressReceipt] = {}
    responses: dict[str, dict[str, JsonValue]] = {}
    for review_pass in passes:
        invocations[review_pass.invocation_id] = resolver.resolve_invocation(
            review_pass.invocation_id
        )
        for digest in review_pass.isolation_receipt_digests:
            isolation_receipt = resolver.resolve_isolation_receipt(digest)
            isolation_receipts[digest] = isolation_receipt
            isolation_permits[isolation_receipt.permit_digest] = (
                resolver.resolve_isolation_permit(isolation_receipt.permit_digest)
            )
        for digest in review_pass.egress_receipt_digests:
            egress_receipt = resolver.resolve_egress_receipt(digest)
            egress_receipts[digest] = egress_receipt
            egress_permits[egress_receipt.permit_digest] = (
                resolver.resolve_egress_permit(egress_receipt.permit_digest)
            )
            responses[egress_receipt.response_digest] = cast(
                dict[str, JsonValue],
                resolver.resolve_response(egress_receipt.response_digest),
            )
    return _receipt_bundle(
        invocations,
        isolation_permits,
        isolation_receipts,
        egress_permits,
        egress_receipts,
        responses,
    )


def _receipt_bundle(
    invocations: dict[str, ProviderInvocation],
    isolation_permits: dict[str, IsolationExecutionPermit],
    isolation_receipts: dict[str, IsolationExecutionReceipt],
    egress_permits: dict[str, ProviderEgressPermit],
    egress_receipts: dict[str, ProviderEgressReceipt],
    responses: dict[str, dict[str, JsonValue]],
) -> CiReviewReceiptBundle:
    return CiReviewReceiptBundle(
        invocations=tuple(invocations[key] for key in sorted(invocations)),
        isolation_permits=tuple(
            isolation_permits[key] for key in sorted(isolation_permits)
        ),
        isolation_receipts=tuple(
            isolation_receipts[key] for key in sorted(isolation_receipts)
        ),
        egress_permits=tuple(egress_permits[key] for key in sorted(egress_permits)),
        egress_receipts=tuple(egress_receipts[key] for key in sorted(egress_receipts)),
        responses={key: responses[key] for key in sorted(responses)},
    )


def _require_canonical(values: tuple[BaseModel, ...], field: str) -> None:
    identities = tuple(str(getattr(item, field)) for item in values)
    if identities != tuple(sorted(set(identities))):
        raise ValueError("CI receipt entities must be canonical")


def _resolve(
    values: tuple[_ModelT, ...],
    field: str,
    identity: str,
) -> _ModelT:
    matches = tuple(item for item in values if getattr(item, field) == identity)
    if len(matches) != 1:
        raise ValueError("CI receipt entity is unavailable")
    return matches[0]


__all__ = [
    "CiCertificateAuthorityEvidence",
    "CiCertificateAuthorityProof",
    "CiReviewReceiptBundle",
    "capture_ci_certificate_authority_evidence",
    "validate_ci_certificate_authority_evidence",
]
