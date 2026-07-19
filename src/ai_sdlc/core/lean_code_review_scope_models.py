"""Durable models for a closed Lean PR-review evidence scope."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

IMPLEMENTATION_CLOSE_PROOF_NAME = "implementation-close-proof.json"
IMPLEMENTATION_CLOSE_PROOF_CREATOR = "ai-sdlc+implementation-close-proof-v1"
LEAN_CLOSED_SCOPE_NAME = "lean-closed-scope.json"


class FrozenArtifact(BaseModel):
    """Path and exact byte digest for one persisted review input."""

    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1)
    digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class ClosedLeanReviewScope(BaseModel):
    """Strongly typed evidence set retained for a closed Lean binding."""

    model_config = ConfigDict(extra="forbid")

    implementation_loop_id: str = Field(min_length=1)
    work_item_id: str = Field(min_length=1)
    close: FrozenArtifact
    close_proof: FrozenArtifact | None = None
    implementation_report: FrozenArtifact
    lean_pointer: FrozenArtifact
    lean_report: FrozenArtifact
    lean_report_markdown: FrozenArtifact
    lean_input: FrozenArtifact
    lean_snapshot: FrozenArtifact
    lean_findings: FrozenArtifact
    lean_policy: FrozenArtifact
    diff_hash: str = Field(min_length=1)
    policy_digest: str = Field(min_length=1)


class ImplementationCloseProof(BaseModel):
    """Forward-compatible sidecar that binds schema-v1 close artifacts."""

    model_config = ConfigDict(extra="forbid")

    proof_kind: Literal["implementation-close-proof"] = "implementation-close-proof"
    implementation_loop_id: str = Field(min_length=1)
    work_item_id: str = Field(min_length=1)
    close: FrozenArtifact
    implementation_report: FrozenArtifact


__all__ = [
    "ClosedLeanReviewScope",
    "FrozenArtifact",
    "IMPLEMENTATION_CLOSE_PROOF_CREATOR",
    "IMPLEMENTATION_CLOSE_PROOF_NAME",
    "ImplementationCloseProof",
    "LEAN_CLOSED_SCOPE_NAME",
]
