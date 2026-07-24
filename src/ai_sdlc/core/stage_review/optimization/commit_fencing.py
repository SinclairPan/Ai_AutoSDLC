"""项目级 OptimizationCommitLeaseClaim 与跨 Worktree Fencing。"""

from __future__ import annotations

import gzip
import hashlib
import hmac
import json
import os
import secrets
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from ai_sdlc.core.stage_review.artifacts import (
    SharedStateIntegrityError,
    ShortFileLock,
    atomic_write_json,
    bind_repository_project,
    create_json_exclusive,
    read_json_object,
    resolve_canonical_shared_state,
    serialized_json_bytes,
)
from ai_sdlc.core.stage_review.canonical import CanonicalizationPolicy, canonical_digest
from ai_sdlc.core.stage_review.optimization.commit_fencing_models import (
    OptimizationCommitLeaseCheckpoint,
    OptimizationCommitLeaseClaim,
    OptimizationCommitLeaseSegment,
)
from ai_sdlc.core.stage_review.optimization.storage_models import StoragePressureError
from ai_sdlc.core.stage_review.registry_versions import require_machine_id
from ai_sdlc.core.stage_review.resource_builders import parse_utc


@dataclass(frozen=True, slots=True)
class LeaseAcquisitionPlan:
    owner_id: str
    scope: str
    expected_head: str
    lease_seconds: float
    state_digest: str
    fencing_epoch: int
    max_write_bytes: int
    plan_digest: str


@dataclass(frozen=True, slots=True)
class LeaseAcquisitionReceipt:
    plan_digest: str
    fencing_epoch: int
    claim_digest: str
    actual_write_bytes: int


@dataclass(frozen=True, slots=True)
class _LeaseWriteAuthorization:
    plan_digest: str
    byte_allowance: int
    authorization_mac: str


@dataclass(frozen=True, slots=True)
class _FencingState:
    checkpoint: OptimizationCommitLeaseCheckpoint
    tail: tuple[OptimizationCommitLeaseClaim, ...]
    stale_claim_paths: tuple[Path, ...]
    state_digest: str


@dataclass(frozen=True, slots=True)
class _RolloverWrite:
    payload: bytes
    segment: OptimizationCommitLeaseSegment
    checkpoint: OptimizationCommitLeaseCheckpoint
    pending_segment_bytes: int
    cleanup_paths: tuple[Path, ...]


@dataclass(frozen=True, slots=True)
class _LeaseWrite:
    claim: OptimizationCommitLeaseClaim
    projection: dict[str, object]
    rollover: _RolloverWrite | None
    recovery_before_claim: bool
    stale_claim_paths: tuple[Path, ...]
    write_bytes: int


class OptimizationCommitLeaseHandle:
    def __init__(
        self,
        store: OptimizationCommitLeaseStore,
        claim: OptimizationCommitLeaseClaim,
        receipt: LeaseAcquisitionReceipt,
    ) -> None:
        self.store = store
        self.claim = claim
        self.receipt = receipt
        self._owns_mutex = True

    def assert_current(self, *, now: datetime | None = None) -> None:
        if not self._owns_mutex:
            raise SharedStateIntegrityError("commit writer no longer owns mutex")
        current = (now or datetime.now(UTC)).astimezone(UTC)
        if current >= parse_utc(self.claim.expires_at):
            raise SharedStateIntegrityError("optimization commit lease expired")
        if self.store.high_watermark() != (
            self.claim.fencing_epoch,
            self.claim.claim_digest,
        ):
            raise SharedStateIntegrityError("optimization commit fencing is stale")

    def release(self) -> None:
        self._owns_mutex = False

    def assert_plan(self, plan: LeaseAcquisitionPlan) -> None:
        if (
            self.receipt.plan_digest != plan.plan_digest
            or self.receipt.fencing_epoch != plan.fencing_epoch
            or self.receipt.claim_digest != self.claim.claim_digest
            or self.receipt.actual_write_bytes > plan.max_write_bytes
        ):
            raise SharedStateIntegrityError(
                "optimization commit lease authorization diverged"
            )


