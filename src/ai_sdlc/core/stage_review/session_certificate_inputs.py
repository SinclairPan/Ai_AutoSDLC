"""在 Session 短锁内读取关闭证书所需的同一事实快照。"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Protocol, cast

from ai_sdlc.core.stage_review.artifacts import ShortFileLock
from ai_sdlc.core.stage_review.binding_models import BindingAuthoritySnapshot
from ai_sdlc.core.stage_review.binding_result_models import (
    ReviewerBindingSet,
    ReviewerDispatchAssignment,
)
from ai_sdlc.core.stage_review.finding_models import FindingLedger, FindingScope
from ai_sdlc.core.stage_review.panel_plan_models import ReviewerPanelPlan
from ai_sdlc.core.stage_review.session_artifact_models import ReviewCohort, ReviewPass
from ai_sdlc.core.stage_review.session_contracts import SessionIntegrityError
from ai_sdlc.core.stage_review.session_models import (
    SessionOperation,
    StageReviewSession,
)
from ai_sdlc.core.stage_review.session_runtime import SessionRuntime


@dataclass(frozen=True, slots=True)
class SessionCertificateInputs:
    session: StageReviewSession
    plan: ReviewerPanelPlan
    authority_snapshot: BindingAuthoritySnapshot
    binding_set: ReviewerBindingSet
    cohort: ReviewCohort
    passes: tuple[ReviewPass, ...]
    assignments: tuple[ReviewerDispatchAssignment, ...]
    ledger: FindingLedger


class _CertificateSessionStore(Protocol):
    def _lock(self, scope: FindingScope) -> ShortFileLock: ...

    def pending_operation(self, scope: FindingScope) -> SessionOperation | None: ...

    def rebuild(self, scope: FindingScope) -> StageReviewSession | None: ...

    def get_cohort(self, scope: FindingScope, cohort_id: str) -> ReviewCohort: ...

    def get_pass(self, scope: FindingScope, pass_id: str) -> ReviewPass: ...


class _SessionCertificateInputsMixin:
    @contextmanager
    def hold_certificate_inputs(
        self,
        scope: FindingScope,
    ) -> Iterator[SessionCertificateInputs]:
        store = cast(_CertificateSessionStore, self.__dict__["_store"])
        runtime = cast(SessionRuntime, self.__dict__["_runtime"])
        with store._lock(scope):
            yield _load_inputs(store, runtime, scope)

    def certificate_inputs_for_operation(
        self,
        session: StageReviewSession,
        command_id: str,
    ) -> SessionCertificateInputs:
        store = cast(_CertificateSessionStore, self.__dict__["_store"])
        runtime = cast(SessionRuntime, self.__dict__["_runtime"])
        pending = store.pending_operation(session.scope)
        current = store.rebuild(session.scope)
        if (
            pending is None
            or pending.command_id != command_id
            or current != session
        ):
            raise SessionIntegrityError(
                "certificate validation is outside the claimed session operation"
            )
        return _load_session_inputs(store, runtime, session)


def _load_inputs(
    store: _CertificateSessionStore,
    runtime: SessionRuntime,
    scope: FindingScope,
) -> SessionCertificateInputs:
    if store.pending_operation(scope) is not None:
        raise SessionIntegrityError(
            "pending session operation prevents certificate issue"
        )
    session = store.rebuild(scope)
    if session is None:
        raise KeyError(scope.session_id)
    return _load_session_inputs(store, runtime, session)


def _load_session_inputs(
    store: _CertificateSessionStore,
    runtime: SessionRuntime,
    session: StageReviewSession,
) -> SessionCertificateInputs:
    scope = session.scope
    plan = runtime.resolver.resolve_plan(session.active_plan_digest)
    binding = runtime.resolver.resolve_binding_set(session.active_binding_set_digest)
    authority = (
        runtime.resolver.resolve_binding_authority(binding.authority_snapshot_digest)
        if binding is not None
        else None
    )
    if plan is None or binding is None or authority is None:
        raise SessionIntegrityError("certificate authority input is unavailable")
    trusted_plan = ReviewerPanelPlan.model_validate(plan.model_dump(mode="json"))
    trusted_authority = BindingAuthoritySnapshot.model_validate(
        authority.model_dump(mode="json")
    )
    trusted_binding = ReviewerBindingSet.model_validate(binding.model_dump(mode="json"))
    cohort = store.get_cohort(scope, session.active_cohort_id)
    active_refs = tuple(
        item
        for item in session.cohort_refs
        if item.artifact_id == session.active_cohort_id
    )
    if (
        len(active_refs) != 1
        or active_refs[0].artifact_digest != cohort.cohort_digest
        or cohort.scope != session.scope
    ):
        raise SessionIntegrityError("active cohort reference is inconsistent")
    passes = _active_passes(store, scope, session)
    return SessionCertificateInputs(
        session=session,
        plan=trusted_plan,
        authority_snapshot=trusted_authority,
        binding_set=trusted_binding,
        cohort=cohort,
        passes=passes,
        assignments=_active_assignments(runtime, passes),
        ledger=runtime.finding_ledger_writer.read(scope),
    )


def _active_passes(
    store: _CertificateSessionStore,
    scope: FindingScope,
    session: StageReviewSession,
) -> tuple[ReviewPass, ...]:
    refs = tuple(
        item for item in session.pass_refs if item.cohort_id == session.active_cohort_id
    )
    loaded = tuple((item, store.get_pass(scope, item.pass_id)) for item in refs)
    if any(
        (
            review_pass.pass_id,
            review_pass.pass_digest,
            review_pass.cohort_id,
            review_pass.slot_id,
        )
        != (ref.pass_id, ref.pass_digest, ref.cohort_id, ref.slot_id)
        for ref, review_pass in loaded
    ):
        raise SessionIntegrityError("review pass reference is inconsistent")
    return tuple(
        sorted(
            (review_pass for _, review_pass in loaded),
            key=lambda item: item.slot_id,
        )
    )


def _active_assignments(
    runtime: SessionRuntime,
    passes: tuple[ReviewPass, ...],
) -> tuple[ReviewerDispatchAssignment, ...]:
    assignments: list[ReviewerDispatchAssignment] = []
    for review_pass in passes:
        assignment = runtime.resolver.resolve_assignment(
            review_pass.assignment_digest
        )
        if assignment is None:
            raise SessionIntegrityError("certificate assignment input is unavailable")
        try:
            trusted = ReviewerDispatchAssignment.model_validate(
                assignment.model_dump(mode="json")
            )
        except (AttributeError, ValueError) as exc:
            raise SessionIntegrityError(
                "certificate assignment input is invalid"
            ) from exc
        assignments.append(trusted)
    return tuple(sorted(assignments, key=lambda item: item.slot_id))
