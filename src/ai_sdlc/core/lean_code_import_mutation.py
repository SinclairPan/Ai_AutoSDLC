"""识别动态导入能力向模块或对象容器的写入传播。"""

from __future__ import annotations

import ast
from collections.abc import Callable, Collection

_KindClassifier = Callable[
    [ast.expr, Collection[str], Collection[str]],
    set[str],
]


def _lineage_mutation_names(
    statement: ast.AST,
    module_aliases: Collection[str],
    callable_aliases: Collection[str],
    classify: _KindClassifier,
) -> set[str]:
    names: set[str] = set()
    for child in ast.walk(statement):
        if isinstance(child, (ast.Attribute, ast.Subscript)) and isinstance(
            child.ctx, (ast.Store, ast.Del)
        ):
            root = _mutation_root_name(child)
            if root in module_aliases:
                names.add(root)
        if isinstance(child, (ast.Assign, ast.AnnAssign)):
            names.update(
                _assignment_mutation_names(
                    child,
                    module_aliases,
                    callable_aliases,
                    classify,
                )
            )
        elif isinstance(child, ast.Call):
            names.update(
                _mutator_call_names(
                    child,
                    module_aliases,
                    callable_aliases,
                    classify,
                )
            )
    return names


def _assignment_mutation_names(
    node: ast.Assign | ast.AnnAssign,
    module_aliases: Collection[str],
    callable_aliases: Collection[str],
    classify: _KindClassifier,
) -> set[str]:
    value = node.value
    if value is None or not classify(value, module_aliases, callable_aliases):
        return set()
    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
    return {root for target in targets if (root := _mutation_root_name(target))}


def _mutator_call_names(
    node: ast.Call,
    module_aliases: Collection[str],
    callable_aliases: Collection[str],
    classify: _KindClassifier,
) -> set[str]:
    if (
        isinstance(node.func, ast.Name)
        and node.func.id in {"setattr", "delattr"}
        and node.args
    ):
        root = _mutation_root_name(node.args[0])
        if root in module_aliases or any(
            classify(value, module_aliases, callable_aliases) for value in node.args[1:]
        ):
            return {root} if root else set()
    if isinstance(node.func, ast.Attribute) and node.func.attr in {
        "update",
        "setdefault",
        "__setitem__",
    }:
        root = _mutation_root_name(node.func.value)
        if root in module_aliases:
            return {root}
    return set()


def _mutation_root_name(node: ast.AST) -> str:
    current = node
    while isinstance(current, (ast.Attribute, ast.Subscript)):
        current = current.value
    return current.id if isinstance(current, ast.Name) else ""


__all__: list[str] = []
