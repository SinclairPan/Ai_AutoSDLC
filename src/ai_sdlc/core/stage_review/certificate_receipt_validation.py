"""ReviewPass ordered Receipt 实体与 Provider Invocation authority 校验。"""

from __future__ import annotations

from ai_sdlc.core.stage_review.binding_authority_validation import (
    _validate_binding_against_descriptor,
)
from ai_sdlc.core.stage_review.binding_models import (
    BindingAuthoritySnapshot,
    ProviderBindingDescriptor,
)
from ai_sdlc.core.stage_review.binding_result_models import (
    ReviewerBinding,
    ReviewerDispatchAssignment,
)
from ai_sdlc.core.stage_review.canonical import CanonicalizationPolicy, canonical_digest
from ai_sdlc.core.stage_review.certificate_receipt_store import (
    ReviewReceiptArtifactResolver,
)
from ai_sdlc.core.stage_review.isolation_models import (
    IsolationExecutionPermit,
    IsolationExecutionReceipt,
)
from ai_sdlc.core.stage_review.provider_execution_evidence import (
    provider_execution_evidence_root_digest,
)
from ai_sdlc.core.stage_review.provider_journal_models import ProviderInvocation
from ai_sdlc.core.stage_review.provider_transport_models import (
    ProviderEgressPermit,
    ProviderEgressReceipt,
)
from ai_sdlc.core.stage_review.resource_builders import parse_utc
from ai_sdlc.core.stage_review.session_artifact_models import ReviewPass


def validate_review_pass_receipts(
    review_pass: ReviewPass,
    resolver: ReviewReceiptArtifactResolver,
    binding: ReviewerBinding,
    assignment: ReviewerDispatchAssignment,
    authority: BindingAuthoritySnapshot,
) -> None:
    descriptor = _validate_binding_against_descriptor(authority, binding, assignment)
    invocation = resolver.resolve_invocation(review_pass.invocation_id)
    _validate_invocation(review_pass, invocation, assignment)
    isolation = tuple(
        resolver.resolve_isolation_receipt(digest)
        for digest in review_pass.isolation_receipt_digests
    )
    egress = tuple(
        resolver.resolve_egress_receipt(digest)
        for digest in review_pass.egress_receipt_digests
    )
    _validate_isolation(
        review_pass,
        isolation,
        resolver,
        assignment,
        descriptor,
    )
    _validate_egress(
        review_pass,
        invocation,
        egress,
        resolver,
        binding,
        assignment,
    )
    expected_root = provider_execution_evidence_root_digest(
        tuple(item.receipt_digest for item in isolation),
        tuple(item.receipt_digest for item in egress),
    )
    if expected_root != review_pass.execution_evidence_root_digest:
        raise ValueError("review pass execution evidence root diverged")


def _validate_invocation(
    review_pass: ReviewPass,
    invocation: ProviderInvocation,
    assignment: ReviewerDispatchAssignment,
) -> None:
    request = invocation.request
    expected = (
        invocation.state == "committed",
        invocation.projection_digest == review_pass.invocation_projection_digest,
        invocation.validation_digest == review_pass.validation_digest,
        request.invocation_id == review_pass.invocation_id,
        request.assignment_digest == review_pass.assignment_digest,
        request.assignment_digest == assignment.assignment_digest,
        request.authorization_scope == "reviewer_binding",
        request.candidate_digest == review_pass.candidate_digest,
        request.provider_id == review_pass.provider_id,
        request.provider_id == assignment.provider_id,
        request.stage_review_session_id == assignment.session_id,
        request.capabilities == assignment.recovery_capabilities,
        invocation.isolation_receipt_digests == review_pass.isolation_receipt_digests,
        invocation.egress_receipt_digests == review_pass.egress_receipt_digests,
        invocation.execution_evidence_root_digest
        == review_pass.execution_evidence_root_digest,
    )
    if not all(expected):
        raise ValueError("review pass provider invocation lineage diverged")


def _validate_isolation(
    review_pass: ReviewPass,
    receipts: tuple[IsolationExecutionReceipt, ...],
    resolver: ReviewReceiptArtifactResolver,
    assignment: ReviewerDispatchAssignment,
    descriptor: ProviderBindingDescriptor,
) -> None:
    _require_order(tuple(item.recorded_at for item in receipts), "isolation")
    backend_identity: tuple[str, str, str, str] | None = None
    for receipt in receipts:
        permit = resolver.resolve_isolation_permit(receipt.permit_digest)
        _validate_isolation_pair(
            review_pass,
            permit,
            receipt,
            assignment,
            descriptor,
        )
        current = (
            receipt.backend_id,
            receipt.backend_version,
            receipt.release_manifest_digest,
            receipt.runtime_identity_digest,
        )
        if backend_identity is not None and current != backend_identity:
            raise ValueError("isolation backend identity changed within review pass")
        backend_identity = current


