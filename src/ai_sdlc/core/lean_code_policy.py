"""Lean Code policy projection and stable artifact digest helpers."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from ai_sdlc.core.lean_code_models import LeanEnforcementMode, LeanPolicy
from ai_sdlc.core.loop_policy import load_loop_policy

STRUCTURED_EXCEPTION_RULES = frozenset(
    {
        "lean.bugfix-regression",
        "lean.classification-unknown",
        "lean.file-budget",
        "lean.function-budget",
        "lean.function-risk",
        "lean.generated-scope",
        "lean.invocation-boundary",
        "lean.public-callers",
        "lean.semantic-capability",
    }
)


def load_lean_policy(root: Path) -> LeanPolicy:
    """Project the canonical LoopPolicyProfile into a persisted Lean snapshot."""

    source = load_loop_policy(root)
    return LeanPolicy(
        enforcement_mode=LeanEnforcementMode(source.lean_enforcement_mode),
        max_rounds=source.lean_max_rounds,
        file_line_budget=source.lean_file_line_budget,
        function_line_budget=source.lean_function_line_budget,
        complexity_budget=source.lean_complexity_budget,
        complexity_delta=source.lean_complexity_delta,
        nesting_budget=source.lean_nesting_budget,
        fan_out_budget=source.lean_fan_out_budget,
        fan_out_delta=source.lean_fan_out_delta,
        public_caller_minimum=source.lean_public_caller_minimum,
        generated_files_per_task_budget=source.lean_generated_files_per_task_budget,
        significant_changed_lines=source.lean_significant_changed_lines,
        significant_changed_ratio=source.lean_significant_changed_ratio,
    )


def stable_artifact_digest(model: BaseModel) -> str:
    """Hash stable model content while excluding provenance timestamps."""

    payload = _without_provenance(model.model_dump(mode="json"))
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return f"sha256:{hashlib.sha256(encoded.encode('utf-8')).hexdigest()}"


def _without_provenance(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _without_provenance(item)
            for key, item in value.items()
            if key not in {"created_at", "ai_sdlc_version"}
        }
    if isinstance(value, list):
        return [_without_provenance(item) for item in value]
    return value


__all__ = [
    "STRUCTURED_EXCEPTION_RULES",
    "load_lean_policy",
    "stable_artifact_digest",
]
