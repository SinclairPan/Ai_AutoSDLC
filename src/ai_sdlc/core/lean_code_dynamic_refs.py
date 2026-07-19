"""识别无法由静态调用计数可靠裁决的动态入口边界。"""

from __future__ import annotations

import ast

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


def _potential_dynamic_reference_names(
    tree: ast.Module,
    parents: dict[ast.AST, ast.AST],
) -> set[str]:
    """一次收集不能作为直接调用证明的名称，供同文件全部目标复用。"""

    names: set[str] = set()
    for node in ast.walk(tree):
        parent = parents.get(node)
        if (
            isinstance(node, ast.Name)
            and isinstance(node.ctx, ast.Load)
            and not (isinstance(parent, ast.Call) and parent.func is node)
        ):
            names.add(node.id)
        if isinstance(node, ast.Attribute) and not (
            isinstance(parent, ast.Call) and parent.func is node
        ):
            names.add(node.attr)
    return names


__all__: list[str] = []
