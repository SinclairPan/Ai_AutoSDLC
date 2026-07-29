"""传播 framework owner 在未知调用边界上的别名污点。"""

from __future__ import annotations

import ast
from collections.abc import Mapping
from typing import Protocol

from ai_sdlc.core.lean_code_framework_effects import (
    _bound_names,
    _target_root_name,
)
from ai_sdlc.core.lean_code_framework_owner_flow import (
    _statement_owner_effects,
)


class _OwnerAliasState(Protocol):
    contract_owner_aliases: dict[str, set[str]]


class _OwnerBindingState(_OwnerAliasState, Protocol):
    def bind_contract_owner_alias(self, name: str, owners: set[str]) -> None: ...


def _assignment_owner_bindings(
    targets: list[ast.expr],
    value: ast.expr,
    state: _OwnerAliasState,
) -> dict[str, set[str]]:
    bindings: dict[str, set[str]] = {}
    for target in targets:
        for name, owners in _target_owner_bindings(target, value, state).items():
            bindings.setdefault(name, set()).update(owners)
    return bindings


def _statement_owner_bindings(
    node: ast.AST,
    state: _OwnerAliasState,
) -> dict[str, set[str]]:
    return _statement_owner_effects(node, state).aliases


def _bind_live_contract_owner_aliases(
    bindings: dict[str, set[str]],
    state: _OwnerBindingState,
    contracts: Mapping[str, object],
) -> None:
    live_owners = {
        symbol.split(".", 1)[0]
        for symbol in contracts
    }
    ordered = sorted(
        bindings.items(),
        key=lambda item: (item[0] not in live_owners, item[0]),
    )
    for name, owners in ordered:
        live = owners & live_owners
        if live:
            state.bind_contract_owner_alias(name, live)


def _indirect_target_roots(node: ast.AST) -> set[str]:
    finder = _IndirectTargetRootFinder()
    finder.visit(node)
    return finder.roots


def _target_owner_bindings(
    target: ast.expr,
    value: ast.expr,
    state: _OwnerAliasState,
) -> dict[str, set[str]]:
    if (
        isinstance(target, (ast.Tuple, ast.List))
        and isinstance(value, (ast.Tuple, ast.List))
        and len(target.elts) == len(value.elts)
        and not any(isinstance(item, ast.Starred) for item in target.elts)
    ):
        bindings: dict[str, set[str]] = {}
        for child_target, child_value in zip(
            target.elts,
            value.elts,
            strict=True,
        ):
            bindings.update(_target_owner_bindings(child_target, child_value, state))
        return bindings
    owners = _contained_contract_owners(value, state)
    names = _bound_names(target)
    root = _target_root_name(target)
    if not names and root:
        names = {root}
    return {name: set(owners) for name in names if owners}


def _contained_contract_owners(
    node: ast.AST,
    state: _OwnerAliasState,
) -> set[str]:
    owners: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Name):
            owners.update(state.contract_owner_aliases.get(child.id, set()))
    return owners


class _StatementOwnerFinder(ast.NodeVisitor):
    def __init__(self, state: _OwnerAliasState) -> None:
        self.contract_owner_aliases = {
            name: set(owners)
            for name, owners in state.contract_owner_aliases.items()
        }
        self.bindings: dict[str, set[str]] = {}

    def visit_Assign(self, node: ast.Assign) -> None:
        self.visit(node.value)
        for target in node.targets:
            self._record(target, node.value)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if node.annotation is not None:
            self.visit(node.annotation)
        if node.value is not None:
            self.visit(node.value)
            self._record(node.target, node.value)

    def visit_NamedExpr(self, node: ast.NamedExpr) -> None:
        self._record(node.target, node.value)
        self.visit(node.value)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        self._record(node.target, node.value, preserve_target=True)
        self.visit(node.value)

    def visit_For(self, node: ast.For) -> None:
        self._visit_loop(node)

    def visit_AsyncFor(self, node: ast.AsyncFor) -> None:
        self._visit_loop(node)

    def visit_With(self, node: ast.With) -> None:
        self._visit_with(node)

    def visit_AsyncWith(self, node: ast.AsyncWith) -> None:
        self._visit_with(node)

    def visit_Match(self, node: ast.Match) -> None:
        self.visit(node.subject)
        owners = _contained_contract_owners(node.subject, self)
        for case in node.cases:
            for name in _match_pattern_names(case.pattern):
                self._bind(name, owners)
            if case.guard is not None:
                self.visit(case.guard)
            for statement in case.body:
                self.visit(statement)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_definition_header(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_definition_header(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        for decorator in node.decorator_list:
            self.visit(decorator)
        for base in node.bases:
            self.visit(base)
        for keyword in node.keywords:
            self.visit(keyword.value)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        for default in (*node.args.defaults, *node.args.kw_defaults):
            if default is not None:
                self.visit(default)

    def _visit_definition_header(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
    ) -> None:
        for decorator in node.decorator_list:
            self.visit(decorator)
        for default in (*node.args.defaults, *node.args.kw_defaults):
            if default is not None:
                self.visit(default)

    def _record(
        self,
        target: ast.expr,
        value: ast.expr,
        *,
        preserve_target: bool = False,
    ) -> None:
        bindings = _target_owner_bindings(target, value, self)
        if preserve_target:
            existing = _contained_contract_owners(target, self)
            for name in _bound_names(target):
                bindings.setdefault(name, set()).update(existing)
        for name, owners in bindings.items():
            self._bind(name, owners)

    def _bind(self, name: str, owners: set[str]) -> None:
        if not owners:
            return
        self.bindings.setdefault(name, set()).update(owners)
        self.contract_owner_aliases.setdefault(name, set()).update(owners)

    def _visit_loop(self, node: ast.For | ast.AsyncFor) -> None:
        self.visit(node.iter)
        self._record(node.target, node.iter)
        for statement in (*node.body, *node.orelse):
            self.visit(statement)

    def _visit_with(self, node: ast.With | ast.AsyncWith) -> None:
        for item in node.items:
            self.visit(item.context_expr)
            if item.optional_vars is not None:
                self._record(item.optional_vars, item.context_expr)
        for statement in node.body:
            self.visit(statement)


def _match_pattern_names(pattern: ast.pattern) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(pattern):
        if isinstance(node, (ast.MatchAs, ast.MatchStar)) and node.name:
            names.add(node.name)
        elif isinstance(node, ast.MatchMapping) and node.rest:
            names.add(node.rest)
    return names


class _IndirectTargetRootFinder(ast.NodeVisitor):
    def __init__(self) -> None:
        self.roots: set[str] = set()

    def visit_Attribute(self, node: ast.Attribute) -> None:
        self._record(node)
        self.generic_visit(node)

    def visit_Subscript(self, node: ast.Subscript) -> None:
        self._record(node)
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        return

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        return

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        return

    def visit_Lambda(self, node: ast.Lambda) -> None:
        return

    def _record(self, node: ast.Attribute | ast.Subscript) -> None:
        if isinstance(node.ctx, (ast.Store, ast.Del)):
            root = _target_root_name(node)
            if root:
                self.roots.add(root)


__all__: list[str] = []
