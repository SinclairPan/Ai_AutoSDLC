"""受 Commit Fencing 保护且可分段恢复的 Holdout 承诺存储。"""

from __future__ import annotations

from pathlib import Path

from ai_sdlc.core.stage_review.artifacts import SharedStateIntegrityError
from ai_sdlc.core.stage_review.optimization.commit_fencing import (
    OptimizationCommitLeaseHandle,
    OptimizationCommitLeaseStore,
)
from ai_sdlc.core.stage_review.optimization.holdout_contracts import (
    HoldoutQueryCommitment,
    HoldoutQueryRequest,
)
from ai_sdlc.core.stage_review.optimization.storage import OptimizationStorage
from ai_sdlc.core.stage_review.optimization.storage_models import (
    OptimizationStoragePolicy,
    OptimizationStorageRecord,
)
from ai_sdlc.core.stage_review.registry_versions import require_machine_id
from ai_sdlc.core.stage_review.resource_builders import stable_id

_STREAM = "query-commitments"


class HoldoutCommitmentStore:
    def __init__(
        self,
        root: Path,
        *,
        project_id: str,
        familywise_alpha: float,
        lock_timeout_seconds: float = 2,
        storage_policy: OptimizationStoragePolicy | None = None,
    ) -> None:
        if not 0 < familywise_alpha < 1:
            raise ValueError("familywise alpha must be between zero and one")
        self.project_id = require_machine_id(project_id, "project_id")
        self.familywise_alpha = familywise_alpha
        self.commit_leases = OptimizationCommitLeaseStore(
            root,
            project_id=self.project_id,
            lock_timeout_seconds=lock_timeout_seconds,
        )
        self.storage = OptimizationStorage(
            root,
            project_id=self.project_id,
            policy=storage_policy or OptimizationStoragePolicy(),
            commit_leases=self.commit_leases,
        )

    @property
    def cumulative_alpha(self) -> float:
        return sum(item.alpha_i for item in self.commitments())

    def commit(self, request: HoldoutQueryRequest) -> HoldoutQueryCommitment:
        trusted = HoldoutQueryRequest.model_validate(request.model_dump(mode="json"))
        idempotency_key = _query_idempotency_key(self.project_id, trusted)
        existing = self._lookup("idempotency_key", idempotency_key)
        if existing is not None:
            return _verify_idempotent(existing, trusted)
        expected_head = self._head_digest()
        with self.commit_leases.acquire(
            owner_id=f"holdout-writer.{trusted.epoch_id}",
            scope="query_commitment",
            expected_head=expected_head,
        ) as lease:
            return self._commit_locked(trusted, idempotency_key, lease)

    def commitments(self) -> tuple[HoldoutQueryCommitment, ...]:
        values = tuple(_commitment(record) for record in self.storage.read_stream(_STREAM))
        for sequence, item in enumerate(values, start=1):
            previous = "" if sequence == 1 else values[sequence - 2].commitment_digest
            if item.test_sequence != sequence or item.previous_commitment_digest != previous:
                raise SharedStateIntegrityError("holdout commitment chain diverged")
        return values

    def _commit_locked(
        self,
        request: HoldoutQueryRequest,
        idempotency_key: str,
        lease: OptimizationCommitLeaseHandle,
    ) -> HoldoutQueryCommitment:
        if self._head_digest() != lease.claim.expected_head:
            raise SharedStateIntegrityError("holdout commitment expected head is stale")
        existing = self._lookup("idempotency_key", idempotency_key)
        if existing is not None:
            return _verify_idempotent(existing, request)
        self._verify_unused(request)
        commitments = self.commitments()
        sequence = len(commitments) + 1
        previous = "" if not commitments else commitments[-1].commitment_digest
        commitment = _build_commitment(
            self.project_id,
            request,
            sequence,
            self.familywise_alpha / (sequence * (sequence + 1)),
            previous,
            lease,
        )
        record = self.storage.append(
            _STREAM,
            commitment.model_dump(mode="json"),
            keys=_lookup_keys(commitment),
            lease=lease,
        )
        if record.sequence != commitment.test_sequence:
            raise SharedStateIntegrityError("holdout storage sequence diverged")
        return _commitment(record)

    def _verify_unused(self, request: HoldoutQueryRequest) -> None:
        if self._lookup("holdout_generation_id", request.holdout_generation_id):
            raise SharedStateIntegrityError("holdout generation was already consumed")
        for session_id in request.holdout_session_ids:
            if self._lookup(_session_key_kind(session_id), session_id):
                raise SharedStateIntegrityError("holdout session was already consumed")

    def _lookup(self, key_kind: str, key: str) -> HoldoutQueryCommitment | None:
        record = self.storage.lookup(_STREAM, key_kind=key_kind, key=key)
        return None if record is None else _commitment(record)

    def _head_digest(self) -> str:
        commitments = self.commitments()
        return (
            commitments[-1].commitment_digest
            if commitments
            else f"holdout-genesis:{self.project_id}"
        )


