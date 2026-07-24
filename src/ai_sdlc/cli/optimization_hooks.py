"""普通用户命令与项目本地离线优化之间的非阻断接线。"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from ai_sdlc.core.stage_review.artifacts import resolve_repository_project_id
from ai_sdlc.core.stage_review.optimization.controller_models import (
    OptimizationMaintenanceResult,
)
from ai_sdlc.core.stage_review.optimization.foreground import ForegroundExecutionLease
from ai_sdlc.core.stage_review.optimization.runtime import (
    _run_bounded_optimization_maintenance as run_bounded_optimization_maintenance,
)


@contextmanager
def foreground_optimization_scope(root: Path, *, dry_run: bool) -> Iterator[None]:
    """前台命令优先；标记故障不能阻断用户开发主路径。"""

    if dry_run:
        yield
        return
    try:
        lease = ForegroundExecutionLease(
            root,
            project_id=resolve_repository_project_id(root),
        )
        lease.__enter__()
    except Exception:
        yield
        return
    try:
        yield
    finally:
        lease.__exit__(None, None, None)


def _maintain_optimization_after_run(
    root: Path,
    *,
    dry_run: bool,
) -> OptimizationMaintenanceResult | None:
    """每次真实 run 最多推进一个安全检查点，普通失败不改变命令结果。"""

    if dry_run:
        return None
    try:
        return run_bounded_optimization_maintenance(root)
    except Exception:
        return None
