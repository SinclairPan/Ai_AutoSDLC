"""Binding attempt 唯一槽、不可变工件与跨 Worktree 共享存储。"""

from __future__ import annotations

import re
from pathlib import Path

from ai_sdlc.core.stage_review.artifacts import (
    SharedStateIntegrityError,
    ShortFileLock,
    bind_repository_project,
    create_json_exclusive,
    resolve_canonical_shared_state,
)
from ai_sdlc.core.stage_review.binding_artifact_io import (
    persist_bundle,
    persist_model,
    read_bundle,
    read_model,
)
from ai_sdlc.core.stage_review.binding_availability_models import (
    ProviderAvailabilityAttestation,
)
from ai_sdlc.core.stage_review.binding_dispatch_store import (
    BindingDispatchStoreMixin,
)
from ai_sdlc.core.stage_review.binding_models import (
    BindingAttemptOperation,
    BindingAuthoritySnapshot,
    HostCapabilitySnapshot,
    IsolationExecutionEvidence,
    ReviewerRuntimeAllocation,
)
from ai_sdlc.core.stage_review.binding_result_models import (
    ReviewerBindingResult,
    ReviewerBindingSet,
)
from ai_sdlc.core.stage_review.resource_builders import stable_id

_OPERATION_ID = re.compile(r"^binding-attempt\.[0-9a-f]{24}$")
_BINDING_SET_ID = re.compile(r"^reviewer-binding-set\.[0-9a-f]{24}$")
_AUTHORITY_ID = re.compile(r"^binding-authority\.[0-9a-f]{24}$")
_HOST_ID = re.compile(r"^host-capability\.[0-9a-f]{24}$")
_AVAILABILITY_ID = re.compile(r"^provider-availability\.[0-9a-f]{24}$")


