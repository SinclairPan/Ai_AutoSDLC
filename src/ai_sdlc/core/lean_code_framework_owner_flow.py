"""按可达执行路径传播 framework contract owner 别名。"""

from __future__ import annotations

import ast
from collections.abc import Collection, Iterable, MutableMapping
from dataclasses import dataclass
from typing import Protocol, TypeVar

from ai_sdlc.core.lean_code_control_flow import (
    _reachable_match_cases,
    _static_truth,
    _statically_empty,
    _statically_nonempty,
)
from ai_sdlc.core.lean_code_exception_flow import _statement_exception_points
from ai_sdlc.core.lean_code_exception_handlers import _reachable_exception_handlers
from ai_sdlc.core.lean_code_framework_effects import _bound_names
from ai_sdlc.core.lean_code_framework_resolution import _qualified_name


class _OwnerState(Protocol):
    contract_owner_aliases: dict[str, set[str]]


class _ContractAliasState(Protocol):
    def invalidate_contract_owner_alias(self, name: str) -> None: ...


_ContractValue = TypeVar("_ContractValue")
@dataclass(frozen=True)
class _OwnerEffects:
    aliases: dict[str, set[str]]
    touched_owners: frozenset[str]
def _statement_owner_effects(
    node: ast.AST,
    state: _OwnerState,
    *,
    owners: Collection[str] = (),
    safe_calls: Collection[str] = (),
) -> _OwnerEffects:
    flow = _OwnerFlow(
        state.contract_owner_aliases,
        frozenset(owners),
        frozenset(safe_calls),
    )
    flow.visit(node)
    return _OwnerEffects(
        aliases={name: set(values) for name, values in flow.aliases.items()},
        touched_owners=frozenset(flow.touched),
    )


def _invalidate_contract_owners(
    owners: Collection[str],
    contracts: MutableMapping[str, _ContractValue],
    state: _ContractAliasState,
) -> None:
    for owner in owners:
        for symbol in tuple(contracts):
            if symbol == owner or symbol.startswith(f"{owner}."):
                contracts.pop(symbol, None)
        state.invalidate_contract_owner_alias(owner)


