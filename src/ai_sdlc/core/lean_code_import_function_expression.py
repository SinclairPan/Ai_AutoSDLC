"""隔离函数流中的短路表达式与局部目标语义。"""

from __future__ import annotations

import ast
from collections.abc import Sequence
from typing import Any

from ai_sdlc.core.lean_code_control_flow import _static_truth
from ai_sdlc.core.lean_code_exception_flow import _statement_exception_points
from ai_sdlc.core.lean_code_exception_handlers import _reachable_exception_handlers


class _FunctionExpressionFlow:
    control: str
    exception: str | None

    def visit(self, node: ast.AST) -> None:
        raise NotImplementedError

    def _fork(self) -> Any:
        raise NotImplementedError

    def _merge(self, branches: Sequence[Any]) -> None:
        raise NotImplementedError

    def _visit_statements(self, statements: list[ast.stmt]) -> None:
        raise NotImplementedError

    def _branch(self, statements: list[ast.stmt]) -> Any:
        raise NotImplementedError

    def _branch_with_normal_control(self, statements: list[ast.stmt]) -> Any:
        raise NotImplementedError

    def visit_IfExp(self, node: ast.IfExp) -> None:  # noqa: N802
        self.visit(node.test)
        truth = _static_truth(node.test)
        if truth is not None:
            self.visit(node.body if truth else node.orelse)
            return
        body = self._fork()
        body.visit(node.body)
        orelse = self._fork()
        orelse.visit(node.orelse)
        self._merge((body, orelse))

    def visit_BoolOp(self, node: ast.BoolOp) -> None:  # noqa: N802
        continuing: list[Any] = [self._fork()]
        completed: list[Any] = []
        for index, value in enumerate(node.values):
            evaluated: list[Any] = []
            for branch in continuing:
                branch.visit(value)
                evaluated.append(branch)
            if index == len(node.values) - 1:
                completed.extend(evaluated)
                break
            truth = _static_truth(value)
            stops = truth is False if isinstance(node.op, ast.And) else truth is True
            continues = (
                truth is True if isinstance(node.op, ast.And) else truth is False
            )
            if stops:
                completed.extend(evaluated)
                continuing = []
                break
            if truth is None:
                completed.extend(branch._fork() for branch in evaluated)
            continuing = evaluated if continues or truth is None else []
        self._merge(completed)

    def visit_Try(self, node: ast.Try) -> None:  # noqa: N802
        self._visit_try(node)

    def visit_TryStar(self, node: ast.TryStar) -> None:  # noqa: N802
        self._visit_try(node)

    def _visit_try(self, node: ast.Try | ast.TryStar) -> None:
        body, exceptions = self._visit_try_body(node.body)
        outcomes = self._try_outcomes(node, body, exceptions)
        for outcome in outcomes:
            previous = outcome.control
            outcome.control = "normal"
            outcome._visit_statements(node.finalbody)
            if outcome.control == "normal":
                outcome.control = previous
        self._merge(outcomes)

    def _try_outcomes(
        self,
        node: ast.Try | ast.TryStar,
        body: Any,
        exceptions: list[Any],
    ) -> list[Any]:
        outcomes: list[Any] = []
        if body.control == "normal":
            body._visit_statements(node.orelse)
            outcomes.append(body)
        elif body.control == "raise":
            exceptions.append(body)
        else:
            outcomes.append(body)
        for exception in exceptions:
            handlers, can_escape = _reachable_exception_handlers(
                node.handlers,
                exception.exception,
            )
            outcomes.extend(
                exception._branch_with_normal_control(handler.body)
                for handler in handlers
            )
            if can_escape:
                outcomes.append(exception)
        return outcomes

    def _visit_try_body(
        self,
        statements: list[ast.stmt],
    ) -> tuple[Any, list[Any]]:
        body = self._fork()
        exceptions: list[Any] = []
        for statement in statements:
            if body.control != "normal":
                break
            for point in _statement_exception_points(statement):
                branch = body._fork()
                branch._visit_statements(list(point.prefix))
                branch.control = "raise"
                branch.exception = point.raised
                exceptions.append(branch)
            body.visit(statement)
        return body, exceptions


def _raised_name(expression: ast.expr | None) -> str | None:
    if isinstance(expression, ast.Name):
        return expression.id
    if isinstance(expression, ast.Call):
        if isinstance(expression.func, ast.Name):
            return expression.func.id
        if isinstance(expression.func, ast.Attribute):
            return expression.func.attr
    return None


def _has_mutation_target(target: ast.expr) -> bool:
    if isinstance(target, (ast.Attribute, ast.Subscript)):
        return True
    if isinstance(target, (ast.Tuple, ast.List)):
        return any(_has_mutation_target(item) for item in target.elts)
    return False


__all__: list[str] = []
