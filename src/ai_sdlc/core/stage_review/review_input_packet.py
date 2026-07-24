"""从冻结 SourceSnapshot 构建最小、内容寻址的 Reviewer 输入包。"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ai_sdlc.core.source_change_capture import capture_path_changes
from ai_sdlc.core.source_snapshot import SourceSnapshot
from ai_sdlc.core.stage_review.artifacts import (
    create_json_exclusive,
    read_json_object,
    resolve_canonical_shared_state,
)
from ai_sdlc.core.stage_review.candidate import (
    CandidateManifest,
    candidate_binding_digest,
)
from ai_sdlc.core.stage_review.canonical import CanonicalizationPolicy, canonical_digest
from ai_sdlc.core.stage_review.panel_plan_models import ReviewerSlot
from ai_sdlc.core.stage_review.source_binding import (
    _require_fresh_protected_snapshot,
)
from ai_sdlc.core.stage_review.source_binding import (
    _source_snapshot_binding_digest as source_snapshot_binding_digest,
)

_CONFIG = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)
_MAX_PACKET_BYTES = 512 * 1024


class ReviewPathState(BaseModel):
    model_config = _CONFIG

    mode: str
    encoding: Literal["utf-8", "binary", "absent"]
    text: str = ""
    content_digest: str
    byte_count: int = Field(ge=0)


class ReviewPathChange(BaseModel):
    model_config = _CONFIG

    path: str
    before: ReviewPathState
    after: ReviewPathState


class ReviewInputPacket(BaseModel):
    model_config = _CONFIG

    schema_version: Literal["review-input-packet.v1"] = "review-input-packet.v1"
    candidate_manifest_digest: str
    source_snapshot_digest: str
    slot_id: str
    role_profile_id: str
    role_contract_digest: str
    capability_ids: tuple[str, ...]
    blocking_authorities: tuple[str, ...]
    primary_dimensions: tuple[str, ...]
    prompt_template_digest: str
    independent_initial_review: Literal[True] = True
    changes: tuple[ReviewPathChange, ...]
    packet_digest: str = ""

    @model_validator(mode="after")
    def _verify_packet(self) -> Self:
        paths = tuple(item.path for item in self.changes)
        if not paths or paths != tuple(sorted(set(paths))):
            raise ValueError("review input packet paths are not canonical")
        expected = canonical_digest(
            self,
            CanonicalizationPolicy(excluded_fields=frozenset({"packet_digest"})),
        )
        if self.packet_digest and self.packet_digest != expected:
            raise ValueError("review input packet digest is invalid")
        if not self.packet_digest:
            object.__setattr__(self, "packet_digest", expected)
        return self


class ReviewInputPacketSet(BaseModel):
    model_config = _CONFIG

    schema_version: Literal["review-input-packet-set.v1"] = (
        "review-input-packet-set.v1"
    )
    project_id: str
    review_session_id: str
    candidate_manifest_digest: str
    source_snapshot_digest: str
    packet_digests: tuple[str, ...]
    packet_set_digest: str = ""

    @model_validator(mode="after")
    def _verify_set(self) -> Self:
        if not self.packet_digests or self.packet_digests != tuple(
            sorted(set(self.packet_digests))
        ):
            raise ValueError("review input packet set is not canonical")
        expected = canonical_digest(
            self,
            CanonicalizationPolicy(
                excluded_fields=frozenset({"packet_set_digest"})
            ),
        )
        if self.packet_set_digest and self.packet_set_digest != expected:
            raise ValueError("review input packet set digest is invalid")
        if not self.packet_set_digest:
            object.__setattr__(self, "packet_set_digest", expected)
        return self


def build_review_input_packet(
    root: Path,
    *,
    candidate: CandidateManifest,
    source_snapshot: SourceSnapshot,
    slot: ReviewerSlot,
) -> ReviewInputPacket:
    _require_fresh_protected_snapshot(
        root,
        source_snapshot,
        list(candidate.review_artifact_exclusion_set),
    )
    _require_snapshot_binding(candidate, source_snapshot)
    captured = capture_path_changes(root, source_snapshot)
    changes = tuple(
        ReviewPathChange(
            path=path,
            before=_path_state(change.before.mode, change.before.payload),
            after=_path_state(change.after.mode, change.after.payload),
        )
        for path, change in sorted(captured.items())
    )
    _require_packet_size(changes)
    return ReviewInputPacket(
        candidate_manifest_digest=candidate_binding_digest(candidate),
        source_snapshot_digest=candidate.source_snapshot_digest,
        slot_id=slot.slot_id,
        role_profile_id=slot.role_profile_id,
        role_contract_digest=slot.role_contract_digest,
        capability_ids=slot.capability_ids,
        blocking_authorities=slot.blocking_authority,
        primary_dimensions=slot.primary_dimensions,
        prompt_template_digest=slot.prompt_template_digest,
        changes=changes,
    )


def persist_review_input_packets(
    root: Path,
    *,
    candidate: CandidateManifest,
    packets: tuple[ReviewInputPacket, ...],
) -> ReviewInputPacketSet:
    packet_set = ReviewInputPacketSet(
        project_id=candidate.project_id,
        review_session_id=candidate.review_session_id,
        candidate_manifest_digest=candidate_binding_digest(candidate),
        source_snapshot_digest=candidate.source_snapshot_digest,
        packet_digests=tuple(sorted(item.packet_digest for item in packets)),
    )
    directory = (
        resolve_canonical_shared_state(root, candidate.project_id)
        / "review-input-packets"
        / candidate.review_session_id
    )
    for packet in packets:
        _persist_packet(directory / f"{packet.slot_id}.json", packet)
    _persist_packet(directory / "packet-set.json", packet_set)
    return packet_set


def load_review_input_packets(
    root: Path,
    *,
    project_id: str,
    review_session_id: str,
) -> tuple[ReviewInputPacketSet, tuple[ReviewInputPacket, ...]] | None:
    directory = (
        resolve_canonical_shared_state(root, project_id)
        / "review-input-packets"
        / review_session_id
    )
    set_path = directory / "packet-set.json"
    if not set_path.is_file():
        return None
    packet_set = ReviewInputPacketSet.model_validate(read_json_object(set_path))
    packets = tuple(
        ReviewInputPacket.model_validate(read_json_object(path))
        for path in sorted(directory.glob("*.json"))
        if path.name != "packet-set.json"
    )
    if (
        packet_set.project_id != project_id
        or packet_set.review_session_id != review_session_id
        or tuple(sorted(item.packet_digest for item in packets))
        != packet_set.packet_digests
    ):
        raise ValueError("review input packet set lineage diverged")
    return packet_set, packets


def review_provider_payload(
    packet: ReviewInputPacket,
    packet_set: ReviewInputPacketSet,
) -> dict[str, object]:
    return {
        "schema": "review-provider-request.v1",
        "packet_set_digest": packet_set.packet_set_digest,
        "packet": packet.model_dump(mode="json"),
        "instructions": (
            "Independently review only this frozen candidate. Return passed only "
            "when no actionable finding exists. Declare reviewed, uncovered, and "
            "evidence-gap areas; do not claim absolute completeness."
        ),
        "required_response_schema": "remote-review-provider-response.v1",
    }


def _require_snapshot_binding(
    candidate: CandidateManifest,
    source_snapshot: SourceSnapshot,
) -> None:
    actual = source_snapshot_binding_digest(
        source_snapshot,
        exclusions=candidate.review_artifact_exclusion_set,
        protected_source_set=candidate.protected_source_set,
        policy_digests=candidate.policy_digests,
    )
    if actual != candidate.source_snapshot_digest:
        raise ValueError("review source snapshot diverged from candidate")


def _path_state(mode: str, payload: bytes) -> ReviewPathState:
    if not mode and not payload:
        return ReviewPathState(
            mode="",
            encoding="absent",
            content_digest=_digest(payload),
            byte_count=0,
        )
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError:
        return ReviewPathState(
            mode=mode,
            encoding="binary",
            content_digest=_digest(payload),
            byte_count=len(payload),
        )
    return ReviewPathState(
        mode=mode,
        encoding="utf-8",
        text=text,
        content_digest=_digest(payload),
        byte_count=len(payload),
    )


def _require_packet_size(changes: tuple[ReviewPathChange, ...]) -> None:
    total = sum(
        item.before.byte_count + item.after.byte_count
        for item in changes
        if item.before.encoding == "utf-8" or item.after.encoding == "utf-8"
    )
    if total > _MAX_PACKET_BYTES:
        raise ValueError("review input packet exceeds the bounded source budget")


def _digest(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _persist_packet(path: Path, value: BaseModel) -> None:
    payload = value.model_dump(mode="json")
    if create_json_exclusive(path, payload):
        return
    if read_json_object(path) != payload:
        raise ValueError("review input packet identity fork")


__all__ = [
    "ReviewInputPacket",
    "ReviewInputPacketSet",
    "ReviewPathChange",
    "build_review_input_packet",
    "load_review_input_packets",
    "persist_review_input_packets",
    "review_provider_payload",
]
