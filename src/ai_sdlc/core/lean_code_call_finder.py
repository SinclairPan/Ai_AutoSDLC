"""Lexical Python call matching for Lean Code caller counts."""

from __future__ import annotations

import ast
from typing import Any

from ai_sdlc.core.lean_code_flow import (
    ReferenceState,
    _annotated_target_instances,
    _calls_target,
    _constructs_target,
    _merge_reference_states,
    _pattern_bound_names,
    _references_target_class,
)
from ai_sdlc.core.lean_code_scope import (
    _argument_names,
    _bound_names,
    _enclosing_class,
    _local_bindings,
    _without_local_roots,
)


def _function_calls_target(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    direct_names: set[str],
    module_names: set[str],
    class_names: set[str],
    target_name: str,
    allow_self_or_cls: bool,
    inherited_instances: set[str],
    local_imports: dict[int, tuple[set[str], set[str], set[str]]],
) -> bool:
    finder = _DirectFunctionCallFinder(
        direct_names,
        module_names,
        class_names,
        target_name,
        allow_self_or_cls,
        inherited_instances,
        node,
        local_imports,
    )
    for statement in node.body:
        finder.visit(statement)
    return finder.found


class _DirectFunctionCallFinder(ast.NodeVisitor):
    """Find calls owned by one function without descending into nested scopes."""

    def __init__(
        self,
        direct_names: set[str],
        module_names: set[str],
        class_names: set[str],
        target_name: str,
        allow_self_or_cls: bool,
        instance_names: set[str],
        node: ast.FunctionDef | ast.AsyncFunctionDef,
        local_imports: dict[int, tuple[set[str], set[str], set[str]]] | None = None,
        watched_call: str = "",
    ) -> None:
        local_names = _local_bindings(node)
        self.direct_names = direct_names - local_names
        self.module_names = _without_local_roots(module_names, local_names)
        self.class_names = _without_local_roots(class_names, local_names)
        self.target_name = target_name
        self.local_imports = local_imports or {}
        self.instance_names = (instance_names - local_names) | (
            _annotated_target_instances(node, class_names)
        )
        if allow_self_or_cls:
            self.instance_names.update({"self", "cls"} & _argument_names(node))
        self.found = False
        self.watched_call = watched_call
        self.captured_instances: set[str] = set()
        self._exception_state_stacks: list[list[ReferenceState]] = []

    def visit(self, node: ast.AST) -> Any:
        if isinstance(node, ast.stmt):
            for states in self._exception_state_stacks:
                states.append(self._reference_state())
        return super().visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Name) and node.func.id == self.watched_call:
            self.captured_instances.update(self.instance_names)
        if _calls_target(
            node.func,
            self.direct_names,
            self.module_names,
            self.class_names,
            self.instance_names,
            self.target_name,
        ):
            self.found = True
            return
        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> None:
        assigned_names = {
            name for target in node.targets for name in _bound_names(target)
        }
        self._update_class_names(
            assigned_names,
            _references_target_class(node.value, self.class_names),
        )
        if _constructs_target(node.value, self.class_names):
            self.instance_names.update(assigned_names)
        else:
            self.instance_names.difference_update(assigned_names)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if node.value is None:
            self.visit(node.annotation)
            return
        if isinstance(node.target, ast.Name):
            self._update_class_names(
                {node.target.id},
                _references_target_class(node.value, self.class_names),
            )
            if _constructs_target(node.value, self.class_names):
                self.instance_names.add(node.target.id)
            else:
                self.instance_names.discard(node.target.id)
        self.generic_visit(node)

    def visit_Delete(self, node: ast.Delete) -> None:
        for target in node.targets:
            self._discard_names(_bound_names(target))

    def visit_NamedExpr(self, node: ast.NamedExpr) -> None:
        names = _bound_names(node.target)
        self._update_class_names(
            names, _references_target_class(node.value, self.class_names)
        )
        if _constructs_target(node.value, self.class_names):
            self.instance_names.update(names)
        else:
            self.instance_names.difference_update(names)
        self.visit(node.value)

    def visit_If(self, node: ast.If) -> None:
        self.visit(node.test)
        initial = self._reference_state()
        self._visit_branch_statements(initial, node.body, node.orelse)

    def visit_IfExp(self, node: ast.IfExp) -> None:
        self.visit(node.test)
        initial = self._reference_state()
        states = []
        for expression in (node.body, node.orelse):
            self._restore_reference_state(initial)
            self.visit(expression)
            states.append(self._reference_state())
        self._restore_reference_state(_merge_reference_states(*states))

    def visit_Try(self, node: ast.Try) -> None:
        self._visit_try(node)

    def visit_TryStar(self, node: ast.TryStar) -> None:
        self._visit_try(node)

    def _visit_try(self, node: ast.Try | ast.TryStar) -> None:
        initial = self._reference_state()
        prefixes = [initial]
        self._exception_state_stacks.append(prefixes)
        try:
            for statement in node.body:
                self.visit(statement)
        finally:
            self._exception_state_stacks.pop()
        for statement in node.orelse:
            self.visit(statement)
        states = [self._reference_state()]
        handler_entry = _merge_reference_states(*prefixes)
        for handler in node.handlers:
            self._restore_reference_state(handler_entry)
            self.visit(handler)
            states.append(self._reference_state())
        self._restore_reference_state(_merge_reference_states(*states))
        for statement in node.finalbody:
            self.visit(statement)

    def visit_Match(self, node: ast.Match) -> None:
        self.visit(node.subject)
        initial = self._reference_state()
        states = [initial]
        for case in node.cases:
            self._restore_reference_state(initial)
            self._discard_names(_pattern_bound_names(case.pattern))
            if case.guard is not None:
                self.visit(case.guard)
            for statement in case.body:
                self.visit(statement)
            states.append(self._reference_state())
        self._restore_reference_state(_merge_reference_states(*states))

    def visit_For(self, node: ast.For) -> None:
        self._visit_loop(node)

    def visit_AsyncFor(self, node: ast.AsyncFor) -> None:
        self._visit_loop(node)

    def _visit_loop(self, node: ast.For | ast.AsyncFor) -> None:
        self.visit(node.iter)
        initial = self._reference_state()
        zero_state = self._state_after(initial, node.orelse)
        self._restore_reference_state(initial)
        self._discard_names(_bound_names(node.target))
        for statement in [*node.body, *node.orelse]:
            self.visit(statement)
        self._restore_reference_state(
            _merge_reference_states(zero_state, self._reference_state())
        )

    def visit_While(self, node: ast.While) -> None:
        self.visit(node.test)
        initial = self._reference_state()
        self._visit_branch_statements(
            initial,
            [*node.body, *node.orelse],
            node.orelse,
        )

    def visit_With(self, node: ast.With) -> None:
        self._visit_with(node)

    def visit_AsyncWith(self, node: ast.AsyncWith) -> None:
        self._visit_with(node)

    def _visit_with(self, node: ast.With | ast.AsyncWith) -> None:
        for item in node.items:
            self.visit(item.context_expr)
            self._discard_names(_bound_names(item.optional_vars))
        for statement in node.body:
            self.visit(statement)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if node.type is not None:
            self.visit(node.type)
        if node.name:
            self._discard_names({node.name})
        for statement in node.body:
            self.visit(statement)

    def visit_Import(self, node: ast.Import) -> None:
        names = {alias.asname or alias.name.split(".", 1)[0] for alias in node.names}
        self._apply_import(node, names)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        names = {
            alias.asname or alias.name for alias in node.names if alias.name != "*"
        }
        self._apply_import(node, names)

    def visit_ListComp(self, node: ast.ListComp) -> None:
        self._visit_comprehension(node.generators, node.elt)

    def visit_SetComp(self, node: ast.SetComp) -> None:
        self._visit_comprehension(node.generators, node.elt)

    def visit_GeneratorExp(self, node: ast.GeneratorExp) -> None:
        self._visit_comprehension(node.generators, node.elt)

    def visit_DictComp(self, node: ast.DictComp) -> None:
        self._visit_comprehension(node.generators, node.key, node.value)

    def _visit_comprehension(
        self, generators: list[ast.comprehension], *outputs: ast.expr
    ) -> None:
        saved = self._reference_state()
        for generator in generators:
            self.visit(generator.iter)
            self._discard_names(_bound_names(generator.target))
            for condition in generator.ifs:
                self.visit(condition)
        for output in outputs:
            self.visit(output)
        self._restore_reference_state(saved)

    def _update_class_names(self, names: set[str], target_alias: bool) -> None:
        self.class_names = _without_local_roots(self.class_names, names)
        if target_alias:
            self.class_names.update(names)

    def _discard_names(self, names: set[str]) -> None:
        self.direct_names.difference_update(names)
        self.module_names = _without_local_roots(self.module_names, names)
        self.class_names = _without_local_roots(self.class_names, names)
        self.instance_names.difference_update(names)

    def _apply_import(self, node: ast.Import | ast.ImportFrom, names: set[str]) -> None:
        self._discard_names(names)
        direct_names, module_names, class_names = self.local_imports.get(
            id(node), (set(), set(), set())
        )
        self.direct_names.update(direct_names & names)
        self.module_names.update(
            name for name in module_names if name.split(".", 1)[0] in names
        )
        self.class_names.update(
            name for name in class_names if name.split(".", 1)[0] in names
        )

    def _reference_state(self) -> ReferenceState:
        return (
            set(self.direct_names),
            set(self.module_names),
            set(self.class_names),
            set(self.instance_names),
        )

    def _restore_reference_state(self, state: ReferenceState) -> None:
        self.direct_names = set(state[0])
        self.module_names = set(state[1])
        self.class_names = set(state[2])
        self.instance_names = set(state[3])

    def _state_after(
        self, initial: ReferenceState, statements: list[ast.stmt]
    ) -> ReferenceState:
        self._restore_reference_state(initial)
        for statement in statements:
            self.visit(statement)
        return self._reference_state()

    def _visit_branch_statements(
        self, initial: ReferenceState, *branches: list[ast.stmt]
    ) -> None:
        states = [self._state_after(initial, branch) for branch in branches]
        self._restore_reference_state(_merge_reference_states(*states))

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        return

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        return

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        return


def _enclosing_target_instances(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    parents: dict[ast.AST, ast.AST],
    class_names: set[str],
    target_class: str,
) -> set[str]:
    ancestors: list[ast.FunctionDef | ast.AsyncFunctionDef] = []
    current = parents.get(node)
    while current is not None:
        if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef)):
            ancestors.append(current)
        current = parents.get(current)
    ordered = list(reversed(ancestors))
    instances: set[str] = set()
    for index, ancestor in enumerate(ordered):
        child = ordered[index + 1] if index + 1 < len(ordered) else node
        instances = _function_instances_at_call(
            ancestor,
            class_names,
            _enclosing_class(ancestor, parents) == target_class,
            instances,
            child.name,
        )
    return instances


def _function_instances_at_call(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    class_names: set[str],
    allow_self_or_cls: bool,
    inherited_instances: set[str],
    child_name: str,
) -> set[str]:
    finder = _DirectFunctionCallFinder(
        set(),
        set(),
        class_names,
        "",
        allow_self_or_cls,
        inherited_instances,
        node,
        watched_call=child_name,
    )
    for statement in node.body:
        finder.visit(statement)
    return finder.captured_instances


__all__: list[str] = []
