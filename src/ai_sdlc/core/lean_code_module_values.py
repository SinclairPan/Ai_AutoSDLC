"""保存模块定义期调用中的结构化可调用值。"""

from __future__ import annotations

import ast
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from typing import Protocol

from ai_sdlc.core.lean_code_import_binding_flow import _DYNAMIC_CALLABLE_NAMES
from ai_sdlc.core.lean_code_import_factory_flow import _returns_dynamic_callable
from ai_sdlc.core.lean_code_static_values import (
    _UNKNOWN,
    _constant_value,
)


@dataclass(frozen=True)
class _CallableValue:
    refs: frozenset[int] = frozenset()
    sequence: tuple[_CallableValue, ...] | None = None
    mapping: tuple[tuple[object, _CallableValue], ...] | None = None
    unknown: bool = False
    dynamic: bool = False


class _ValueState(Protocol):
    def refs(
        self,
        expression: ast.expr,
        overrides: Mapping[str, set[int]] | None = None,
    ) -> set[int]: ...


def _expression_value(
    state: _ValueState,
    expression: ast.expr,
    overrides: Mapping[str, _CallableValue] | None = None,
    dynamic_names: Collection[str] = (),
) -> _CallableValue:
    if isinstance(expression, ast.Starred):
        return _expression_value(
            state,
            expression.value,
            overrides,
            dynamic_names,
        )
    if (
        isinstance(expression, ast.Name)
        and overrides is not None
        and expression.id in overrides
    ):
        return overrides[expression.id]
    structured = _structured_expression_value(
        state,
        expression,
        overrides,
        dynamic_names,
    )
    if structured is not None:
        return structured
    raw_overrides = (
        {name: set(value.refs) for name, value in overrides.items()}
        if overrides is not None
        else None
    )
    refs = frozenset(state.refs(expression, raw_overrides))
    if refs:
        return _CallableValue(refs=refs)
    dynamic = _qualified_name(expression) in dynamic_names
    nested_refs = frozenset().union(
        *(
            state.refs(node, raw_overrides)
            for node in ast.walk(expression)
            if isinstance(node, ast.expr)
        )
    )
    return _CallableValue(
        refs=nested_refs,
        unknown=bool(nested_refs),
        dynamic=dynamic,
    )


def _structured_expression_value(
    state: _ValueState,
    expression: ast.expr,
    overrides: Mapping[str, _CallableValue] | None,
    dynamic_names: Collection[str],
) -> _CallableValue | None:
    if isinstance(expression, (ast.Tuple, ast.List)):
        return _CallableValue(
            sequence=tuple(
                _expression_value(state, item, overrides, dynamic_names)
                for item in expression.elts
            )
        )
    if isinstance(expression, ast.Dict):
        mapping = _dict_value(state, expression, overrides, dynamic_names)
        if mapping is not None:
            return _CallableValue(mapping=tuple(mapping.items()))
    if isinstance(expression, ast.Call):
        constructed = _constructed_value(
            state,
            expression,
            overrides,
            dynamic_names,
        )
        if constructed is not None:
            return constructed
    if isinstance(expression, ast.Subscript):
        selected = _select_value(state, expression, overrides, dynamic_names)
        if selected is not None:
            return selected
    return None


def _merge_values(values: tuple[_CallableValue, ...]) -> _CallableValue:
    if not values:
        return _CallableValue()
    sequences = [value.sequence for value in values]
    sequence = None
    if all(items is not None for items in sequences):
        lengths = {len(items) for items in sequences if items is not None}
        if len(lengths) == 1:
            sequence = tuple(
                _merge_values(
                    tuple(items[index] for items in sequences if items is not None)
                )
                for index in range(lengths.pop())
            )
    mappings = [dict(value.mapping or ()) for value in values]
    mapping = None
    if all(value.mapping is not None for value in values):
        keys = set().union(*(items.keys() for items in mappings))
        mapping = tuple(
            (
                key,
                _merge_values(
                    tuple(items.get(key, _CallableValue()) for items in mappings)
                ),
            )
            for key in sorted(keys, key=repr)
        )
    return _CallableValue(
        refs=frozenset().union(*(value.refs for value in values)),
        sequence=sequence,
        mapping=mapping,
        unknown=any(value.unknown for value in values),
        dynamic=any(value.dynamic for value in values),
    )


def _value_key(value: _CallableValue) -> tuple[object, ...]:
    return (
        tuple(sorted(value.refs)),
        (
            tuple(_value_key(item) for item in value.sequence)
            if value.sequence is not None
            else None
        ),
        (
            tuple((repr(key), _value_key(item)) for key, item in value.mapping)
            if value.mapping is not None
            else None
        ),
        value.unknown,
        value.dynamic,
    )


def _function_returns_dynamic_value(
    state: _ValueState,
    function: ast.FunctionDef | ast.AsyncFunctionDef,
    dynamic_names: Collection[str],
) -> bool:
    finder = _ReturnValueFinder()
    for statement in function.body:
        finder.visit(statement)
    return any(
        _value_contains_dynamic(
            _expression_value(
                state,
                expression,
                dynamic_names=dynamic_names,
            )
        )
        for expression in finder.values
    )