class BindingArtifactStore(BindingDispatchStoreMixin):
    def __init__(
        self,
        root: Path,
        *,
        project_id: str,
        lock_timeout_seconds: float,
    ) -> None:
        self.shared_root = resolve_canonical_shared_state(root, project_id)
        self.project_id = project_id
        self.root = self.shared_root / "reviewer-bindings"
        self.lock_timeout_seconds = lock_timeout_seconds

    def prepare_operation(
        self,
        operation: BindingAttemptOperation,
    ) -> BindingAttemptOperation:
        request = operation.request
        lock_path = self._session_root(request.stage_review_session_id) / "attempt.lock"
        with ShortFileLock(lock_path, timeout_seconds=self.lock_timeout_seconds):
            bind_repository_project(self.shared_root, self.project_id)
            existing = self._read_operation(operation.operation_id)
            if existing is not None:
                if existing.operation_digest != operation.operation_digest:
                    raise SharedStateIntegrityError("binding attempt lineage fork")
                return existing
            self._verify_next_attempt(operation)
            if not create_json_exclusive(
                self._operation_path(operation.operation_id),
                operation.model_dump(mode="json"),
            ):
                concurrent = self._read_operation(operation.operation_id)
                if concurrent is None:
                    raise SharedStateIntegrityError(
                        "binding attempt create result is inconsistent"
                    )
                if concurrent.operation_digest != operation.operation_digest:
                    raise SharedStateIntegrityError("binding attempt lineage fork")
                return concurrent
            return operation

    def attempt_execution_lock(
        self,
        operation: BindingAttemptOperation,
    ) -> ShortFileLock:
        request = operation.request
        path = self._session_root(request.stage_review_session_id) / (
            f"attempt-{request.attempt_index}.execution.lock"
        )
        return ShortFileLock(path, timeout_seconds=self.lock_timeout_seconds)

    def persist_authority(self, snapshot: BindingAuthoritySnapshot) -> None:
        persist_model(
            self._artifact_path("authority", snapshot.snapshot_id, _AUTHORITY_ID),
            snapshot,
            BindingAuthoritySnapshot,
            snapshot.snapshot_digest,
            "binding authority",
        )

    def persist_host(self, snapshot: HostCapabilitySnapshot) -> None:
        persist_model(
            self._artifact_path("host-snapshots", snapshot.snapshot_id, _HOST_ID),
            snapshot,
            HostCapabilitySnapshot,
            snapshot.snapshot_digest,
            "host capability snapshot",
        )

    def find_host_by_digest(
        self,
        snapshot_digest: str,
    ) -> HostCapabilitySnapshot | None:
        directory = self.root / "host-snapshots"
        if not directory.exists():
            return None
        matches = [
            item
            for path in sorted(directory.glob("*.json"))
            if (item := read_model(path, HostCapabilitySnapshot, "host snapshot"))
            is not None
            and item.snapshot_digest == snapshot_digest
        ]
        if len(matches) > 1:
            raise SharedStateIntegrityError("host snapshot digest is not unique")
        return matches[0] if matches else None

    def _find_authority_by_digest(
        self,
        snapshot_digest: str,
    ) -> BindingAuthoritySnapshot | None:
        directory = self.root / "authority"
        if not directory.exists():
            return None
        matches = [
            item
            for path in sorted(directory.glob("*.json"))
            if (
                item := read_model(
                    path,
                    BindingAuthoritySnapshot,
                    "binding authority",
                )
            )
            is not None
            and item.snapshot_digest == snapshot_digest
        ]
        if len(matches) > 1:
            raise SharedStateIntegrityError("binding authority digest is not unique")
        return matches[0] if matches else None

    def persist_availability(
        self,
        attestation: ProviderAvailabilityAttestation,
    ) -> None:
        persist_model(
            self._artifact_path(
                "provider-availability",
                attestation.attestation_id,
                _AVAILABILITY_ID,
            ),
            attestation,
            ProviderAvailabilityAttestation,
            attestation.attestation_digest,
            "provider availability attestation",
        )

    def persist_allocations(
        self,
        operation_id: str,
        allocations: tuple[ReviewerRuntimeAllocation, ...],
    ) -> tuple[ReviewerRuntimeAllocation, ...]:
        return persist_bundle(
            self.root / "allocations" / f"{operation_id}.json",
            operation_id,
            allocations,
            ReviewerRuntimeAllocation,
            "runtime allocation",
        )

    def persist_evidence(
        self,
        operation_id: str,
        evidence: tuple[IsolationExecutionEvidence, ...],
    ) -> tuple[IsolationExecutionEvidence, ...]:
        return persist_bundle(
            self.root / "isolation-evidence" / f"{operation_id}.json",
            operation_id,
            evidence,
            IsolationExecutionEvidence,
            "isolation evidence",
        )

    def persist_binding_set(self, binding_set: ReviewerBindingSet) -> None:
        persist_model(
            self._binding_set_path(binding_set.binding_set_id),
            binding_set,
            ReviewerBindingSet,
            binding_set.binding_set_digest,
            "reviewer binding set",
        )

    def persist_result(self, result: ReviewerBindingResult) -> None:
        persist_model(
            self._result_path(result.operation_id),
            result,
            ReviewerBindingResult,
            result.result_digest,
            "binding attempt result",
        )

    def get_binding_set(self, binding_set_id: str) -> ReviewerBindingSet | None:
        if _BINDING_SET_ID.fullmatch(binding_set_id) is None:
            raise ValueError("reviewer binding set identity is invalid")
        return read_model(
            self._binding_set_path(binding_set_id),
            ReviewerBindingSet,
            "reviewer binding set",
        )

    def get_binding_set_for_operation(
        self,
        operation: BindingAttemptOperation,
    ) -> ReviewerBindingSet | None:
        binding_set_id = stable_id("reviewer-binding-set", operation.operation_id)
        binding_set = self.get_binding_set(binding_set_id)
        if binding_set is None:
            return None
        if (
            binding_set.attempt_operation_id != operation.operation_id
            or binding_set.attempt_operation_digest != operation.operation_digest
        ):
            raise SharedStateIntegrityError("binding set operation lineage diverged")
        return binding_set

    def get_attempt_result(self, operation_id: str) -> ReviewerBindingResult | None:
        if _OPERATION_ID.fullmatch(operation_id) is None:
            raise ValueError("binding operation identity is invalid")
        return read_model(
            self._result_path(operation_id),
            ReviewerBindingResult,
            "binding attempt result",
        )

    def get_operation(self, operation_id: str) -> BindingAttemptOperation | None:
        return self._read_operation(operation_id)

    def find_binding_set_by_digest(
        self,
        binding_set_digest: str,
    ) -> ReviewerBindingSet | None:
        sets_dir = self.root / "sets"
        if not sets_dir.exists():
            return None
        matches = [
            item
            for path in sorted(sets_dir.glob("*.json"))
            if (item := self.get_binding_set(path.stem)) is not None
            and item.binding_set_digest == binding_set_digest
        ]
        if len(matches) > 1:
            raise SharedStateIntegrityError("binding set digest is not unique")
        return matches[0] if matches else None

    def has_reviewer_provider(self, provider_id: str) -> bool:
        sets_dir = self.root / "sets"
        if not sets_dir.exists():
            return False
        return any(
            binding.provider_id == provider_id
            for path in sorted(sets_dir.glob("*.json"))
            if (binding_set := self.get_binding_set(path.stem)) is not None
            for binding in binding_set.bindings
        )

    def allocations(
        self,
        operation_id: str,
    ) -> tuple[ReviewerRuntimeAllocation, ...]:
        return read_bundle(
            self.root / "allocations" / f"{operation_id}.json",
            operation_id,
            ReviewerRuntimeAllocation,
            "runtime allocation",
        )

    def evidence(
        self,
        operation_id: str,
    ) -> tuple[IsolationExecutionEvidence, ...]:
        return read_bundle(
            self.root / "isolation-evidence" / f"{operation_id}.json",
            operation_id,
            IsolationExecutionEvidence,
            "isolation evidence",
        )

    def persisted_evidence(
        self,
        operation_id: str,
    ) -> tuple[IsolationExecutionEvidence, ...] | None:
        path = self.root / "isolation-evidence" / f"{operation_id}.json"
        if not path.exists():
            return None
        return self.evidence(operation_id)

    def _verify_next_attempt(self, operation: BindingAttemptOperation) -> None:
        request = operation.request
        prior = self._session_operations(request.stage_review_session_id)
        if request.attempt_index != len(prior) + 1:
            raise SharedStateIntegrityError("binding attempt index is not contiguous")
        if prior and self.get_attempt_result(prior[-1].operation_id) is None:
            raise SharedStateIntegrityError("previous binding attempt is incomplete")
        if prior and any(
            item.request.project_id != request.project_id
            or item.request.work_item_id != request.work_item_id
            for item in prior
        ):
            raise SharedStateIntegrityError("binding attempt session identity diverged")
        latest = self._latest_binding_digest(prior)
        if request.previous_binding_set_digest != latest:
            raise SharedStateIntegrityError("binding previous lineage is stale")

    def _session_operations(
        self,
        session_id: str,
    ) -> tuple[BindingAttemptOperation, ...]:
        if not (self.root / "operations").exists():
            return ()
        values = [
            item
            for path in sorted((self.root / "operations").glob("*.json"))
            if (item := self._read_operation(path.stem)) is not None
            and item.request.stage_review_session_id == session_id
        ]
        return tuple(sorted(values, key=lambda item: item.request.attempt_index))

    def _latest_binding_digest(
        self,
        operations: tuple[BindingAttemptOperation, ...],
    ) -> str:
        digest = ""
        for operation in operations:
            result = self.get_attempt_result(operation.operation_id)
            if result is not None and result.binding_set is not None:
                digest = result.binding_set.binding_set_digest
        return digest

    def _read_operation(self, operation_id: str) -> BindingAttemptOperation | None:
        if _OPERATION_ID.fullmatch(operation_id) is None:
            raise ValueError("binding operation identity is invalid")
        return read_model(
            self._operation_path(operation_id),
            BindingAttemptOperation,
            "binding attempt operation",
        )

    def _operation_path(self, operation_id: str) -> Path:
        return self.root / "operations" / f"{operation_id}.json"

    def _result_path(self, operation_id: str) -> Path:
        return self.root / "results" / f"{operation_id}.json"

    def _binding_set_path(self, binding_set_id: str) -> Path:
        return self.root / "sets" / f"{binding_set_id}.json"

    def _session_root(self, session_id: str) -> Path:
        return self.root / "sessions" / stable_id("binding-session", session_id)

    def _artifact_path(
        self,
        directory: str,
        artifact_id: str,
        identity_pattern: re.Pattern[str],
    ) -> Path:
        if identity_pattern.fullmatch(artifact_id) is None:
            raise ValueError("binding artifact identity is invalid")
        parent = (self.root / directory).resolve()
        path = (parent / f"{artifact_id}.json").resolve()
        if path.parent != parent:
            raise ValueError("binding artifact path escapes its store")
        return path