def _query_idempotency_key(project_id: str, request: HoldoutQueryRequest) -> str:
    return stable_id(
        "holdout-query",
        project_id,
        request.hypothesis_digest,
        request.holdout_generation_id,
        request.baseline_snapshot_digest,
        request.finalist_candidate_digest,
    )


def _build_commitment(
    project_id: str,
    request: HoldoutQueryRequest,
    sequence: int,
    alpha_i: float,
    previous_digest: str,
    lease: OptimizationCommitLeaseHandle,
) -> HoldoutQueryCommitment:
    idempotency_key = _query_idempotency_key(project_id, request)
    return HoldoutQueryCommitment(
        commitment_id=stable_id("holdout-commitment", idempotency_key),
        project_id=project_id,
        epoch_id=request.epoch_id,
        idempotency_key=idempotency_key,
        hypothesis_digest=request.hypothesis_digest,
        holdout_generation_id=request.holdout_generation_id,
        baseline_snapshot_digest=request.baseline_snapshot_digest,
        finalist_candidate_digest=request.finalist_candidate_digest,
        holdout_session_ids=request.holdout_session_ids,
        provider_query_idempotency_key=request.provider_query_idempotency_key,
        test_sequence=sequence,
        alpha_i=alpha_i,
        previous_commitment_digest=previous_digest,
        commit_fencing_epoch=lease.claim.fencing_epoch,
        commit_claim_digest=lease.claim.claim_digest,
        epoch_lease_fencing_epoch=request.epoch_lease_fencing_epoch,
        epoch_lease_claim_digest=request.epoch_lease_claim_digest,
    )


def _commitment(record: OptimizationStorageRecord) -> HoldoutQueryCommitment:
    commitment = HoldoutQueryCommitment.model_validate(record.payload)
    if commitment.test_sequence != record.sequence:
        raise SharedStateIntegrityError("holdout record sequence diverged")
    return commitment


def _verify_idempotent(
    existing: HoldoutQueryCommitment,
    request: HoldoutQueryRequest,
) -> HoldoutQueryCommitment:
    expected = (
        existing.epoch_id == request.epoch_id,
        existing.hypothesis_digest == request.hypothesis_digest,
        existing.holdout_generation_id == request.holdout_generation_id,
        existing.baseline_snapshot_digest == request.baseline_snapshot_digest,
        existing.finalist_candidate_digest == request.finalist_candidate_digest,
        existing.holdout_session_ids == request.holdout_session_ids,
        existing.provider_query_idempotency_key
        == request.provider_query_idempotency_key,
    )
    if not all(expected):
        raise SharedStateIntegrityError("holdout idempotency content diverged")
    return existing


def _lookup_keys(commitment: HoldoutQueryCommitment) -> dict[str, str]:
    keys = {
        "idempotency_key": commitment.idempotency_key,
        "holdout_generation_id": commitment.holdout_generation_id,
    }
    keys.update(
        {
            _session_key_kind(session_id): session_id
            for session_id in commitment.holdout_session_ids
        }
    )
    return keys


def _session_key_kind(session_id: str) -> str:
    return f"holdout_session_id:{stable_id('holdout-session-key', session_id)}"
