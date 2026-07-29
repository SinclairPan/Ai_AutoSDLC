"""解析 Lean 指标的语言与声明范围。"""

from __future__ import annotations

import fnmatch
from pathlib import Path


def _language(path: str) -> str:
    suffix = Path(path).suffix.lower()
    if suffix == ".py":
        return "python"
    if suffix in {".ts", ".tsx"}:
        return "typescript"
    return {".java": "java", ".go": "go"}.get(suffix, "unknown")


def _in_scope(path: str, scope: tuple[str, ...]) -> bool:
    normalized = path.replace("\\", "/")
    for item in scope:
        pattern = item.strip().strip("`").replace("\\", "/").rstrip("/")
        if not pattern:
            continue
        if fnmatch.fnmatchcase(normalized, pattern):
            return True
        if not any(token in pattern for token in "*?[") and normalized.startswith(
            f"{pattern}/"
        ):
            return True
    return False


__all__: list[str] = []
