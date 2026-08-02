"""按类身份、顺序绑定与 C3 MRO 传播动态工厂能力。"""

from __future__ import annotations

import ast
from collections.abc import Collection, Iterable
from dataclasses import dataclass

from ai_sdlc.core.lean_code_import_class_aliases import _reflective_member_name
from ai_sdlc.core.lean_code_import_class_identity import (
    _class_refs,
    _ClassCandidate,
    _ClassInfo,
    _member_value,
    _merge_scope_states,
    _possible_mros,
    _qualified_paths,
    _ScopeState,
)
from ai_sdlc.core.lean_code_import_class_members import (
    _descriptor_kind,
    _has_unknown_decorator,
    _Member,
    _merge_member_maps,
)
from ai_sdlc.core.lean_code_import_class_paths import _statement_paths
from ai_sdlc.core.lean_code_import_class_scope import _apply_scope_statement
from ai_sdlc.core.lean_code_import_descriptor_scope import (
    _resolve_descriptor_decorators,
)
from ai_sdlc.core.lean_code_import_expression_flow import _named_expression_bindings


@dataclass(frozen=True)
class _ClassFlowContext:
    candidates: dict[int, _ClassCandidate]
    infos: dict[int, _ClassInfo]
    dynamic_nodes: frozenset[int]
    dynamic_names: dict[tuple[int, ...], frozenset[str]]


def _dynamic_class_callable_paths(
    tree: ast.Module,
    classes: Collection[_ClassCandidate],
    dynamic_nodes: Collection[int],
    dynamic_names_by_scope: dict[tuple[int, ...], frozenset[str]],
) -> dict[int, frozenset[str]]:
    _resolve_descriptor_decorators(tree)
    candidates = {id(item.node): item for item in classes}
    infos: dict[int, _ClassInfo] = {}
    context = _ClassFlowContext(
        candidates,
        infos,
        frozenset(dynamic_nodes),
        dynamic_names_by_scope,
    )
    _process_scope(
        tree.body,
        (),
        _ScopeState({}),
        context,
    )
    queried_names = frozenset(
        name
        for node in ast.walk(tree)
        if isinstance(node, ast.expr)
        for name in (
            node.attr if isinstance(node, ast.Attribute) else "",
            _reflective_member_name(node),
        )
        if name
    )
    return _qualified_paths(classes, infos, queried_names)


def _process_scope(
    statements: Iterable[ast.stmt],
    scope: tuple[int, ...],
    state: _ScopeState,
    context: _ClassFlowContext,
) -> None:
    for statement in statements:
        _process_scope_statement(
            statement,
            scope,
            state,
            context,
        )


def _process_scope_statement(
    statement: ast.stmt,
    scope: tuple[int, ...],
    state: _ScopeState,
    context: _ClassFlowContext,
) -> None:
    blocks: tuple[list[ast.stmt], ...]
    if isinstance(statement, ast.ClassDef):
        info = _build_class(
            statement,
            scope,
            state,
            context,
        )
        state.bindings[statement.name] = {id(info.candidate.node)}
        return
    if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
        state.bindings.pop(statement.name, None)
        blocks = (statement.body,)
        child_scope = (*scope, id(statement))
    elif (paths := _statement_paths(statement)) is not None:
        _process_scope_paths(paths, scope, state, context)
        return
    else:
        _apply_scope_statement(statement, state, context.infos)
        blocks = ()
        child_scope = scope
    for block in blocks:
        _process_scope(
            block,
            child_scope,
            state.fork() if child_scope != scope else state,
            context,
        )


def _build_class(
    node: ast.ClassDef,
    scope: tuple[int, ...],
    outer: _ScopeState,
    context: _ClassFlowContext,
) -> _ClassInfo:
    candidate = context.candidates[id(node)]
    base_choices = [
        _class_refs(base, outer.bindings, context.infos) for base in node.bases
    ]
    mros = _possible_mros(id(node), base_choices, context.infos)
    metaclass_refs = frozenset().union(
        *(
            _class_refs(keyword.value, outer.bindings, context.infos)
            for keyword in node.keywords
            if keyword.arg == "metaclass"
        )
    )
    info = _ClassInfo(candidate, mros, metaclass_refs)
    context.infos[id(node)] = info
    class_scope = (*scope, id(node))
    local = outer.fork()
    for statement in node.body:
        _apply_class_statement(
            statement,
            info,
            class_scope,
            local,
            context,
        )
    return info


