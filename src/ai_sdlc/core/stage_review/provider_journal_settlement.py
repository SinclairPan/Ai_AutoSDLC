"""Resource settlement responsibility for Provider Invocation Journal."""

from __future__ import annotations

from datetime import datetime

from ai_sdlc.core.stage_review import provider_journal_builders as _builders
from ai_sdlc.core.stage_review import provider_journal_resource as _resource
from ai_sdlc.core.stage_review.certificate_receipt_store import (
    FilesystemReviewReceiptArtifactStore,
)
from ai_sdlc.core.stage_review.provider_execution_evidence import (
    ProviderExecutionOutcome,
)
from ai_sdlc.core.stage_review.provider_journal_models import (
    ProviderInvocation,
    ProviderInvocationState,
    ProviderJournalResult,
    ProviderJournalResultCode,
    ProviderSubmission,
)
from ai_sdlc.core.stage_review.provider_journal_store import ProviderJournalStore
from ai_sdlc.core.stage_review.provider_usage_models import AccountedProviderUsage
from ai_sdlc.core.stage_review.resource_builders import stable_id
from ai_sdlc.core.stage_review.resource_ledger_models import ResourceReservationResult
from ai_sdlc.core.stage_review.resources import ResourceGovernor


def settle_and_commit(
    store: ProviderJournalStore,
    resources: ResourceGovernor,
    receipt_artifacts: FilesystemReviewReceiptArtifactStore,
    invocation: ProviderInvocation,
    lease_owner: str,
    now: datetime | None,
) -> ProviderJournalResult:
    submission = store.load_submission(invocation.request)
    if (
        submission is None
        or submission.submission_digest != invocation.submission_digest
    ):
        return _builders.build_journal_result("state_corrupt", invocation)
    operation_id = _builders.settlement_operation_id(invocation.request)
    settlement = _provider_settlement(
        resources,
        invocation,
        submission.accounted_usage,
        lease_owner,
        operation_id,
        now,
    )
    target = settlement.operation_reservation
    event = resources.get_operation_event(operation_id)
    if (
        settlement.result_code != "settled"
        or target is None
        or event is None
        or not _resource.settlement_event_matches(invocation, submission, event, target)
    ):
        return _builders.build_journal_result(
            "invalid_resource_binding", invocation, submission
        )
    committed, _ = store.advance(
        invocation.request,
        "committed",
        resource_settlement_operation_id=operation_id,
        settlement_reservation_digest=target.reservation_digest,
        resource_settlement_event_digest=event.event_digest,
    )
    receipt_artifacts.persist_invocation(committed)
    return _builders.build_journal_result("committed", committed, submission)


def settle_and_refuse(
    store: ProviderJournalStore,
    resources: ResourceGovernor,
    invocation: ProviderInvocation,
    outcome: ProviderExecutionOutcome,
    *,
    lease_owner: str,
    now: datetime | None,
) -> ProviderJournalResult:
    usage = outcome.accounted_usage
    if usage is None:
        return _builders.build_journal_result("needs_user", invocation)
    return _settle_terminal(
        store,
        resources,
        invocation,
        usage,
        target_state="refused",
        result_code="needs_user",
        operation_kind="provider-refusal-settlement",
        lease_owner=lease_owner,
        now=now,
        execution_outcome=outcome,
    )


def settle_invalid_submission(
    store: ProviderJournalStore,
    resources: ResourceGovernor,
    invocation: ProviderInvocation,
    submission: ProviderSubmission,
    *,
    lease_owner: str,
    now: datetime | None,
) -> ProviderJournalResult:
    return _settle_terminal(
        store,
        resources,
        invocation,
        submission.accounted_usage,
        target_state="executed_invalid",
        result_code="provider_output_invalid",
        operation_kind="provider-invalid-settlement",
        lease_owner=lease_owner,
        now=now,
        submission=submission,
    )


def _settle_terminal(
    store: ProviderJournalStore,
    resources: ResourceGovernor,
    invocation: ProviderInvocation,
    usage: AccountedProviderUsage,
    target_state: ProviderInvocationState,
    result_code: ProviderJournalResultCode,
    operation_kind: str,
    lease_owner: str,
    now: datetime | None,
    submission: ProviderSubmission | None = None,
    execution_outcome: ProviderExecutionOutcome | None = None,
) -> ProviderJournalResult:
    operation_id = stable_id(
        operation_kind,
        invocation.invocation_id,
        invocation.request.request_artifact_digest,
    )
    result = _provider_settlement(
        resources, invocation, usage, lease_owner, operation_id, now
    )
    event = resources.get_operation_event(operation_id)
    target = result.operation_reservation
    if (
        result.result_code != "settled"
        or target is None
        or event is None
        or event.actual_usage != usage.amounts
        or event.provider_permit is None
        or event.provider_permit.invocation_id != invocation.invocation_id
    ):
        return _builders.build_journal_result(
            "invalid_resource_binding", invocation, submission
        )
    isolation, egress, root = _terminal_evidence(submission, execution_outcome)
    terminal, _ = store.advance(
        invocation.request,
        target_state,
        submission_digest="" if submission is None else submission.submission_digest,
        isolation_receipt_digests=isolation,
        egress_receipt_digests=egress,
        execution_evidence_root_digest=root,
        resource_settlement_operation_id=operation_id,
        settlement_reservation_digest=target.reservation_digest,
        resource_settlement_event_digest=event.event_digest,
    )
    return _builders.build_journal_result(result_code, terminal, submission)


def _terminal_evidence(
    submission: ProviderSubmission | None,
    outcome: ProviderExecutionOutcome | None,
) -> tuple[tuple[str, ...], tuple[str, ...], str]:
    source = submission or outcome
    if source is None:
        return (), (), ""
    return (
        source.isolation_receipt_digests,
        source.egress_receipt_digests,
        source.execution_evidence_root_digest,
    )


def _provider_settlement(
    resources: ResourceGovernor,
    invocation: ProviderInvocation,
    usage: AccountedProviderUsage,
    lease_owner: str,
    operation_id: str,
    now: datetime | None,
) -> ResourceReservationResult:
    result = resources.settle_provider_call(
        invocation.request.reservation_id,
        invocation_id=invocation.invocation_id,
        actual_usage=usage.amounts,
        lease_owner=lease_owner,
        expected_fencing_token=invocation.request.expected_fencing_token,
        operation_id=operation_id,
        now=now,
    )
    if result.result_code == "settled" and result.operation_reservation is not None:
        return result
    return resources.reconcile_expired_provider_call(
        invocation.request.reservation_id,
        invocation_id=invocation.invocation_id,
        actual_usage=usage.amounts,
        lease_owner=lease_owner,
        expected_fencing_token=invocation.request.expected_fencing_token,
        operation_id=operation_id,
        now=now,
    )


__all__: list[str] = []
