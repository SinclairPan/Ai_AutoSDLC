"""识别无法由静态调用计数可靠裁决的动态入口边界。"""

from __future__ import annotations

import ast
from collections.abc import Sequence

_DIRECT_DECORATORS = {
    "abstractmethod",
    "cache",
    "cached_property",
    "classmethod",
    "final",
    "lru_cache",
    "overload",
    "override",
    "property",
    "staticmethod",
}


def _invocation_boundary(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    decorators = {_decorator_name(item) for item in node.decorator_list}
    if decorators - _DIRECT_DECORATORS:
        return "decorated-indeterminate"
    if node.name.startswith("pytest_"):
        return "framework-convention-indeterminate"
    return ""


def _decorator_name(node: ast.expr) -> str:
    target = node.func if isinstance(node, ast.Call) else node
    if isinstance(target, ast.Attribute):
        return target.attr
    if isinstance(target, ast.Name):
        return target.id
    return "unknown"


def _potential_dynamic_references(
    tree: ast.Module,
    parents: dict[ast.AST, ast.AST],
) -> dict[str, set[str]]:
    """一次收集不能静态归因的名称及其调用位置。"""

    references: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        dynamic_name = _dynamic_call_name(node)
        if dynamic_name:
            _record_reference(references, dynamic_name, node, parents)
    return references


def _dynamic_call_name(node: ast.Call) -> str:
    if isinstance(node.func, ast.Call) and _is_getattr(node.func):
        name = node.func.args[1]
        if isinstance(name, ast.Constant) and isinstance(name.value, str):
            return name.value
    return ""


def _unresolved_chained_reference_locations(
    tree: ast.Module,
    parents: dict[ast.AST, ast.AST],
    target_name: str,
    class_instances: dict[str, set[str]],
    nodes: Sequence[ast.AST] | None = None,
) -> set[str]:
    references: dict[str, set[str]] = {}
    for node in nodes or tuple(ast.walk(tree)):
        helper = _chained_helper_name(node, target_name)
        if not helper:
            continue
        owner = _enclosing_function(node, parents)
        owner_class = _enclosing_class_name(owner, parents)
        if helper in class_instances.get(owner_class, set()):
            continue
        _record_reference(references, target_name, node, parents)
    return references.get(target_name, set())


def _unresolved_member_reference_locations(
    tree: ast.Module,
    parents: dict[ast.AST, ast.AST],
    target_name: str,
    class_instances: dict[str, set[str]],
    nodes: Sequence[ast.AST] | None = None,
) -> set[str]:
    """保留无法从类型信息确认的 self/cls 成员调用证据。"""

    references: dict[str, set[str]] = {}
    for node in nodes or tuple(ast.walk(tree)):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        receiver = _dotted_name(node.func.value)
        if node.func.attr != target_name or not receiver.startswith(("self.", "cls.")):
            continue
        owner = _enclosing_function(node, parents)
        owner_class = _enclosing_class_name(owner, parents)
        if receiver in class_instances.get(owner_class, set()):
            continue
        _record_reference(references, target_name, node, parents)
    return references.get(target_name, set())


def _chained_helper_name(node: ast.AST, target_name: str) -> str:
    if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
        return ""
    receiver = node.func.value
    if node.func.attr != target_name or not isinstance(receiver, ast.Call):
        return ""
    return (
        _dotted_name(receiver.func) if isinstance(receiver.func, ast.Attribute) else ""
    )


def _enclosing_class_name(
    node: ast.AST | None,
    parents: dict[ast.AST, ast.AST],
) -> str:
    current = parents.get(node) if node is not None else None
    while current is not None:
        if isinstance(current, ast.ClassDef):
            return current.name
        current = parents.get(current)
    return ""


def _dotted_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _dotted_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return ""


def _is_getattr(node: ast.Call) -> bool:
    return (
        isinstance(node.func, ast.Name)
        and node.func.id == "getattr"
        and len(node.args) >= 2
        and isinstance(node.args[1], ast.Constant)
        and isinstance(node.args[1].value, str)
    )


def _record_reference(
    references: dict[str, set[str]],
    name: str,
    node: ast.AST,
    parents: dict[ast.AST, ast.AST],
) -> None:
    owner = _enclosing_function(node, parents)
    references.setdefault(name, set()).add(_expression_location(node, owner, parents))


def _expression_location(
    node: ast.AST,
    owner: ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda | ast.ClassDef | None,
    parents: dict[ast.AST, ast.AST] | None = None,
) -> str:
    symbol = (
        owner.name
        if isinstance(owner, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        else "<lambda>"
        if isinstance(owner, ast.Lambda)
        else "<module>"
    )
    owner_line = getattr(owner, "lineno", 0) if owner is not None else 0
    line = getattr(node, "lineno", 0)
    column = getattr(node, "col_offset", 0)
    end_line = getattr(node, "end_lineno", line)
    end_column = getattr(node, "end_col_offset", column)
    return f"{symbol}:{owner_line}:{line}:{column}:{end_line}:{end_column}"


def _enclosing_function(
    node: ast.AST,
    parents: dict[ast.AST, ast.AST],
) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    current = parents.get(node)
    while current is not None:
        if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return current
        current = parents.get(current)
    return None


def _referenced_symbol_names(
    tree: ast.Module,
    dynamic_references: dict[str, set[str]],
) -> set[str]:
    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    names.update(
        node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
    )
    names.update(
        node.name.rsplit(".", 1)[-1]
        for node in ast.walk(tree)
        if isinstance(node, ast.alias)
    )
    names.update(dynamic_references)
    return names


__all__: list[str] = []