class OptimizationCommitLeaseStore:
    def __init__(
        self,
        root: Path,
        *,
        project_id: str,
        lock_timeout_seconds: float = 2,
    ) -> None:
        self.project_id = require_machine_id(project_id, "project_id")
        shared = resolve_canonical_shared_state(root, self.project_id)
        bind_repository_project(shared, self.project_id)
        # Fencing is bounded control-plane state. Keeping it outside the
        # offline data quota prevents a lease write from racing unrelated
        # data-plane writers between admission and persistence.
        self.root = shared / "optimization-control" / "commit-fence"
        self.claim_root = self.root / "commit-fence-events"
        self.segment_root = self.root / "commit-fence-segments"
        self.checkpoint_path = self.root / "commit-fence-checkpoint.json"
        self.projection_path = self.root / "commit-lease.json"
        self.lock_path = shared / "locks" / "optimization-commit-lease.lock"
        self.lock_timeout_seconds = lock_timeout_seconds
        self._authorization_secret = secrets.token_bytes(32)

    def _authorize_plan(
        self,
        plan: LeaseAcquisitionPlan,
    ) -> _LeaseWriteAuthorization:
        self._authorize_against_default_hard_limit(plan.max_write_bytes)
        return _LeaseWriteAuthorization(
            plan_digest=plan.plan_digest,
            byte_allowance=plan.max_write_bytes,
            authorization_mac=self._authorization_mac(
                plan.plan_digest,
                plan.max_write_bytes,
            ),
        )

    def preview_acquire(
        self,
        *,
        owner_id: str,
        scope: str,
        expected_head: str,
        lease_seconds: float = 2,
    ) -> LeaseAcquisitionPlan:
        _require_lease_duration(lease_seconds)
        with ShortFileLock(
            self.lock_path,
            timeout_seconds=self.lock_timeout_seconds,
        ):
            return self._build_plan_locked(
                owner_id=owner_id,
                scope=scope,
                expected_head=expected_head,
                lease_seconds=lease_seconds,
            )

    @contextmanager
    def acquire(
        self,
        *,
        owner_id: str,
        scope: str,
        expected_head: str,
        now: datetime | None = None,
        lease_seconds: float = 2,
        plan: LeaseAcquisitionPlan | None = None,
        authorization: _LeaseWriteAuthorization | None = None,
    ) -> Iterator[OptimizationCommitLeaseHandle]:
        _require_lease_duration(lease_seconds)
        lock = ShortFileLock(
            self.lock_path,
            timeout_seconds=self.lock_timeout_seconds,
        )
        lock.__enter__()
        handle: OptimizationCommitLeaseHandle | None = None
        try:
            current_plan = self._build_plan_locked(
                owner_id=owner_id,
                scope=scope,
                expected_head=expected_head,
                lease_seconds=lease_seconds,
            )
            if plan is not None and current_plan != plan:
                raise SharedStateIntegrityError(
                    "optimization commit lease plan is stale"
                )
            if plan is not None and not self._valid_authorization(plan, authorization):
                raise StoragePressureError(
                    "optimization commit lease write is not fully authorized"
                )
            write = self._build_lease_write(
                self._state(),
                owner_id=owner_id,
                scope=scope,
                expected_head=expected_head,
                acquired=(now or datetime.now(UTC)).astimezone(UTC),
                lease_seconds=lease_seconds,
            )
            if write.write_bytes > current_plan.max_write_bytes:
                raise StoragePressureError(
                    "optimization commit lease exceeded its preview"
                )
            self._authorize_against_default_hard_limit(
                current_plan.max_write_bytes
            )
            claim = self._persist_lease_write(write)
            receipt = LeaseAcquisitionReceipt(
                plan_digest=current_plan.plan_digest,
                fencing_epoch=claim.fencing_epoch,
                claim_digest=claim.claim_digest,
                actual_write_bytes=write.write_bytes,
            )
            handle = OptimizationCommitLeaseHandle(self, claim, receipt)
            yield handle
        finally:
            if handle is not None:
                handle.release()
            lock.__exit__(None, None, None)

    def claims(self) -> tuple[OptimizationCommitLeaseClaim, ...]:
        checkpoint = self._checkpoint()
        compacted = tuple(
            claim
            for segment in checkpoint.segments
            for claim in self._read_segment(segment)
        )
        tail = self._tail_claims(checkpoint)
        return (*compacted, *tail)

    def high_watermark(self) -> tuple[int, str]:
        state = self._state()
        checkpoint = state.checkpoint
        tail = state.tail
        sequence, digest = (
            (checkpoint.compacted_through, checkpoint.compacted_claim_digest)
            if not tail
            else (tail[-1].fencing_epoch, tail[-1].claim_digest)
        )
        self._verify_projection(sequence, digest, checkpoint, tail)
        return sequence, digest

    def _build_plan_locked(
        self,
        *,
        owner_id: str,
        scope: str,
        expected_head: str,
        lease_seconds: float,
    ) -> LeaseAcquisitionPlan:
        state = self._state()
        placeholder = datetime(2099, 12, 31, 23, 59, 59, 999999, tzinfo=UTC)
        write = self._build_lease_write(
            state,
            owner_id=owner_id,
            scope=scope,
            expected_head=expected_head,
            acquired=placeholder,
            lease_seconds=lease_seconds,
        )
        values: dict[str, object] = {
            "owner_id": owner_id,
            "scope": scope,
            "expected_head": expected_head,
            "lease_seconds": lease_seconds,
            "state_digest": state.state_digest,
            "fencing_epoch": write.claim.fencing_epoch,
            "max_write_bytes": write.write_bytes,
        }
        return LeaseAcquisitionPlan(
            owner_id=owner_id,
            scope=scope,
            expected_head=expected_head,
            lease_seconds=lease_seconds,
            state_digest=state.state_digest,
            fencing_epoch=write.claim.fencing_epoch,
            max_write_bytes=write.write_bytes,
            plan_digest=canonical_digest(values, CanonicalizationPolicy()),
        )

    def _build_lease_write(
        self,
        state: _FencingState,
        *,
        owner_id: str,
        scope: str,
        expected_head: str,
        acquired: datetime,
        lease_seconds: float,
    ) -> _LeaseWrite:
        checkpoint = state.checkpoint
        tail = state.tail
        recovery_before_claim = len(tail) == 129
        rollover: _RolloverWrite | None = None
        if recovery_before_claim:
            rollover = self._build_rollover(checkpoint, tail[:128])
            checkpoint = rollover.checkpoint
            tail = tail[128:]
        high_watermark = (
            tail[-1].fencing_epoch if tail else checkpoint.compacted_through
        )
        previous = (
            tail[-1].claim_digest if tail else checkpoint.compacted_claim_digest
        )
        sequence = high_watermark + 1
        normalized_acquired = acquired.astimezone(UTC)
        claim = OptimizationCommitLeaseClaim(
            project_id=self.project_id,
            owner_id=owner_id,
            scope=scope,
            fencing_epoch=sequence,
            expected_head=expected_head,
            acquired_at=normalized_acquired.isoformat(timespec="microseconds"),
            expires_at=(normalized_acquired + timedelta(seconds=lease_seconds)).isoformat(
                timespec="microseconds"
            ),
            previous_claim_digest=previous,
        )
        projection = _projection_payload(claim)
        if not recovery_before_claim and sequence - checkpoint.compacted_through > 128:
            rollover = self._build_rollover(checkpoint, tail)
        write_bytes = len(serialized_json_bytes(claim.model_dump(mode="json"))) + len(
            serialized_json_bytes(projection)
        )
        if rollover is not None:
            write_bytes += rollover.pending_segment_bytes + len(
                serialized_json_bytes(rollover.checkpoint.model_dump(mode="json"))
            )
        return _LeaseWrite(
            claim=claim,
            projection=projection,
            rollover=rollover,
            recovery_before_claim=recovery_before_claim,
            stale_claim_paths=state.stale_claim_paths,
            write_bytes=write_bytes,
        )

    def _persist_lease_write(
        self,
        write: _LeaseWrite,
    ) -> OptimizationCommitLeaseClaim:
        for path in write.stale_claim_paths:
            path.unlink(missing_ok=True)
        if write.recovery_before_claim and write.rollover is not None:
            self._persist_rollover(write.rollover)
        path = self.claim_root / f"{write.claim.fencing_epoch:020d}.json"
        if not create_json_exclusive(path, write.claim.model_dump(mode="json")):
            raise SharedStateIntegrityError("optimization commit claim collided")
        atomic_write_json(self.projection_path, write.projection)
        if not write.recovery_before_claim and write.rollover is not None:
            self._persist_rollover(write.rollover)
        return write.claim

    def _checkpoint(self) -> OptimizationCommitLeaseCheckpoint:
        if not self.checkpoint_path.is_file():
            return OptimizationCommitLeaseCheckpoint(
                project_id=self.project_id,
                compacted_through=0,
            )
        checkpoint = OptimizationCommitLeaseCheckpoint.model_validate(
            read_json_object(self.checkpoint_path)
        )
        if checkpoint.project_id != self.project_id:
            raise SharedStateIntegrityError("commit lease checkpoint project diverged")
        return checkpoint

    def _tail_claims(
        self,
        checkpoint: OptimizationCommitLeaseCheckpoint,
    ) -> tuple[OptimizationCommitLeaseClaim, ...]:
        start = checkpoint.compacted_through + 1
        paths = (
            sorted(self.claim_root.glob("*.json"))
            if self.claim_root.is_dir()
            else []
        )
        claims = tuple(
            OptimizationCommitLeaseClaim.model_validate(read_json_object(path))
            for path in paths
            if int(path.stem) >= start
        )
        _verify_claim_range(
            claims,
            first_sequence=start,
            previous_digest=checkpoint.compacted_claim_digest,
        )
        if len(claims) > 129:
            raise SharedStateIntegrityError("optimization commit claim tail is unbounded")
        return claims

    def _state(self) -> _FencingState:
        checkpoint = self._checkpoint()
        tail = self._tail_claims(checkpoint)
        all_paths = (
            tuple(sorted(self.claim_root.glob("*.json")))
            if self.claim_root.is_dir()
            else ()
        )
        stale = tuple(
            path for path in all_paths if int(path.stem) <= checkpoint.compacted_through
        )
        if stale:
            compacted = {
                claim.fencing_epoch: claim
                for segment in checkpoint.segments
                for claim in self._read_segment(segment)
            }
            for path in stale:
                claim = OptimizationCommitLeaseClaim.model_validate(
                    read_json_object(path)
                )
                if compacted.get(claim.fencing_epoch) != claim:
                    raise SharedStateIntegrityError(
                        "stale commit lease claim diverged from checkpoint"
                    )
        sequence, digest = (
            (checkpoint.compacted_through, checkpoint.compacted_claim_digest)
            if not tail
            else (tail[-1].fencing_epoch, tail[-1].claim_digest)
        )
        projection = self._verify_projection(sequence, digest, checkpoint, tail)
        state_values: dict[str, object] = {
            "checkpoint_digest": checkpoint.checkpoint_digest,
            "tail_claim_digests": [claim.claim_digest for claim in tail],
            "stale_claims": [
                [path.name, _bytes_digest(path.read_bytes())] for path in stale
            ],
            "projection": projection,
        }
        return _FencingState(
            checkpoint=checkpoint,
            tail=tail,
            stale_claim_paths=stale,
            state_digest=canonical_digest(state_values, CanonicalizationPolicy()),
        )

    def _build_rollover(
        self,
        checkpoint: OptimizationCommitLeaseCheckpoint,
        claims: tuple[OptimizationCommitLeaseClaim, ...],
    ) -> _RolloverWrite:
        if len(claims) != 128:
            raise SharedStateIntegrityError(
                "commit lease rollover requires exactly 128 claims"
            )
        _verify_claim_range(
            claims,
            first_sequence=checkpoint.compacted_through + 1,
            previous_digest=checkpoint.compacted_claim_digest,
        )
        payload, segment = self._segment_artifact(claims)
        advanced = OptimizationCommitLeaseCheckpoint(
            project_id=self.project_id,
            compacted_through=claims[-1].fencing_epoch,
            compacted_claim_digest=claims[-1].claim_digest,
            segments=(*checkpoint.segments, segment),
        )
        path = self.root / segment.relative_path
        pending_segment_bytes = len(payload)
        if path.is_file():
            if path.read_bytes() != payload:
                raise SharedStateIntegrityError(
                    "commit lease segment content diverged"
                )
            pending_segment_bytes = 0
        return _RolloverWrite(
            payload=payload,
            segment=segment,
            checkpoint=advanced,
            pending_segment_bytes=pending_segment_bytes,
            cleanup_paths=tuple(
                self.claim_root / f"{claim.fencing_epoch:020d}.json"
                for claim in claims
            ),
        )

    def _segment_artifact(
        self,
        claims: tuple[OptimizationCommitLeaseClaim, ...],
    ) -> tuple[bytes, OptimizationCommitLeaseSegment]:
        raw = "\n".join(
            json.dumps(item.model_dump(mode="json"), sort_keys=True)
            for item in claims
        ).encode()
        payload = gzip.compress(raw, mtime=0)
        name = f"{claims[0].fencing_epoch:020d}-{claims[-1].fencing_epoch:020d}.jsonl.gz"
        path = self.segment_root / name
        segment = OptimizationCommitLeaseSegment(
            first_sequence=claims[0].fencing_epoch,
            last_sequence=claims[-1].fencing_epoch,
            first_previous_claim_digest=claims[0].previous_claim_digest,
            last_claim_digest=claims[-1].claim_digest,
            relative_path=path.relative_to(self.root).as_posix(),
            payload_digest=_bytes_digest(payload),
        )
        return payload, segment

    def _persist_rollover(self, rollover: _RolloverWrite) -> None:
        if rollover.pending_segment_bytes:
            _create_bytes_idempotent(
                self.root / rollover.segment.relative_path,
                rollover.payload,
            )
        atomic_write_json(
            self.checkpoint_path,
            rollover.checkpoint.model_dump(mode="json"),
        )
        for path in rollover.cleanup_paths:
            path.unlink(missing_ok=True)

    def _read_segment(
        self,
        segment: OptimizationCommitLeaseSegment,
    ) -> tuple[OptimizationCommitLeaseClaim, ...]:
        path = self.root / segment.relative_path
        payload = path.read_bytes()
        if _bytes_digest(payload) != segment.payload_digest:
            raise SharedStateIntegrityError("commit lease segment digest diverged")
        claims = tuple(
            OptimizationCommitLeaseClaim.model_validate(json.loads(line))
            for line in gzip.decompress(payload).decode().splitlines()
        )
        _verify_claim_range(
            claims,
            first_sequence=segment.first_sequence,
            previous_digest=segment.first_previous_claim_digest,
        )
        if claims[-1].claim_digest != segment.last_claim_digest:
            raise SharedStateIntegrityError("commit lease segment head diverged")
        return claims

    def _verify_projection(
        self,
        sequence: int,
        digest: str,
        checkpoint: OptimizationCommitLeaseCheckpoint,
        tail: tuple[OptimizationCommitLeaseClaim, ...],
    ) -> dict[str, object] | None:
        if sequence == 0:
            if self.projection_path.is_file():
                raise SharedStateIntegrityError("commit lease projection is stale")
            return None
        if not self.projection_path.is_file():
            return None
        projection = read_json_object(self.projection_path)
        ordered_heads = (
            (
                (checkpoint.compacted_through, checkpoint.compacted_claim_digest),
            )
            if checkpoint.compacted_through
            else ()
        ) + tuple((claim.fencing_epoch, claim.claim_digest) for claim in tail)
        valid_heads = set(ordered_heads[-2:])
        projected_head = (
            projection.get("fencing_epoch"),
            projection.get("claim_digest"),
        )
        if (
            projection.get("project_id") != self.project_id
            or projected_head not in valid_heads
        ):
            raise SharedStateIntegrityError("commit lease projection diverged")
        if (sequence, digest) not in valid_heads:
            raise SharedStateIntegrityError("commit lease projection head diverged")
        projected_claim = next(
            (
                claim
                for claim in tail
                if claim.fencing_epoch == projection.get("fencing_epoch")
            ),
            None,
        )
        if (
            projected_claim is None
            and checkpoint.compacted_through == projection.get("fencing_epoch")
            and checkpoint.segments
        ):
            projected_claim = self._read_segment(checkpoint.segments[-1])[-1]
        if (
            projected_claim is None
            or projection != _projection_payload(projected_claim)
        ):
            raise SharedStateIntegrityError("commit lease projection content diverged")
        return projection

    def _authorize_against_default_hard_limit(self, write_bytes: int) -> None:
        maximum = 1024**3
        if _directory_bytes(self.root) + write_bytes > maximum:
            raise StoragePressureError(
                "optimization commit lease exceeds storage hard limit"
            )

    def _valid_authorization(
        self,
        plan: LeaseAcquisitionPlan,
        authorization: _LeaseWriteAuthorization | None,
    ) -> bool:
        if authorization is None or authorization.byte_allowance < plan.max_write_bytes:
            return False
        expected = self._authorization_mac(
            authorization.plan_digest,
            authorization.byte_allowance,
        )
        return (
            authorization.plan_digest == plan.plan_digest
            and hmac.compare_digest(authorization.authorization_mac, expected)
        )

    def _authorization_mac(self, plan_digest: str, byte_allowance: int) -> str:
        payload = f"{plan_digest}\0{byte_allowance}".encode()
        return hmac.new(self._authorization_secret, payload, hashlib.sha256).hexdigest()


