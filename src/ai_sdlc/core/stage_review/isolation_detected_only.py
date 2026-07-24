"""detected_only 的受控一次性污染探针与持久化证据。"""

from __future__ import annotations

import secrets
import shutil
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Self

from pydantic import ConfigDict, field_validator, model_validator

from ai_sdlc.core.stage_review.artifacts import atomic_write_json, read_json_object
from ai_sdlc.core.stage_review.canonical import (
    CanonicalizationPolicy,
    canonical_digest,
)
from ai_sdlc.core.stage_review.contracts import StageReviewArtifactModel
from ai_sdlc.core.stage_review.isolation_models import IsolationEvidenceManifest
from ai_sdlc.core.stage_review.resource_builders import parse_utc, stable_id, utc_iso

if TYPE_CHECKING:
    from ai_sdlc.core.stage_review.isolation_launcher import IsolationLaunchContext

DetectedOnlyStage = Literal["polluted", "cleaned", "cleanup_failed"]
_SENTINEL = b"ai-sdlc-detected-only-sentinel-v1"


class DetectedOnlySentinelEvidence(StageReviewArtifactModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    artifact_kind: Literal["detected-only-sentinel-evidence"] = (
        "detected-only-sentinel-evidence"
    )
    evidence_id: str
    stage: DetectedOnlyStage
    allocation_digest: str
    assignment_digest: str
    layout_digest: str
    host_snapshot_digest: str
    manifest_digest: str
    sentinel_root: str
    sentinel_digest: str
    observed_digest: str
    previous_evidence_digest: str
    cleanup_succeeded: bool
    untrusted_command_started: bool
    recorded_at: str
    evidence_digest: str

    @field_validator("recorded_at")
    @classmethod
    def _timestamp_is_utc(cls, value: str) -> str:
        parse_utc(value)
        return value

    @model_validator(mode="after")
    def _verify_evidence(self) -> Self:
        expected = _evidence_digest(self)
        if self.evidence_digest != expected:
            raise ValueError("detected-only evidence digest does not match content")
        if self.untrusted_command_started:
            raise ValueError("detected-only cannot start an untrusted command")
        if self.stage == "cleaned" and not self.cleanup_succeeded:
            raise ValueError("cleaned evidence must confirm cleanup")
        return self


class DetectedOnlySentinelStore:
    def __init__(self, root: Path) -> None:
        self._root = root / "isolation-execution" / "detected-only-evidence"

    def run(
        self,
        context: IsolationLaunchContext,
        manifest: IsolationEvidenceManifest,
        now: datetime,
    ) -> tuple[DetectedOnlySentinelEvidence, ...]:
        sentinel_root = _sentinel_root(context)
        sentinel_root.mkdir(parents=True, exist_ok=False)
        marker = sentinel_root / "controlled-sentinel.bin"
        marker.write_bytes(_SENTINEL)
        polluted = _build_evidence(
            context,
            manifest,
            sentinel_root,
            stage="polluted",
            observed_digest=_path_digest(sentinel_root),
            previous_evidence_digest="",
            cleanup_succeeded=False,
            now=now,
        )
        try:
            self._persist(polluted, sequence=1)
        except OSError:
            self._cleanup(context, manifest, sentinel_root, polluted, now)
            raise
        cleaned = self._cleanup(context, manifest, sentinel_root, polluted, now)
        self._persist(cleaned, sequence=2)
        return polluted, cleaned

    def evidences(self) -> tuple[DetectedOnlySentinelEvidence, ...]:
        if not self._root.exists():
            return ()
        return tuple(
            DetectedOnlySentinelEvidence.model_validate(read_json_object(path))
            for path in sorted(self._root.glob("*.json"))
        )

    def _cleanup(
        self,
        context: IsolationLaunchContext,
        manifest: IsolationEvidenceManifest,
        root: Path,
        polluted: DetectedOnlySentinelEvidence,
        now: datetime,
    ) -> DetectedOnlySentinelEvidence:
        try:
            shutil.rmtree(root)
            succeeded = not root.exists()
        except OSError:
            succeeded = False
        stage: DetectedOnlyStage = "cleaned" if succeeded else "cleanup_failed"
        return _build_evidence(
            context,
            manifest,
            root,
            stage=stage,
            observed_digest=_path_digest(root),
            previous_evidence_digest=polluted.evidence_digest,
            cleanup_succeeded=succeeded,
            now=now,
        )

    def _persist(
        self,
        evidence: DetectedOnlySentinelEvidence,
        *,
        sequence: int,
    ) -> None:
        name = f"{evidence.evidence_id}.{sequence}.json"
        atomic_write_json(self._root / name, evidence.model_dump(mode="json"))


def _sentinel_root(context: IsolationLaunchContext) -> Path:
    run_root = Path(context.normalized_run_root).resolve(strict=False)
    root = (run_root / "detected-only" / secrets.token_hex(16)).resolve(strict=False)
    forbidden = (
        context.candidate_root,
        context.protected_home_root,
        *context.peer_output_roots,
        *context.protected_config_roots,
    )
    if not root.is_relative_to(run_root):
        raise ValueError("detected-only sentinel escaped disposable run root")
    if any(root.is_relative_to(Path(value)) for value in forbidden):
        raise ValueError("detected-only sentinel overlaps a protected root")
    return root


def _build_evidence(
    context: IsolationLaunchContext,
    manifest: IsolationEvidenceManifest,
    sentinel_root: Path,
    *,
    stage: DetectedOnlyStage,
    observed_digest: str,
    previous_evidence_digest: str,
    cleanup_succeeded: bool,
    now: datetime,
) -> DetectedOnlySentinelEvidence:
    values = {
        "evidence_id": stable_id(
            "detected-only-sentinel",
            context.assignment_digest,
            str(sentinel_root),
        ),
        "stage": stage,
        "allocation_digest": context.allocation_digest,
        "assignment_digest": context.assignment_digest,
        "layout_digest": context.layout_digest,
        "host_snapshot_digest": context.host_snapshot.snapshot_digest,
        "manifest_digest": manifest.manifest_digest,
        "sentinel_root": str(sentinel_root),
        "sentinel_digest": canonical_digest(_SENTINEL.hex(), CanonicalizationPolicy()),
        "observed_digest": observed_digest,
        "previous_evidence_digest": previous_evidence_digest,
        "cleanup_succeeded": cleanup_succeeded,
        "untrusted_command_started": False,
        "recorded_at": utc_iso(now),
    }
    draft = DetectedOnlySentinelEvidence.model_construct(
        **values,  # type: ignore[arg-type]
        evidence_digest="",
    )
    payload = draft.model_dump(mode="json")
    payload["evidence_digest"] = _evidence_digest(draft)
    return DetectedOnlySentinelEvidence.model_validate(payload)


def _path_digest(path: Path) -> str:
    if not path.exists():
        return "missing"
    rows = tuple(
        (str(item.relative_to(path)), item.read_bytes().hex())
        for item in sorted(path.rglob("*"))
        if item.is_file()
    )
    return canonical_digest(rows, CanonicalizationPolicy())


def _evidence_digest(value: object) -> str:
    return canonical_digest(
        value,
        CanonicalizationPolicy(excluded_fields=frozenset({"evidence_digest"})),
    )


__all__ = ["DetectedOnlySentinelEvidence", "DetectedOnlySentinelStore"]
