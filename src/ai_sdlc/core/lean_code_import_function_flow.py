"""按函数内可达路径传播动态导入绑定。"""

from __future__ import annotations

import ast
from collections.abc import Sequence

from ai_sdlc.core.lean_code_context_manager_metadata import (
    _CONTEXT_MANAGER_UNCERTAIN_ATTRIBUTE,
)
from ai_sdlc.core.lean_code_control_flow import (
    _match_pattern_names,
    _reachable_match_cases,
    _statically_empty,
    _statically_nonempty,
)
from ai_sdlc.core.lean_code_import_binding_flow import (
    _assigned_dynamic_aliases,
    _dynamic_dependency_call,
    _dynamic_lineage_mutation_names,
)
from ai_sdlc.core.lean_code_import_class_aliases import (
    _assigned_dynamic_class_callables,
    _discard_callable_binding,
)
from ai_sdlc.core.lean_code_import_factories import _DynamicFactoryBindings
from ai_sdlc.core.lean_code_import_function_comprehension import (
    _FunctionComprehensionFlow,
)
from ai_sdlc.core.lean_code_import_function_expression import (
    _FunctionExpressionFlow,
    _has_mutation_target,
    _raised_name,
)
from ai_sdlc.core.lean_code_import_uncertainty import _StoreNameFinder
from ai_sdlc.core.lean_code_scope import _bound_names