def _registered_callable_returns_dynamic(
    state: _ValueState,
    function: ast.FunctionDef | ast.AsyncFunctionDef,
    modules: Collection[str],
    callables: Collection[str],
    shadowed_names: Collection[str],
) -> bool:
    dynamic_names = {
        *callables,
        *(_DYNAMIC_CALLABLE_NAMES - set(shadowed_names)),
    }
    if _function_returns_dynamic_value(state, function, dynamic_names):
        return True
    if any(
        isinstance(node, ast.Name) and node.id in shadowed_names
        for node in ast.walk(function)
    ):
        return False
    return _returns_dynamic_callable(function, modules, callables)


def _value_contains_dynamic(value: _CallableValue) -> bool:
    return (
        value.dynamic
        or any(
            _value_contains_dynamic(item)
            for item in value.sequence or ()
        )
        or any(
            _value_contains_dynamic(item)
            for _, item in value.mapping or ()
        )
    )


class _ReturnValueFinder(ast.NodeVisitor):
    def __init__(self) -> None:
        self.values: list[ast.expr] = []

    def visit_Return(self, node: ast.Return) -> None:
        if node.value is not None:
            self.values.append(node.value)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        return

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        return

    def visit_Lambda(self, node: ast.Lambda) -> None:
        return

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        return


def _constructed_value(
    state: _ValueState,
    expression: ast.Call,
    overrides: Mapping[str, _CallableValue] | None,
    dynamic_names: Collection[str],
) -> _CallableValue | None:
    if not isinstance(expression.func, ast.Name):
        return None
    if expression.func.id in {"list", "tuple"}:
        return _constructed_sequence_value(
            state,
            expression,
            overrides,
            dynamic_names,
        )
    if expression.func.id != "dict":
        return None
    return _constructed_mapping_value(
        state,
        expression,
        overrides,
        dynamic_names,
    )


def _constructed_sequence_value(
    state: _ValueState,
    expression: ast.Call,
    overrides: Mapping[str, _CallableValue] | None,
    dynamic_names: Collection[str],
) -> _CallableValue | None:
    if len(expression.args) != 1 or expression.keywords:
        return None
    value = _expression_value(
        state,
        expression.args[0],
        overrides,
        dynamic_names,
    )
    if value.sequence is None:
        return None
    return _CallableValue(
        sequence=value.sequence,
        unknown=value.unknown,
        dynamic=value.dynamic,
    )


def _constructed_mapping_value(
    state: _ValueState,
    expression: ast.Call,
    overrides: Mapping[str, _CallableValue] | None,
    dynamic_names: Collection[str],
) -> _CallableValue | None:
    values: dict[object, _CallableValue] = {}
    if expression.args:
        if len(expression.args) != 1:
            return None
        nested = _expression_value(
            state,
            expression.args[0],
            overrides,
            dynamic_names,
        )
        if nested.mapping is None:
            return None
        values.update(dict(nested.mapping))
    for keyword in expression.keywords:
        if keyword.arg is None:
            nested = _expression_value(
                state,
                keyword.value,
                overrides,
                dynamic_names,
            )
            if nested.mapping is None:
                return None
            values.update(dict(nested.mapping))
        else:
            values[keyword.arg] = _expression_value(
                state,
                keyword.value,
                overrides,
                dynamic_names,
            )
    return _CallableValue(mapping=tuple(values.items()))


def _dict_value(
    state: _ValueState,
    expression: ast.Dict,
    overrides: Mapping[str, _CallableValue] | None,
    dynamic_names: Collection[str],
) -> dict[object, _CallableValue] | None:
    values: dict[object, _CallableValue] = {}
    for key_node, value in zip(expression.keys, expression.values, strict=True):
        if key_node is None:
            nested = _expression_value(state, value, overrides, dynamic_names)
            if nested.mapping is None:
                return None
            values.update(dict(nested.mapping))
            continue
        key = _constant_value(key_node)
        if key is _UNKNOWN:
            return None
        values[key] = _expression_value(
            state,
            value,
            overrides,
            dynamic_names,
        )
    return values


def _select_value(
    state: _ValueState,
    expression: ast.Subscript,
    overrides: Mapping[str, _CallableValue] | None,
    dynamic_names: Collection[str],
) -> _CallableValue | None:
    container = _expression_value(
        state,
        expression.value,
        overrides,
        dynamic_names,
    )
    key = _constant_value(expression.slice)
    if key is _UNKNOWN:
        return None
    if (
        container.sequence is not None
        and isinstance(key, int)
        and -len(container.sequence) <= key < len(container.sequence)
    ):
        return container.sequence[key]
    if container.mapping is not None:
        return dict(container.mapping).get(key)
    return None


def _qualified_name(expression: ast.expr) -> str:
    if isinstance(expression, ast.Name):
        return expression.id
    if isinstance(expression, ast.Attribute):
        root = _qualified_name(expression.value)
        return f"{root}.{expression.attr}" if root else ""
    return ""


__all__: list[str] = []
