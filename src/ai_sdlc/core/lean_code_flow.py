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
    if _dotted_name(node) in direct_names:
        return True
    getattr_receiver = _getattr_receiver(node, target_name)
    if getattr_receiver:
        return getattr_receiver in class_names or getattr_receiver in instance_names
    if not isinstance(node, ast.Attribute) or node.attr != target_name:
        return False
    receiver = _call_receiver(node.value)
    return (
        receiver in module_names
        or receiver in class_names
        or receiver in instance_names
        or _constructs_target(node.value, class_names)
    )


def _call_receiver(node: ast.expr) -> str:
    if isinstance(node, ast.Call):
        return _dotted_name(node.func)
    return _dotted_name(node)


def _getattr_receiver(node: ast.expr, target_name: str) -> str:
    if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
        return ""
    if node.func.id != "getattr" or len(node.args) < 2:
        return ""
    name = node.args[1]
    if not isinstance(name, ast.Constant) or name.value != target_name:
        return ""
    return _dotted_name(node.args[0])


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


def _class_target_instances(
    tree: ast.Module,
    class_names: set[str],
) -> dict[str, set[str]]:
    """从显式类型注解和初始化赋值推导类属性，不猜测运行时类型。"""

    return {
        node.name: _target_members(node, class_names)
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef)
    }


def _target_members(node: ast.ClassDef, class_names: set[str]) -> set[str]:
    members: set[str] = set()
    for statement in node.body:
        if (
            isinstance(statement, ast.AnnAssign)
            and _annotation_name(statement.annotation) in class_names
        ):
            members.update(_class_member_names(statement.target))
        if not isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if _annotation_name(statement.returns) in class_names:
            members.update({f"self.{statement.name}", f"cls.{statement.name}"})
        if statement.name == "__init__":
            members.update(_init_target_members(statement, class_names))
    return members


def _init_target_members(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    class_names: set[str],
) -> set[str]:
    typed = _annotated_target_instances(node, class_names)
    members: set[str] = set()
    for statement in ast.walk(node):
        if isinstance(statement, (ast.Assign, ast.AnnAssign)):
            value = statement.value
            targets = (
                statement.targets
                if isinstance(statement, ast.Assign)
                else [statement.target]
            )
            if value is not None and (
                _dotted_name(value) in typed or _constructs_target(value, class_names)
            ):
                for target in targets:
                    members.update(_member_names(target))
    return members


def _member_names(node: ast.AST) -> set[str]:
    name = _dotted_name(node) if isinstance(node, ast.expr) else ""
    return {name} if name.startswith(("self.", "cls.")) else set()


def _class_member_names(node: ast.AST) -> set[str]:
    if isinstance(node, ast.Name):
        return {f"self.{node.id}", f"cls.{node.id}"}
    return _member_names(node)


def _rebind_named_expressions(
    node: ast.AST,
    state: ReferenceState,
) -> ReferenceState:
    """按表达式中的海象赋值更新模块绑定。"""

    current = state
    for candidate in ast.walk(node):
        if not isinstance(candidate, ast.NamedExpr):
            continue
        names = _bound_expression_names(candidate.target)
        direct = current[0] - names
        modules = _without_roots(current[1], names)
        classes = _without_roots(current[2], names)
        if _references_target_class(candidate.value, current[2]):
            classes.update(names)
        current = direct, modules, classes, set()
    return current


def _bound_expression_names(node: ast.AST) -> set[str]:
    if isinstance(node, ast.Name):
        return {node.id}
    if isinstance(node, (ast.Tuple, ast.List)):
        return set().union(*(_bound_expression_names(item) for item in node.elts))
    return set()


def _without_roots(names: set[str], roots: set[str]) -> set[str]:
    return {name for name in names if name.split(".", 1)[0] not in roots}


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
