"""Provider 输出恢复与 lineage 校验。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import TypeVar, cast

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

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class _ProviderResumeContext:
    store: ProviderJournalStore
    resources: ResourceGovernor
    receipt_artifacts: FilesystemReviewReceiptArtifactStore
    validator: ProviderOutputValidator
    lease_owner: str
    now: datetime | None
    resource_ready: Callable[[ProviderInvocation, datetime | None], bool]
    authorize_dispatch: Callable[[], None] | None


def resume_provider_invocation(
    invocation: ProviderInvocation,
    driver: ProviderInvocationDriver,
    context: _ProviderResumeContext,
) -> ProviderJournalResult:
    for _ in range(5):
        terminal = _terminal_result(invocation, context)
        if terminal is not None:
            return terminal
        if invocation.state == "prepared":
            if not context.resource_ready(invocation, context.now):
                return build_journal_result("invalid_resource_binding", invocation)
            dispatched = _dispatch_or_recover(
                invocation,
                driver,
                context,
                fresh_dispatch=True,
            )
            if isinstance(dispatched, ProviderJournalResult):
                return dispatched
            invocation = dispatched
            continue
        if invocation.state == "dispatched":
            recovered = _dispatch_or_recover(
                invocation,
                driver,
                context,
                fresh_dispatch=False,
            )
            if isinstance(recovered, ProviderJournalResult):
                return recovered
            invocation = recovered
            continue
        if invocation.state == "submitted":
            unauthorized = _authorize_effect(invocation, context.authorize_dispatch)
            if unauthorized is not None:
                return unauthorized
            validated = _validate_submission(invocation, context)
            if isinstance(validated, ProviderJournalResult):
                return validated
            invocation = validated
            continue
        if invocation.state == "validated":
            current = invocation
            return _commit_authorized(
                current,
                context.authorize_dispatch,
                lambda target=current: _settle(target, context),
            )
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
        return _commit_authorized(
            invocation,
            context.authorize_dispatch,
            lambda: _settle(invocation, context),
        )
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
        return _commit_authorized(
            invocation,
            context.authorize_dispatch,
            lambda: settle_invalid_submission(
                context.store,
                context.resources,
                invocation,
                submission,
                lease_owner=context.lease_owner,
                now=context.now,
            ),
        )
    return _commit_authorized(
        invocation,
        context.authorize_dispatch,
        lambda: context.store.advance(
            invocation.request,
            "validated",
            validation_digest=validation_digest,
        )[0],
    )


def _dispatch_or_recover(
    invocation: ProviderInvocation,
    driver: ProviderInvocationDriver,
    context: _ProviderResumeContext,
    *,
    fresh_dispatch: bool,
) -> ProviderInvocation | ProviderJournalResult:
    store = context.store
    with store.provider_call_claim(invocation.invocation_id) as owns_call:
        if not owns_call:
            return build_journal_result("retry_wait", invocation)
        current = store.get(invocation.invocation_id)
        if current is None:
            return build_journal_result("state_corrupt", invocation)
        if fresh_dispatch:
            if current.state != "prepared":
                return current
            unauthorized = _authorize_effect(current, context.authorize_dispatch)
            if unauthorized is not None:
                return unauthorized
            advanced = _commit_authorized(
                current,
                context.authorize_dispatch,
                lambda target=current: store.advance(
                    target.request,
                    "dispatched",
                )[0],
            )
            if isinstance(advanced, ProviderJournalResult):
                return advanced
            current = advanced
        elif current.state != "dispatched":
            return current
        else:
            unauthorized = _authorize_effect(current, context.authorize_dispatch)
            if unauthorized is not None:
                return unauthorized
        invocation = current
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
        unauthorized = _authorize_effect(invocation, context.authorize_dispatch)
        if unauthorized is not None:
            return unauthorized
    return _commit_authorized(
        invocation,
        context.authorize_dispatch,
        lambda: store.advance(
            invocation.request,
            "submitted",
            submission_digest=submission.submission_digest,
            isolation_receipt_digests=submission.isolation_receipt_digests,
            egress_receipt_digests=submission.egress_receipt_digests,
            execution_evidence_root_digest=(
                submission.execution_evidence_root_digest
            ),
        )[0],
    )


def _authorize_effect(
    invocation: ProviderInvocation,
    authorize_dispatch: Callable[[], None] | None,
) -> ProviderJournalResult | None:
    requires_authorization = (
        invocation.request.authorization_scope == "optimization_shadow"
    )
    if requires_authorization and not callable(
        getattr(authorize_dispatch, "commit", None)
    ):
        return build_journal_result("dispatch_unauthorized", invocation)
    if authorize_dispatch is None:
        return (
            build_journal_result("dispatch_unauthorized", invocation)
            if requires_authorization
            else None
        )
    try:
        authorize_dispatch()
    except Exception:
        return build_journal_result("dispatch_unauthorized", invocation)
    return None


def _commit_authorized(
    invocation: ProviderInvocation,
    authorize_dispatch: Callable[[], None] | None,
    operation: Callable[[], T],
) -> T | ProviderJournalResult:
    if invocation.request.authorization_scope != "optimization_shadow":
        return operation()
    commit = getattr(authorize_dispatch, "commit", None)
    if not callable(commit):
        return build_journal_result("dispatch_unauthorized", invocation)
    try:
        return cast(T, commit(operation))
    except Exception:
        return build_journal_result("dispatch_unauthorized", invocation)


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
