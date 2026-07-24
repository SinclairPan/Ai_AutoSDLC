"""项目级 OptimizationCommitLeaseClaim 与跨 Worktree Fencing。"""

from __future__ import annotations

import gzip
import hashlib
import json
from collections.abc import Iterator
from contextlib import contextmanager
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
)
from ai_sdlc.core.stage_review.optimization.commit_fencing_models import (
    OptimizationCommitLeaseCheckpoint,
    OptimizationCommitLeaseClaim,
    OptimizationCommitLeaseSegment,
)
from ai_sdlc.core.stage_review.registry_versions import require_machine_id
from ai_sdlc.core.stage_review.resource_builders import parse_utc


class OptimizationCommitLeaseHandle:
    def __init__(
        self,
        store: OptimizationCommitLeaseStore,
        claim: OptimizationCommitLeaseClaim,
    ) -> None:
        self.store = store
        self.claim = claim
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
        self.root = shared / "offline-optimization"
        self.claim_root = self.root / "commit-fence-events"
        self.segment_root = self.root / "commit-fence-segments"
        self.checkpoint_path = self.root / "commit-fence-checkpoint.json"
        self.projection_path = self.root / "commit-lease.json"
        self.lock_path = self.root / "commit-lease.lock"
        self.lock_timeout_seconds = lock_timeout_seconds

    @contextmanager
    def acquire(
        self,
        *,
        owner_id: str,
        scope: str,
        expected_head: str,
        now: datetime | None = None,
        lease_seconds: float = 2,
    ) -> Iterator[OptimizationCommitLeaseHandle]:
        if lease_seconds <= 0 or lease_seconds > 2:
            raise ValueError("commit lease duration must be within two seconds")
        lock = ShortFileLock(
            self.lock_path,
            timeout_seconds=self.lock_timeout_seconds,
        )
        lock.__enter__()
        handle: OptimizationCommitLeaseHandle | None = None
        try:
            claim = self._mint_claim(
                owner_id=owner_id,
                scope=scope,
                expected_head=expected_head,
                now=now,
                lease_seconds=lease_seconds,
            )
            handle = OptimizationCommitLeaseHandle(self, claim)
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
        checkpoint = self._checkpoint()
        tail = self._tail_claims(checkpoint)
        sequence, digest = (
            (checkpoint.compacted_through, checkpoint.compacted_claim_digest)
            if not tail
            else (tail[-1].fencing_epoch, tail[-1].claim_digest)
        )
        self._verify_projection(sequence, digest)
        return sequence, digest

    def _mint_claim(
        self,
        *,
        owner_id: str,
        scope: str,
        expected_head: str,
        now: datetime | None,
        lease_seconds: float,
    ) -> OptimizationCommitLeaseClaim:
        acquired = (now or datetime.now(UTC)).astimezone(UTC)
        high_watermark, previous = self.high_watermark()
        sequence = high_watermark + 1
        claim = OptimizationCommitLeaseClaim(
            project_id=self.project_id,
            owner_id=owner_id,
            scope=scope,
            fencing_epoch=sequence,
            expected_head=expected_head,
            acquired_at=acquired.isoformat(),
            expires_at=(acquired + timedelta(seconds=lease_seconds)).isoformat(),
            previous_claim_digest=previous,
        )
        path = self.claim_root / f"{sequence:020d}.json"
        if not create_json_exclusive(path, claim.model_dump(mode="json")):
            raise SharedStateIntegrityError("optimization commit claim collided")
        atomic_write_json(
            self.projection_path,
            {
                "project_id": self.project_id,
                "owner_id": owner_id,
                "scope": scope,
                "fencing_epoch": sequence,
                "claim_digest": claim.claim_digest,
                "expires_at": claim.expires_at,
            },
        )
        self._compact_claim_tail(claim)
        return claim

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
        paths = sorted(self.claim_root.glob("*.json")) if self.claim_root.is_dir() else []
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
        if len(claims) > 128:
            raise SharedStateIntegrityError("optimization commit claim tail is unbounded")
        return claims

    def _compact_claim_tail(self, head: OptimizationCommitLeaseClaim) -> None:
        checkpoint = self._checkpoint()
        if head.fencing_epoch - checkpoint.compacted_through <= 128:
            return
        first = checkpoint.compacted_through + 1
        last = head.fencing_epoch - 1
        claims = tuple(
            OptimizationCommitLeaseClaim.model_validate(
                read_json_object(self.claim_root / f"{sequence:020d}.json")
            )
            for sequence in range(first, last + 1)
        )
        _verify_claim_range(
            claims,
            first_sequence=first,
            previous_digest=checkpoint.compacted_claim_digest,
        )
        segment = self._persist_segment(claims)
        advanced = OptimizationCommitLeaseCheckpoint(
            project_id=self.project_id,
            compacted_through=last,
            compacted_claim_digest=claims[-1].claim_digest,
            segments=(*checkpoint.segments, segment),
        )
        atomic_write_json(self.checkpoint_path, advanced.model_dump(mode="json"))
        for sequence in range(first, last + 1):
            (self.claim_root / f"{sequence:020d}.json").unlink(missing_ok=True)

    def _persist_segment(
        self,
        claims: tuple[OptimizationCommitLeaseClaim, ...],
    ) -> OptimizationCommitLeaseSegment:
        raw = "\n".join(
            json.dumps(item.model_dump(mode="json"), sort_keys=True)
            for item in claims
        ).encode()
        payload = gzip.compress(raw, mtime=0)
        name = f"{claims[0].fencing_epoch:020d}-{claims[-1].fencing_epoch:020d}.jsonl.gz"
        path = self.segment_root / name
        _create_bytes_idempotent(path, payload)
        return OptimizationCommitLeaseSegment(
            first_sequence=claims[0].fencing_epoch,
            last_sequence=claims[-1].fencing_epoch,
            first_previous_claim_digest=claims[0].previous_claim_digest,
            last_claim_digest=claims[-1].claim_digest,
            relative_path=path.relative_to(self.root).as_posix(),
            payload_digest=_bytes_digest(payload),
        )

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

    def _verify_projection(self, sequence: int, digest: str) -> None:
        if sequence == 0:
            if self.projection_path.is_file():
                raise SharedStateIntegrityError("commit lease projection is stale")
            return
        if not self.projection_path.is_file():
            claim = OptimizationCommitLeaseClaim.model_validate(
                read_json_object(self.claim_root / f"{sequence:020d}.json")
            )
            if claim.claim_digest != digest:
                raise SharedStateIntegrityError("commit lease projection head diverged")
            atomic_write_json(
                self.projection_path,
                {
                    "project_id": self.project_id,
                    "owner_id": claim.owner_id,
                    "scope": claim.scope,
                    "fencing_epoch": sequence,
                    "claim_digest": digest,
                    "expires_at": claim.expires_at,
                },
            )
            return
        projection = read_json_object(self.projection_path)
        if (
            projection.get("project_id") != self.project_id
            or projection.get("fencing_epoch") != sequence
            or projection.get("claim_digest") != digest
        ):
            raise SharedStateIntegrityError("commit lease projection diverged")


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
    try:
        with path.open("xb") as handle:
            handle.write(payload)
    except FileExistsError:
        if path.read_bytes() != payload:
            raise SharedStateIntegrityError("commit lease segment content diverged") from None


def _bytes_digest(payload: bytes) -> str:
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"
