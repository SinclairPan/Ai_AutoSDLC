"""延迟对象从创建到真实执行或逸出的身份传播入口。"""

from __future__ import annotations

import ast

from ai_sdlc.core.lean_code_identity_flow import (
    _trace_identity,
    _trace_target_calls,
    _trace_target_references,
)
from ai_sdlc.core.lean_code_identity_models import (
    _IdentityPath,
    _IdentityTrace,
    _IdentityValue,
)

_FunctionNode = ast.FunctionDef | ast.AsyncFunctionDef


def _target_execution_anchors(
    scope: ast.Module | _FunctionNode | ast.ClassDef,
    imported_callables: dict[str, _IdentityValue],
) -> tuple[tuple[tuple[str, str], ast.AST], ...]:
    return _trace_target_calls(scope, imported_callables)


def _target_reference_anchors(
    scope: ast.Module | _FunctionNode | ast.ClassDef,
    imported_callables: dict[str, _IdentityValue],
) -> tuple[tuple[tuple[str, str], ast.AST], ...]:
    return _trace_target_references(scope, imported_callables)


def _callable_origin_anchors(
    scope: ast.Module | _FunctionNode | ast.ClassDef,
    origin: ast.AST,
    imported_callables: dict[str, _IdentityValue] | None = None,
) -> tuple[ast.AST, ...]:
    return _trace_identity(
        scope,
        origin,
        "callable",
        imported_callables=imported_callables,
    ).anchors


def _nested_execution_anchors(
    owner: _FunctionNode,
    outer: _FunctionNode,
    parents: dict[ast.AST, ast.AST],
    imported_callables: dict[str, _IdentityValue] | None = None,
) -> tuple[ast.AST, ...]:
    return _nested_execution_trace(owner, outer, parents, imported_callables).anchors


def _nested_execution_paths(
    owner: _FunctionNode,
    outer: _FunctionNode,
    parents: dict[ast.AST, ast.AST],
    imported_callables: dict[str, _IdentityValue] | None = None,
) -> tuple[_IdentityPath, ...]:
    return _nested_execution_trace(owner, outer, parents, imported_callables).paths


def _nested_execution_trace(
    owner: _FunctionNode,
    outer: _FunctionNode,
    parents: dict[ast.AST, ast.AST],
    imported_callables: dict[str, _IdentityValue] | None = None,
) -> _IdentityTrace:
    module = _module_scope(outer, parents)
    return _trace_identity(
        module or outer,
        owner,
        "callable",
        imported_callables=imported_callables,
    )


def _deferred_lambda_anchors(
    owner: ast.Lambda,
    parents: dict[ast.AST, ast.AST],
    imported_callables: dict[str, _IdentityValue] | None = None,
) -> tuple[ast.AST, ...]:
    return _deferred_lambda_trace(owner, parents, imported_callables).anchors


def _deferred_lambda_paths(
    owner: ast.Lambda,
    parents: dict[ast.AST, ast.AST],
    imported_callables: dict[str, _IdentityValue] | None = None,
) -> tuple[_IdentityPath, ...]:
    return _deferred_lambda_trace(owner, parents, imported_callables).paths


def _deferred_lambda_trace(
    owner: ast.Lambda,
    parents: dict[ast.AST, ast.AST],
    imported_callables: dict[str, _IdentityValue] | None = None,
) -> _IdentityTrace:
    scope = _execution_scope(owner, parents)
    if scope is None:
        return _IdentityTrace((), False, False)
    return _trace_identity(
        _module_scope(owner, parents) or scope,
        owner,
        "callable",
        imported_callables=imported_callables,
    )


def _deferred_generator_anchors(
    node: ast.AST,
    parents: dict[ast.AST, ast.AST],
    imported_callables: dict[str, _IdentityValue] | None = None,
) -> tuple[ast.AST, ...]:
    return _deferred_generator_trace(node, parents, imported_callables).anchors


def _deferred_generator_paths(
    node: ast.AST,
    parents: dict[ast.AST, ast.AST],
    imported_callables: dict[str, _IdentityValue] | None = None,
) -> tuple[_IdentityPath, ...]:
    return _deferred_generator_trace(node, parents, imported_callables).paths


def _deferred_generator_trace(
    node: ast.AST,
    parents: dict[ast.AST, ast.AST],
    imported_callables: dict[str, _IdentityValue] | None,
) -> _IdentityTrace:
    generator = _enclosing_generator(node, parents)
    if generator is None:
        return _IdentityTrace((), False, False)
    scope = _execution_scope(generator, parents)
    if scope is None:
        return _IdentityTrace((), False, False)
    root = _module_scope(generator, parents)
    trace = _trace_identity(
        root or scope,
        generator,
        "generator",
        generator,
        imported_callables=imported_callables,
    )
    return trace


def _returned_generator_trace(
    generator: ast.GeneratorExp,
    scope: ast.Module | _FunctionNode | ast.ClassDef,
    parents: dict[ast.AST, ast.AST],
    imported_callables: dict[str, _IdentityValue] | None = None,
) -> _IdentityTrace:
    if not isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return _IdentityTrace((), False, False)
    outer = _execution_scope(scope, parents)
    if outer is None:
        return _IdentityTrace((), False, False)
    return _trace_identity(
        outer,
        scope,
        "factory",
        generator,
        imported_callables=imported_callables,
    )


def _enclosing_generator(
    node: ast.AST,
    parents: dict[ast.AST, ast.AST],
) -> ast.GeneratorExp | None:
    current = parents.get(node)
    while current is not None:
        if isinstance(current, ast.GeneratorExp):
            return current
        current = parents.get(current)
    return None


def _execution_scope(
    node: ast.AST,
    parents: dict[ast.AST, ast.AST],
) -> ast.Module | _FunctionNode | ast.ClassDef | None:
    current = parents.get(node)
    while current is not None:
        if isinstance(
            current, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
        ):
            if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef)) and not any(
                _is_ancestor(item, node, parents) for item in current.body
            ):
                current = parents.get(current)
                continue
            return current
        current = parents.get(current)
    return None


def _module_scope(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> ast.Module | None:
    current: ast.AST | None = node
    while current is not None:
        if isinstance(current, ast.Module):
            return current
        current = parents.get(current)
    return None


def _in_deferred_lambda_body(
    node: ast.AST,
    parents: dict[ast.AST, ast.AST],
) -> bool:
    current = parents.get(node)
    while current is not None:
        if isinstance(current, ast.Lambda):
            return _is_ancestor(current.body, node, parents)
        current = parents.get(current)
    return False


def _enclosing_lambda(
    node: ast.AST,
    parents: dict[ast.AST, ast.AST],
) -> ast.Lambda | None:
    current = parents.get(node)
    while current is not None:
        if isinstance(current, ast.Lambda) and _is_ancestor(
            current.body, node, parents
        ):
            return current
        current = parents.get(current)
    return None


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


__all__: list[str] = []
