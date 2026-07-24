"""Dispatch 幂等槽、assignment 与隔离证据的共享存储。"""

from __future__ import annotations

import re
from pathlib import Path

from ai_sdlc.core.stage_review.artifacts import (
    SharedStateIntegrityError,
    ShortFileLock,
)
from ai_sdlc.core.stage_review.binding_artifact_io import persist_model, read_model
from ai_sdlc.core.stage_review.binding_models import IsolationExecutionEvidence
from ai_sdlc.core.stage_review.binding_result_models import ReviewerDispatchAssignment

_ASSIGNMENT_ID = re.compile(r"^reviewer-dispatch\.[0-9a-f]{24}$")


class BindingDispatchStoreMixin:
    root: Path
    lock_timeout_seconds: float

    def dispatch_lock(self, assignment_id: str) -> ShortFileLock:
        self._validate_assignment_id(assignment_id)
        path = self.root / "dispatch-slots" / f"{assignment_id}.lock"
        return ShortFileLock(path, timeout_seconds=self.lock_timeout_seconds)

    def persist_dispatch_evidence(
        self,
        assignment_id: str,
        evidence: IsolationExecutionEvidence,
    ) -> None:
        persist_model(
            self._dispatch_evidence_path(assignment_id),
            evidence,
            IsolationExecutionEvidence,
            evidence.isolation_evidence_digest,
            "dispatch isolation evidence",
        )

    def get_dispatch_evidence(
        self,
        assignment_id: str,
    ) -> IsolationExecutionEvidence | None:
        return read_model(
            self._dispatch_evidence_path(assignment_id),
            IsolationExecutionEvidence,
            "dispatch isolation evidence",
        )

    def persist_dispatch_assignment(
        self,
        assignment: ReviewerDispatchAssignment,
    ) -> None:
        persist_model(
            self._dispatch_assignment_path(assignment.assignment_id),
            assignment,
            ReviewerDispatchAssignment,
            assignment.assignment_digest,
            "dispatch assignment",
        )

    def get_dispatch_assignment(
        self,
        assignment_id: str,
    ) -> ReviewerDispatchAssignment | None:
        return read_model(
            self._dispatch_assignment_path(assignment_id),
            ReviewerDispatchAssignment,
            "dispatch assignment",
        )

    def find_dispatch_assignment(
        self,
        assignment_digest: str,
    ) -> ReviewerDispatchAssignment | None:
        directory = self.root / "dispatch-assignments"
        if not directory.exists():
            return None
        matches = [
            item
            for path in sorted(directory.glob("*.json"))
            if (
                item := read_model(
                    path, ReviewerDispatchAssignment, "dispatch assignment"
                )
            )
            is not None
            and item.assignment_digest == assignment_digest
        ]
        if len(matches) > 1:
            raise SharedStateIntegrityError("dispatch assignment digest is not unique")
        return matches[0] if matches else None

    def _dispatch_assignment_path(self, assignment_id: str) -> Path:
        self._validate_assignment_id(assignment_id)
        return self.root / "dispatch-assignments" / f"{assignment_id}.json"

    def _dispatch_evidence_path(self, assignment_id: str) -> Path:
        self._validate_assignment_id(assignment_id)
        return self.root / "dispatch-evidence" / f"{assignment_id}.json"

    @staticmethod
    def _validate_assignment_id(assignment_id: str) -> None:
        if _ASSIGNMENT_ID.fullmatch(assignment_id) is None:
            raise ValueError("reviewer dispatch assignment identity is invalid")
