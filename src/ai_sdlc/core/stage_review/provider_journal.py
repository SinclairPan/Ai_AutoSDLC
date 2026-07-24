"""统一 Provider Invocation Journal 与五窗口安全恢复。"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from math import isfinite
from pathlib import Path
from typing import cast

from pydantic import ValidationError

from ai_sdlc.core.stage_review import provider_journal_builders as _builders
from ai_sdlc.core.stage_review import provider_journal_resource as _resource
from ai_sdlc.core.stage_review.artifacts import (
    ResourceLockUnavailableError,
    SharedStateIntegrityError,
    resolve_canonical_shared_state,
)
from ai_sdlc.core.stage_review.certificate_receipt_store import (
    FilesystemReviewReceiptArtifactStore,
)
from ai_sdlc.core.stage_review.provider_journal_driver import (
    ProviderDriverRefused,
    ProviderInvocationDriver,
    ProviderOutputValidator,
)
from ai_sdlc.core.stage_review.provider_journal_driver import (
    driver_matches as _driver_matches,
)
from ai_sdlc.core.stage_review.provider_journal_models import (
    ProviderInvocation,
    ProviderInvocationEvent,
    ProviderInvocationRequest,
    ProviderJournalResult,
    ProviderJournalResultCode,
    ProviderQueryResult,
    ProviderRecoveryCapabilities,
    ProviderSubmission,
)
from ai_sdlc.core.stage_review.provider_journal_recovery import (
    _ProviderResumeContext,
    resume_provider_invocation,
)
from ai_sdlc.core.stage_review.provider_journal_settlement import (
    settle_and_refuse,
)
from ai_sdlc.core.stage_review.provider_journal_store import ProviderJournalStore
from ai_sdlc.core.stage_review.resource_runtime import utc_now
from ai_sdlc.core.stage_review.resources import ResourceGovernor

_result = _builders.build_journal_result


class ProviderInvocationJournal:
    """外部调用不持锁；每次本地状态推进只使用短事务。"""

    def __init__(
        self,
        root: Path,
        *,
        project_id: str,
        resource_governor: ResourceGovernor,
        lock_timeout_seconds: float = 2,
    ) -> None:
        if not isfinite(lock_timeout_seconds) or lock_timeout_seconds <= 0:
            raise ValueError("provider journal lock timeout must be positive")
        shared_root = resolve_canonical_shared_state(root, project_id)
        self._store = ProviderJournalStore(
            shared_root,
            project_id=project_id,
            lock_timeout_seconds=lock_timeout_seconds,
        )
        self._project_id = project_id
        self._resources = resource_governor
        self._receipt_artifacts = FilesystemReviewReceiptArtifactStore(
            root,
            project_id=project_id,
        )
        self._reviewer_driver_preparer: (
            Callable[
                [ProviderInvocationRequest, ProviderInvocationDriver, datetime],
                tuple[
                    ProviderInvocationDriver | None, ProviderJournalResultCode | None
                ],
            ]
            | None
        ) = None

    def register_reviewer_driver_preparer(
        self,
        preparer: Callable[
            [ProviderInvocationRequest, ProviderInvocationDriver, datetime],
            tuple[ProviderInvocationDriver | None, ProviderJournalResultCode | None],
        ],
    ) -> None:
        if self._reviewer_driver_preparer is not None:
            raise ValueError("reviewer driver preparer is already registered")
        self._reviewer_driver_preparer = preparer

    def prepare(
        self,
        request: ProviderInvocationRequest,
        *,
        lease_owner: str,
        now: datetime | None = None,
    ) -> ProviderJournalResult:
        try:
            trusted = ProviderInvocationRequest.model_validate(
                request.model_dump(mode="json")
            )
            return self._prepare_validated(trusted, lease_owner=lease_owner, now=now)
        except ResourceLockUnavailableError:
            return _result("lock_unavailable")
        except SharedStateIntegrityError:
            return _result("state_corrupt")
        except KeyError:
            return _result("invalid_resource_binding")
        except (ValidationError, ValueError, AttributeError):
            return _result("invalid_request")

    def _prepare_validated(
        self,
        request: ProviderInvocationRequest,
        *,
        lease_owner: str,
        now: datetime | None,
    ) -> ProviderJournalResult:
        reservation = self._resources.get_reservation(request.reservation_id)
        ancestor = self._resources.get_reservation_ancestor(
            request.reservation_id,
            request.expected_reservation_digest,
        )
        if not _resource.resource_lineage_matches(
            request, reservation, ancestor, self._project_id
        ):
            return _result("invalid_resource_binding")
        existing = self._store.get(request.invocation_id)
        if existing is not None:
            if (
                existing.request.request_artifact_digest
                != request.request_artifact_digest
            ):
                return _result("state_corrupt", existing)
            return _result(_builders.existing_result_code(existing), existing)
        authorization = self._resources.provider_call_authorized(
            request.reservation_id,
            invocation_id=request.invocation_id,
            anticipated_usage=request.anticipated_usage,
            lease_owner=lease_owner,
            expected_fencing_token=request.expected_fencing_token,
            operation_id=_builders.authorization_operation_id(request),
            now=now,
        )
        target = authorization.operation_reservation
        if (
            authorization.result_code != "authorized"
            or target is None
            or not _resource.resource_identity_matches(request, target)
        ):
            return _result("invalid_resource_binding")
        invocation, _ = self._store.advance(
            request,
            "prepared",
            authorized_reservation_digest=target.reservation_digest,
        )
        return _result("prepared", invocation)

    def resume(
        self,
        invocation_id: str,
        *,
        driver: ProviderInvocationDriver,
        validator: ProviderOutputValidator,
        lease_owner: str,
        now: datetime | None = None,
    ) -> ProviderJournalResult:
        try:
            try:
                invocation = self._store.get(invocation_id)
            except ValueError:
                return _result("invalid_request")
            if invocation is None:
                return _result("invalid_request")
            if not _driver_matches(invocation.request, driver):
                return _result("invalid_request", invocation)
            preparer = self._reviewer_driver_preparer
            if preparer is not None:
                prepared_driver, refusal = preparer(
                    invocation.request,
                    driver,
                    now or utc_now(None),
                )
                if prepared_driver is None:
                    return _result(refusal or "needs_user", invocation)
                driver = prepared_driver
            elif invocation.request.authorization_scope == "reviewer_binding":
                return _result("needs_user", invocation)
            return self._resume_known(
                invocation,
                driver=driver,
                validator=validator,
                lease_owner=lease_owner,
                now=now,
            )
        except ResourceLockUnavailableError:
            return _result("lock_unavailable")
        except SharedStateIntegrityError:
            return _result("state_corrupt")
        except KeyError:
            return _result("invalid_resource_binding")
        except ProviderDriverRefused as exc:
            return self._refused_result(invocation_id, exc, lease_owner, now)
        except (ValidationError, ValueError, AttributeError):
            return _result("state_corrupt")

    def get(self, invocation_id: str) -> ProviderInvocation | None:
        return self._store.get(invocation_id)

    def _refused_result(
        self,
        invocation_id: str,
        refusal: ProviderDriverRefused,
        lease_owner: str,
        now: datetime | None,
    ) -> ProviderJournalResult:
        current = self._store.get(invocation_id)
        if current is None:
            return _result("needs_user")
        if current.state == "refused":
            return _result("needs_user", current)
        if current.state == "executed_invalid":
            return _result("provider_output_invalid", current)
        if refusal.accounted_usage is not None:
            return settle_and_refuse(
                self._store,
                self._resources,
                current,
                refusal.outcome,
                lease_owner=lease_owner,
                now=now,
            )
        return _result("needs_user", current)

    def events(self, invocation_id: str) -> tuple[ProviderInvocationEvent, ...]:
        return cast(
            tuple[ProviderInvocationEvent, ...], self._store.events(invocation_id)
        )

    def submission_path(self, invocation_id: str) -> Path:
        return cast(Path, self._store.submission_path(invocation_id))

    def get_submission(self, invocation_id: str) -> ProviderSubmission | None:
        invocation = self._store.get(invocation_id)
        if invocation is None or invocation.state not in {
            "submitted",
            "executed_invalid",
            "validated",
            "committed",
        }:
            return None
        return self._store.load_submission(invocation.request)

    def _resume_known(
        self,
        invocation: ProviderInvocation,
        *,
        driver: ProviderInvocationDriver,
        validator: ProviderOutputValidator,
        lease_owner: str,
        now: datetime | None,
    ) -> ProviderJournalResult:
        context = _ProviderResumeContext(
            store=self._store,
            resources=self._resources,
            receipt_artifacts=self._receipt_artifacts,
            validator=validator,
            lease_owner=lease_owner,
            now=now,
            resource_ready=self._resource_ready,
        )
        return resume_provider_invocation(invocation, driver, context)

    def _resource_ready(
        self,
        invocation: ProviderInvocation,
        now: datetime | None,
    ) -> bool:
        reservation = self._resources.get_reservation(invocation.request.reservation_id)
        return cast(
            bool, _resource.resource_ready(invocation.request, reservation, now)
        )


build_provider_invocation_request = _builders.build_provider_invocation_request
build_provider_submission = _builders.build_provider_submission


__all__ = [
    "ProviderInvocationDriver",
    "ProviderInvocationJournal",
    "ProviderInvocationRequest",
    "ProviderQueryResult",
    "ProviderRecoveryCapabilities",
    "ProviderSubmission",
    "build_provider_invocation_request",
    "build_provider_submission",
]
