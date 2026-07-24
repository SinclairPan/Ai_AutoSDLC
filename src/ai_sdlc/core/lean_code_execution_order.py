"""Python 表达式求值顺序与延迟执行边界。"""

from __future__ import annotations

import ast
from typing import Any, cast

from ai_sdlc.core.lean_code_scope import _bound_names

_FunctionNode = ast.FunctionDef | ast.AsyncFunctionDef


def _expression_binding_events_before(
    node: ast.AST,
    parents: dict[ast.AST, ast.AST],
) -> tuple[tuple[str, str | None], ...]:
    root = _execution_root(node, parents)
    if root is None:
        return ()
    finder = _ExecutionPrefixFinder(node, parents)
    finder.visit(root)
    return tuple(finder.binding_events)


def _execution_root(
    node: ast.AST,
    parents: dict[ast.AST, ast.AST],
) -> ast.AST | None:
    current: ast.AST | None = node
    while current is not None:
        parent = parents.get(current)
        if isinstance(current, ast.stmt):
            return current
        if isinstance(parent, ast.Lambda) and current is not parent.body:
            return parent
        current = parent
    return None


def _same_deferred_scope(
    candidate: ast.AST,
    node: ast.AST,
    parents: dict[ast.AST, ast.AST],
) -> bool:
    return _deferred_scope(candidate, parents) is _deferred_scope(node, parents)


def _deferred_scope(
    node: ast.AST,
    parents: dict[ast.AST, ast.AST],
) -> ast.AST | None:
    current = parents.get(node)
    while current is not None:
        if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if any(_is_ancestor(item, node, parents) for item in current.body):
                return current
        elif isinstance(current, ast.Lambda):
            if _is_ancestor(current.body, node, parents):
                return current
        elif isinstance(current, ast.GeneratorExp) and _generator_body_contains(
            current, node, parents
        ):
            return current
        current = parents.get(current)
    return None


def _in_deferred_generator_body(
    node: ast.AST,
    parents: dict[ast.AST, ast.AST],
) -> bool:
    current = parents.get(node)
    while current is not None:
        if isinstance(current, ast.GeneratorExp):
            return _generator_body_contains(current, node, parents)
        current = parents.get(current)
    return False


def _generator_body_contains(
    generator: ast.GeneratorExp,
    node: ast.AST,
    parents: dict[ast.AST, ast.AST],
) -> bool:
    return not _is_ancestor(generator.generators[0].iter, node, parents)


