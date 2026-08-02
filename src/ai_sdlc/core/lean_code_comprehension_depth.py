"""为超过递归深度预算的推导式生成有界依赖摘要。"""

from __future__ import annotations

import ast
from collections.abc import Callable

from ai_sdlc.core.lean_code_comprehension_values import _known_iteration_values
from ai_sdlc.core.lean_code_path_summary import _overflow_binding_paths

_Binding = tuple[ast.expr, ast.expr]
_BindingPath = tuple[_Binding, ...]
_BindingPaths = Callable[[ast.expr], list[_BindingPath]]
_SequencePaths = Callable[[tuple[ast.expr, ...]], list[_BindingPath]]


def _deep_comprehension_summary(
    generators: tuple[ast.comprehension, ...],
    terminal: tuple[ast.expr, ...],
    binding_paths: _BindingPaths,
    sequence_paths: _SequencePaths,
) -> list[_BindingPath]:
    bindings: list[_Binding] = []
    for generator in generators:
        values = _known_iteration_values(generator.iter)
        value = _merged_iteration_value(values, generator.iter)
        bindings.append((generator.target, value))
        for condition in generator.ifs:
            for path in binding_paths(condition):
                bindings.extend(path)
    terminal_paths = sequence_paths(terminal)
    return _overflow_binding_paths(
        [(*bindings, *path) for path in terminal_paths]
    )


def _merged_iteration_value(
    values: tuple[ast.expr, ...] | None,
    fallback: ast.expr,
) -> ast.expr:
    if not values:
        return fallback
    if len(values) == 1:
        return values[0]
    return ast.Tuple(elts=list(values), ctx=ast.Load())


__all__: list[str] = []
