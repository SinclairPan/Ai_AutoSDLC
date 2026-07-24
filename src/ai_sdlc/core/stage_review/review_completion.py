"""已授权 Review Session 的不可变完成证明。"""

from __future__ import annotations

from pathlib import Path
from typing import Literal, Self

from pydantic import ConfigDict, model_validator

from ai_sdlc.core.stage_review.artifact_compat import (
    ArtifactCompatibility,
    fill_artifact_digest,
)
from ai_sdlc.core.stage_review.artifacts import create_json_exclusive, read_json_object
from ai_sdlc.core.stage_review.finding_models import FindingScope
from ai_sdlc.core.stage_review.session_models import StageReviewSession


class ReviewSessionCompletion(ArtifactCompatibility):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["review-session-completion.v1"] = (
        "review-session-completion.v1"
    )
    artifact_kind: Literal["review-session-completion"] = "review-session-completion"
    scope: FindingScope
    session_digest: str
    session_head_event_digest: str
    candidate_manifest_digest: str
    panel_plan_digest: str
    binding_set_digest: str
    initial_review_seal_digest: str
    finding_ledger_digest: str
    required_pass_digests: tuple[str, ...]
    completed_at: str
    completion_digest: str = ""

    @model_validator(mode="after")
    def _verify_completion(self) -> Self:
        if not self.required_pass_digests or self.required_pass_digests != tuple(
            sorted(set(self.required_pass_digests))
        ):
            raise ValueError("review completion pass set is invalid")
        return fill_artifact_digest(self, "completion_digest")


def build_review_completion(
    session: StageReviewSession,
    *,
    completed_at: str,
) -> ReviewSessionCompletion:
    if session.state != "authorized" or len(session.initial_seal_refs) != 1:
        raise ValueError("review session is not authorized")
    return ReviewSessionCompletion(
        scope=session.scope,
        session_digest=session.session_digest,
        session_head_event_digest=session.head_event_digest,
        candidate_manifest_digest=session.active_candidate_digest,
        panel_plan_digest=session.active_plan_digest,
        binding_set_digest=session.active_binding_set_digest,
        initial_review_seal_digest=session.initial_seal_refs[0].artifact_digest,
        finding_ledger_digest=session.finding_ledger_digest,
        required_pass_digests=tuple(
            sorted(item.pass_digest for item in session.pass_refs)
        ),
        completed_at=completed_at,
    )


def persist_review_completion(
    session_path: Path,
    completion: ReviewSessionCompletion,
) -> Path:
    path = session_path.parent / "completion.json"
    payload = completion.model_dump(mode="json")
    if create_json_exclusive(path, payload):
        return path
    existing = ReviewSessionCompletion.model_validate(read_json_object(path))
    if existing != completion:
        raise ValueError("review completion identity fork")
    return path


def read_review_completion(
    session_path: Path,
) -> ReviewSessionCompletion | None:
    path = session_path.parent / "completion.json"
    if not path.exists():
        return None
    return ReviewSessionCompletion.model_validate(read_json_object(path))


__all__ = [
    "ReviewSessionCompletion",
    "build_review_completion",
    "persist_review_completion",
    "read_review_completion",
]