def _apply_class_statement(
    statement: ast.stmt,
    info: _ClassInfo,
    scope: tuple[int, ...],
    state: _ScopeState,
    context: _ClassFlowContext,
) -> None:
    if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
        _apply_class_function(
            statement,
            info,
            scope,
            state,
            context,
        )
        return
    if isinstance(statement, ast.ClassDef):
        _apply_nested_class(
            statement,
            info,
            scope,
            state,
            context,
        )
        return
    _apply_class_non_definition(statement, info, scope, state, context)


def _apply_class_non_definition(
    statement: ast.stmt,
    info: _ClassInfo,
    scope: tuple[int, ...],
    state: _ScopeState,
    context: _ClassFlowContext,
) -> None:
    if isinstance(statement, (ast.Assign, ast.AnnAssign)):
        _apply_class_assignment(
            statement,
            info,
            state,
            context.infos,
            context.dynamic_names.get(
                info.candidate.parent_scope,
                frozenset(),
            ),
        )
        return
    if isinstance(statement, ast.Delete):
        for target in statement.targets:
            if isinstance(target, ast.Name):
                info.members.pop(target.id, None)
                state.bindings.pop(target.id, None)
        return
    if isinstance(statement, ast.Expr):
        _apply_named_bindings(
            statement.value,
            info,
            state,
            context,
        )
        return
    paths = _statement_paths(statement)
    if paths is not None:
        _apply_class_paths(paths, info, scope, state, context)
        return


def _apply_class_function(
    statement: ast.FunctionDef | ast.AsyncFunctionDef,
    info: _ClassInfo,
    scope: tuple[int, ...],
    state: _ScopeState,
    context: _ClassFlowContext,
) -> None:
    dynamic = (
        id(statement) in context.dynamic_nodes
        or _has_unknown_decorator(statement)
    )
    descriptor = _descriptor_kind(statement)
    info.members[statement.name] = _Member(
        dynamic=dynamic,
        raw_dynamic=dynamic and descriptor not in {"classmethod", "property"},
        bound_dynamic=dynamic and bool(descriptor),
    )
    state.bindings.pop(statement.name, None)
    _process_scope(
        statement.body,
        (*scope, id(statement)),
        state.fork(),
        context,
    )


def _apply_nested_class(
    statement: ast.ClassDef,
    info: _ClassInfo,
    scope: tuple[int, ...],
    state: _ScopeState,
    context: _ClassFlowContext,
) -> None:
    nested = _build_class(
        statement,
        scope,
        state,
        context,
    )
    class_id = id(nested.candidate.node)
    info.members[statement.name] = _Member(class_refs=frozenset({class_id}))
    state.bindings[statement.name] = {class_id}


def _apply_class_assignment(
    statement: ast.Assign | ast.AnnAssign,
    info: _ClassInfo,
    state: _ScopeState,
    infos: dict[int, _ClassInfo],
    dynamic_names: Collection[str],
) -> None:
    targets = (
        statement.targets if isinstance(statement, ast.Assign) else [statement.target]
    )
    value = statement.value
    if value is None:
        return
    member = _member_value(value, state.bindings, infos, dynamic_names)
    for target in targets:
        if isinstance(target, ast.Name):
            info.members[target.id] = member
            if member.class_refs:
                state.bindings[target.id] = set(member.class_refs)
            else:
                state.bindings.pop(target.id, None)


def _apply_named_bindings(
    expression: ast.expr,
    info: _ClassInfo,
    state: _ScopeState,
    context: _ClassFlowContext,
) -> None:
    for target, value in _named_expression_bindings(expression):
        assignment = ast.Assign(targets=[target], value=value)
        _apply_class_assignment(
            assignment,
            info,
            state,
            context.infos,
            context.dynamic_names.get(info.candidate.parent_scope, frozenset()),
        )

def _process_scope_paths(
    paths: tuple[tuple[list[ast.stmt], ...], ...],
    scope: tuple[int, ...],
    state: _ScopeState,
    context: _ClassFlowContext,
) -> None:
    branches = []
    for path in paths:
        branch = state.fork()
        for block in path:
            _process_scope(block, scope, branch, context)
        branches.append(branch)
    _merge_scope_states(state, branches)


def _apply_class_paths(
    paths: tuple[tuple[list[ast.stmt], ...], ...],
    info: _ClassInfo,
    scope: tuple[int, ...],
    state: _ScopeState,
    context: _ClassFlowContext,
) -> None:
    original = dict(info.members)
    outcomes: list[dict[str, _Member]] = []
    states: list[_ScopeState] = []
    for path in paths:
        info.members = dict(original)
        branch = state.fork()
        for block in path:
            for child in block:
                _apply_class_statement(child, info, scope, branch, context)
        outcomes.append(dict(info.members))
        states.append(branch)
    info.members = _merge_member_maps(outcomes)
    _merge_scope_states(state, states)


__all__: list[str] = []
