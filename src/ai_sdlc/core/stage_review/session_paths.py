"""Session scope 的安全目录映射。"""

from __future__ import annotations

import re
from pathlib import Path

from ai_sdlc.core.stage_review.finding_models import FindingScope
from ai_sdlc.core.stage_review.session_contracts import SessionIntegrityError

_SCOPE_IDENTITY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def _scope_parts(scope: FindingScope) -> tuple[str, str, str]:
    return scope.work_item_id, scope.stage_instance_id, scope.session_id


def _session_scope_root(
    root: Path,
    project_id: str,
    scope: FindingScope,
) -> Path:
    if scope.project_id != project_id:
        raise SessionIntegrityError("session project lineage mismatch")
    parts = _scope_parts(scope)
    if any(_SCOPE_IDENTITY.fullmatch(item) is None for item in parts):
        raise ValueError("session scope identity is invalid")
    return root / "sessions" / parts[0] / parts[1] / parts[2]
