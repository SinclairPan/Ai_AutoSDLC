"""首次 Review Seal 到唯一 FindingLedger 的提交适配。"""

from __future__ import annotations

from ai_sdlc.core.stage_review.finding_command_models import (
    FindingInitialBatchCommand,
    FindingLineageAdvanceCommand,
)
from ai_sdlc.core.stage_review.finding_models import FindingLedger
from ai_sdlc.core.stage_review.finding_trust_models import InitialReviewSeal
from ai_sdlc.core.stage_review.resource_builders import stable_id
from ai_sdlc.core.stage_review.session_artifact_models import (
    ArtifactRef,
    ReviewCohort,
    ReviewPass,
)
from ai_sdlc.core.stage_review.session_builders import build_initial_review_seal
from ai_sdlc.core.stage_review.session_contracts import SessionIntegrityError
from ai_sdlc.core.stage_review.session_models import (
    SessionOperation,
    SessionProjectionData,
    StageReviewSession,
    replace_projection,
)
from ai_sdlc.core.stage_review.session_runtime import SessionRuntime


def initialize_finding_ledger(
    runtime: SessionRuntime,
    session: StageReviewSession,
    seal: InitialReviewSeal,
    passes: tuple[ReviewPass, ...],
) -> FindingLedger:
    command_id = stable_id(
        "finding-initial-batch",
        session.scope.session_id,
        seal.initial_cohort_id,
    )
    command = FindingInitialBatchCommand(
        scope=session.scope,
        command_id=command_id,
        idempotency_key=stable_id("finding-initial-key", command_id),
        expected_revision=0,
        session_fencing_epoch=session.resource_fencing_epoch,
        candidate_digest=seal.initial_candidate_digest,
        policy_digest=seal.policy_digest,
        plan_digest=seal.plan_digest,
        binding_set_digest=seal.binding_set_digest,
        initial_review_seal_digest=seal.seal_digest,
        findings=tuple(finding for item in passes for finding in item.findings),
    )
    ledger = runtime.finding_ledger_writer.append(command).ledger
    lineage = (
        ledger.initialized,
        ledger.scope == session.scope,
        ledger.initial_review_seal_digest == seal.seal_digest,
        ledger.candidate_digest == seal.initial_candidate_digest,
        ledger.policy_digest == seal.policy_digest,
        ledger.plan_digest == seal.plan_digest,
        ledger.binding_set_digest == seal.binding_set_digest,
    )
    if not all(lineage):
        raise SessionIntegrityError("finding ledger initialization lineage is invalid")
    return ledger


def build_seal_projection(
    runtime: SessionRuntime,
    session: StageReviewSession,
    cohort: ReviewCohort,
    review_pass: ReviewPass,
    projection: SessionProjectionData,
    operation: SessionOperation,
    *,
    initial: bool,
) -> tuple[SessionProjectionData, tuple[ArtifactRef, ...]]:
    passes = _cohort_passes(runtime, session, cohort, review_pass)
    sealed = tuple(sorted({*projection.sealed_cohort_ids, cohort.cohort_id}))
    if not initial:
        ledger = _validated_rereview_ledger(runtime, session, cohort, passes)
        state, resume = _sealed_state(projection, ledger)
        return (
            replace_projection(
                projection,
                state=state,
                budget_resume_state=resume,
                sealed_cohort_ids=sealed,
                finding_ledger_digest=ledger.ledger_digest,
            ),
            (),
        )
    seal = build_initial_review_seal(
        session,
        cohort,
        passes,
        sealed_at=operation.prepared_at,
    )
    artifact_id = runtime.store.persist_initial_seal(seal)
    ledger = initialize_finding_ledger(runtime, session, seal, passes)
    ref = ArtifactRef(artifact_id=artifact_id, artifact_digest=seal.seal_digest)
    state, resume = _sealed_state(projection, ledger)
    updated = replace_projection(
        projection,
        state=state,
        budget_resume_state=resume,
        sealed_cohort_ids=sealed,
        initial_seal_refs=(*projection.initial_seal_refs, ref),
        finding_ledger_digest=ledger.ledger_digest,
    )
    return updated, (ref,)


