"""解析不执行用户代码即可确定的字面量值。"""

from __future__ import annotations

import ast


def _known_hash_safe(value: ast.expr) -> bool:
    if isinstance(value, ast.Constant):
        try:
            hash(value.value)
        except TypeError:
            return False
        return True
    if isinstance(value, ast.Tuple):
        return not any(
            isinstance(item, ast.Starred) for item in value.elts
        ) and all(_known_hash_safe(item) for item in value.elts)
    return False


def _constant_subscript_value(expression: ast.Subscript) -> ast.expr | None:
    key = _constant_value(expression.slice)
    if isinstance(expression.value, (ast.Tuple, ast.List)) and isinstance(key, int):
        items = expression.value.elts
        if -len(items) <= key < len(items):
            return items[key]
    if isinstance(expression.value, ast.Dict):
        values = _literal_dict_values(expression.value)
        if values is not None:
            return values.get(key)
    return None


def _literal_dict_values(node: ast.Dict) -> dict[object, ast.expr] | None:
    values: dict[object, ast.expr] = {}
    for key_node, value in zip(node.keys, node.values, strict=True):
        if key_node is None:
            if not isinstance(value, ast.Dict):
                return None
            nested = _literal_dict_values(value)
            if nested is None:
                return None
            values.update(nested)
            continue
        key = _constant_value(key_node)
        if key is _UNKNOWN:
            return None
        try:
            values[key] = value
        except TypeError:
            return None
    return values


def _literal_key_node(value: object) -> ast.Constant | None:
    if value is None or value is Ellipsis:
        return ast.Constant(value)
    if isinstance(value, (str, bytes, bool, int, float, complex)):
        return ast.Constant(value)
    return None


def _constant_value(node: ast.expr) -> object:
    if isinstance(node, ast.Constant):
        return node.value
    if (
        isinstance(node, ast.UnaryOp)
        and isinstance(node.op, ast.USub)
        and isinstance(node.operand, ast.Constant)
        and isinstance(node.operand.value, (int, float))
    ):
        return -node.operand.value
    return _UNKNOWN


_UNKNOWN = object()
