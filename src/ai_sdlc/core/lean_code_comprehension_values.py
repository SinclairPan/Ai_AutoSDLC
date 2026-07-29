"""解析无需执行用户协议即可枚举的推导式迭代值。"""

from __future__ import annotations

import ast

from ai_sdlc.core.lean_code_path_summary import _merged_expression


def _known_iteration_values(
    expression: ast.expr,
) -> tuple[ast.expr, ...] | None:
    if isinstance(expression, (ast.List, ast.Tuple)):
        if any(isinstance(item, ast.Starred) for item in expression.elts):
            return None
        return tuple(expression.elts)
    if isinstance(expression, ast.Constant) and isinstance(
        expression.value,
        (str, bytes, tuple),
    ):
        return tuple(ast.Constant(value) for value in expression.value)
    return None


def _bounded_iteration_values(
    values: tuple[ast.expr, ...],
    *,
    remaining_levels: int,
    sample_limit: int,
) -> tuple[ast.expr, ...]:
    maximum = max(2, int(sample_limit ** (1 / remaining_levels)))
    if len(values) <= maximum:
        return values
    retained = values[: maximum - 1]
    return (*retained, _merged_expression(values[maximum - 1 :]))


__all__: list[str] = []