def _validated_rereview_ledger(
    runtime: SessionRuntime,
    session: StageReviewSession,
    cohort: ReviewCohort,
    passes: tuple[ReviewPass, ...],
) -> FindingLedger:
    ledger = runtime.finding_ledger_writer.read(session.scope)
    expected = (
        cohort.candidate_digest,
        cohort.policy_digest,
        cohort.plan_digest,
        cohort.binding_set_digest,
    )
    actual = (
        ledger.candidate_digest,
        ledger.policy_digest,
        ledger.plan_digest,
        ledger.binding_set_digest,
    )
    if (
        actual != expected
        or ledger.cohort_id != cohort.cohort_id
        or ledger.lineage_contract_version != "explicit-v2"
    ):
        ledger = _advance_ledger_lineage(runtime, session, cohort, ledger)
    lineage = (
        ledger.initialized,
        ledger.integrity_ok,
        ledger.scope == session.scope,
        ledger.candidate_digest == cohort.candidate_digest,
        ledger.policy_digest == cohort.policy_digest,
        ledger.plan_digest == cohort.plan_digest,
        ledger.binding_set_digest == cohort.binding_set_digest,
        ledger.cohort_id == cohort.cohort_id,
        ledger.lineage_contract_version == "explicit-v2",
        not ledger.pending_handoff_ids,
        not ledger.pending_identity_target_keys,
    )
    if not all(lineage):
        raise SessionIntegrityError("rereview finding ledger lineage is invalid")
    _validate_submitted_findings(ledger, passes)
    return ledger


def _validate_submitted_findings(
    ledger: FindingLedger,
    passes: tuple[ReviewPass, ...],
) -> None:
    submitted = {
        finding.identity.identity_digest
        for review_pass in passes
        for finding in review_pass.findings
    }
    recorded = {item.identity_digest for item in ledger.records}
    if not submitted <= recorded:
        raise SessionIntegrityError("rereview finding is missing from ledger")
    resolved = {
        item.identity_digest
        for item in ledger.records
        if item.state in {"verified", "waived", "superseded"}
    }
    if submitted & resolved:
        raise SessionIntegrityError(
            "rereview finding refers to a resolved ledger record"
        )


def _advance_ledger_lineage(
    runtime: SessionRuntime,
    session: StageReviewSession,
    cohort: ReviewCohort,
    ledger: FindingLedger,
) -> FindingLedger:
    activation = next(
        (
            event
            for event in reversed(runtime.store.load_events(session.scope))
            if event.event_kind == "new_cohort_activated"
            and event.projection_after.active_cohort_id == cohort.cohort_id
        ),
        None,
    )
    if activation is None:
        raise SessionIntegrityError("rereview cohort activation proof is missing")
    command_id = stable_id(
        "finding-lineage-advance",
        session.scope.session_id,
        cohort.cohort_id,
    )
    command = FindingLineageAdvanceCommand(
        scope=session.scope,
        command_id=command_id,
        idempotency_key=stable_id("finding-lineage-key", command_id),
        expected_revision=ledger.revision,
        session_fencing_epoch=session.resource_fencing_epoch,
        candidate_digest=cohort.candidate_digest,
        policy_digest=cohort.policy_digest,
        plan_digest=cohort.plan_digest,
        binding_set_digest=cohort.binding_set_digest,
        cohort_id=cohort.cohort_id,
        previous_ledger_digest=ledger.ledger_digest,
        session_event_digest=activation.event_digest,
        advanced_at=activation.occurred_at,
    )
    return runtime.finding_ledger_writer.advance_lineage(command).ledger


def _sealed_state(
    projection: SessionProjectionData,
    ledger: FindingLedger,
) -> tuple[str, str | None]:
    normal = (
        "remediation_required"
        if any(item.blocking for item in ledger.records)
        else "authorized"
    )
    if projection.budget_resume_state is not None:
        return "needs_user", normal
    if projection.state == "needs_user":
        return "needs_user", None
    return normal, None


def _cohort_passes(
    runtime: SessionRuntime,
    session: StageReviewSession,
    cohort: ReviewCohort,
    current: ReviewPass,
) -> tuple[ReviewPass, ...]:
    refs = tuple(
        item for item in session.pass_refs if item.cohort_id == cohort.cohort_id
    )
    previous = tuple(
        runtime.store.get_pass(session.scope, item.pass_id) for item in refs
    )
    return *previous, current
