"""Session 派生投影的读取、分叉检测与可修复写入。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Protocol, cast

from pydantic import ValidationError

from ai_sdlc.core.stage_review.artifacts import (
    SharedStateIntegrityError,
    atomic_write_json,
    read_json_object,
)
from ai_sdlc.core.stage_review.finding_models import FindingScope
from ai_sdlc.core.stage_review.session_artifact_codec import decode_session_artifact
from ai_sdlc.core.stage_review.session_contracts import SessionIntegrityError
from ai_sdlc.core.stage_review.session_models import StageReviewSession


class _SessionProjectionStoreHost(Protocol):
    def projection_path(self, scope: FindingScope) -> Path: ...


class SessionProjectionStoreMixin:
    def _repair_projection(
        self,
        scope: FindingScope,
        rebuilt: StageReviewSession | None,
    ) -> None:
        host = cast(_SessionProjectionStoreHost, self)
        path = host.projection_path(scope)
        if rebuilt is None:
            if path.exists():
                raise SessionIntegrityError(
                    "session projection exists without event truth"
                )
            return
        persisted = _read_projection(path)
        if persisted is not None and persisted.revision > rebuilt.revision:
            raise SessionIntegrityError("session projection is ahead of event truth")
        if persisted is not None and persisted.revision == rebuilt.revision:
            if persisted.compatibility_mode == "read-only-legacy":
                atomic_write_json(path, rebuilt.model_dump(mode="json"))
                return
            if persisted.session_digest != rebuilt.session_digest:
                raise SessionIntegrityError("session projection digest fork")
            return
        atomic_write_json(path, rebuilt.model_dump(mode="json"))


def _read_projection(path: Path) -> StageReviewSession | None:
    if not path.exists():
        return None
    try:
        return decode_session_artifact(
            StageReviewSession,
            read_json_object(path),
        )
    except SessionIntegrityError:
        raise
    except (
        json.JSONDecodeError,
        ValidationError,
        ValueError,
        SharedStateIntegrityError,
    ):
        return None