class _OwnerFlow(ast.NodeVisitor):
    def __init__(
        self,
        aliases: dict[str, set[str]],
        owners: frozenset[str],
        safe_calls: frozenset[str],
    ) -> None:
        self.aliases = {name: set(values) for name, values in aliases.items()}
        self.owners = owners
        self.safe_calls = safe_calls
        self.touched: set[str] = set()
        self.control = "normal"

    def visit_Assign(self, node: ast.Assign) -> None:
        self.visit(node.value)
        for target in node.targets:
            self._bind_target(target, node.value)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        self.visit(node.annotation)
        if node.value is not None:
            self.visit(node.value)
            self._bind_target(node.target, node.value)

    def visit_NamedExpr(self, node: ast.NamedExpr) -> None:
        self.visit(node.value)
        self._bind_target(node.target, node.value)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        owners = self._owners_in(node.target) | self._owners_in(node.value)
        self.visit(node.target)
        self.visit(node.value)
        self._bind_names(_bound_names(node.target), owners)

    def visit_If(self, node: ast.If) -> None:
        self.visit(node.test)
        truth = _static_truth(node.test)
        if truth is not None:
            self._visit_statements(node.body if truth else node.orelse)
            return
        self._merge_blocks((node.body, node.orelse))

    def visit_For(self, node: ast.For) -> None:
        self._visit_loop(node)

    def visit_AsyncFor(self, node: ast.AsyncFor) -> None:
        self._visit_loop(node)

    def visit_While(self, node: ast.While) -> None:
        self.visit(node.test)
        truth = _static_truth(node.test)
        if truth is False:
            self._visit_statements(node.orelse)
            return
        iteration = self._branch(node.body)
        if truth is True and iteration.control in {"normal", "continue"}:
            iteration.control = "loop"
            self._merge((iteration,))
            return
        if iteration.control == "break":
            iteration.control = "normal"
        elif iteration.control in {"normal", "continue"}:
            iteration.control = "normal"
            iteration._visit_statements(node.orelse)
        if truth is True:
            self._merge((iteration,))
            return
        skipped = self._branch(node.orelse)
        self._merge((iteration, skipped))

    def visit_With(self, node: ast.With) -> None:
        self._visit_with(node)

    def visit_AsyncWith(self, node: ast.AsyncWith) -> None:
        self._visit_with(node)

    def visit_Try(self, node: ast.Try) -> None:
        self._visit_try(node)

    def visit_TryStar(self, node: ast.TryStar) -> None:
        self._visit_try(node)

    def visit_Match(self, node: ast.Match) -> None:
        self.visit(node.subject)
        subject_owners = self._owners_in(node.subject)
        blocks: list[tuple[list[ast.stmt], set[str]]] = []
        cases, no_match = _reachable_match_cases(node.subject, node.cases)
        for case in cases:
            names = _match_pattern_names(case.pattern)
            block = list(case.body)
            if case.guard is not None:
                block.insert(0, ast.Expr(value=case.guard))
            blocks.append((block, names))
        outcomes = []
        for block, names in blocks:
            branch = self._fork()
            branch._bind_names(names, subject_owners)
            branch._visit_statements(block)
            outcomes.append(branch)
        if no_match:
            outcomes.append(self._fork())
        self._merge(outcomes)

    def visit_Return(self, node: ast.Return) -> None:
        if node.value is not None:
            self.visit(node.value)
        self.control = "return"

    def visit_Raise(self, node: ast.Raise) -> None:
        for expression in (node.exc, node.cause):
            if expression is not None:
                self.visit(expression)
        self.control = "raise"

    def visit_Break(self, node: ast.Break) -> None:
        self.control = "break"

    def visit_Continue(self, node: ast.Continue) -> None:
        self.control = "continue"

    def visit_Call(self, node: ast.Call) -> None:
        call_name = _qualified_name(node.func)
        if call_name not in self.safe_calls and not _constant_print_call(node):
            names = {
                child.id for child in ast.walk(node) if isinstance(child, ast.Name)
            }
            direct = names & self.owners
            aliased = set().union(*(self.aliases.get(name, set()) for name in names))
            self.touched.update(direct | aliased)
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function_header(node)
        self._bind_names({node.name}, set())

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function_header(node)
        self._bind_names({node.name}, set())

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        for expression in (
            *node.decorator_list,
            *node.bases,
            *(keyword.value for keyword in node.keywords),
            *getattr(node, "type_params", ()),
        ):
            self.visit(expression)
        self._bind_names({node.name}, set())

    def visit_Lambda(self, node: ast.Lambda) -> None:
        self._visit_defaults(node.args)

    def _visit_loop(self, node: ast.For | ast.AsyncFor) -> None:
        self.visit(node.iter)
        if _statically_empty(node.iter):
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
        if _statically_nonempty(node.iter):
            self._merge((iteration,))
        else:
            skipped = self._fork()
            skipped._visit_statements(node.orelse)
            self._merge((iteration, skipped))

    def _visit_with(self, node: ast.With | ast.AsyncWith) -> None:
        for item in node.items:
            self.visit(item.context_expr)
            if item.optional_vars is not None:
                self._bind_target(item.optional_vars, item.context_expr)
        self._visit_statements(node.body)

    def _visit_try(self, node: ast.Try | ast.TryStar) -> None:
        body, possible_exceptions = self._visit_try_body(node.body)
        outcomes = []
        if body.control == "normal":
            body._visit_statements(node.orelse)
            outcomes.append(body)
        else:
            outcomes.append(body)
        outcomes.extend(
            source._branch_with_normal_control(handler.body)
            for source, raised in possible_exceptions
            for handler in _reachable_exception_handlers(
                node.handlers,
                raised,
            )[0]
        )
        for outcome in outcomes:
            previous = outcome.control
            outcome.control = "normal"
            outcome._visit_statements(node.finalbody)
            if outcome.control == "normal":
                outcome.control = previous
        self._merge(outcomes)

    def _visit_try_body(
        self,
        statements: Iterable[ast.stmt],
    ) -> tuple[_OwnerFlow, tuple[tuple[_OwnerFlow, str | None], ...]]:
        branch = self._fork()
        exceptions: list[tuple[_OwnerFlow, str | None]] = []
        for statement in statements:
            if branch.control != "normal":
                break
            for point in _statement_exception_points(statement):
                prefix = branch._fork()
                prefix._visit_statements(point.prefix)
                exceptions.append((prefix, point.raised))
            branch.visit(statement)
        return branch, tuple(exceptions)

    def _visit_function_header(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
    ) -> None:
        for expression in (
            *node.decorator_list,
            *node.args.defaults,
            *(item for item in node.args.kw_defaults if item is not None),
            *getattr(node, "type_params", ()),
        ):
            self.visit(expression)

    def _visit_defaults(self, arguments: ast.arguments) -> None:
        for default in (*arguments.defaults, *arguments.kw_defaults):
            if default is not None:
                self.visit(default)

    def _visit_statements(self, statements: Iterable[ast.stmt]) -> None:
        for statement in statements:
            if self.control != "normal":
                break
            self.visit(statement)

    def _bind_target(self, target: ast.expr, value: ast.expr) -> None:
        if _parallel_sequence(target, value):
            assert isinstance(target, (ast.Tuple, ast.List))
            assert isinstance(value, (ast.Tuple, ast.List))
            for child_target, child_value in zip(
                target.elts,
                value.elts,
                strict=True,
            ):
                self._bind_target(child_target, child_value)
            return
        self._bind_names(_bound_names(target), self._owners_in(value))

    def _bind_names(self, names: Collection[str], owners: Collection[str]) -> None:
        for name in names:
            if owners:
                self.aliases[name] = set(owners)
            else:
                self.aliases.pop(name, None)

    def _owners_in(self, node: ast.AST) -> set[str]:
        names = {child.id for child in ast.walk(node) if isinstance(child, ast.Name)}
        return (names & self.owners) | set().union(
            *(self.aliases.get(name, set()) for name in names)
        )

    def _branch(self, statements: Iterable[ast.stmt]) -> _OwnerFlow:
        branch = self._fork()
        branch._visit_statements(statements)
        return branch

    def _branch_with_normal_control(
        self,
        statements: Iterable[ast.stmt],
    ) -> _OwnerFlow:
        branch = self._fork()
        branch.control = "normal"
        branch._visit_statements(statements)
        return branch

    def _merge_blocks(self, blocks: Iterable[list[ast.stmt]]) -> None:
        self._merge(tuple(self._branch(block) for block in blocks))

    def _fork(self) -> _OwnerFlow:
        branch = _OwnerFlow(self.aliases, self.owners, self.safe_calls)
        branch.touched = set(self.touched)
        branch.control = self.control
        return branch

    def _merge(self, outcomes: Iterable[_OwnerFlow]) -> None:
        branches = tuple(outcomes)
        names = set().union(*(branch.aliases.keys() for branch in branches))
        self.aliases = {
            name: set().union(*(branch.aliases.get(name, set()) for branch in branches))
            for name in names
            if any(branch.aliases.get(name) for branch in branches)
        }
        self.touched = set().union(*(branch.touched for branch in branches))
        controls = {branch.control for branch in branches}
        self.control = controls.pop() if len(controls) == 1 else "normal"


def _parallel_sequence(target: ast.expr, value: ast.expr) -> bool:
    return (
        isinstance(target, (ast.Tuple, ast.List))
        and isinstance(value, (ast.Tuple, ast.List))
        and len(target.elts) == len(value.elts)
        and not any(isinstance(item, ast.Starred) for item in target.elts)
    )


def _match_pattern_names(pattern: ast.pattern) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(pattern):
        if isinstance(node, (ast.MatchAs, ast.MatchStar)) and node.name:
            names.add(node.name)
        elif isinstance(node, ast.MatchMapping) and node.rest:
            names.add(node.rest)
    return names


def _constant_print_call(node: ast.Call) -> bool:
    return (
        isinstance(node.func, ast.Name)
        and node.func.id == "print"
        and not node.keywords
        and all(isinstance(argument, ast.Constant) for argument in node.args)
    )


__all__: list[str] = []
