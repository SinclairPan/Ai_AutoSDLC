"""保存类成员能力、描述符类型与分支合并规则。"""

from __future__ import annotations

import ast
from collections.abc import Collection
from dataclasses import dataclass

from ai_sdlc.core.lean_code_import_descriptor_scope import (
    _resolved_descriptor_kinds,
)


@dataclass(frozen=True)
class _Member:
    dynamic: bool = False
    raw_dynamic: bool = False
    bound_dynamic: bool = False
    class_refs: frozenset[int] = frozenset()


def _descriptor_kind(
    statement: ast.FunctionDef | ast.AsyncFunctionDef,
) -> str:
    resolved = _resolved_descriptor_kinds(statement)
    if resolved is not None:
        effective = ""
        for kind in reversed(resolved):
            effective = kind
        return effective
    for decorator in statement.decorator_list:
        if isinstance(decorator, ast.Name):
            name = decorator.id
        elif (
            isinstance(decorator, ast.Attribute)
            and isinstance(decorator.value, ast.Name)
            and decorator.value.id == "builtins"
        ):
            name = decorator.attr
        else:
            continue
        if name in {"classmethod", "property", "staticmethod"}:
            return name
    return ""


def _has_unknown_decorator(
    statement: ast.FunctionDef | ast.AsyncFunctionDef,
) -> bool:
    resolved = _resolved_descriptor_kinds(statement)
    if resolved is not None:
        return any(not kind for kind in resolved)
    known = {"classmethod", "property", "staticmethod"}
    return any(
        not (
            isinstance(decorator, ast.Name)
            and decorator.id in known
        )
        and not (
            isinstance(decorator, ast.Attribute)
            and isinstance(decorator.value, ast.Name)
            and decorator.value.id == "builtins"
            and decorator.attr in known
        )
        for decorator in statement.decorator_list
    )


def _merge_member_maps(
    maps: Collection[dict[str, _Member]],
) -> dict[str, _Member]:
    names = set().union(*(members for members in maps))
    return {
        name: _Member(
            dynamic=any(members.get(name, _Member()).dynamic for members in maps),
            raw_dynamic=any(
                members.get(name, _Member()).raw_dynamic for members in maps
            ),
            bound_dynamic=any(
                members.get(name, _Member()).bound_dynamic for members in maps
            ),
            class_refs=frozenset().union(
                *(members.get(name, _Member()).class_refs for members in maps)
            ),
        )
        for name in names
        if any(name in members for members in maps)
    }


__all__: list[str] = []
