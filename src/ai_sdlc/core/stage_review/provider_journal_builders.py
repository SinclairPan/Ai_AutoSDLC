"""Provider Journal 的内容寻址 Request、Submission 与资源操作身份。"""

from __future__ import annotations

from typing import Any

from ai_sdlc.core.stage_review.artifacts import SharedStateIntegrityError
from ai_sdlc.core.stage_review.canonical import (
    CanonicalizationPolicy,
    canonical_digest,
)
from ai_sdlc.core.stage_review.provider_execution_evidence import (
    provider_execution_evidence_root_digest,
)
from ai_sdlc.core.stage_review.provider_journal_models import (
    ProviderInvocation,
    ProviderInvocationRequest,
    ProviderJournalResult,
    ProviderJournalResultCode,
    ProviderRecoveryCapabilities,
    ProviderSubmission,
    request_artifact_digest,
    submission_digest,
)
from ai_sdlc.core.stage_review.provider_usage_models import AccountedProviderUsage
from ai_sdlc.core.stage_review.resource_builders import stable_id
from ai_sdlc.core.stage_review.resource_models import ResourceAmounts


def build_provider_invocation_request(
    *,
    project_id: str,
    work_item_id: str,
    stage_review_session_id: str,
    owner_scope_id: str,
    candidate_digest: str,
    assignment_digest: str,
    epoch_id: str,
    provider_id: str,
    request_digest: str,
    reservation_id: str,
    expected_reservation_digest: str,
    expected_fencing_token: int,
    anticipated_usage: ResourceAmounts,
    capabilities: ProviderRecoveryCapabilities,
    command_id: str,
    idempotency_key: str,
    authorization_scope: str = "generic",
) -> ProviderInvocationRequest:
    values: dict[str, Any] = {
        "project_id": project_id,
        "work_item_id": work_item_id,
        "stage_review_session_id": stage_review_session_id,
        "owner_scope_id": owner_scope_id,
        "candidate_digest": candidate_digest,
        "assignment_digest": assignment_digest,
        "authorization_scope": authorization_scope,
        "epoch_id": epoch_id,
        "provider_id": provider_id,
        "request_digest": request_digest,
        "reservation_id": reservation_id,
        "expected_reservation_digest": expected_reservation_digest,
        "expected_fencing_token": expected_fencing_token,
        "anticipated_usage": ResourceAmounts.model_validate(anticipated_usage),
        "capabilities": ProviderRecoveryCapabilities.model_validate(capabilities),
        "command_id": command_id,
        "idempotency_key": idempotency_key,
    }
    values["invocation_id"] = _invocation_id(
        project_id, stage_review_session_id, provider_id, idempotency_key
    )
    draft = ProviderInvocationRequest.model_construct(
        request_artifact_digest="",
        **values,
    )
    values["request_artifact_digest"] = request_artifact_digest(draft)
    return ProviderInvocationRequest.model_validate(values)


def build_provider_submission(
    request: ProviderInvocationRequest,
    *,
    provider_call_id: str,
    output_payload: dict[str, object],
    accounted_usage: AccountedProviderUsage,
    isolation_receipt_digests: tuple[str, ...] = (),
    egress_receipt_digests: tuple[str, ...] = (),
) -> ProviderSubmission:
    draft = ProviderSubmission.model_construct(
        invocation_id=request.invocation_id,
        idempotency_key=request.idempotency_key,
        request_artifact_digest=request.request_artifact_digest,
        provider_id=request.provider_id,
        provider_call_id=provider_call_id,
        output_payload=output_payload,
        output_digest=canonical_digest(output_payload, CanonicalizationPolicy()),
        accounted_usage=accounted_usage,
        submission_digest="",
        isolation_receipt_digests=isolation_receipt_digests,
        egress_receipt_digests=egress_receipt_digests,
        execution_evidence_root_digest=provider_execution_evidence_root_digest(
            isolation_receipt_digests,
            egress_receipt_digests,
        ),
    )
    payload = draft.model_dump(mode="json", warnings=False)
    payload["accounted_usage"] = accounted_usage
    payload["submission_digest"] = submission_digest(draft)
    return ProviderSubmission.model_validate(payload)


def _bind_submission_isolation_receipt(
    submission: ProviderSubmission,
    receipt_digest_value: str,
) -> ProviderSubmission:
    values = submission.model_dump(mode="json", exclude={"submission_digest"})
    values["accounted_usage"] = submission.accounted_usage
    values["egress_receipt_digests"] = submission.egress_receipt_digests
    values["isolation_receipt_digests"] = (
        *submission.isolation_receipt_digests,
        receipt_digest_value,
    )
    values["execution_evidence_root_digest"] = provider_execution_evidence_root_digest(
        tuple(values["isolation_receipt_digests"]),
        submission.egress_receipt_digests,
    )
    draft = ProviderSubmission.model_construct(**values, submission_digest="")
    values["submission_digest"] = submission_digest(draft)
    return ProviderSubmission.model_validate(values)


def _bind_submission_egress_receipt(
    submission: ProviderSubmission,
    receipt_digest_value: str,
) -> ProviderSubmission:
    values = submission.model_dump(mode="json", exclude={"submission_digest"})
    values["accounted_usage"] = submission.accounted_usage
    values["isolation_receipt_digests"] = submission.isolation_receipt_digests
    values["egress_receipt_digests"] = (
        *submission.egress_receipt_digests,
        receipt_digest_value,
    )
    values["execution_evidence_root_digest"] = provider_execution_evidence_root_digest(
        submission.isolation_receipt_digests,
        tuple(values["egress_receipt_digests"]),
    )
    draft = ProviderSubmission.model_construct(**values, submission_digest="")
    values["submission_digest"] = submission_digest(draft)
    return ProviderSubmission.model_validate(values)


def verify_submission_lineage(
    request: ProviderInvocationRequest,
    submission: ProviderSubmission,
) -> None:
    expected = (
        submission.invocation_id == request.invocation_id,
        submission.idempotency_key == request.idempotency_key,
        submission.request_artifact_digest == request.request_artifact_digest,
        submission.provider_id == request.provider_id,
    )
    if not all(expected):
        raise SharedStateIntegrityError("provider submission lineage diverged")


def authorization_operation_id(request: ProviderInvocationRequest) -> str:
    return stable_id("provider-journal-authorize", request.invocation_id)


def settlement_operation_id(request: ProviderInvocationRequest) -> str:
    return stable_id("provider-journal-settle", request.invocation_id)


def permit_id(request: ProviderInvocationRequest) -> str:
    return stable_id("provider-permit", request.reservation_id, request.invocation_id)


def existing_result_code(
    invocation: ProviderInvocation,
) -> ProviderJournalResultCode:
    if invocation.state == "committed":
        return "committed"
    if invocation.state == "executed_invalid":
        return "provider_output_invalid"
    if invocation.state == "refused":
        return "needs_user"
    return "prepared"


def build_journal_result(
    code: ProviderJournalResultCode,
    invocation: ProviderInvocation | None = None,
    submission: ProviderSubmission | None = None,
) -> ProviderJournalResult:
    return ProviderJournalResult(
        result_code=code,
        invocation=invocation,
        submission=submission,
    )


def _invocation_id(
    project_id: str,
    session_id: str,
    provider_id: str,
    idempotency_key: str,
) -> str:
    return stable_id(
        "provider-invocation",
        project_id,
        session_id,
        provider_id,
        idempotency_key,
    )
