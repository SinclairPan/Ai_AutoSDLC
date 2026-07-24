"""Provider 输出恢复与 lineage 校验。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from pydantic import ValidationError

from ai_sdlc.core.stage_review.artifacts import SharedStateIntegrityError
from ai_sdlc.core.stage_review.certificate_receipt_store import (
    FilesystemReviewReceiptArtifactStore,
)
from ai_sdlc.core.stage_review.provider_journal_builders import (
    build_journal_result,
    verify_submission_lineage,
)
from ai_sdlc.core.stage_review.provider_journal_driver import (
    ProviderInvocationDriver,
    ProviderOutputValidator,
    recover_provider_submission,
)
from ai_sdlc.core.stage_review.provider_journal_models import (
    ProviderInvocation,
    ProviderJournalResult,
    ProviderSubmission,
)
from ai_sdlc.core.stage_review.provider_journal_settlement import (
    settle_and_commit,
    settle_invalid_submission,
)
from ai_sdlc.core.stage_review.provider_journal_store import ProviderJournalStore
from ai_sdlc.core.stage_review.resources import ResourceGovernor


@dataclass(frozen=True, slots=True)
class _ProviderResumeContext:
    store: ProviderJournalStore
    resources: ResourceGovernor
    receipt_artifacts: FilesystemReviewReceiptArtifactStore
    validator: ProviderOutputValidator
    lease_owner: str
    now: datetime | None
    resource_ready: Callable[[ProviderInvocation, datetime | None], bool]


def resume_provider_invocation(
    invocation: ProviderInvocation,
    driver: ProviderInvocationDriver,
    context: _ProviderResumeContext,
) -> ProviderJournalResult:
    fresh_dispatch = False
    for _ in range(5):
        terminal = _terminal_result(invocation, context)
        if terminal is not None:
            return terminal
        if invocation.state == "prepared":
            if not context.resource_ready(invocation, context.now):
                return build_journal_result("invalid_resource_binding", invocation)
            invocation, fresh_dispatch = context.store.advance(
                invocation.request, "dispatched"
            )
            continue
        if invocation.state == "dispatched":
            recovered = _submit_or_recover(
                context.store,
                invocation,
                driver,
                fresh_dispatch=fresh_dispatch,
            )
            if isinstance(recovered, ProviderJournalResult):
                return recovered
            invocation = recovered
            fresh_dispatch = False
            continue
        if invocation.state == "submitted":
            validated = _validate_submission(invocation, context)
            if isinstance(validated, ProviderJournalResult):
                return validated
            invocation = validated
            continue
        if invocation.state == "validated":
            return _settle(invocation, context)
    raise SharedStateIntegrityError("provider journal recovery did not converge")


def _terminal_result(
    invocation: ProviderInvocation,
    context: _ProviderResumeContext,
) -> ProviderJournalResult | None:
    if invocation.state == "refused":
        return build_journal_result("needs_user", invocation)
    if invocation.state == "executed_invalid":
        return build_journal_result(
            "provider_output_invalid",
            invocation,
            _require_submission(context.store, invocation),
        )
    if invocation.state == "committed":
        return _settle(invocation, context)
    return None


def _validate_submission(
    invocation: ProviderInvocation,
    context: _ProviderResumeContext,
) -> ProviderInvocation | ProviderJournalResult:
    submission = _require_submission(context.store, invocation)
    try:
        validation_digest = context.validator(submission).strip()
    except (ValidationError, ValueError):
        validation_digest = ""
    if not validation_digest:
        return settle_invalid_submission(
            context.store,
            context.resources,
            invocation,
            submission,
            lease_owner=context.lease_owner,
            now=context.now,
        )
    validated, _ = context.store.advance(
        invocation.request,
        "validated",
        validation_digest=validation_digest,
    )
    return validated


def _submit_or_recover(
    store: ProviderJournalStore,
    invocation: ProviderInvocation,
    driver: ProviderInvocationDriver,
    *,
    fresh_dispatch: bool,
) -> ProviderInvocation | ProviderJournalResult:
    with store.provider_call_claim(invocation.invocation_id) as owns_call:
        if not owns_call:
            return build_journal_result("retry_wait", invocation)
        submission = store.load_submission(invocation.request)
        if submission is None:
            recovered = _recover_provider_output(
                invocation,
                driver,
                fresh_dispatch=fresh_dispatch,
            )
            if isinstance(recovered, ProviderJournalResult):
                return recovered
            submission = recovered
            store.persist_submission(submission)
    advanced, _ = store.advance(
        invocation.request,
        "submitted",
        submission_digest=submission.submission_digest,
        isolation_receipt_digests=submission.isolation_receipt_digests,
        egress_receipt_digests=submission.egress_receipt_digests,
        execution_evidence_root_digest=submission.execution_evidence_root_digest,
    )
    return advanced


def _require_submission(
    store: ProviderJournalStore,
    invocation: ProviderInvocation,
) -> ProviderSubmission:
    submission = store.load_submission(invocation.request)
    if submission is None or submission.submission_digest != invocation.submission_digest:
        raise SharedStateIntegrityError("submitted provider output is missing")
    return submission


def _settle(
    invocation: ProviderInvocation,
    context: _ProviderResumeContext,
) -> ProviderJournalResult:
    return settle_and_commit(
        context.store,
        context.resources,
        context.receipt_artifacts,
        invocation,
        context.lease_owner,
        context.now,
    )


def _recover_provider_output(
    invocation: ProviderInvocation,
    driver: ProviderInvocationDriver,
    *,
    fresh_dispatch: bool,
) -> ProviderSubmission | ProviderJournalResult:
    try:
        submission, decision = recover_provider_submission(
            invocation.request,
            driver,
            fresh_dispatch=fresh_dispatch,
        )
    except (ValidationError, AttributeError):
        return build_journal_result("provider_output_invalid", invocation)
    if decision is not None:
        return build_journal_result(decision, invocation)
    assert submission is not None
    try:
        verify_submission_lineage(invocation.request, submission)
    except (SharedStateIntegrityError, ValueError):
        return build_journal_result("provider_output_invalid", invocation)
    return submission
