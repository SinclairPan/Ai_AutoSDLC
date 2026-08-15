"""Pure, read-only inputs and values for bounded dynamic expert review."""

from __future__ import annotations

import hashlib
import json
import os
import stat
from collections.abc import Sequence
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

LoopReviewType = Literal[
    "requirement",
    "design-contract",
    "implementation",
    "frontend-evidence",
    "local-pr-review",
]
ReviewSeverity = Literal["blocker", "important", "advisory"]
ReviewExecutionStatus = Literal["completed", "failed"]

_ROLE_BRIEF = "Choose one primary expert and at most one cross-risk expert."
_SEVERITY_RANK: dict[ReviewSeverity, int] = {
    "advisory": 0,
    "important": 1,
    "blocker": 2,
}


class ReviewInput(BaseModel):
    """One immutable view of the substantive result to inspect."""

    model_config = ConfigDict(extra="forbid")

    loop_id: str
    loop_type: LoopReviewType
    round_number: int = Field(ge=1)
    input_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    artifact_paths: list[str] = Field(min_length=1)
    upstream_context_paths: list[str] = Field(default_factory=list)
    risk_signals: list[str] = Field(default_factory=list)
    role_brief: str = _ROLE_BRIEF

    @field_validator("loop_id", "role_brief")
    @classmethod
    def _require_text(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("review input text is required")
        return text

    @field_validator("artifact_paths", "upstream_context_paths", "risk_signals")
    @classmethod
    def _require_unique_values(cls, values: list[str]) -> list[str]:
        normalized = [value.strip() for value in values]
        if any(not value for value in normalized):
            raise ValueError("review input values cannot be empty")
        if len(normalized) != len(set(normalized)):
            raise ValueError("review input values must be unique")
        return normalized

    @model_validator(mode="after")
    def _paths_cannot_overlap(self) -> ReviewInput:
        if set(self.artifact_paths) & set(self.upstream_context_paths):
            raise ValueError("artifact and upstream paths cannot overlap")
        return self


class ReviewFinding(BaseModel):
    """One actionable observation from an ephemeral expert."""

    model_config = ConfigDict(extra="forbid")

    severity: ReviewSeverity
    role: str
    location: str
    summary: str
    recommendation: str

    @field_validator("role", "location", "summary", "recommendation")
    @classmethod
    def _require_finding_text(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("review finding text is required")
        return text


class ReviewExecution(BaseModel):
    """In-memory execution status and findings; never a close credential."""

    model_config = ConfigDict(extra="forbid")

    status: ReviewExecutionStatus
    roles: list[str] = Field(default_factory=list, max_length=2)
    role_reasons: dict[str, str] = Field(default_factory=dict)
    findings: list[ReviewFinding] = Field(default_factory=list)
    failure_kind: str = ""
    failure_reason: str = ""

    @model_validator(mode="after")
    def _execution_shape_matches_status(self) -> ReviewExecution:
        normalized_roles = [role.strip() for role in self.roles]
        if any(not role for role in normalized_roles):
            raise ValueError("expert role cannot be empty")
        if len(normalized_roles) != len(set(normalized_roles)):
            raise ValueError("expert roles must be unique")
        self.roles = normalized_roles

        normalized_reasons = {
            role.strip(): reason.strip() for role, reason in self.role_reasons.items()
        }
        if set(normalized_reasons) != set(normalized_roles):
            raise ValueError("every expert role requires exactly one reason")
        if any(not reason for reason in normalized_reasons.values()):
            raise ValueError("expert role reason cannot be empty")
        self.role_reasons = normalized_reasons

        absent_roles = {finding.role for finding in self.findings} - set(
            normalized_roles
        )
        if absent_roles:
            raise ValueError("finding role must be present in the execution")

        if self.status == "completed":
            if not normalized_roles:
                raise ValueError("completed review requires at least one expert")
            if self.failure_kind.strip() or self.failure_reason.strip():
                raise ValueError("completed review cannot carry failure state")
        else:
            if not self.failure_kind.strip() or not self.failure_reason.strip():
                raise ValueError("failed review requires failure kind and reason")
            if self.findings:
                raise ValueError("failed review cannot be treated as findings")
        return self


def build_review_input(
    root: Path,
    *,
    loop_id: str,
    loop_type: LoopReviewType,
    round_number: int,
    artifact_paths: Sequence[str | Path],
    upstream_context_paths: Sequence[str | Path],
    risk_signals: Sequence[str],
) -> ReviewInput:
    """Read stable regular files and bind their raw bytes to one review input."""

    resolved_root = root.resolve(strict=True)
    artifacts = _read_paths(resolved_root, artifact_paths)
    upstream = _read_paths(resolved_root, upstream_context_paths)
    all_paths = [path for path, _, _, _ in (*artifacts, *upstream)]
    if len(all_paths) != len(set(all_paths)):
        raise ValueError("review paths must be unique")

    normalized_signals = [signal.strip() for signal in risk_signals]
    if any(not signal for signal in normalized_signals):
        raise ValueError("risk signal cannot be empty")
    if len(normalized_signals) != len(set(normalized_signals)):
        raise ValueError("risk signals must be unique")

    digest_payload = {
        "loop_id": loop_id.strip(),
        "loop_type": loop_type,
        "round_number": round_number,
        "artifacts": [
            _digest_record(path, mode, size, digest)
            for path, mode, size, digest in artifacts
        ],
        "upstream": [
            _digest_record(path, mode, size, digest)
            for path, mode, size, digest in upstream
        ],
        "risk_signals": normalized_signals,
    }
    encoded = json.dumps(
        digest_payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return ReviewInput(
        loop_id=loop_id,
        loop_type=loop_type,
        round_number=round_number,
        input_digest=hashlib.sha256(encoded).hexdigest(),
        artifact_paths=[path for path, _, _, _ in artifacts],
        upstream_context_paths=[path for path, _, _, _ in upstream],
        risk_signals=normalized_signals,
    )


def merge_expert_findings(executions: Sequence[ReviewExecution]) -> ReviewExecution:
    """Combine completed expert outputs without interpreting them as a verdict."""

    if not executions:
        raise ValueError("at least one expert execution is required")
    failed = [execution for execution in executions if execution.status == "failed"]
    if failed:
        roles, reasons = _merge_roles(executions)
        return ReviewExecution(
            status="failed",
            roles=roles,
            role_reasons=reasons,
            failure_kind="expert-execution-failed",
            failure_reason="; ".join(
                f"{item.failure_kind}: {item.failure_reason}" for item in failed
            ),
        )

    roles, reasons = _merge_roles(executions)
    findings: list[ReviewFinding] = []
    seen: dict[tuple[str, str, str], int] = {}
    for execution in executions:
        for finding in execution.findings:
            identity = (
                finding.role,
                finding.location,
                finding.summary,
            )
            existing_index = seen.get(identity)
            if existing_index is None:
                seen[identity] = len(findings)
                findings.append(finding)
                continue
            existing = findings[existing_index]
            if _SEVERITY_RANK[finding.severity] > _SEVERITY_RANK[existing.severity]:
                findings[existing_index] = existing.model_copy(
                    update={"severity": finding.severity}
                )
    return ReviewExecution(
        status="completed",
        roles=roles,
        role_reasons=reasons,
        findings=findings,
    )


def _read_paths(
    root: Path,
    paths: Sequence[str | Path],
) -> list[tuple[str, int, int, str]]:
    records: list[tuple[str, int, int, str]] = []
    for raw_path in paths:
        candidate = Path(raw_path)
        path = candidate if candidate.is_absolute() else root / candidate
        try:
            original = path.lstat()
        except FileNotFoundError as exc:
            raise ValueError(f"review path is missing: {raw_path}") from exc
        if stat.S_ISLNK(original.st_mode):
            raise ValueError(f"review path is not a regular file: {raw_path}")
        try:
            resolved = path.resolve(strict=True)
        except FileNotFoundError as exc:
            raise ValueError(f"review path is missing: {raw_path}") from exc
        try:
            relative = resolved.relative_to(root).as_posix()
        except ValueError as exc:
            raise ValueError(f"review path escapes project: {raw_path}") from exc

        before = resolved.stat(follow_symlinks=False)
        if not stat.S_ISREG(before.st_mode) or resolved.is_symlink():
            raise ValueError(f"review path is not a regular file: {relative}")
        descriptor = os.open(resolved, os.O_RDONLY | getattr(os, "O_BINARY", 0))
        try:
            opened = os.fstat(descriptor)
            digest = hashlib.sha256()
            content_size = 0
            while chunk := os.read(descriptor, 1024 * 1024):
                digest.update(chunk)
                content_size += len(chunk)
            after = os.fstat(descriptor)
        finally:
            os.close(descriptor)
        closed = resolved.stat(follow_symlinks=False)
        if not _file_snapshot_is_stable(
            before,
            opened,
            after,
            closed,
            content_size=content_size,
        ):
            raise ValueError(f"review path changed while reading: {relative}")
        records.append(
            (relative, stat.S_IMODE(opened.st_mode), content_size, digest.hexdigest())
        )
    records.sort(key=lambda item: item[0])
    return records


def _file_snapshot_is_stable(
    before: os.stat_result,
    opened: os.stat_result,
    after: os.stat_result,
    closed: os.stat_result,
    *,
    content_size: int,
) -> bool:
    def identity(item: os.stat_result) -> tuple[int, int, int, int, int]:
        return (
            item.st_dev,
            item.st_ino,
            item.st_size,
            item.st_mtime_ns,
            stat.S_IMODE(item.st_mode),
        )

    identities = {identity(item) for item in (before, opened, after, closed)}
    return len(identities) == 1 and content_size == opened.st_size


def _digest_record(
    path: str,
    mode: int,
    size: int,
    digest: str,
) -> dict[str, object]:
    return {
        "path": path,
        "mode": mode,
        "size": size,
        "sha256": digest,
    }


def _merge_roles(
    executions: Sequence[ReviewExecution],
) -> tuple[list[str], dict[str, str]]:
    roles: list[str] = []
    reasons: dict[str, str] = {}
    for execution in executions:
        for role in execution.roles:
            if role not in roles:
                roles.append(role)
                reasons[role] = execution.role_reasons[role]
    if len(roles) > 2:
        raise ValueError("merged review cannot contain more than two roles")
    return roles, reasons


__all__ = [
    "ReviewExecution",
    "ReviewFinding",
    "ReviewInput",
    "build_review_input",
    "merge_expert_findings",
]
