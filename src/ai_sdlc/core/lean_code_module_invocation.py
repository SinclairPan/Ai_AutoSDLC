"""解析模块定义期调用的实参与局部绑定。"""

from __future__ import annotations

import ast
from collections.abc import Collection, Iterable, Mapping
from dataclasses import replace

from ai_sdlc.core.lean_code_control_flow import (
    _match_pattern_names,
    _reachable_match_cases,
    _static_truth,
    _statically_empty,
)
from ai_sdlc.core.lean_code_exception_flow import _statement_exception_points
from ai_sdlc.core.lean_code_exception_handlers import (
    _raised_name,
    _reachable_exception_handlers,
)
from ai_sdlc.core.lean_code_module_parameters import (
    _Invocation,
    _InvocationState,
    _visit_argument_defaults,
    _visit_callable_header,
)
from ai_sdlc.core.lean_code_module_values import (
    _CallableValue,
    _expression_value,
    _merge_values,
)
from ai_sdlc.core.lean_code_scope import _bound_names


class _DefinitionTimeCallFinder(ast.NodeVisitor):
    def __init__(
        self,
        state: _InvocationState,
        overrides: Mapping[str, _CallableValue] | None = None,
        dynamic_modules: Collection[str] = (),
        dynamic_names: Collection[str] = (),
        *,
        evaluate_annotations: bool = True,
    ) -> None:
        self.state = state
        self.overrides = {
            name: value for name, value in (overrides or {}).items()
        }
        self.dynamic_modules = frozenset(dynamic_modules)
        self.dynamic_names = frozenset(dynamic_names)
        self.evaluate_annotations = evaluate_annotations
        self.invocations: list[_Invocation] = []
        self.dynamic_invoked = False
        self.control = "normal"
        self.exception: str | None = None

    def visit_Call(self, node: ast.Call) -> None:
        self.visit(node.func)
        for argument in node.args:
            self.visit(argument)
        for keyword in node.keywords:
            self.visit(keyword.value)
        positional = tuple(
            (
                isinstance(argument, ast.Starred),
                self._value(
                    argument.value if isinstance(argument, ast.Starred) else argument
                ),
            )
            for argument in node.args
        )
        keywords = tuple(
            (
                keyword.arg,
                self._value(keyword.value),
            )
            for keyword in node.keywords
        )
        callable_value = self._value(node.func)
        self.dynamic_invoked = self.dynamic_invoked or callable_value.dynamic
        callable_refs = set(callable_value.refs)
        if callable_value.unknown:
            callable_refs.update(self.state.all_callable_refs())
        self.invocations.extend(
            _Invocation(function_id, positional, keywords)
            for function_id in callable_refs
        )

    def visit_Assign(self, node: ast.Assign) -> None:
        self.visit(node.value)
        for target in node.targets:
            self._bind_target(target, node.value)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if self.evaluate_annotations and node.simple:
            self.visit(node.annotation)
        if node.value is not None:
            self.visit(node.value)
            self._bind_target(node.target, node.value)
        else:
            self._kill_target(node.target)

    def visit_NamedExpr(self, node: ast.NamedExpr) -> None:
        self.visit(node.value)
        self._bind_target(node.target, node.value)

    def visit_If(self, node: ast.If) -> None:
        self.visit(node.test)
        if isinstance(node.test, ast.Constant):
            self._visit_statements(node.body if bool(node.test.value) else node.orelse)
            return
        self._visit_branches((node.body, node.orelse))

    def visit_For(self, node: ast.For) -> None:
        self._visit_for(node)

    def visit_AsyncFor(self, node: ast.AsyncFor) -> None:
        self._visit_for(node)

    def visit_While(self, node: ast.While) -> None:
        self.visit(node.test)
        truth = _static_truth(node.test)
        if truth is False:
            self._visit_statements(node.orelse)
            return
        if truth is True:
            self._visit_statements(node.body)
            if self.control == "break":
                self.control = "normal"
            elif self.control in {"normal", "continue"}:
                self.control = "loop"
            return
        self._visit_branches((node.body, node.orelse))

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        _visit_callable_header(
            self,
            node,
            evaluate_annotations=self.evaluate_annotations,
        )
        self.overrides[node.name] = _CallableValue(
            refs=frozenset({self.state.register_callable(node)})
        )

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        _visit_callable_header(
            self,
            node,
            evaluate_annotations=self.evaluate_annotations,
        )
        self.overrides[node.name] = _CallableValue(
            refs=frozenset({self.state.register_callable(node)})
        )

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        for expression in (
            *node.decorator_list,
            *node.bases,
            *(keyword.value for keyword in node.keywords),
            *getattr(node, "type_params", ()),
        ):
            self.visit(expression)
        child = _DefinitionTimeCallFinder(
            self.state,
            self.overrides,
            self.dynamic_modules,
            self.dynamic_names,
            evaluate_annotations=self.evaluate_annotations,
        )
        child._visit_statements(node.body)
        self.invocations.extend(child.invocations)
        self.dynamic_invoked = self.dynamic_invoked or child.dynamic_invoked

    def visit_Match(self, node: ast.Match) -> None:
        self.visit(node.subject)
        cases, no_match = _reachable_match_cases(node.subject, node.cases)
        branches: list[_DefinitionTimeCallFinder] = []
        for case in cases:
            branch = self._fork()
            names = _match_pattern_names(case.pattern)
            if names:
                target = ast.Tuple(
                    elts=[
                        ast.Name(id=name, ctx=ast.Store())
                        for name in sorted(names)
                    ],
                    ctx=ast.Store(),
                )
                branch._bind_target(target, node.subject)
            if case.guard is not None:
                branch.visit(case.guard)
            branch._visit_statements(case.body)
            branches.append(branch)
        if no_match:
            branches.append(self._fork())
        self._merge_finders(branches)

    def visit_Try(self, node: ast.Try) -> None:
        self._visit_try(node)

    def visit_TryStar(self, node: ast.TryStar) -> None:
        self._visit_try(node)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        _visit_argument_defaults(self, node.args)
        self.state.register_callable(node)

    def visit_Return(self, node: ast.Return) -> None:
        if node.value is not None:
            self.visit(node.value)
        self.control = "return"

    def visit_Raise(self, node: ast.Raise) -> None:
        if node.exc is not None:
            self.visit(node.exc)
        if node.cause is not None:
            self.visit(node.cause)
        self.control = "raise"
        self.exception = _raised_name(node.exc)

    def visit_Break(self, node: ast.Break) -> None:
        self.control = "break"

    def visit_Continue(self, node: ast.Continue) -> None:
        self.control = "continue"

    def _visit_for(self, node: ast.For | ast.AsyncFor) -> None:
        self.visit(node.iter)
        if _statically_empty(node.iter):
            self._visit_statements(node.orelse)
            return
        if isinstance(node.iter, (ast.List, ast.Tuple)) and node.iter.elts:
            broke = False
            for item in node.iter.elts:
                self._bind_target(node.target, item)
                self._visit_statements(node.body)
                if self.control == "break":
                    self.control = "normal"
                    broke = True
                    break
                if self.control == "continue":
                    self.control = "normal"
                elif self.control != "normal":
                    return
            if not broke:
                self._visit_statements(node.orelse)
            return
        iteration = self._fork()
        iteration._bind_target(node.target, node.iter)
        iteration._visit_statements(node.body)
        if iteration.control == "break":
            iteration.control = "normal"
        elif iteration.control in {"normal", "continue"}:
            iteration.control = "normal"
            iteration._visit_statements(node.orelse)
        skipped = self._fork()
        skipped._visit_statements(node.orelse)
        self._merge_finders([iteration, skipped])

    def _visit_try(self, node: ast.Try | ast.TryStar) -> None:
        body = self._fork()
        raised: list[_DefinitionTimeCallFinder] = []
        for statement in node.body:
            if body.control != "normal":
                break
            for point in _statement_exception_points(statement):
                exception = body._fork()
                exception._visit_statements(point.prefix)
                exception.control = "raise"
                exception.exception = point.raised
                raised.append(exception)
            body.visit(statement)
        outcomes: list[_DefinitionTimeCallFinder] = []
        if body.control == "normal":
            body._visit_statements(node.orelse)
            outcomes.append(body)
        elif body.control == "raise":
            raised.append(body)
        else:
            outcomes.append(body)
        for source in raised:
            handlers, can_escape = _reachable_exception_handlers(
                node.handlers,
                source.exception,
            )
            for handler in handlers:
                branch = source._fork()
                branch.control = "normal"
                branch.exception = None
                branch._visit_statements(handler.body)
                outcomes.append(branch)
            if can_escape:
                outcomes.append(source)
        finalized = [self._apply_finally(item, node.finalbody) for item in outcomes]
        self._merge_finders(finalized)

    def _visit_statements(self, statements: Iterable[ast.stmt]) -> None:
        for statement in statements:
            if self.control != "normal":
                break
            self.visit(statement)

    def _visit_branches(self, blocks: Iterable[list[ast.stmt]]) -> None:
        branches = []
        for block in blocks:
            branch = self._fork()
            branch._visit_statements(block)
            branches.append(branch)
        self._merge_finders(branches)

    def _fork(self) -> _DefinitionTimeCallFinder:
        branch = _DefinitionTimeCallFinder(
            self.state,
            self.overrides,
            self.dynamic_modules,
            self.dynamic_names,
            evaluate_annotations=self.evaluate_annotations,
        )
        branch.control = self.control
        branch.exception = self.exception
        branch.dynamic_invoked = self.dynamic_invoked
        return branch

    def _apply_finally(
        self,
        branch: _DefinitionTimeCallFinder,
        statements: list[ast.stmt],
    ) -> _DefinitionTimeCallFinder:
        previous = branch.control
        branch.control = "normal"
        branch._visit_statements(statements)
        if branch.control == "normal":
            branch.control = previous
        return branch

    def _merge_finders(
        self,
        branches: list[_DefinitionTimeCallFinder],
    ) -> None:
        if not branches:
            return
        self.invocations.extend(
            invocation
            for branch in branches
            for invocation in branch.invocations
        )
        self.dynamic_invoked = any(branch.dynamic_invoked for branch in branches)
        self._merge_override_states(
            tuple(branch.overrides for branch in branches)
        )
        controls = {branch.control for branch in branches}
        self.control = controls.pop() if len(controls) == 1 else "normal"
        exceptions = {branch.exception for branch in branches}
        self.exception = exceptions.pop() if len(exceptions) == 1 else None

    def _merge_override_states(
        self,
        branches: tuple[dict[str, _CallableValue], ...],
    ) -> None:
        names = set().union(*(branch.keys() for branch in branches))
        self.overrides = {
            name: _merge_values(
                tuple(branch.get(name, _CallableValue()) for branch in branches)
            )
            for name in names
        }

    def _bind_target(self, target: ast.expr, value: ast.expr) -> None:
        refs = self._value(value)
        for name in _bound_names(target):
            self.overrides[name] = refs

    def _kill_target(self, target: ast.expr) -> None:
        for name in _bound_names(target):
            self.overrides[name] = _CallableValue()

    def _value(self, expression: ast.expr) -> _CallableValue:
        value = _expression_value(
            self.state,
            expression,
            self.overrides,
            self.dynamic_names,
        )
        if isinstance(expression, ast.Call) and any(
            self.state.callable_returns_dynamic(
                function_id,
                self.dynamic_modules,
                self.dynamic_names,
            )
            for function_id in value.refs
        ):
            return replace(value, dynamic=True)
        return value


__all__: list[str] = []
