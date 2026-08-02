"""Canonical Stage Review 与项目本地优化观测的唯一接线。"""

from __future__ import annotations

from pathlib import Path

from ai_sdlc.core.stage_review.candidate import CandidateManifest
from ai_sdlc.core.stage_review.optimization.runtime import build_optimization_runtime
from ai_sdlc.core.stage_review.optimization.session_coordinator import (
    SessionOptimizationCoordinator,
)
from ai_sdlc.core.stage_review.session_contracts import SessionTrustResolver


def build_session_optimization_coordinator(
    root: Path,
    *,
    candidate: CandidateManifest,
    resolver: SessionTrustResolver,
) -> SessionOptimizationCoordinator:
    runtime = build_optimization_runtime(root)
    if runtime.project_id != candidate.project_id:
        raise ValueError("optimization runtime project identity diverged")
    size_bucket = classify_candidate_size(candidate)
    return runtime.session_coordinator(
        resolver,
        candidate_size_classifier=lambda _digest: size_bucket,
    )


def classify_candidate_size(candidate: CandidateManifest) -> str:
    paths = set(candidate.change_surface)
    paths.update(candidate.input_artifacts)
    paths.update(candidate.output_artifacts)
    count = len(paths)
    if count <= 10:
        return "small"
    if count <= 50:
        return "medium"
    return "large"


__all__ = ["build_session_optimization_coordinator", "classify_candidate_size"]
