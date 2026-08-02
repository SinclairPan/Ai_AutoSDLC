"""按 Python 数据模型顺序记录协议异常与分支前缀。"""

from __future__ import annotations

import ast
import copy
from collections.abc import Iterable

from ai_sdlc.core.lean_code_control_flow import _static_truth, _subscript_exception
from ai_sdlc.core.lean_code_exception_points import _deduplicate_prefixes
from ai_sdlc.core.lean_code_static_values import _known_hash_safe


class _ProtocolExceptionVisitor(ast.NodeVisitor):
    prefixes: list[tuple[ast.stmt, ...]]

    def _record(self, raised: str | None = None) -> None:
        raise NotImplementedError

    def visit_Subscript(self, node: ast.Subscript) -> None:
        self.visit(node.value)
        self.visit(node.slice)
        raised = (
            _subscript_exception(node)
            if _known_subscript_protocol(node)
            else None
        )
        self._record(raised)

    def visit_BinOp(self, node: ast.BinOp) -> None:
        self.visit(node.left)
        self.visit(node.right)
        raised = _known_binary_exception(node)
        self._record(raised)

    def visit_If(self, node: ast.If) -> None:
        self.visit(node.test)
        self._record_truth_protocol(node.test)
        truth = _static_truth(node.test)
        blocks = (
            (node.body,)
            if truth is True
            else (node.orelse,)
            if truth is False
            else (node.body, node.orelse)
        )
        self._visit_statement_alternatives(blocks)

    def visit_While(self, node: ast.While) -> None:
        self.visit(node.test)
        self._record_truth_protocol(node.test)
        truth = _static_truth(node.test)
        blocks = (
            (node.body,)
            if truth is True
            else (node.orelse,)
            if truth is False
            else (node.body, node.orelse)
        )
        self._visit_statement_alternatives(blocks)

    def visit_IfExp(self, node: ast.IfExp) -> None:
        self.visit(node.test)
        self._record_truth_protocol(node.test)
        truth = _static_truth(node.test)
        expressions = (
            (node.body,)
            if truth is True
            else (node.orelse,)
            if truth is False
            else (node.body, node.orelse)
        )
        self._visit_expression_alternatives(expressions)

    def visit_BoolOp(self, node: ast.BoolOp) -> None:
        continuing = list(self.prefixes)
        completed: list[tuple[ast.stmt, ...]] = []
        for index, value in enumerate(node.values):
            self.prefixes = continuing
            self.visit(value)
            if index == len(node.values) - 1:
                completed.extend(self.prefixes)
                break
            self._record_truth_protocol(value)
            evaluated = list(self.prefixes)
            truth = _static_truth(value)
            stops = truth is False if isinstance(node.op, ast.And) else truth is True
            if stops:
                completed.extend(evaluated)
                break
            if truth is None:
                completed.extend(evaluated)
            continuing = evaluated
        self.prefixes = _deduplicate_prefixes(completed)

    def visit_With(self, node: ast.With) -> None:
        self._visit_context_manager(node.items, node.body, async_with=False)

    def visit_AsyncWith(self, node: ast.AsyncWith) -> None:
        self._visit_context_manager(node.items, node.body, async_with=True)

    def _visit_context_manager(
        self,
        items: list[ast.withitem],
        body: list[ast.stmt],
        *,
        async_with: bool,
    ) -> None:
        for item in items:
            self.visit(item.context_expr)
            self._record()
            if item.optional_vars is not None:
                self.visit(item.optional_vars)
                binding = ast.Assign(
                    targets=[copy.deepcopy(item.optional_vars)],
                    value=_context_enter_result(item.context_expr, async_with),
                )
                self.prefixes = [
                    (*prefix, binding)
                    for prefix in self.prefixes
                ]
        for statement in body:
            self.visit(statement)
        for _item in reversed(items):
            self._record()

    def _record_truth_protocol(self, value: ast.expr) -> None:
        if not _known_truth_protocol(value):
            self._record()

    def _visit_expression_alternatives(
        self,
        expressions: Iterable[ast.expr],
    ) -> None:
        initial = list(self.prefixes)
        outcomes: list[tuple[ast.stmt, ...]] = []
        for expression in expressions:
            self.prefixes = list(initial)
            self.visit(expression)
            outcomes.extend(self.prefixes)
        self.prefixes = _deduplicate_prefixes(outcomes)

    def _visit_statement_alternatives(
        self,
        blocks: Iterable[list[ast.stmt]],
    ) -> None:
        initial = list(self.prefixes)
        outcomes: list[tuple[ast.stmt, ...]] = []
        for block in blocks:
            self.prefixes = list(initial)
            for statement in block:
                self.visit(statement)
            outcomes.extend(self.prefixes)
        self.prefixes = _deduplicate_prefixes(outcomes)


def _known_subscript_protocol(node: ast.Subscript) -> bool:
    if isinstance(node.value, (ast.List, ast.Tuple)):
        return isinstance(node.slice, ast.Constant) and isinstance(
            node.slice.value,
            int,
        )
    if isinstance(node.value, ast.Dict):
        return _known_hash_safe(node.slice)
    return False


def _known_binary_exception(node: ast.BinOp) -> str | None:
    if not (
        isinstance(node.left, ast.Constant)
        and isinstance(node.left.value, (int, float, complex))
        and isinstance(node.right, ast.Constant)
        and isinstance(node.right.value, (int, float, complex))
    ):
        return None
    values = (node.left.value, node.right.value)
    if isinstance(node.op, (ast.FloorDiv, ast.Mod)) and any(
        isinstance(value, complex) for value in values
    ):
        return "TypeError"
    if isinstance(node.op, (ast.Div, ast.FloorDiv, ast.Mod)):
        return "ZeroDivisionError"
    return None


def _known_truth_protocol(value: ast.expr) -> bool:
    return isinstance(
        value,
        (ast.Constant, ast.List, ast.Tuple, ast.Set, ast.Dict),
    )


def _context_enter_result(
    expression: ast.expr,
    async_with: bool,
) -> ast.expr:
    call = ast.Call(
        func=ast.Attribute(
            value=copy.deepcopy(expression),
            attr="__aenter__" if async_with else "__enter__",
            ctx=ast.Load(),
        ),
        args=[],
        keywords=[],
    )
    return ast.Await(value=call) if async_with else call


__all__: list[str] = []
