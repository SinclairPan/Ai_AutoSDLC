"""异常点值对象与有界前缀去重。"""

from __future__ import annotations

import ast
from collections.abc import Iterable
from dataclasses import dataclass

from ai_sdlc.core.lean_code_path_summary import _overflow_prefixes


@dataclass(frozen=True)
class _ExceptionPoint:
    prefix: tuple[ast.stmt, ...]
    raised: str | None = None


def _deduplicate_prefixes(
    prefixes: Iterable[tuple[ast.stmt, ...]],
) -> list[tuple[ast.stmt, ...]]:
    unique: dict[tuple[str, ...], tuple[ast.stmt, ...]] = {}
    for prefix in prefixes:
        key = tuple(ast.dump(statement) for statement in prefix)
        unique.setdefault(key, prefix)
    if len(unique) > 64:
        return _overflow_prefixes(tuple(unique.values()))
    return list(unique.values())


__all__: list[str] = []
