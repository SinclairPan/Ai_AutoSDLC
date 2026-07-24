"""普通 ai-sdlc run 对离线优化发出的跨进程抢占信号。"""

from __future__ import annotations

import os
from pathlib import Path
from types import TracebackType
from uuid import uuid4

from ai_sdlc.core.stage_review.artifacts import (
    SharedStateIntegrityError,
    create_json_exclusive,
    resolve_canonical_shared_state,
)
from ai_sdlc.core.stage_review.artifacts import (
    _file_lock_is_active as file_lock_is_active,
)


class ForegroundExecutionLease:
    def __init__(
        self,
        root: Path,
        *,
        project_id: str,
    ) -> None:
        shared = resolve_canonical_shared_state(root, project_id)
        request_id = f"{os.getpid()}.{uuid4().hex}"
        self.path = (
            shared
            / "offline-optimization"
            / "foreground-requests"
            / f"{request_id}.json"
        )
        self._entered = False

    def __enter__(self) -> ForegroundExecutionLease:
        if self._entered or not create_json_exclusive(self.path, {"pid": os.getpid()}):
            raise SharedStateIntegrityError("foreground execution marker collided")
        self._entered = True
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc_value, traceback
        if self._entered:
            self.path.unlink(missing_ok=True)
            self._entered = False


def _foreground_execution_requested(root: Path, *, project_id: str) -> bool:
    shared = resolve_canonical_shared_state(root, project_id)
    request_root = shared / "offline-optimization" / "foreground-requests"
    return any(file_lock_is_active(path) for path in request_root.glob("*.json"))
