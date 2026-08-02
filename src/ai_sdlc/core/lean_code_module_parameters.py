"""绑定模块定义期调用的结构化实参与默认值。"""

from __future__ import annotations

import ast
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from typing import Protocol

from ai_sdlc.core.lean_code_control_flow import _function_annotation_expressions
from ai_sdlc.core.lean_code_module_values import (
    _CallableValue,
    _merge_values,
    _value_key,
)


@dataclass(frozen=True)
class _Invocation:
    function_id: int
    positional: tuple[tuple[bool, _CallableValue], ...]
    keywords: tuple[tuple[str | None, _CallableValue], ...]


class _InvocationState(Protocol):
    def refs(
        self,
        expression: ast.expr,
        overrides: Mapping[str, set[int]] | None = None,
    ) -> set[int]: ...

    def register_callable(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda,
    ) -> int: ...

    def all_callable_refs(self) -> set[int]: ...

    def callable_returns_dynamic(
        self,
        function_id: int,
        modules: Collection[str],
        callables: Collection[str],
    ) -> bool: ...


class _HeaderVisitor(Protocol):
    def visit(self, node: ast.AST) -> None: ...


def _visit_callable_header(
    visitor: _HeaderVisitor,
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    *,
    evaluate_annotations: bool = True,
) -> None:
    for expression in (
        *node.decorator_list,
        *getattr(node, "type_params", ()),
        *node.args.defaults,
        *(item for item in node.args.kw_defaults if item is not None),
        *(
            _function_annotation_expressions(node)
            if evaluate_annotations
            else ()
        ),
    ):
        visitor.visit(expression)


def _visit_argument_defaults(
    visitor: _HeaderVisitor,
    arguments: ast.arguments,
) -> None:
    for default in (*arguments.defaults, *arguments.kw_defaults):
        if default is not None:
            visitor.visit(default)


def _invocation_parameter_refs(
    arguments: ast.arguments,
    invocation: _Invocation,
    defaults: Mapping[str, _CallableValue],
) -> dict[str, _CallableValue]:
    refs = dict(defaults)
    positional, expanded, unresolved_positional = _bind_positional_refs(
        arguments,
        invocation,
        refs,
    )
    valid_keywords, keywords, unresolved_keywords = _bind_keyword_refs(
        arguments,
        invocation,
        refs,
        positional,
    )
    if arguments.vararg is not None:
        refs[arguments.vararg.arg] = _CallableValue(
            sequence=tuple(
                [*expanded[len(positional) :], *unresolved_positional]
            )
        )
    if arguments.kwarg is not None:
        refs[arguments.kwarg.arg] = _CallableValue(
            mapping=tuple(
                (name, value)
                for name, value in keywords.items()
                if name not in valid_keywords
            ),
            refs=frozenset().union(
                *(value.refs for value in unresolved_keywords)
            ),
            unknown=bool(unresolved_keywords),
            dynamic=any(value.dynamic for value in unresolved_keywords),
        )
    return refs


def _bind_positional_refs(
    arguments: ast.arguments,
    invocation: _Invocation,
    refs: dict[str, _CallableValue],
) -> tuple[list[ast.arg], list[_CallableValue], list[_CallableValue]]:
    expanded: list[_CallableValue] = []
    unresolved: list[_CallableValue] = []
    for unpack, value in invocation.positional:
        if unpack and value.sequence is not None:
            expanded.extend(value.sequence)
        elif unpack:
            unresolved.append(value)
        else:
            expanded.append(value)
    positional = [*arguments.posonlyargs, *arguments.args]
    for argument, value in zip(positional, expanded, strict=False):
        refs[argument.arg] = value
    for value in unresolved:
        for argument in positional:
            refs[argument.arg] = _merge_values(
                (refs.get(argument.arg, _CallableValue()), value)
            )
    return positional, expanded, unresolved


def _bind_keyword_refs(
    arguments: ast.arguments,
    invocation: _Invocation,
    refs: dict[str, _CallableValue],
    positional: list[ast.arg],
) -> tuple[set[str], dict[str, _CallableValue], list[_CallableValue]]:
    valid = {argument.arg for argument in (*positional, *arguments.kwonlyargs)}
    keywords: dict[str, _CallableValue] = {}
    unresolved: list[_CallableValue] = []
    for name, value in invocation.keywords:
        if name is None and value.mapping is not None:
            keywords.update((str(key), item) for key, item in value.mapping)
        elif name is None:
            unresolved.append(value)
        else:
            keywords[name] = value
    for name, value in keywords.items():
        if name in valid:
            refs[name] = value
    for value in unresolved:
        for name in valid:
            refs[name] = _merge_values(
                (refs.get(name, _CallableValue()), value)
            )
    return valid, keywords, unresolved


def _invocation_key(
    invocation: _Invocation,
    parameters: Mapping[str, _CallableValue],
) -> tuple[int, tuple[tuple[str, tuple[object, ...]], ...]]:
    return (
        invocation.function_id,
        tuple(
            sorted(
                (name, _value_key(values))
                for name, values in parameters.items()
            )
        ),
    )


__all__: list[str] = []