class _ExecutionPrefixFinder(ast.NodeVisitor):
    def __init__(
        self,
        target: ast.AST,
        parents: dict[ast.AST, ast.AST],
    ) -> None:
        self._target = target
        self._parents = parents
        self.reached = False
        self.binding_events: list[tuple[str, str | None]] = []

    def visit(self, node: ast.AST) -> None:
        if self.reached:
            return
        if node is self._target:
            self.reached = True
            return
        super().visit(node)

    def visit_NamedExpr(self, node: ast.NamedExpr) -> None:
        self.visit(node.value)
        if not self.reached and _same_deferred_scope(node, self._target, self._parents):
            source = _lineage_source_key(node.value)
            for name in _bound_names(node.target):
                self.binding_events.append((name, source))

    def visit_Assign(self, node: ast.Assign) -> None:
        self.visit(node.value)
        for target in node.targets:
            self.visit(target)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if node.value is not None:
            self.visit(node.value)
        self.visit(node.target)
        self.visit(node.annotation)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function_header(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function_header(node)

    def _visit_function_header(self, node: _FunctionNode) -> None:
        for decorator in node.decorator_list:
            self.visit(decorator)
        for default in (*node.args.defaults, *node.args.kw_defaults):
            if default is not None:
                self.visit(default)
        for argument in _function_arguments(node.args):
            if argument.annotation is not None:
                self.visit(argument.annotation)
        if node.returns is not None:
            self.visit(node.returns)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        for default in (*node.args.defaults, *node.args.kw_defaults):
            if default is not None:
                self.visit(default)
        if _is_ancestor(node.body, self._target, self._parents):
            self.visit(node.body)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        for decorator in node.decorator_list:
            self.visit(decorator)
        for base in node.bases:
            self.visit(base)
        for keyword in node.keywords:
            self.visit(keyword.value)

    def visit_BoolOp(self, node: ast.BoolOp) -> None:
        for value in node.values:
            self.visit(value)
            truth = _constant_truth(value)
            if isinstance(node.op, ast.And) and truth is False:
                return
            if isinstance(node.op, ast.Or) and truth is True:
                return

    def visit_Compare(self, node: ast.Compare) -> None:
        left = node.left
        self.visit(left)
        for operator, comparator in zip(node.ops, node.comparators, strict=True):
            self.visit(comparator)
            if _comparison_truth(left, operator, comparator) is False:
                return
            left = comparator

    def visit_IfExp(self, node: ast.IfExp) -> None:
        self.visit(node.test)
        truth = _constant_truth(node.test)
        if truth is not None:
            self.visit(node.body if truth else node.orelse)
        elif _is_ancestor(node.body, self._target, self._parents):
            self.visit(node.body)
        elif _is_ancestor(node.orelse, self._target, self._parents):
            self.visit(node.orelse)

    def visit_Dict(self, node: ast.Dict) -> None:
        for key, value in zip(node.keys, node.values, strict=True):
            if key is not None:
                self.visit(key)
            self.visit(value)

    def visit_ListComp(self, node: ast.ListComp) -> None:
        self._visit_comprehension(node.generators, (node.elt,))

    def visit_SetComp(self, node: ast.SetComp) -> None:
        self._visit_comprehension(node.generators, (node.elt,))

    def visit_GeneratorExp(self, node: ast.GeneratorExp) -> None:
        self._visit_comprehension(node.generators, (node.elt,))

    def visit_DictComp(self, node: ast.DictComp) -> None:
        self._visit_comprehension(node.generators, (node.key, node.value))

    def _visit_comprehension(
        self,
        generators: list[ast.comprehension],
        outputs: tuple[ast.expr, ...],
    ) -> None:
        for generator in generators:
            self.visit(generator.iter)
            self.visit(generator.target)
            for condition in generator.ifs:
                self.visit(condition)
        for output in outputs:
            self.visit(output)


def _function_arguments(arguments: ast.arguments) -> tuple[ast.arg, ...]:
    items = [*arguments.posonlyargs, *arguments.args, *arguments.kwonlyargs]
    if arguments.vararg is not None:
        items.append(arguments.vararg)
    if arguments.kwarg is not None:
        items.append(arguments.kwarg)
    return tuple(items)


def _lineage_source_key(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if (
        isinstance(node, ast.Subscript)
        and isinstance(node.value, ast.Name)
        and isinstance(node.slice, ast.Constant)
        and isinstance(node.slice.value, int)
    ):
        return _lineage_item_key(node.value.id, node.slice.value)
    return None


def _lineage_item_key(name: str, index: int) -> str:
    return f"\0item:{name}:{index}"


def _constant_truth(node: ast.AST) -> bool | None:
    value = _constant_value(node)
    if value is not _UNKNOWN:
        try:
            return bool(value)
        except (TypeError, ValueError):
            pass
    return None


def _constant_value(node: ast.AST) -> object:
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        truth = _constant_truth(node.operand)
        return not truth if truth is not None else _UNKNOWN
    return _UNKNOWN


def _comparison_truth(
    left: ast.AST,
    operator: ast.cmpop,
    right: ast.AST,
) -> bool | None:
    lhs = cast(Any, _constant_value(left))
    rhs = cast(Any, _constant_value(right))
    if lhs is _UNKNOWN or rhs is _UNKNOWN:
        return None
    try:
        if isinstance(operator, ast.Gt):
            return bool(lhs > rhs)
        if isinstance(operator, ast.Lt):
            return bool(lhs < rhs)
        if isinstance(operator, ast.Eq):
            return bool(lhs == rhs)
        if isinstance(operator, ast.NotEq):
            return bool(lhs != rhs)
        if isinstance(operator, ast.GtE):
            return bool(lhs >= rhs)
        if isinstance(operator, ast.LtE):
            return bool(lhs <= rhs)
    except (TypeError, ValueError):
        return None
    return None


def _start_location(node: ast.AST) -> tuple[int, int]:
    return getattr(node, "lineno", 0), getattr(node, "col_offset", 0)


def _is_ancestor(
    ancestor: ast.AST,
    node: ast.AST,
    parents: dict[ast.AST, ast.AST],
) -> bool:
    current: ast.AST | None = node
    while current is not None:
        if current is ancestor:
            return True
        current = parents.get(current)
    return False


_UNKNOWN = object()


__all__: list[str] = []
