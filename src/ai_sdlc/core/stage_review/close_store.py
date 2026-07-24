"""阶段关闭 Claim、Event、Receipt 与可重建投影存储。"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Literal, TypeVar

from pydantic import BaseModel, ValidationError

from ai_sdlc.core.stage_review.artifacts import (
    SharedStateIntegrityError,
    ShortFileLock,
    atomic_write_json,
    bind_repository_project,
    create_json_exclusive,
    read_json_object,
    resolve_canonical_shared_state,
)
from ai_sdlc.core.stage_review.canonical import CanonicalizationPolicy, canonical_digest
from ai_sdlc.core.stage_review.close_builders import (
    _build_close_event as build_close_event,
)
from ai_sdlc.core.stage_review.close_models import (
    CloseArtifactContract,
    CloseConsumptionClaim,
    CloseConsumptionEvent,
    CloseConsumptionState,
    CloseEventKind,
    StageCloseConsumptionReceipt,
)
from ai_sdlc.core.stage_review.transaction_artifact_codec import (
    decode_transaction_artifact,
)


class CloseStoreConflictError(SharedStateIntegrityError):
    """关闭不可变事实已存在但与当前命令分叉。"""


class StageCloseStore:
    def __init__(
        self,
        root: Path,
        *,
        project_id: str,
        lock_timeout_seconds: float,
    ) -> None:
        self.worktree_root = root.resolve()
        self.shared_root = resolve_canonical_shared_state(root, project_id)
        self.project_id = project_id
        self.root = self.shared_root / "stage-close-authorizer"
        self.claims_dir = self.root / "claims"
        self.events_root = self.root / "events"
        self.receipts_dir = self.root / "receipts"
        self.projections_dir = self.root / "states"
        self.locks_dir = self.root / "locks"
        self.lock_timeout_seconds = lock_timeout_seconds

    @contextmanager
    def locked(self, certificate_id: str) -> Iterator[None]:
        with ShortFileLock(
            self.locks_dir / f"{certificate_id}.lock",
            timeout_seconds=self.lock_timeout_seconds,
        ):
            bind_repository_project(self.shared_root, self.project_id)
            yield

    def read_claim(self, certificate_id: str) -> CloseConsumptionClaim | None:
        path = self.claims_dir / f"{certificate_id}.json"
        if not path.exists():
            return None
        return _read_model(path, CloseConsumptionClaim)

    def create_claim(self, claim: CloseConsumptionClaim) -> CloseConsumptionClaim:
        trusted = CloseConsumptionClaim.model_validate(claim.model_dump(mode="json"))
        path = self.claims_dir / f"{trusted.certificate_id}.json"
        if create_json_exclusive(path, trusted.model_dump(mode="json")):
            return trusted
        existing = _read_model(path, CloseConsumptionClaim)
        if existing != trusted:
            raise CloseStoreConflictError("close consumption claim already exists")
        return existing

    def load_state(self, claim: CloseConsumptionClaim) -> CloseConsumptionState:
        trusted = CloseConsumptionClaim.model_validate(claim.model_dump(mode="json"))
        events = self._read_events(trusted.certificate_id)
        state = _rebuild_state(trusted, events)
        self._verify_projection(trusted.certificate_id, state)
        return state

    def require_consumable_state(
        self,
        claim: CloseConsumptionClaim,
    ) -> CloseConsumptionState:
        trusted = CloseConsumptionClaim.model_validate(claim.model_dump(mode="json"))
        persisted = self.read_claim(trusted.certificate_id)
        if persisted != trusted:
            raise SharedStateIntegrityError("close claim is not the persisted authority")
        events = self._read_events(trusted.certificate_id)
        receipt = self.read_receipt(trusted.claim_id)
        artifacts = (trusted, *events, *((receipt,) if receipt is not None else ()))
        if any(item.compatibility_mode != "strict" for item in artifacts):
            raise SharedStateIntegrityError("previous close schema is read-only")
        state = _rebuild_state(trusted, events)
        self._verify_projection(trusted.certificate_id, state)
        return state

    def last_event(
        self,
        certificate_id: str,
    ) -> CloseConsumptionEvent | None:
        events = self._read_events(certificate_id)
        return events[-1] if events else None

    def append_event(
        self,
        claim: CloseConsumptionClaim,
        state: CloseConsumptionState,
        event_kind: CloseEventKind,
        *,
        occurred_at: str,
        close_artifact_digest: str | None = None,
        receipt_digest: str = "",
        governance_decision_digest: str = "",
        authorize_write: Callable[[], object],
    ) -> CloseConsumptionState:
        claim = CloseConsumptionClaim.model_validate(claim.model_dump(mode="json"))
        state = CloseConsumptionState.model_validate(state.model_dump(mode="json"))
        existing_events = self._read_events(claim.certificate_id)
        if claim.compatibility_mode != "strict" or any(
            event.compatibility_mode != "strict" for event in existing_events
        ):
            raise SharedStateIntegrityError("previous close schema is read-only")
        event = build_close_event(
            claim,
            sequence=state.revision + 1,
            event_kind=event_kind,
            previous_event_digest=state.head_event_digest,
            occurred_at=occurred_at,
            close_artifact_digest=close_artifact_digest,
            receipt_digest=receipt_digest,
            governance_decision_digest=governance_decision_digest,
        )
        path = self._events_dir(claim.certificate_id) / f"{event.sequence:020d}.json"
        authorize_write()
        if not create_json_exclusive(path, event.model_dump(mode="json")):
            existing = _read_model(path, CloseConsumptionEvent)
            if existing != event:
                raise CloseStoreConflictError("close consumption event sequence fork")
        return _rebuild_state(claim, self._read_events(claim.certificate_id))

    def write_artifact(
        self,
        contract: CloseArtifactContract,
        *,
        authorize_write: Callable[[], object],
    ) -> str:
        trusted = CloseArtifactContract.model_validate(contract.model_dump(mode="json"))
        path = self.artifact_path(trusted.artifact_path)
        payload = dict(trusted.payload)
        authorize_write()
        if (
            not create_json_exclusive(path, payload)
            and read_json_object(path) != payload
        ):
            raise CloseStoreConflictError("formal close artifact content diverged")
        return canonical_digest(payload, CanonicalizationPolicy())

    def require_artifact(
        self,
        contract: CloseArtifactContract,
        expected_digest: str,
    ) -> str:
        trusted = CloseArtifactContract.model_validate(contract.model_dump(mode="json"))
        payload = read_json_object(self.artifact_path(trusted.artifact_path))
        if payload != dict(trusted.payload):
            raise CloseStoreConflictError("formal close artifact content diverged")
        digest = canonical_digest(payload, CanonicalizationPolicy())
        if digest != expected_digest:
            raise CloseStoreConflictError("formal close artifact digest diverged")
        return digest

    def create_receipt(
        self,
        receipt: StageCloseConsumptionReceipt,
        *,
        authorize_write: Callable[[], object],
    ) -> StageCloseConsumptionReceipt:
        trusted = StageCloseConsumptionReceipt.model_validate(
            receipt.model_dump(mode="json")
        )
        path = self.receipts_dir / f"{trusted.claim_id}.json"
        authorize_write()
        if create_json_exclusive(path, trusted.model_dump(mode="json")):
            return trusted
        existing = _read_model(path, StageCloseConsumptionReceipt)
        if existing != trusted:
            raise CloseStoreConflictError("stage close receipt diverged")
        return existing

    def read_receipt(
        self,
        claim_id: str,
    ) -> StageCloseConsumptionReceipt | None:
        path = self.receipts_dir / f"{claim_id}.json"
        if not path.exists():
            return None
        return _read_model(path, StageCloseConsumptionReceipt)

    def claims_for_command(
        self,
        command_id: str,
    ) -> tuple[CloseConsumptionClaim, ...]:
        return tuple(
            claim
            for path in sorted(self.claims_dir.glob("*.json"))
            if (claim := _read_model(path, CloseConsumptionClaim)).command_id
            == command_id
        )

    def materialize(
        self,
        state: CloseConsumptionState,
        *,
        authorize_write: Callable[[], object],
    ) -> None:
        trusted = CloseConsumptionState.model_validate(state.model_dump(mode="json"))
        authorize_write()
        atomic_write_json(
            self.projections_dir / f"{trusted.claim_id}.json",
            trusted.model_dump(mode="json"),
        )

    def projection_is_current(self, state: CloseConsumptionState) -> bool:
        trusted = CloseConsumptionState.model_validate(state.model_dump(mode="json"))
        return self._verify_projection(trusted.certificate_id, trusted)

    def artifact_path(self, relative_path: str) -> Path:
        path = (self.worktree_root / relative_path).resolve()
        try:
            path.relative_to(self.worktree_root)
        except ValueError as exc:
            raise SharedStateIntegrityError("close artifact escapes worktree") from exc
        return path

    def _events_dir(self, certificate_id: str) -> Path:
        return self.events_root / certificate_id

    def _read_events(self, certificate_id: str) -> tuple[CloseConsumptionEvent, ...]:
        directory = self._events_dir(certificate_id)
        if not directory.exists():
            return ()
        return tuple(
            _read_model(path, CloseConsumptionEvent)
            for path in sorted(directory.glob("*.json"))
        )

    def _verify_projection(
        self,
        certificate_id: str,
        rebuilt: CloseConsumptionState,
    ) -> bool:
        path = self.projections_dir / f"{rebuilt.claim_id}.json"
        if not path.exists():
            return False
        try:
            projected = CloseConsumptionState.model_validate(read_json_object(path))
        except (ValidationError, ValueError):
            return False
        if projected.revision > rebuilt.revision:
            raise SharedStateIntegrityError("close state projection is ahead")
        if (
            projected.revision == rebuilt.revision
            and projected.state_digest != rebuilt.state_digest
        ):
            raise SharedStateIntegrityError(
                f"close state projection fork: {certificate_id}"
            )
        return (
            projected.revision == rebuilt.revision
            and projected.state_digest == rebuilt.state_digest
        )


_ModelT = TypeVar("_ModelT", bound=BaseModel)


def _read_model(path: Path, model: type[_ModelT]) -> _ModelT:
    try:
        return decode_transaction_artifact(model, read_json_object(path))
    except (ValidationError, ValueError) as exc:
        raise SharedStateIntegrityError(f"close artifact is invalid: {path}") from exc


def _rebuild_state(
    claim: CloseConsumptionClaim,
    events: tuple[CloseConsumptionEvent, ...],
) -> CloseConsumptionState:
    previous = ""
    kinds: list[CloseEventKind] = []
    artifact_digest = ""
    receipt_digest = ""
    for sequence, event in enumerate(events, start=1):
        _verify_event(
            claim,
            event,
            sequence,
            previous,
            tuple(kinds),
            artifact_digest,
        )
        kinds.append(event.event_kind)
        artifact_digest = event.close_artifact_digest or artifact_digest
        receipt_digest = event.receipt_digest or receipt_digest
        previous = event.event_digest
    status: Literal["consuming", "closed", "aborted"] = "consuming"
    if kinds and kinds[-1] == "committed":
        status = "closed"
    elif kinds and kinds[-1] == "aborted":
        status = "aborted"
    return CloseConsumptionState(
        claim_id=claim.claim_id,
        claim_digest=claim.claim_digest,
        certificate_id=claim.certificate_id,
        consumed_by_command_id=claim.command_id,
        status=status,
        revision=len(events),
        event_kinds=tuple(kinds),
        head_event_digest=previous,
        close_artifact_digest=artifact_digest,
        receipt_digest=receipt_digest,
        closed=status == "closed",
    )


def _verify_event(
    claim: CloseConsumptionClaim,
    event: CloseConsumptionEvent,
    sequence: int,
    previous: str,
    kinds: tuple[CloseEventKind, ...],
    confirmed_artifact_digest: str,
) -> None:
    if event.sequence != sequence or event.previous_event_digest != previous:
        raise SharedStateIntegrityError("close consumption event chain diverged")
    bindings = (
        event.claim_id == claim.claim_id,
        event.claim_digest == claim.claim_digest,
        event.close_intent_digest == claim.close_intent_digest,
        event.artifact_path == claim.artifact_path,
        event.content_contract_digest == claim.content_contract_digest,
        event.resource_reconciliation_digest == claim.resource_reconciliation_digest,
    )
    if not all(bindings):
        raise SharedStateIntegrityError("close consumption event binding diverged")
    if not _transition_allowed(kinds, event.event_kind):
        raise SharedStateIntegrityError("close consumption event transition is invalid")
    if event.event_kind in {"reconciled", "committed"} and (
        event.close_artifact_digest != confirmed_artifact_digest
    ):
        raise SharedStateIntegrityError("close consumption artifact digest changed")
    if event.event_kind == "aborted" and event.close_artifact_digest != (
        confirmed_artifact_digest or None
    ):
        raise SharedStateIntegrityError("aborted close artifact digest changed")


def _transition_allowed(
    kinds: tuple[CloseEventKind, ...],
    target: CloseEventKind,
) -> bool:
    if not kinds:
        return target == "prepared"
    current = kinds[-1]
    allowed: dict[CloseEventKind, tuple[CloseEventKind, ...]] = {
        "prepared": ("close_written", "aborted"),
        "close_written": ("reconciled", "aborted"),
        "reconciled": ("committed", "aborted"),
        "committed": (),
        "aborted": (),
    }
    return target in allowed[current]
