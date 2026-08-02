"""解析异常类型与可达 except 处理器。"""

from __future__ import annotations

import ast
import builtins
from collections.abc import Sequence


def _raised_name(expression: ast.expr | None) -> str | None:
    if isinstance(expression, ast.Name):
        return expression.id
    if isinstance(expression, ast.Call):
        if isinstance(expression.func, ast.Name):
            return expression.func.id
        if isinstance(expression.func, ast.Attribute):
            return expression.func.attr
    return None


def _handler_may_catch(handler: ast.ExceptHandler, raised: str | None) -> bool:
    return _handler_match(handler, raised) is not False


def _handler_match(
    handler: ast.ExceptHandler,
    raised: str | None,
) -> bool | None:
    if handler.type is None or raised is None:
        return True if handler.type is None else None
    matches = tuple(
        _exception_name_matches(name, raised)
        for name in _exception_type_names(handler.type)
    )
    if not matches:
        return None
    if any(match is True for match in matches):
        return True
    if any(match is None for match in matches):
        return None
    return False


def _reachable_exception_handlers(
    handlers: Sequence[ast.ExceptHandler],
    raised: str | None,
) -> tuple[tuple[ast.ExceptHandler, ...], bool]:
    reachable: list[ast.ExceptHandler] = []
    for handler in handlers:
        match = _handler_match(handler, raised)
        if match is False:
            continue
        reachable.append(handler)
        if match is True:
            return tuple(reachable), False
    return tuple(reachable), True


def _exception_type_names(expression: ast.expr) -> set[str]:
    if isinstance(expression, ast.Name):
        return {expression.id}
    if isinstance(expression, ast.Attribute):
        return {expression.attr}
    if isinstance(expression, ast.Tuple):
        return set().union(*(_exception_type_names(item) for item in expression.elts))
    return set()


def _exception_name_matches(handler: str, raised: str) -> bool | None:
    raised_type = getattr(builtins, raised, None)
    handler_type = getattr(builtins, handler, None)
    if not (
        isinstance(raised_type, type)
        and isinstance(handler_type, type)
        and issubclass(raised_type, BaseException)
        and issubclass(handler_type, BaseException)
    ):
        return None
    return issubclass(raised_type, handler_type)
