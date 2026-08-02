"""按类语句顺序验证 Protocol property 装饰器与生命周期。"""

from __future__ import annotations

import ast
from typing import Protocol

from ai_sdlc.core.lean_code_framework_effects import _TopLevelStoreFinder

Contract = tuple[str, tuple[str, ...]]


class _PropertyClassState(Protocol):
    identity_method_decorators: set[str]
    builtin_decorator_modules: set[str]
    property_decorators: set[str]
    property_decorator_modules: set[str]
    abc_decorator_modules: set[str]


def _protocol_property_decorator(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    property_decorators: set[str],
    property_modules: set[str],
) -> bool:
    return any(
        (name := _decorator_name(decorator)) in property_decorators
        or any(name == f"{module}.property" for module in property_modules)
        for decorator in node.decorator_list
    )


def _protocol_decorators_preserve_contract(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    state: _PropertyClassState,
    property_decorators: set[str],
    property_modules: set[str],
) -> bool:
    for decorator in node.decorator_list:
        name = _decorator_name(decorator)
        if name in property_decorators or any(
            name == f"{module}.property" for module in property_modules
        ):
            continue
        if _looks_like_property_decorator(name, state):
            return False
        if name in state.identity_method_decorators:
            continue
        if any(
            name == f"{module}.{member}"
            for module in state.builtin_decorator_modules
            for member in ("property", "classmethod", "staticmethod")
        ):
            continue
        if any(
            name == f"{module}.abstractmethod" for module in state.abc_decorator_modules
        ):
            continue
        return False
    return True


def _looks_like_property_decorator(
    name: str,
    state: _PropertyClassState,
) -> bool:
    return name in state.property_decorators or any(
        name == f"{module}.property" for module in state.property_decorator_modules
    )


def _update_class_property_bindings(
    statement: ast.stmt,
    property_decorators: set[str],
    property_modules: set[str],
) -> None:
    rebound = _TopLevelStoreFinder()
    rebound.visit(statement)
    if rebound.unresolved:
        property_decorators.clear()
        property_modules.clear()
        return
    property_decorators.difference_update(rebound.names)
    property_modules.difference_update(rebound.names)
    if isinstance(statement, ast.Import):
        property_modules.update(
            alias.asname or alias.name.split(".", 1)[0]
            for alias in statement.names
            if alias.name == "builtins"
        )
    elif (
        isinstance(statement, ast.ImportFrom)
        and statement.level == 0
        and statement.module == "builtins"
    ):
        property_decorators.update(
            alias.asname or alias.name
            for alias in statement.names
            if alias.name == "property"
        )


def _protocol_property_lifecycle_preserves_contract(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    previous_contract: Contract | None,
    *,
    previous_property: bool,
) -> bool:
    if (
        not previous_property
        or previous_contract is None
        or previous_contract[0] != "protocol-member"
        or len(node.decorator_list) != 1
    ):
        return False
    decorator = node.decorator_list[0]
    return (
        isinstance(decorator, ast.Attribute)
        and decorator.attr in {"getter", "setter", "deleter"}
        and isinstance(decorator.value, ast.Name)
        and decorator.value.id == node.name
    )


def _decorator_name(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _decorator_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return ""


__all__: list[str] = []
