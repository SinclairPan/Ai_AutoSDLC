"""为优化终态观测读取同一 Session 的可信 FindingEvent 摘要。"""

from __future__ import annotations

from pathlib import Path

from ai_sdlc.core.stage_review.artifacts import (
    SharedStateIntegrityError,
    read_json_object,
    resolve_canonical_shared_state,
)
from ai_sdlc.core.stage_review.finding_artifact_codec import decode_finding_event
from ai_sdlc.core.stage_review.finding_models import FindingEvent
from ai_sdlc.core.stage_review.registry_versions import require_machine_id


class FindingEventLineageReader:
    def __init__(self, root: Path, *, project_id: str) -> None:
        self.project_id = require_machine_id(project_id, "project_id")
        shared = resolve_canonical_shared_state(root, self.project_id)
        self.root = shared / "finding-ledgers" / "sessions"

    def event_digests(self, session_id: str) -> tuple[str, ...]:
        return tuple(sorted(item.event_digest for item in self.events(session_id)))

    def events(self, session_id: str) -> tuple[FindingEvent, ...]:
        stable = require_machine_id(session_id, "session_id")
        directories = tuple(self.root.glob(f"*/*/{stable}/events"))
        if len(directories) > 1:
            raise SharedStateIntegrityError("finding session identity is ambiguous")
        if not directories:
            return ()
        events = tuple(
            decode_finding_event(read_json_object(path))
            for path in sorted(directories[0].glob("*.json"))
        )
        if any(
            item.scope.project_id != self.project_id
            or item.scope.session_id != stable
            for item in events
        ):
            raise SharedStateIntegrityError("finding session lineage diverged")
        return tuple(sorted(events, key=lambda item: item.sequence))
