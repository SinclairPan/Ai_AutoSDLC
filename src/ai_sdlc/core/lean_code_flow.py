"""Reference-state primitives for deterministic Python caller analysis."""

from __future__ import annotations

import ast

ReferenceState = tuple[set[str], set[str], set[str], set[str]]


def _merge_reference_states(*states: ReferenceState) -> ReferenceState:
    merged: ReferenceState = (set(), set(), set(), set())
    for state in states:
        merged = (
            merged[0] | state[0],
            merged[1] | state[1],
            merged[2] | state[2],
            merged[3] | state[3],
        )
    return merged


def _calls_target(
    node: ast.expr,
    direct_names: set[str],
    module_names: set[str],
    class_names: set[str],
    instance_names: set[str],
    target_name: str,
) -> bool:
    if isinstance(node, ast.Name):
        return node.id in direct_names
    if not isinstance(node, ast.Attribute) or node.attr != target_name:
        return False
    receiver = _dotted_name(node.value)
    return (
        receiver in module_names
        or receiver in class_names
        or receiver in instance_names
        or _constructs_target(node.value, class_names)
    )


def _constructs_target(node: ast.expr | None, class_names: set[str]) -> bool:
    if isinstance(node, ast.NamedExpr):
        return _constructs_target(node.value, class_names)
    if isinstance(node, ast.IfExp):
        return _constructs_target(node.body, class_names) or _constructs_target(
            node.orelse, class_names
        )
    return isinstance(node, ast.Call) and _references_target_class(
        node.func, class_names
    )


def _references_target_class(node: ast.expr, class_names: set[str]) -> bool:
    if isinstance(node, ast.NamedExpr):
        return _references_target_class(node.value, class_names)
    if isinstance(node, ast.IfExp):
        return _references_target_class(
            node.body, class_names
        ) or _references_target_class(node.orelse, class_names)
    return _dotted_name(node) in class_names


def _annotated_target_instances(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    class_names: set[str],
) -> set[str]:
    arguments = [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
    if node.args.vararg is not None:
        arguments.append(node.args.vararg)
    if node.args.kwarg is not None:
        arguments.append(node.args.kwarg)
    return {
        argument.arg
        for argument in arguments
        if _annotation_name(argument.annotation) in class_names
    }


def _annotation_name(node: ast.expr | None) -> str:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return _dotted_name(node) if node is not None else ""


def _pattern_bound_names(node: ast.AST) -> set[str]:
    if isinstance(node, ast.MatchAs):
        names = {node.name} if node.name else set()
        return names | (_pattern_bound_names(node.pattern) if node.pattern else set())
    if isinstance(node, ast.MatchStar):
        return {node.name} if node.name else set()
    if isinstance(node, ast.MatchMapping):
        names = {node.rest} if node.rest else set()
        return names | set().union(
            *(_pattern_bound_names(item) for item in node.patterns)
        )
    if isinstance(node, ast.MatchSequence):
        return set().union(*(_pattern_bound_names(item) for item in node.patterns))
    if isinstance(node, ast.MatchClass):
        patterns = [*node.patterns, *node.kwd_patterns]
        return set().union(*(_pattern_bound_names(item) for item in patterns))
    if isinstance(node, ast.MatchOr):
        return set().union(*(_pattern_bound_names(item) for item in node.patterns))
    return set()


def _dotted_name(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _dotted_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return ""


__all__: list[str] = []
