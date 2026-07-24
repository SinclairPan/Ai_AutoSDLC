"""Opaque runtime allocation 到可信绝对路径布局的唯一解析边界。"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import sys
import sysconfig
from pathlib import Path
from typing import Literal, Protocol, Self

from pydantic import ConfigDict, model_validator

from ai_sdlc.core.source_snapshot import SourceSnapshot
from ai_sdlc.core.source_snapshot_view import materialized_source_view
from ai_sdlc.core.stage_review.binding_models import ReviewerRuntimeAllocation
from ai_sdlc.core.stage_review.candidate import (
    CandidateManifest,
    candidate_binding_digest,
)
from ai_sdlc.core.stage_review.canonical import (
    CanonicalizationPolicy,
    canonical_digest,
)
from ai_sdlc.core.stage_review.contracts import StageReviewArtifactModel
from ai_sdlc.core.stage_review.isolation_layout_identity import _runtime_layout_digest
from ai_sdlc.core.stage_review.source_binding import candidate_source_binding


class IsolationRuntimeLayout(StageReviewArtifactModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    artifact_kind: Literal["isolation-runtime-layout"] = "isolation-runtime-layout"
    allocation_digest: str
    assignment_digest: str
    candidate_digest: str
    normalized_run_root: str
    candidate_root: str
    peer_output_roots: tuple[str, ...]
    disposable_home_root: str
    disposable_config_root: str
    disposable_credential_root: str
    output_root: str
    controller_config_root: str
    protected_home_root: str
    protected_config_roots: tuple[str, ...]
    runtime_read_roots: tuple[str, ...]
    layout_digest: str

    @model_validator(mode="after")
    def _verify_layout(self) -> Self:
        paths = _layout_paths(self)
        if any(not value or not Path(value).is_absolute() for value in paths):
            raise ValueError("isolation runtime layout paths must be absolute")
        if any(str(Path(value).resolve(strict=False)) != value for value in paths):
            raise ValueError("isolation runtime layout paths must be canonical")
        if self.peer_output_roots != tuple(sorted(set(self.peer_output_roots))):
            raise ValueError("isolation peer roots are not canonical")
        if self.layout_digest != _runtime_layout_digest(self):
            raise ValueError("isolation runtime layout digest does not match content")
        _verify_write_boundaries(self)
        return self


class AllocationPathResolver(Protocol):
    def resolve(
        self,
        allocation: ReviewerRuntimeAllocation,
        *,
        peer_allocations: tuple[ReviewerRuntimeAllocation, ...],
        assignment_digest: str,
    ) -> IsolationRuntimeLayout: ...


class FilesystemAllocationPathResolver:
    """仅把 opaque ID 作为摘要输入，绝不把它解释为宿主路径。"""

    def __init__(
        self,
        root: Path,
        *,
        protected_home_root: Path | None = None,
        protected_config_roots: tuple[Path, ...] | None = None,
    ) -> None:
        self._root = root.resolve(strict=False)
        home = (protected_home_root or Path.home()).resolve(strict=False)
        configs = protected_config_roots or _default_protected_configs(home)
        self._protected_home = home
        self._protected_configs = tuple(
            sorted(str(item.resolve(strict=False)) for item in configs)
        )

    def resolve(
        self,
        allocation: ReviewerRuntimeAllocation,
        *,
        peer_allocations: tuple[ReviewerRuntimeAllocation, ...],
        assignment_digest: str,
    ) -> IsolationRuntimeLayout:
        _verify_candidate_snapshot(self._root, allocation)
        allocation_root = self._root / "allocations" / _opaque_key(
            allocation.allocation_digest
        )
        values = _layout_values(
            self._root,
            allocation_root,
            allocation,
            peer_allocations,
            assignment_digest,
            self._protected_home,
            self._protected_configs,
        )
        draft = IsolationRuntimeLayout.model_construct(
            **values,  # type: ignore[arg-type]
            layout_digest="",
        )
        return IsolationRuntimeLayout.model_validate(
            {**values, "layout_digest": _runtime_layout_digest(draft)}
        )

    def materialize_candidate_snapshot(
        self,
        allocation: ReviewerRuntimeAllocation,
        source_root: Path,
        *,
        candidate: CandidateManifest,
        source_snapshot: SourceSnapshot,
    ) -> Path:
        source = source_root.resolve(strict=True)
        trusted_candidate = CandidateManifest.model_validate(
            candidate.model_dump(mode="json")
        )
        trusted_snapshot = SourceSnapshot.model_validate(
            source_snapshot.model_dump(mode="json")
        )
        _validate_candidate_authority(
            allocation,
            source,
            trusted_candidate,
            trusted_snapshot,
        )
        target = _candidate_root(self._root, allocation)
        if target.exists():
            _verify_candidate_snapshot(self._root, allocation)
            return target
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            with materialized_source_view(source, trusted_snapshot) as frozen_source:
                _reject_source_symlinks(frozen_source)
                shutil.copytree(frozen_source, target, symlinks=True)
            _finalize_candidate_snapshot(target, allocation, trusted_candidate)
            _verify_candidate_snapshot(self._root, allocation)
        except BaseException:
            shutil.rmtree(target, ignore_errors=True)
            raise
        return target

    def provision_runtime(self, allocation: ReviewerRuntimeAllocation) -> None:
        allocation_root = self._root / "allocations" / _opaque_key(
            allocation.allocation_digest
        )
        for name in ("run", "home", "child-config", "credentials", "output"):
            (allocation_root / name).mkdir(parents=True, exist_ok=True)


def _reject_source_symlinks(root: Path) -> None:
    for directory, names, files in os.walk(root, followlinks=False):
        parent = Path(directory)
        for name in (*names, *files):
            if (parent / name).is_symlink():
                raise ValueError("materialized source view contains a symlink")


def _finalize_candidate_snapshot(
    target: Path,
    allocation: ReviewerRuntimeAllocation,
    candidate: CandidateManifest,
) -> None:
    _remove_review_artifacts(target)
    marker = {
        "candidate_snapshot_id": allocation.candidate_snapshot_id,
        "candidate_digest": allocation.candidate_manifest_digest,
        "source_tree_digest": candidate.source_tree_digest,
        "tree_digest": _candidate_tree_digest(target),
    }
    (target / ".ai-sdlc-candidate-snapshot.json").write_text(
        json.dumps(marker, sort_keys=True),
        encoding="utf-8",
    )


def _layout_values(
    root: Path,
    allocation_root: Path,
    allocation: ReviewerRuntimeAllocation,
    peers: tuple[ReviewerRuntimeAllocation, ...],
    assignment_digest: str,
    protected_home: Path,
    protected_configs: tuple[str, ...],
) -> dict[str, object]:
    return {
        "allocation_digest": allocation.allocation_digest,
        "assignment_digest": assignment_digest,
        "candidate_digest": allocation.candidate_manifest_digest,
        "normalized_run_root": str((allocation_root / "run").resolve(strict=False)),
        "candidate_root": str(_candidate_root(root, allocation)),
        "peer_output_roots": _peer_roots(root, peers),
        "disposable_home_root": str((allocation_root / "home").resolve(strict=False)),
        "disposable_config_root": str((allocation_root / "child-config").resolve(strict=False)),
        "disposable_credential_root": str((allocation_root / "credentials").resolve(strict=False)),
        "output_root": str((allocation_root / "output").resolve(strict=False)),
        "controller_config_root": str((root / "controller" / _opaque_key(allocation.allocation_digest)).resolve(strict=False)),
        "protected_home_root": str(protected_home),
        "protected_config_roots": protected_configs,
        "runtime_read_roots": _runtime_read_roots(),
    }


def _candidate_root(root: Path, allocation: ReviewerRuntimeAllocation) -> Path:
    return (root / "candidates" / _opaque_key(allocation.candidate_snapshot_id)).resolve(
        strict=False
    )


def _verify_candidate_snapshot(
    root: Path,
    allocation: ReviewerRuntimeAllocation,
) -> None:
    target = _candidate_root(root, allocation)
    marker_path = target / ".ai-sdlc-candidate-snapshot.json"
    try:
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError) as exc:
        raise ValueError("candidate snapshot is not materialized") from exc
    expected = {
        "candidate_snapshot_id": allocation.candidate_snapshot_id,
        "candidate_digest": allocation.candidate_manifest_digest,
        "source_tree_digest": marker.get("source_tree_digest"),
        "tree_digest": _candidate_tree_digest(target),
    }
    if not marker.get("source_tree_digest") or marker != expected:
        raise ValueError("candidate snapshot identity or digest is invalid")


def _candidate_tree_digest(root: Path) -> str:
    rows: list[tuple[str, str]] = []
    for item in sorted(root.rglob("*")):
        if item.name == ".ai-sdlc-candidate-snapshot.json":
            continue
        if item.is_symlink():
            raise ValueError("candidate snapshot cannot contain symlinks")
        if item.is_file():
            relative = str(item.relative_to(root))
            rows.append((relative, hashlib.sha256(item.read_bytes()).hexdigest()))
    return canonical_digest(rows, CanonicalizationPolicy())


def _remove_review_artifacts(target: Path) -> None:
    review_root = target / ".ai-sdlc" / "reviews"
    if review_root.exists():
        shutil.rmtree(review_root)


def _validate_candidate_authority(
    allocation: ReviewerRuntimeAllocation,
    source_root: Path,
    candidate: CandidateManifest,
    snapshot: SourceSnapshot,
) -> None:
    if candidate_binding_digest(candidate) != allocation.candidate_manifest_digest:
        raise ValueError("candidate snapshot manifest binding is invalid")
    binding = candidate_source_binding(
        snapshot,
        candidate.review_artifact_exclusion_set,
        candidate.protected_source_set,
        candidate.policy_digests,
    )
    expected = (
        candidate.source_snapshot_digest,
        candidate.source_tree_digest,
        candidate.change_surface_digest,
    )
    if expected != (
        binding.snapshot_digest,
        binding.source_tree_digest,
        binding.change_surface_digest,
    ):
        raise ValueError("candidate snapshot source binding is invalid")
    _verify_snapshot_files(source_root, snapshot)


def _verify_snapshot_files(root: Path, snapshot: SourceSnapshot) -> None:
    for relative, expected in snapshot.file_digests.items():
        target = (root / relative).resolve(strict=False)
        if not target.is_relative_to(root) or not target.is_file():
            raise ValueError("candidate snapshot source file is missing")
        actual = f"sha256:{hashlib.sha256(target.read_bytes()).hexdigest()}"
        if actual != expected:
            raise ValueError("candidate snapshot source tree is invalid")


def _peer_roots(
    root: Path,
    peers: tuple[ReviewerRuntimeAllocation, ...],
) -> tuple[str, ...]:
    return tuple(
        sorted(
            str(
                (
                    root
                    / "allocations"
                    / _opaque_key(item.allocation_digest)
                    / "output"
                ).resolve(strict=False)
            )
            for item in peers
        )
    )


def _runtime_read_roots() -> tuple[str, ...]:
    executable = Path(sys.executable)
    configured = (
        executable.parent,
        executable.resolve().parent,
        Path(sys.prefix),
        Path(sys.base_prefix),
        Path(sysconfig.get_path("stdlib")),
        Path(sysconfig.get_path("platstdlib")),
    )
    roots = {item.resolve(strict=False) for item in configured}
    if platform.system().lower() == "darwin":
        command_line_tools = Path("/Library/Developer/CommandLineTools")
        if command_line_tools.is_dir():
            roots.add(command_line_tools.resolve(strict=False))
    return tuple(sorted(str(item) for item in roots))


def _default_protected_configs(home: Path) -> tuple[Path, ...]:
    return (
        home / ".codex",
        home / ".gitconfig",
        home / ".profile",
        home / ".zshrc",
    )


def _opaque_key(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _layout_paths(layout: IsolationRuntimeLayout) -> tuple[str, ...]:
    return (
        layout.normalized_run_root,
        layout.candidate_root,
        layout.disposable_home_root,
        layout.disposable_config_root,
        layout.disposable_credential_root,
        layout.output_root,
        layout.controller_config_root,
        layout.protected_home_root,
        *layout.peer_output_roots,
        *layout.protected_config_roots,
        *layout.runtime_read_roots,
    )


def _verify_write_boundaries(layout: IsolationRuntimeLayout) -> None:
    run = Path(layout.normalized_run_root)
    controller = Path(layout.controller_config_root)
    if controller.is_relative_to(run) or run.is_relative_to(controller):
        raise ValueError("controller config must be outside reviewer run root")
    # HOME deny 允许可信 allocation root 使用更具体规则 carve-out；配置路径不得重叠。
    protected = layout.protected_config_roots
    writable = (
        layout.normalized_run_root,
        layout.disposable_home_root,
        layout.disposable_config_root,
        layout.disposable_credential_root,
        layout.output_root,
    )
    for left in protected:
        for right in writable:
            if _paths_overlap(Path(left), Path(right)):
                raise ValueError("protected and writable isolation roots overlap")


def _paths_overlap(left: Path, right: Path) -> bool:
    return left == right or left.is_relative_to(right) or right.is_relative_to(left)


__all__ = [
    "AllocationPathResolver",
    "FilesystemAllocationPathResolver",
    "IsolationRuntimeLayout",
]
