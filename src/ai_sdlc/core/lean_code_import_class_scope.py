"""应用模块与类外层作用域的类身份绑定。"""

from __future__ import annotations

import ast

from ai_sdlc.core.lean_code_import_class_aliases import _parallel_sequence
from ai_sdlc.core.lean_code_import_class_identity import (
    _class_refs,
    _ClassInfo,
    _delete_scope_target,
    _member_value,
    _ScopeState,
)
from ai_sdlc.core.lean_code_import_expression_flow import (
    _named_expression_bindings,
)


def _apply_scope_statement(
    statement: ast.stmt,
    state: _ScopeState,
    infos: dict[int, _ClassInfo],
) -> None:
    if isinstance(statement, (ast.Assign, ast.AnnAssign)):
        targets = (
            statement.targets
            if isinstance(statement, ast.Assign)
            else [statement.target]
        )
        value = statement.value
        if value is None:
            return
        for target in targets:
            _bind_scope_target(target, value, state, infos)
    elif isinstance(statement, ast.Delete):
        for target in statement.targets:
            _delete_scope_target(target, state, infos)
    elif isinstance(statement, ast.Expr):
        for target, value in _named_expression_bindings(statement.value):
            _bind_scope_target(target, value, state, infos)


def _bind_scope_target(
    target: ast.expr,
    value: ast.expr,
    state: _ScopeState,
    infos: dict[int, _ClassInfo],
) -> None:
    if _parallel_sequence(target, value):
        assert isinstance(target, (ast.Tuple, ast.List))
        assert isinstance(value, (ast.Tuple, ast.List))
        for child_target, child_value in zip(target.elts, value.elts, strict=True):
            _bind_scope_target(child_target, child_value, state, infos)
        return
    refs = _class_refs(value, state.bindings, infos)
    if isinstance(target, ast.Name):
        if refs:
            state.bindings[target.id] = refs
        else:
            state.bindings.pop(target.id, None)
    elif isinstance(target, ast.Attribute):
        for class_id in _class_refs(target.value, state.bindings, infos):
            info = infos.get(class_id)
            if info is not None:
                info.members[target.attr] = _member_value(
                    value,
                    state.bindings,
                    infos,
                    (),
                )


__all__: list[str] = []
