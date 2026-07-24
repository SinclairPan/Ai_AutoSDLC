"""CandidateManifest 与既有 SourceSnapshot 的不可伪造绑定。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar, Literal

from pydantic import Field, field_validator, model_validator

from ai_sdlc.core.source_snapshot import SourceSnapshot
from ai_sdlc.core.stage_review.canonical import (
    CanonicalizationPolicy,
    canonical_digest,
    normalize_repo_path,
)
from ai_sdlc.core.stage_review.contracts import (
    _CURRENT_SCHEMA_VERSION,
    _PREVIOUS_SCHEMA_VERSION,
    StageReviewArtifactModel,
)
from ai_sdlc.core.stage_review.source_binding import (
    _artifact_digests,
    _is_portable_alias_at_or_below,
    _outside_roots,
    _require_fresh_protected_snapshot,
    _review_session_root,
    candidate_source_binding,
)

_CANDIDATE_POLICY = CanonicalizationPolicy(
    excluded_fields=frozenset({"created_at", "created_by", "ai_sdlc_version"}),
    set_like_fields=frozenset(
        {
            "input_artifacts",
            "output_artifacts",
            "change_surface",
            "test_evidence_digests",
            "policy_digests",
            "toolchain_ids",
            "target_platform_ids",
            "protected_source_set",
            "review_artifact_exclusion_set",
        }
    ),
    path_fields=frozenset(
        {
            "input_artifacts",
            "output_artifacts",
            "change_surface",
            "protected_source_set",
            "review_artifact_exclusion_set",
        }
    ),
)
@dataclass(frozen=True, slots=True)
class CandidateBuildContext:
    """五类 Stage Adapter 共享的非持久化 Candidate 构建输入。"""

    work_item_id: str
    project_id: str
    loop_id: str
    loop_round_number: int
    stage_key: str
    stage_instance_id: str
    review_session_id: str
    adapter_id: str
    adapter_version: str
    adapter_contract_digest: str
    input_artifacts: tuple[str, ...]
    output_artifacts: tuple[str, ...]
    test_evidence_digests: tuple[str, ...]
    policy_digests: tuple[str, ...]
    toolchain_ids: tuple[str, ...]
    target_platform_ids: tuple[str, ...]
    protected_source_set: tuple[str, ...]
    extensions: Mapping[str, object] | None = None


class CandidateManifest(StageReviewArtifactModel):
    """某个阶段关闭请求所评审的完整、不可变候选身份。"""

    artifact_kind: Literal["candidate-manifest"] = "candidate-manifest"
    identity_fields: ClassVar[tuple[str, ...]] = (
        "work_item_id",
        "project_id",
        "loop_id",
        "loop_round_number",
        "stage_key",
        "stage_instance_id",
        "review_session_id",
        "adapter_id",
        "adapter_version",
        "adapter_contract_digest",
    )
    digest_covered_fields: ClassVar[tuple[str, ...]] = (
        "schema_version",
        "artifact_kind",
        "extensions",
        "canonicalization_version",
        "compatibility_mode",
        "work_item_id",
        "project_id",
        "loop_id",
        "loop_round_number",
        "stage_key",
        "stage_instance_id",
        "review_session_id",
        "adapter_id",
        "adapter_version",
        "adapter_contract_digest",
        "input_artifacts",
        "input_digests",
        "output_artifacts",
        "output_digests",
        "change_surface",
        "change_surface_digest",
        "source_snapshot_digest",
        "source_tree_digest",
        "test_evidence_digests",
        "policy_digests",
        "toolchain_ids",
        "target_platform_ids",
        "protected_source_set",
        "review_artifact_exclusion_set",
    )
    work_item_id: str
    project_id: str
    loop_id: str
    loop_round_number: int = Field(ge=1)
    stage_key: str
    stage_instance_id: str
    review_session_id: str
    adapter_id: str
    adapter_version: str
    adapter_contract_digest: str
    input_artifacts: list[str] = Field(default_factory=list)
    input_digests: dict[str, str] = Field(default_factory=dict)
    output_artifacts: list[str] = Field(default_factory=list)
    output_digests: dict[str, str] = Field(default_factory=dict)
    change_surface: list[str] = Field(default_factory=list)
    test_evidence_digests: list[str] = Field(default_factory=list)
    policy_digests: list[str] = Field(default_factory=list)
    toolchain_ids: list[str] = Field(default_factory=list)
    target_platform_ids: list[str] = Field(default_factory=list)
    protected_source_set: list[str] = Field(default_factory=list)
    review_artifact_exclusion_set: list[str] = Field(default_factory=list)
    source_snapshot_digest: str
    source_tree_digest: str
    change_surface_digest: str

    @field_validator(
        "work_item_id",
        "project_id",
        "loop_id",
        "stage_key",
        "stage_instance_id",
        "review_session_id",
        "adapter_id",
        "adapter_version",
        "adapter_contract_digest",
        "source_snapshot_digest",
        "source_tree_digest",
        "change_surface_digest",
    )
    @classmethod
    def _require_identity(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("candidate identity values must not be empty")
        return value.strip()

    @field_validator(
        "input_artifacts",
        "output_artifacts",
        "change_surface",
        "protected_source_set",
        "review_artifact_exclusion_set",
    )
    @classmethod
    def _normalize_paths(cls, values: list[str]) -> list[str]:
        return sorted({normalize_repo_path(value) for value in values})

    @field_validator(
        "test_evidence_digests",
        "policy_digests",
        "toolchain_ids",
        "target_platform_ids",
    )
    @classmethod
    def _normalize_sets(cls, values: list[str]) -> list[str]:
        return sorted({value.strip() for value in values if value.strip()})

    @field_validator("input_digests", "output_digests")
    @classmethod
    def _normalize_digest_paths(cls, values: dict[str, str]) -> dict[str, str]:
        normalized: dict[str, str] = {}
        for raw_path, digest in values.items():
            path = normalize_repo_path(raw_path)
            clean_digest = digest.strip()
            if not clean_digest:
                raise ValueError(f"artifact digest must not be empty: {path}")
            if path in normalized and normalized[path] != clean_digest:
                raise ValueError(f"conflicting digests for normalized path: {path}")
            normalized[path] = clean_digest
        return dict(sorted(normalized.items()))

    @model_validator(mode="after")
    def _verify_coverage_and_exclusions(self) -> CandidateManifest:
        if set(self.input_artifacts) != set(self.input_digests):
            raise ValueError("input artifacts and digests must cover the same paths")
        if set(self.output_artifacts) != set(self.output_digests):
            raise ValueError("output artifacts and digests must cover the same paths")
        expected_root = _review_session_root(
            self.project_id,
            self.work_item_id,
            self.stage_instance_id,
            self.review_session_id,
        )
        exclusions = self.review_artifact_exclusion_set
        if exclusions != [expected_root]:
            raise ValueError("candidate exclusion does not match current review identity")
        protected = set(self.input_artifacts + self.output_artifacts + self.change_surface)
        if any(
            _is_portable_alias_at_or_below(path, excluded)
            for path in protected
            for excluded in exclusions
        ):
            raise ValueError("review artifact cannot appear in candidate protected artifacts")
        if any(
            _outside_roots(path, self.protected_source_set)
            for path in self.change_surface
        ):
            raise ValueError("candidate change is outside protected_source_set")
        return self


def candidate_binding_digest(candidate: CandidateManifest) -> str:
    """生成 ReviewPass 与 Certificate 必须绑定的候选语义摘要。"""

    return canonical_digest(candidate, _CANDIDATE_POLICY)


def build_candidate_manifest(
    *,
    root: Path,
    source_snapshot: SourceSnapshot,
    context: CandidateBuildContext,
) -> CandidateManifest:
    """验证当前仓库真值后构建 Candidate。"""

    exclusions = _context_exclusions(context)
    _require_fresh_protected_snapshot(root, source_snapshot, exclusions)
    return _build_candidate_manifest(root, source_snapshot, context)


def _build_candidate_manifest(
    root: Path,
    source_snapshot: SourceSnapshot,
    context: CandidateBuildContext,
) -> CandidateManifest:
    """对已经验证新鲜度的 SourceSnapshot 构建 Candidate。"""

    exclusions = _context_exclusions(context)
    roots, normalized_policy_digests = _normalized_candidate_inputs(context)
    source_binding = candidate_source_binding(
        source_snapshot,
        exclusions,
        roots,
        normalized_policy_digests,
    )
    input_digests = _artifact_digests(
        root, source_snapshot, list(context.input_artifacts)
    )
    output_digests = _artifact_digests(
        root, source_snapshot, list(context.output_artifacts)
    )
    return CandidateManifest(
        work_item_id=context.work_item_id,
        project_id=context.project_id,
        loop_id=context.loop_id,
        loop_round_number=context.loop_round_number,
        stage_key=context.stage_key,
        stage_instance_id=context.stage_instance_id,
        review_session_id=context.review_session_id,
        adapter_id=context.adapter_id,
        adapter_version=context.adapter_version,
        adapter_contract_digest=context.adapter_contract_digest,
        input_artifacts=list(context.input_artifacts),
        input_digests=input_digests,
        output_artifacts=list(context.output_artifacts),
        output_digests=output_digests,
        change_surface=source_binding.change_surface,
        test_evidence_digests=list(context.test_evidence_digests),
        policy_digests=normalized_policy_digests,
        toolchain_ids=list(context.toolchain_ids),
        target_platform_ids=list(context.target_platform_ids),
        protected_source_set=roots,
        review_artifact_exclusion_set=exclusions,
        source_snapshot_digest=source_binding.snapshot_digest,
        source_tree_digest=source_binding.source_tree_digest,
        change_surface_digest=source_binding.change_surface_digest,
        extensions=dict(context.extensions or {}),
    )


def _normalized_candidate_inputs(
    context: CandidateBuildContext,
) -> tuple[list[str], list[str]]:
    roots = sorted(
        {normalize_repo_path(path) for path in context.protected_source_set}
    )
    policy_digests = sorted(
        {digest.strip() for digest in context.policy_digests if digest.strip()}
    )
    return roots, policy_digests


def read_candidate_manifest(
    payload: Mapping[str, object],
    *,
    root: Path,
    source_snapshot: SourceSnapshot,
    context: CandidateBuildContext,
    expected_legacy_digest: str | None = None,
) -> CandidateManifest:
    """验证仓库新鲜度后读取并重放 Candidate。"""

    _candidate_schema_version(payload)
    _require_fresh_protected_snapshot(
        root,
        source_snapshot,
        _context_exclusions(context),
    )
    return _read_candidate_manifest(
        payload,
        root=root,
        source_snapshot=source_snapshot,
        context=context,
        expected_legacy_digest=expected_legacy_digest,
    )


def _read_candidate_manifest(
    payload: Mapping[str, object],
    *,
    root: Path,
    source_snapshot: SourceSnapshot,
    context: CandidateBuildContext,
    expected_legacy_digest: str | None = None,
) -> CandidateManifest:
    """对已经验证新鲜度的 SourceSnapshot 重放 Candidate。"""

    version = _candidate_schema_version(payload)
    if version == _PREVIOUS_SCHEMA_VERSION:
        from ai_sdlc.core.stage_review.legacy import _migrate_legacy_candidate

        return _migrate_legacy_candidate(
            payload,
            root,
            source_snapshot,
            context,
            expected_legacy_digest,
        )
    candidate = CandidateManifest.model_validate(payload)
    rebuilt = _build_candidate_manifest(root, source_snapshot, context)
    if candidate_binding_digest(candidate) != candidate_binding_digest(rebuilt):
        raise ValueError("candidate source snapshot binding does not match current source")
    return candidate


def _candidate_schema_version(payload: Mapping[str, object]) -> str:
    """在任何仓库 I/O 前完成 Candidate Schema 路由。"""

    version = str(payload.get("schema_version", ""))
    kind = str(payload.get("artifact_kind", ""))
    if kind != "candidate-manifest" or version not in {
        _CURRENT_SCHEMA_VERSION,
        _PREVIOUS_SCHEMA_VERSION,
    }:
        raise ValueError(f"unknown stage-review schema: {kind}/{version}")
    return version


def _context_exclusions(context: CandidateBuildContext) -> list[str]:
    return [
        _review_session_root(
            context.project_id,
            context.work_item_id,
            context.stage_instance_id,
            context.review_session_id,
        )
    ]
