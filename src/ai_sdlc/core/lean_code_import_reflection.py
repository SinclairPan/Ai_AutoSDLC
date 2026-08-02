"""规范化反射函数身份与属性路径。"""

from __future__ import annotations

import ast
from collections.abc import Collection

from ai_sdlc.core.lean_code_static_values import _constant_subscript_value

_GETATTR_ALIAS = "__ai_sdlc_getattr__"
_VARS_ALIAS = "__ai_sdlc_vars__"


def _reflection_alias_markers(
    value: ast.expr,
    callable_aliases: Collection[str],
) -> set[str]:
    if isinstance(value, ast.NamedExpr):
        return _reflection_alias_markers(value.value, callable_aliases)
    if isinstance(value, ast.IfExp):
        return {
            *_reflection_alias_markers(value.body, callable_aliases),
            *_reflection_alias_markers(value.orelse, callable_aliases),
        }
    if isinstance(value, ast.BoolOp):
        return set().union(
            *(
                _reflection_alias_markers(item, callable_aliases)
                for item in value.values
            )
        )
    kind = _reflection_callable_kind(value, callable_aliases)
    if kind == "getattr":
        return {_GETATTR_ALIAS}
    if kind == "vars":
        return {_VARS_ALIAS}
    return set()


def _reflection_callable_kind(
    value: ast.expr,
    callable_aliases: Collection[str],
) -> str:
    if isinstance(value, ast.NamedExpr):
        return _reflection_callable_kind(value.value, callable_aliases)
    if isinstance(value, ast.Subscript):
        selected = _constant_subscript_value(value)
        if selected is not None:
            return _reflection_callable_kind(selected, callable_aliases)
    path = _attribute_path(value)
    if path in {"getattr", "builtins.getattr"} or (
        path and f"{path}.{_GETATTR_ALIAS}" in callable_aliases
    ):
        return "getattr"
    if path in {"vars", "builtins.vars"} or (
        path and f"{path}.{_VARS_ALIAS}" in callable_aliases
    ):
        return "vars"
    return ""


def _attribute_path(value: ast.expr) -> str:
    if isinstance(value, ast.NamedExpr):
        return _attribute_path(value.value)
    if isinstance(value, ast.Name):
        return value.id
    if isinstance(value, ast.Attribute):
        root = _attribute_path(value.value)
        return f"{root}.{value.attr}" if root else ""
    if isinstance(value, ast.Call):
        return _attribute_path(value.func)
    return ""


__all__: list[str] = []