def _validate_isolation_pair(
    review_pass: ReviewPass,
    permit: IsolationExecutionPermit,
    receipt: IsolationExecutionReceipt,
    assignment: ReviewerDispatchAssignment,
    descriptor: ProviderBindingDescriptor,
) -> None:
    if not (
        permit.release_manifest_digest
        == receipt.release_manifest_digest
        == descriptor.provider_policy_evidence_digest
    ):
        raise ValueError("isolation provider release authority diverged")
    if not (
        permit.host_snapshot_digest
        == receipt.host_snapshot_digest
        == assignment.host_snapshot_digest
    ):
        raise ValueError("isolation host snapshot authority diverged")
    lineage = (
        receipt.command_started,
        receipt.command_kind in {"invoke", "query"},
        receipt.reason_id == "isolation.command-completed",
        receipt.cleanup_succeeded,
        receipt.before_digest == receipt.after_digest,
        receipt.assignment_digest == review_pass.assignment_digest,
        receipt.candidate_digest == review_pass.candidate_digest,
        permit.permit_digest == receipt.permit_digest,
        permit.assignment_digest == receipt.assignment_digest,
        permit.candidate_digest == receipt.candidate_digest,
        permit.backend_id == receipt.backend_id,
        permit.backend_version == receipt.backend_version,
        permit.backend_instance_id == receipt.backend_instance_id,
        permit.backend_epoch == receipt.backend_epoch,
        permit.manifest_digest == receipt.manifest_digest,
        bool(receipt.runtime_identity_digest),
        permit.runtime_identity_digest == receipt.runtime_identity_digest,
    )
    if not all(lineage):
        raise ValueError("isolation receipt entity lineage diverged")


def _validate_egress(
    review_pass: ReviewPass,
    invocation: ProviderInvocation,
    receipts: tuple[ProviderEgressReceipt, ...],
    resolver: ReviewReceiptArtifactResolver,
    binding: ReviewerBinding,
    assignment: ReviewerDispatchAssignment,
) -> None:
    _require_turn_order(tuple(item.turn_index for item in receipts))
    for receipt in receipts:
        permit = resolver.resolve_egress_permit(receipt.permit_digest)
        response = resolver.resolve_response(receipt.response_digest)
        _validate_egress_pair(
            review_pass,
            invocation,
            permit,
            receipt,
            response,
            binding,
            assignment,
        )


def _validate_egress_pair(
    review_pass: ReviewPass,
    invocation: ProviderInvocation,
    permit: ProviderEgressPermit,
    receipt: ProviderEgressReceipt,
    response: dict[str, object],
    binding: ReviewerBinding,
    assignment: ReviewerDispatchAssignment,
) -> None:
    request = invocation.request
    _require_egress_execution_authority(permit, receipt, binding, assignment)
    lineage = (
        receipt.invocation_id == review_pass.invocation_id,
        receipt.assignment_digest == review_pass.assignment_digest,
        receipt.provider_id == review_pass.provider_id,
        receipt.request_digest == request.request_digest,
        receipt.idempotency_key == request.idempotency_key,
        permit.permit_digest == receipt.permit_digest,
        permit.invocation_id == receipt.invocation_id,
        permit.assignment_digest == receipt.assignment_digest,
        permit.provider_id == receipt.provider_id,
        permit.request_digest == receipt.request_digest,
        permit.turn_index == receipt.turn_index,
        permit.idempotency_key == receipt.idempotency_key,
        permit.credential_view_digest == receipt.credential_view_digest,
        permit.backend_epoch == receipt.backend_epoch,
        permit.endpoint_id == receipt.endpoint_id,
        permit.transport_contract_digest == receipt.transport_contract_digest,
        permit.transport_authority_digest == receipt.transport_authority_digest,
        receipt.transport_contract_attested,
        canonical_digest(response, CanonicalizationPolicy()) == receipt.response_digest,
    )
    if not all(lineage):
        raise ValueError("egress receipt entity lineage diverged")


def _require_egress_execution_authority(
    permit: ProviderEgressPermit,
    receipt: ProviderEgressReceipt,
    binding: ReviewerBinding,
    assignment: ReviewerDispatchAssignment,
) -> None:
    identity_lineage = (
        permit.execution_identity == receipt.execution_identity,
        permit.execution_identity == binding.execution_identity,
        permit.execution_identity.identity_digest
        == assignment.provider_execution_identity_digest,
    )
    if not all(identity_lineage):
        raise ValueError("egress execution identity lineage diverged")
    trusted_transport_lineage = (
        permit.transport_contract_digest == binding.transport_contract_digest,
        receipt.transport_contract_digest == binding.transport_contract_digest,
        assignment.transport_contract_digest == binding.transport_contract_digest,
        permit.transport_authority_digest == binding.transport_authority_digest,
        receipt.transport_authority_digest == binding.transport_authority_digest,
        assignment.transport_authority_digest == binding.transport_authority_digest,
        assignment.transport_profile_digest == binding.transport_profile_digest,
    )
    if not all(trusted_transport_lineage):
        raise ValueError("egress trusted transport authority diverged")
    if not receipt.remote_provider_exercised:
        raise ValueError("remote provider execution was not exercised")


def _require_order(values: tuple[str, ...], kind: str) -> None:
    parsed = tuple(parse_utc(value) for value in values)
    if parsed != tuple(sorted(parsed)) or len(set(parsed)) != len(parsed):
        raise ValueError(f"{kind} receipt order is invalid")


def _require_turn_order(values: tuple[int, ...]) -> None:
    if values != tuple(sorted(values)) or len(set(values)) != len(values):
        raise ValueError("egress receipt order is invalid")


_validate_review_pass_receipts = validate_review_pass_receipts
