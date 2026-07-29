"""传播生成器值身份并保存调用点的消费效果。"""

from __future__ import annotations

import ast
from collections.abc import Mapping

from ai_sdlc.core.lean_code_static_values import _constant_subscript_value

_CONSUMER_EFFECT_MARKER = "_ai_sdlc_generator_consumer"
_CONSUMER_GENERATORS_MARKER = "_ai_sdlc_generator_lineage"
_CONSUMER_MODE_MARKER = "_ai_sdlc_generator_consumer_mode"
_CONSUMER_OFFSETS_MARKER = "_ai_sdlc_generator_offsets"
_GENERATOR_CONSUMERS = frozenset(
    {
        "all",
        "any",
        "dict",
        "frozenset",
        "list",
        "max",
        "min",
        "next",
        "set",
        "sorted",
        "sum",
        "tuple",
    }
)


def _generator_lineage(
    expression: ast.expr,
    bindings: Mapping[str, tuple[ast.GeneratorExp, ...]],
) -> tuple[ast.GeneratorExp, ...]:
    if isinstance(expression, ast.GeneratorExp):
        return (expression,)
    if isinstance(expression, ast.Name):
        return bindings.get(expression.id, ())
    if isinstance(expression, ast.Starred):
        return _generator_lineage(expression.value, bindings)
    if isinstance(expression, ast.Subscript):
        selected = _constant_subscript_value(expression)
        if selected is not None:
            return _generator_lineage(selected, bindings)
    if isinstance(expression, ast.IfExp):
        return _merge_lineages(
            _generator_lineage(expression.body, bindings),
            _generator_lineage(expression.orelse, bindings),
        )
    if isinstance(expression, (ast.BoolOp, ast.List, ast.Tuple, ast.Set)):
        values = expression.values if isinstance(expression, ast.BoolOp) else expression.elts
        return _merge_lineages(
            *(_generator_lineage(value, bindings) for value in values)
        )
    if isinstance(expression, ast.Dict):
        return _merge_lineages(
            *(
                _generator_lineage(value, bindings)
                for value in (*expression.keys, *expression.values)
                if value is not None
            )
        )
    if isinstance(expression, ast.Call):
        return _merge_lineages(
            *(
                _generator_lineage(value, bindings)
                for value in (
                    *expression.args,
                    *(keyword.value for keyword in expression.keywords),
                )
            )
        )
    return ()


def _set_consumer_metadata(
    node: ast.Call,
    effect: str,
    generators: tuple[ast.GeneratorExp, ...],
    offsets: tuple[int | None, ...] = (),
) -> None:
    if not generators:
        return
    normalized = "consume" if effect.startswith("consume-") else effect
    mode = effect.removeprefix("consume-") if effect.startswith("consume-") else ""
    setattr(node, _CONSUMER_EFFECT_MARKER, normalized)
    setattr(node, _CONSUMER_GENERATORS_MARKER, generators)
    setattr(node, _CONSUMER_MODE_MARKER, mode)
    setattr(node, _CONSUMER_OFFSETS_MARKER, offsets)


def _generator_consumer_effect(node: ast.Call) -> str:
    marker = getattr(node, _CONSUMER_EFFECT_MARKER, "")
    return marker if marker in {"consume", "no-consume", "unknown"} else ""


def _consumer_generator_lineage(
    node: ast.Call,
) -> tuple[ast.GeneratorExp, ...]:
    marker = getattr(node, _CONSUMER_GENERATORS_MARKER, ())
    return tuple(
        expression
        for expression in marker
        if isinstance(expression, ast.GeneratorExp)
    )


def _generator_consumer_mode(node: ast.Call) -> str:
    marker = getattr(node, _CONSUMER_MODE_MARKER, "")
    return marker if marker in {"one", "all", "unknown"} else ""


def _generator_consumer_offsets(
    node: ast.Call,
) -> tuple[int | None, ...]:
    marker = getattr(node, _CONSUMER_OFFSETS_MARKER, ())
    if not isinstance(marker, tuple):
        return ()
    return tuple(
        value if isinstance(value, int) and value >= 0 else None
        for value in marker
    )


def _call_generator_lineage(
    expression: ast.Call,
) -> tuple[ast.GeneratorExp, ...]:
    annotated = _consumer_generator_lineage(expression)
    if annotated:
        return annotated
    return tuple(
        value
        for value in (
            *expression.args,
            *(keyword.value for keyword in expression.keywords),
        )
        if isinstance(value, ast.GeneratorExp)
    )


def _direct_generator_consumer(expression: ast.Call) -> bool:
    effect = _generator_consumer_effect(expression)
    if effect:
        return effect != "no-consume"
    return (
        isinstance(expression.func, ast.Name)
        and _is_builtin_generator_consumer(expression.func.id)
        and any(isinstance(argument, ast.GeneratorExp) for argument in expression.args)
    )


def _is_builtin_generator_consumer(name: str) -> bool:
    return name in _GENERATOR_CONSUMERS


def _merge_lineages(
    *groups: tuple[ast.GeneratorExp, ...],
) -> tuple[ast.GeneratorExp, ...]:
    unique: dict[int, ast.GeneratorExp] = {}
    for expression in (item for group in groups for item in group):
        unique.setdefault(id(expression), expression)
    return tuple(unique.values())


__all__: list[str] = []
