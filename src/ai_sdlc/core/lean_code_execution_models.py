"""Data contracts for controlled Lean command execution."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from ai_sdlc.core.loop_models import LoopArtifactModel


class LeanCommandExecutionReceipt(LoopArtifactModel):
    """Runtime-written proof that one argv was executed against one source view."""

    artifact_kind: str = "lean-command-execution-receipt"
    receipt_id: str
    loop_id: str
    purpose: str
    command_argv: list[str]
    cwd: str = "."
    source_snapshot_ref: str
    source_snapshot_digest: str
    diff_hash: str
    exit_code: int
    output_ref: str
    output_digest: str
    test_source_ref: str = ""
    test_source_digest: str = ""
    failure_signature: str = ""
    runner_adapter: str = ""
    toolchain_fingerprint: str
    toolchain_executable: str = ""
    toolchain_executable_digest: str = ""
    environment_fingerprint: str = ""
    started_at: str
    finished_at: str
    accepted: bool = False


class LeanExecutionResult(BaseModel):
    """CLI-facing result for one controlled command invocation."""

    model_config = ConfigDict(extra="forbid")

    status: str
    result: str
    blocker: str = ""
    next_action: str = ""
    receipt_path: str = ""
    receipt_digest: str = ""
    output_path: str = ""
    exit_code: int = 0
    command_argv: list[str] = Field(default_factory=list)


@dataclass(frozen=True)
class LeanExecutionOptions:
    """Inputs for shell-free subprocess execution and receipt capture."""

    root: Path
    loop_id: str
    purpose: str
    command_argv: tuple[str, ...]
    cwd: str = "."
    receipt_id: str = ""
    test_source_ref: str = ""
    failure_signature: str = ""
    source_kind: str = "local-unstaged"
    base_ref: str = ""
    head_ref: str = "HEAD"
    patch_file: str = ""
    timeout_seconds: float = 300.0


__all__ = [
    "LeanCommandExecutionReceipt",
    "LeanExecutionOptions",
    "LeanExecutionResult",
]
