"""身份表达式使用的有界标量运算。"""

from __future__ import annotations

import ast


def _binary_scalar(
    operator: ast.operator,
    left: object,
    right: object,
) -> tuple[object, str | None]:
    try:
        if isinstance(operator, ast.Add):
            return left + right, None  # type: ignore[operator]
        if isinstance(operator, ast.Sub):
            return left - right, None  # type: ignore[operator]
        if isinstance(operator, ast.Mult):
            return left * right, None  # type: ignore[operator]
        if isinstance(operator, ast.Div):
            return left / right, None  # type: ignore[operator]
        if isinstance(operator, ast.FloorDiv):
            return left // right, None  # type: ignore[operator]
        if isinstance(operator, ast.Mod):
            return left % right, None  # type: ignore[operator]
    except (ArithmeticError, TypeError, ValueError) as exc:
        return None, type(exc).__name__
    return None, None


__all__: list[str] = []
