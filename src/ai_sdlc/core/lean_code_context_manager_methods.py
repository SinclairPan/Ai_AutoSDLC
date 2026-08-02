"""从已执行的进入方法和已知基类解析上下文管理器血缘。"""

from __future__ import annotations

import ast
from collections.abc import Callable
from typing import Protocol, TypeVar

from ai_sdlc.core.lean_code_context_manager_lineage import (
    _context_manager_expression_protocols,
    _ContextManagerProtocols,
    _ProtocolLineage,
)
from ai_sdlc.core.lean_code_generator_identity import _merge_lineages
from ai_sdlc.core.lean_code_scope import _bound_names

_RETURN_LINEAGE_ATTRIBUTE = "_ai_sdlc_context_manager_return_lineage"


class _ManagerState(Protocol):
    generators: dict[str, tuple[ast.GeneratorExp, ...]]
    context_manager_protocols: dict[str, _ContextManagerProtocols]


_StateT = TypeVar("_StateT", bound=_ManagerState)


def _context_manager_generator_lineage(
    node: ast.ClassDef,
    state: _StateT,
    compile_function_body: Callable[
        [ast.FunctionDef | ast.AsyncFunctionDef, _StateT],
        object,
    ],
) -> _ContextManagerProtocols:
    inherited = tuple(
        _context_manager_expression_protocols(base, state) for base in node.bases
    )
    protocols = _ContextManagerProtocols(
        sync=_local_protocol(
            node.body,
            "__enter__",
            tuple(value.sync for value in inherited),
            state,
            compile_function_body,
        ),
        async_=_local_protocol(
            node.body,
            "__aenter__",
            tuple(value.async_ for value in inherited),
            state,
            compile_function_body,
        ),
    )
    if not node.decorator_list:
        return protocols
    return _ContextManagerProtocols(
        sync=_uncertain_protocol(protocols.sync),
        async_=_uncertain_protocol(protocols.async_),
    )


def _local_protocol(
    body: list[ast.stmt],
    name: str,
    inherited: tuple[_ProtocolLineage, ...],
    state: _StateT,
    compile_function_body: Callable[
        [ast.FunctionDef | ast.AsyncFunctionDef, _StateT],
        object,
    ],
) -> _ProtocolLineage:
    method, mutation = _final_protocol_binding(body, name)
    if mutation is not None:
        return _ProtocolLineage(defined=mutation, uncertain=True)
    return _resolved_protocol(
        method,
        inherited,
        state,
        compile_function_body,
    )


def _resolved_protocol(
    method: ast.FunctionDef | ast.AsyncFunctionDef | None,
    inherited: tuple[_ProtocolLineage, ...],
    state: _StateT,
    compile_function_body: Callable[
        [ast.FunctionDef | ast.AsyncFunctionDef, _StateT],
        object,
    ],
) -> _ProtocolLineage:
    if method is not None:
        compile_function_body(method, state)
        lineage = _merge_lineages(
            *(
                getattr(child, _RETURN_LINEAGE_ATTRIBUTE, ())
                for child in _immediate_returns(method)
            )
        )
        return _ProtocolLineage(
            defined=True,
            generators=lineage,
            uncertain=bool(method.decorator_list),
        )
    for protocol in inherited:
        if protocol.defined or protocol.uncertain:
            return protocol
    return _ProtocolLineage()


def _final_protocol_binding(
    body: list[ast.stmt],
    name: str,
) -> tuple[ast.FunctionDef | ast.AsyncFunctionDef | None, bool | None]:
    method = None
    mutation = None
    for statement in body:
        if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if statement.name == name:
                method = statement
                mutation = None
        else:
            rebound = _protocol_mutation(statement, name)
            if rebound is not None:
                method = None
                mutation = rebound
    return method, mutation


def _protocol_mutation(statement: ast.stmt, name: str) -> bool | None:
    if isinstance(statement, ast.Assign):
        if any(name in _bound_names(target) for target in statement.targets):
            return True
    elif isinstance(statement, ast.AnnAssign):
        if statement.value is not None and name in _bound_names(statement.target):
            return True
    elif isinstance(statement, ast.Delete) and any(
        name in _bound_names(target) for target in statement.targets
    ):
        return False
    return None


def _uncertain_protocol(protocol: _ProtocolLineage) -> _ProtocolLineage:
    return _ProtocolLineage(
        defined=protocol.defined,
        generators=protocol.generators,
        uncertain=True,
    )


def _immediate_returns(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> tuple[ast.Return, ...]:
    finder = _ImmediateReturnFinder()
    for statement in node.body:
        finder.visit(statement)
    return tuple(finder.nodes)


class _ImmediateReturnFinder(ast.NodeVisitor):
    def __init__(self) -> None:
        self.nodes: list[ast.Return] = []

    def visit_Return(self, node: ast.Return) -> None:
        self.nodes.append(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        return

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        return

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        return

    def visit_Lambda(self, node: ast.Lambda) -> None:
        return


__all__: list[str] = []