class _FunctionLoadNameFinder(
    ast.NodeVisitor,
    _FunctionComprehensionFlow,
    _FunctionExpressionFlow,
):
    def __init__(
        self,
        dynamic_modules: frozenset[str],
        dynamic_callables: frozenset[str],
        dynamic_factories: _DynamicFactoryBindings | None = None,
    ) -> None:
        self.names: set[str] = set()
        self.shadowed: list[tuple[set[str], bool]] = []
        self.comprehension_depth = 0
        self.uncertain = False
        self.dynamic_modules = set(dynamic_modules)
        self.dynamic_callables = set(dynamic_callables)
        self.dynamic_factories = dynamic_factories
        self.class_depth = 0
        self.control = "normal"
        self.exception: str | None = None

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, ast.Load) and not any(
            node.id in scope
            for scope, is_class_scope in self.shadowed
            if not (self.comprehension_depth and is_class_scope)
        ):
            self.names.add(node.id)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.visit_function_header(node)
        self._record_dynamic_factory(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.visit_function_header(node)
        self._record_dynamic_factory(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        for expression in (
            *node.decorator_list,
            *node.bases,
            *(keyword.value for keyword in node.keywords),
            *getattr(node, "type_params", ()),
        ):
            self.visit(expression)
        scope: set[str] = set()
        self.shadowed.append((scope, True))
        outer_depth = self.class_depth
        outer_modules = set(self.dynamic_modules)
        outer_callables = set(self.dynamic_callables)
        self.class_depth += 1
        try:
            self._visit_class_body(node, scope)
        finally:
            self.class_depth = outer_depth
            self.shadowed.pop()
            self.dynamic_modules = outer_modules
            self.dynamic_callables = outer_callables
        if outer_depth == 0:
            _discard_callable_binding(self.dynamic_callables, node.name)
            if self.dynamic_factories is not None:
                self.dynamic_callables.update(
                    self.dynamic_factories.callables_for_class(node)
                )

    def visit_Lambda(self, node: ast.Lambda) -> None:
        self._visit_defaults(node.args)

    def visit_If(self, node: ast.If) -> None:
        self.visit(node.test)
        if isinstance(node.test, ast.Constant):
            self._visit_statements(node.body if bool(node.test.value) else node.orelse)
            return
        self._merge_branches((node.body, node.orelse))

    def visit_For(self, node: ast.For) -> None:
        self._visit_loop(node)

    def visit_AsyncFor(self, node: ast.AsyncFor) -> None:
        self._visit_loop(node)

    def visit_While(self, node: ast.While) -> None:
        self.visit(node.test)
        if isinstance(node.test, ast.Constant) and not bool(node.test.value):
            self._visit_statements(node.orelse)
            return
        branch = self._fork()
        branch._visit_statements(node.body)
        if (
            isinstance(node.test, ast.Constant)
            and bool(node.test.value)
            and branch.control in {"normal", "continue"}
        ):
            branch.control = "loop"
        else:
            branch._finish_loop(node.orelse)
        if isinstance(node.test, ast.Constant):
            self._merge((branch,))
        else:
            skipped = self._fork()
            skipped._visit_statements(node.orelse)
            self._merge((branch, skipped))

    def visit_With(self, node: ast.With) -> None:
        self._visit_with(node)

    def visit_AsyncWith(self, node: ast.AsyncWith) -> None:
        self._visit_with(node)

    def visit_Match(self, node: ast.Match) -> None:
        self.visit(node.subject)
        branches: list[_FunctionLoadNameFinder] = []
        cases, no_match = _reachable_match_cases(node.subject, node.cases)
        for case in cases:
            branch = self._match_branch(case, node.subject)
            branches.append(branch)
        if no_match:
            branches.append(self._fork())
        self._merge(branches)

    def visit_Return(self, node: ast.Return) -> None:
        if node.value is not None:
            self.visit(node.value)
        self.control = "return"

    def visit_Raise(self, node: ast.Raise) -> None:
        for expression in (node.exc, node.cause):
            if expression is not None:
                self.visit(expression)
        self.control = "raise"
        self.exception = _raised_name(node.exc)

    def visit_Break(self, node: ast.Break) -> None:
        self.control = "break"

    def visit_Continue(self, node: ast.Continue) -> None:
        self.control = "continue"

    def visit_Assign(self, node: ast.Assign) -> None:
        self.visit(node.value)
        self._record_dynamic_aliases(node.targets, node.value)
        if any(_has_mutation_target(target) for target in node.targets):
            self._record_dynamic_mutations(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if node.annotation is not None:
            self.visit(node.annotation)
        if node.value is not None:
            self.visit(node.value)
            self._record_dynamic_aliases([node.target], node.value)
            if _has_mutation_target(node.target):
                self._record_dynamic_mutations(node)

    def visit_NamedExpr(self, node: ast.NamedExpr) -> None:
        self.visit(node.value)
        self._record_dynamic_aliases([node.target], node.value)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if isinstance(node.ctx, (ast.Store, ast.Del)):
            self._record_dynamic_mutations(node)
        self.generic_visit(node)

    def visit_Subscript(self, node: ast.Subscript) -> None:
        if isinstance(node.ctx, (ast.Store, ast.Del)):
            self._record_dynamic_mutations(node)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        self._record_dynamic_mutations(node)
        if _dynamic_dependency_call(
            node,
            self.dynamic_modules,
            self.dynamic_callables,
        ):
            self.uncertain = True
        self.generic_visit(node)

    def _visit_class_body(self, node: ast.ClassDef, scope: set[str]) -> None:
        for statement in node.body:
            if isinstance(statement, (ast.Import, ast.ImportFrom)):
                self.uncertain = True
            self.visit(statement)
            finder = _StoreNameFinder()
            finder.visit(statement)
            scope.update(finder.names)

    def _record_dynamic_aliases(
        self,
        targets: list[ast.expr],
        value: ast.expr,
    ) -> None:
        modules, callables = _assigned_dynamic_aliases(
            ast.Assign(targets=targets, value=value),
            self.dynamic_modules,
            self.dynamic_callables,
        )
        class_callables = _assigned_dynamic_class_callables(
            ast.Assign(targets=targets, value=value),
            self.dynamic_callables,
        )
        names = set().union(*(_bound_names(target) for target in targets))
        for name in names:
            self.dynamic_modules.discard(name)
            _discard_callable_binding(self.dynamic_callables, name)
        self.dynamic_modules.update(modules)
        self.dynamic_callables.update({*callables, *class_callables})

    def _record_dynamic_factory(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
    ) -> None:
        if self.class_depth:
            return
        _discard_callable_binding(self.dynamic_callables, node.name)
        if self.dynamic_factories is not None and self.dynamic_factories.is_function(
            node
        ):
            self.dynamic_callables.add(node.name)

    def _record_dynamic_mutations(self, node: ast.AST) -> None:
        names = _dynamic_lineage_mutation_names(
            node,
            self.dynamic_modules,
            self.dynamic_callables,
        )
        if names:
            self.uncertain = True
            self.dynamic_callables.update(names)

    def _visit_statements(self, statements: list[ast.stmt]) -> None:
        for statement in statements:
            if self.control != "normal":
                break
            self.visit(statement)

    def _visit_loop(self, node: ast.For | ast.AsyncFor) -> None:
        self.visit(node.iter)
        if _statically_empty(node.iter):
            self._visit_statements(node.orelse)
            return
        branch = self._fork()
        branch._record_dynamic_aliases([node.target], node.iter)
        branch._visit_statements(node.body)
        branch._finish_loop(node.orelse)
        if _statically_nonempty(node.iter):
            self._merge((branch,))
        else:
            skipped = self._fork()
            skipped._visit_statements(node.orelse)
            self._merge((branch, skipped))

    def _finish_loop(self, orelse: list[ast.stmt]) -> None:
        if self.control == "break":
            self.control = "normal"
        elif self.control in {"normal", "continue"}:
            self.control = "normal"
            self._visit_statements(orelse)

    def _visit_with(self, node: ast.With | ast.AsyncWith) -> None:
        if getattr(node, _CONTEXT_MANAGER_UNCERTAIN_ATTRIBUTE, False):
            self.uncertain = True
        for item in node.items:
            self.visit(item.context_expr)
            if item.optional_vars is not None:
                self._record_dynamic_aliases(
                    [item.optional_vars],
                    item.context_expr,
                )
        self._visit_statements(node.body)

    def _match_branch(
        self,
        case: ast.match_case,
        subject: ast.expr,
    ) -> _FunctionLoadNameFinder:
        branch = self._fork()
        names = _match_pattern_names(case.pattern)
        if names:
            target = ast.Tuple(
                elts=[ast.Name(id=name, ctx=ast.Store()) for name in names],
                ctx=ast.Store(),
            )
            branch._record_dynamic_aliases([target], subject)
        if case.guard is not None:
            branch.visit(case.guard)
        branch._visit_statements(case.body)
        return branch

    def _merge_branches(self, blocks: tuple[list[ast.stmt], ...]) -> None:
        branches = [self._branch(block) for block in blocks]
        self._merge(branches)

    def _branch(self, statements: list[ast.stmt]) -> _FunctionLoadNameFinder:
        branch = self._fork()
        branch._visit_statements(statements)
        return branch

    def _branch_with_normal_control(
        self,
        statements: list[ast.stmt],
    ) -> _FunctionLoadNameFinder:
        branch = self._fork()
        branch.control = "normal"
        branch._visit_statements(statements)
        return branch

    def _fork(self) -> _FunctionLoadNameFinder:
        branch = _FunctionLoadNameFinder(
            frozenset(self.dynamic_modules),
            frozenset(self.dynamic_callables),
            self.dynamic_factories,
        )
        branch.names = set(self.names)
        branch.shadowed = [(set(names), is_class) for names, is_class in self.shadowed]
        branch.comprehension_depth = self.comprehension_depth
        branch.uncertain = self.uncertain
        branch.class_depth = self.class_depth
        branch.control = self.control
        branch.exception = self.exception
        return branch

    def _merge(
        self,
        branches: Sequence[_FunctionLoadNameFinder],
    ) -> None:
        self.names = set().union(*(branch.names for branch in branches))
        self.dynamic_modules = set().union(
            *(branch.dynamic_modules for branch in branches)
        )
        self.dynamic_callables = set().union(
            *(branch.dynamic_callables for branch in branches)
        )
        self.uncertain = any(branch.uncertain for branch in branches)
        controls = {branch.control for branch in branches}
        self.control = controls.pop() if len(controls) == 1 else "normal"
        exceptions = {branch.exception for branch in branches}
        self.exception = exceptions.pop() if len(exceptions) == 1 else None


__all__: list[str] = []