def _verify_claim_range(
    claims: tuple[OptimizationCommitLeaseClaim, ...],
    *,
    first_sequence: int,
    previous_digest: str,
) -> None:
    previous = previous_digest
    for offset, claim in enumerate(claims):
        if (
            claim.fencing_epoch != first_sequence + offset
            or claim.previous_claim_digest != previous
        ):
            raise SharedStateIntegrityError("optimization commit claim chain diverged")
        previous = claim.claim_digest


def _create_bytes_idempotent(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        for _attempt in range(16):
            candidate = path.with_name(f".{secrets.token_hex(8)}.tmp")
            try:
                descriptor = os.open(
                    candidate,
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                    0o600,
                )
            except FileExistsError:
                continue
            temporary = candidate
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            break
        if temporary is None:
            raise FileExistsError("could not allocate commit lease segment temporary")
        try:
            os.link(temporary, path)
        except FileExistsError:
            if path.read_bytes() != payload:
                raise SharedStateIntegrityError(
                    "commit lease segment content diverged"
                ) from None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _bytes_digest(payload: bytes) -> str:
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def _projection_payload(
    claim: OptimizationCommitLeaseClaim,
) -> dict[str, object]:
    return {
        "project_id": claim.project_id,
        "owner_id": claim.owner_id,
        "scope": claim.scope,
        "fencing_epoch": claim.fencing_epoch,
        "claim_digest": claim.claim_digest,
        "expires_at": claim.expires_at,
    }


def _require_lease_duration(lease_seconds: float) -> None:
    if lease_seconds <= 0 or lease_seconds > 2:
        raise ValueError("commit lease duration must be within two seconds")


def _directory_bytes(root: Path) -> int:
    return sum(
        path.stat().st_size
        for path in root.rglob("*")
        if path.is_file()
    ) if root.is_dir() else 0


__all__ = [
    "LeaseAcquisitionPlan",
    "LeaseAcquisitionReceipt",
    "OptimizationCommitLeaseHandle",
    "OptimizationCommitLeaseStore",
]
