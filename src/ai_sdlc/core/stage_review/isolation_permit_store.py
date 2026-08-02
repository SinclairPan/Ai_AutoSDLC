"""隔离 Permit 的单次消费、收据与执行观察持久化。"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from ai_sdlc.core.stage_review.artifacts import (
    SharedStateIntegrityError,
    atomic_write_json,
    create_json_exclusive,
    portable_content_digest_name,
    read_json_object,
)
from ai_sdlc.core.stage_review.isolation_models import (
    IsolationEvidenceManifest,
    IsolationExecutionObservation,
    IsolationExecutionPermit,
    IsolationExecutionReceipt,
)
from ai_sdlc.core.stage_review.isolation_models import (
    _receipt_digest as receipt_digest,
)
from ai_sdlc.core.stage_review.resource_builders import parse_utc, stable_id, utc_iso


class IsolationPermitRefused(RuntimeError):  # noqa: N818
    pass


class IsolationPermitStore:
    def __init__(self, root: Path) -> None:
        self.root = root / "isolation-execution"

    def consume(
        self,
        permit: IsolationExecutionPermit,
        *,
        allocation_digest: str,
        assignment_digest: str,
        candidate_digest: str,
        host_snapshot_digest: str,
        backend_instance_id: str,
        backend_epoch: str,
        layout_digest: str,
        now: datetime,
    ) -> None:
        reason = _permit_refusal_reason(
            permit,
            allocation_digest=allocation_digest,
            assignment_digest=assignment_digest,
            candidate_digest=candidate_digest,
            host_snapshot_digest=host_snapshot_digest,
            backend_instance_id=backend_instance_id,
            backend_epoch=backend_epoch,
            layout_digest=layout_digest,
            now=now,
        )
        if reason:
            self._refuse(permit, reason, now)
        marker = self.root / "consumed" / f"{permit.permit_id}.json"
        if not create_json_exclusive(marker, permit.model_dump(mode="json")):
            self._refuse(permit, "isolation.permit-already-consumed", now)

    def persist_receipt(self, receipt: IsolationExecutionReceipt) -> None:
        path = self.root / "receipts" / f"{receipt.receipt_id}.json"
        atomic_write_json(path, receipt.model_dump(mode="json"))

    def persist_manifest(self, manifest: IsolationEvidenceManifest) -> None:
        name = portable_content_digest_name(manifest.manifest_digest)
        path = self.root / "manifests" / f"{name}.json"
        if create_json_exclusive(path, manifest.model_dump(mode="json")):
            return
        existing = IsolationEvidenceManifest.model_validate(read_json_object(path))
        if existing.manifest_digest != manifest.manifest_digest:
            raise SharedStateIntegrityError("isolation manifest fork detected")

    def persist_observation(
        self,
        observation: IsolationExecutionObservation,
    ) -> None:
        path = (
            self.root
            / "observations"
            / observation.permit_digest.replace(":", "-")
            / f"{observation.stage}.json"
        )
        if create_json_exclusive(path, observation.model_dump(mode="json")):
            return
        existing = IsolationExecutionObservation.model_validate(
            read_json_object(path)
        )
        if existing.observation_digest != observation.observation_digest:
            raise SharedStateIntegrityError("isolation observation fork detected")

    def observations(self) -> tuple[IsolationExecutionObservation, ...]:
        directory = self.root / "observations"
        if not directory.exists():
            return ()
        return tuple(
            IsolationExecutionObservation.model_validate(read_json_object(path))
            for path in sorted(directory.glob("*/*.json"))
        )

    def has_incomplete_execution(self, assignment_digest: str) -> bool:
        by_permit: dict[str, set[str]] = {}
        for item in self.observations():
            if item.assignment_digest == assignment_digest:
                by_permit.setdefault(item.permit_digest, set()).add(item.stage)
        return any(
            "completed" in stages
            and not ({"cleaned", "cleanup_failed"} & stages)
            for stages in by_permit.values()
        )

    def receipts(self) -> tuple[IsolationExecutionReceipt, ...]:
        directory = self.root / "receipts"
        if not directory.exists():
            return ()
        return tuple(
            IsolationExecutionReceipt.model_validate(read_json_object(path))
            for path in sorted(directory.glob("*.json"))
        )

    def _refuse(
        self,
        permit: IsolationExecutionPermit,
        reason: str,
        now: datetime,
    ) -> None:
        self.persist_receipt(build_refusal_receipt(permit, reason=reason, now=now))
        raise IsolationPermitRefused(reason.removeprefix("isolation.permit-"))


def build_refusal_receipt(
    permit: IsolationExecutionPermit,
    *,
    reason: str,
    now: datetime,
) -> IsolationExecutionReceipt:
    recorded_at = utc_iso(now)
    values = _receipt_identity(permit, reason, recorded_at)
    draft = IsolationExecutionReceipt.model_construct(
        **values,  # type: ignore[arg-type]
        receipt_digest="",
    )
    payload = draft.model_dump(mode="json")
    payload["receipt_digest"] = receipt_digest(draft)
    return IsolationExecutionReceipt.model_validate(payload)


def _receipt_identity(
    permit: IsolationExecutionPermit,
    reason: str,
    recorded_at: str,
) -> dict[str, object]:
    return {
        "receipt_id": stable_id(
            "isolation-receipt", permit.permit_digest, reason, recorded_at
        ),
        "permit_digest": permit.permit_digest,
        "manifest_digest": permit.manifest_digest,
        "release_manifest_digest": permit.release_manifest_digest,
        "runtime_identity_digest": permit.runtime_identity_digest,
        "allocation_digest": permit.allocation_digest,
        "assignment_digest": permit.assignment_digest,
        "candidate_digest": permit.candidate_digest,
        "host_snapshot_digest": permit.host_snapshot_digest,
        "backend_id": permit.backend_id,
        "backend_version": permit.backend_version,
        "backend_instance_id": permit.backend_instance_id,
        "backend_epoch": permit.backend_epoch,
        "layout_digest": permit.layout_digest,
        "command_kind": "refusal",
        "command_started": False,
        "process_id": 0,
        "parent_process_id": 0,
        "boundary_results": (),
        "os_native_denials": (),
        "before_digest": "",
        "after_digest": "",
        "cleanup_succeeded": True,
        "reason_id": reason,
        "recorded_at": recorded_at,
    }


def _permit_refusal_reason(
    permit: IsolationExecutionPermit,
    *,
    allocation_digest: str,
    assignment_digest: str,
    candidate_digest: str,
    host_snapshot_digest: str,
    backend_instance_id: str,
    backend_epoch: str,
    layout_digest: str,
    now: datetime,
) -> str:
    if not parse_utc(permit.issued_at) <= now < parse_utc(permit.expires_at):
        return "isolation.permit-expired"
    if host_snapshot_digest != permit.host_snapshot_digest:
        return "isolation.permit-host-stale"
    if backend_instance_id != permit.backend_instance_id:
        return "isolation.permit-backend-instance-stale"
    if backend_epoch != permit.backend_epoch:
        return "isolation.permit-backend-epoch-stale"
    if layout_digest != permit.layout_digest:
        return "isolation.permit-layout-stale"
    identities = (
        allocation_digest == permit.allocation_digest,
        assignment_digest == permit.assignment_digest,
        candidate_digest == permit.candidate_digest,
    )
    return "" if all(identities) else "isolation.permit-identity-mismatch"
