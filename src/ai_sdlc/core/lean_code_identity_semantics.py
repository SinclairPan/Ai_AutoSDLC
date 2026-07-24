"""身份流使用的常量、消费与分支语义。"""

from __future__ import annotations

import ast
import operator
from typing import Any, cast

from ai_sdlc.core.lean_code_execution_order import _constant_truth
from ai_sdlc.core.lean_code_identity_models import (
    _EMPTY_VALUE,
    _CallableNode,
    _IdentityState,
    _IdentityValue,
)

_EAGER_CONSUMERS = frozenset(
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


def _builtin_consumer(node: ast.expr, state: _IdentityState) -> bool:
    return (
        isinstance(node, ast.Name)
        and node.id in _EAGER_CONSUMERS
        and node.id not in state.values
    )


def _callable_consumes_argument(node: _CallableNode, index: int) -> bool:
    arguments = _function_arguments(node.args)
    if index >= len(arguments):
        return False
    finder = _ParameterConsumptionFinder(arguments[index].arg)
    target = (
        node.body
        if isinstance(node, ast.Lambda)
        else ast.Module(body=node.body, type_ignores=[])
    )
    finder.visit(target)
    return finder.consumed


class _ParameterConsumptionFinder(ast.NodeVisitor):
    def __init__(self, name: str) -> None:
        self.name = name
        self.consumed = False

    def visit_Call(self, node: ast.Call) -> None:
        if (
            isinstance(node.func, ast.Name)
            and node.func.id in _EAGER_CONSUMERS
            and node.args
            and isinstance(node.args[0], ast.Name)
            and node.args[0].id == self.name
        ):
            self.consumed = True
        self.generic_visit(node)

    def visit_For(self, node: ast.For) -> None:
        self._visit_loop(node)

    def visit_AsyncFor(self, node: ast.AsyncFor) -> None:
        self._visit_loop(node)

    def _visit_loop(self, node: ast.For | ast.AsyncFor) -> None:
        if isinstance(node.iter, ast.Name) and node.iter.id == self.name:
            self.consumed = True
        self.generic_visit(node)


def _generator_body_may_execute(node: ast.GeneratorExp) -> bool:
    for generator in node.generators:
        items = _literal_items(generator.iter)
        if items == ():
            return False
        if any(_predicate_truth(condition) is False for condition in generator.ifs):
            return False
    return True


def _literal_items(node: ast.AST) -> tuple[_IdentityValue, ...] | None:
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        return tuple(_EMPTY_VALUE for _ in node.elts)
    if isinstance(node, ast.Dict):
        return tuple(_EMPTY_VALUE for _ in node.keys)
    if isinstance(node, ast.Constant) and isinstance(node.value, (str, bytes, tuple)):
        return tuple(_EMPTY_VALUE for _ in node.value)
    return None


def _literal_value(node: ast.AST) -> object:
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


def _pattern_matches(pattern: ast.pattern, subject: _IdentityValue) -> bool | None:
    if isinstance(pattern, ast.MatchValue) and isinstance(pattern.value, ast.Constant):
        return (
            None if not subject.scalar_known else subject.scalar == pattern.value.value
        )
    if isinstance(pattern, ast.MatchSingleton):
        return None if not subject.scalar_known else subject.scalar is pattern.value
    if isinstance(pattern, ast.MatchAs):
        return (
            True
            if pattern.pattern is None
            else _pattern_matches(pattern.pattern, subject)
        )
    if isinstance(pattern, ast.MatchSequence):
        return _sequence_pattern_matches(pattern, subject)
    if isinstance(pattern, ast.MatchMapping):
        return _mapping_pattern_matches(pattern, subject)
    if isinstance(pattern, ast.MatchOr):
        outcomes = [_pattern_matches(item, subject) for item in pattern.patterns]
        if any(item is True for item in outcomes):
            return True
        if all(item is False for item in outcomes):
            return False
    return None


def _mapping_pattern_matches(
    pattern: ast.MatchMapping, subject: _IdentityValue
) -> bool | None:
    if subject.kind != "container":
        return None
    entries = dict(subject.entries)
    keys = [_literal_value(key) for key in pattern.keys]
    if any(key is _UNKNOWN for key in keys):
        return None
    literal_keys = [key for key in keys if isinstance(key, (str, int))]
    if len(literal_keys) != len(keys) or any(
        key not in entries for key in literal_keys
    ):
        return False
    outcomes = [
        _pattern_matches(item, entries[key])
        for key, item in zip(literal_keys, pattern.patterns, strict=True)
    ]
    if all(item is True for item in outcomes):
        return True
    if any(item is False for item in outcomes):
        return False
    return None


def _sequence_pattern_matches(
    pattern: ast.MatchSequence, subject: _IdentityValue
) -> bool | None:
    if subject.kind != "container":
        return None
    if any(isinstance(item, ast.MatchStar) for item in pattern.patterns):
        return None
    if len(pattern.patterns) != len(subject.items):
        return False
    outcomes = [
        _pattern_matches(item, value)
        for item, value in zip(pattern.patterns, subject.items, strict=True)
    ]
    if all(item is True for item in outcomes):
        return True
    if any(item is False for item in outcomes):
        return False
    return None


def _pattern_bindings(
    pattern: ast.pattern, subject: _IdentityValue
) -> dict[str, _IdentityValue]:
    bindings: dict[str, _IdentityValue] = {}
    if isinstance(pattern, ast.MatchAs):
        if pattern.name is not None:
            bindings[pattern.name] = subject
        if pattern.pattern is not None:
            bindings.update(_pattern_bindings(pattern.pattern, subject))
    elif isinstance(pattern, ast.MatchSequence) and len(pattern.patterns) == len(
        subject.items
    ):
        for item, value in zip(pattern.patterns, subject.items, strict=True):
            bindings.update(_pattern_bindings(item, value))
    elif isinstance(pattern, ast.MatchMapping):
        entries = dict(subject.entries)
        matched_keys: set[str | int] = set()
        for key, item in zip(pattern.keys, pattern.patterns, strict=True):
            literal = _literal_value(key)
            if isinstance(literal, (str, int)) and literal in entries:
                matched_keys.add(literal)
                bindings.update(_pattern_bindings(item, entries[literal]))
        if pattern.rest is not None:
            rest = tuple(
                (key, value)
                for key, value in subject.entries
                if key not in matched_keys
            )
            bindings[pattern.rest] = _IdentityValue(
                "container", entries=rest, truth=bool(rest)
            )
    elif isinstance(pattern, ast.MatchOr):
        for item in pattern.patterns:
            if _pattern_matches(item, subject) is True:
                return _pattern_bindings(item, subject)
    return bindings


def _predicate_truth(node: ast.AST) -> bool | None:
    direct = _constant_truth(node)
    if direct is not None:
        return direct
    if isinstance(node, ast.Compare):
        left = node.left
        for operator, comparator in zip(node.ops, node.comparators, strict=True):
            outcome = _comparison_truth(left, operator, comparator)
            if outcome is not True:
                return outcome
            left = comparator
        return True
    return None


def _comparison_truth(
    left: ast.AST,
    operator: ast.cmpop,
    right: ast.AST,
) -> bool | None:
    lhs = cast(Any, _literal_value(left))
    rhs = cast(Any, _literal_value(right))
    if lhs is _UNKNOWN or rhs is _UNKNOWN:
        return None
    try:
        if isinstance(operator, ast.Gt):
            return bool(lhs > rhs)
        if isinstance(operator, ast.Lt):
            return bool(lhs < rhs)
        if isinstance(operator, ast.Eq):
            return bool(lhs == rhs)
        if isinstance(operator, ast.NotEq):
            return bool(lhs != rhs)
        if isinstance(operator, ast.GtE):
            return bool(lhs >= rhs)
        if isinstance(operator, ast.LtE):
            return bool(lhs <= rhs)
    except (TypeError, ValueError):
        return None
    return None


def _statement_may_raise(node: ast.stmt) -> bool:
    if isinstance(node, ast.Pass):
        return False
    if isinstance(node, ast.Assign):
        return not _safe_expression(node.value) or any(
            not _simple_target(item) for item in node.targets
        )
    if isinstance(node, ast.AnnAssign):
        return node.value is not None and (
            not _safe_expression(node.value) or not _simple_target(node.target)
        )
    return True


def _safe_expression(node: ast.AST) -> bool:
    if isinstance(node, (ast.Constant, ast.GeneratorExp, ast.Lambda, ast.Name)):
        return True
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        return all(_safe_expression(item) for item in node.elts)
    if isinstance(node, ast.Dict):
        return all(
            (key is None or _safe_expression(key)) and _safe_expression(value)
            for key, value in zip(node.keys, node.values, strict=True)
        )
    if isinstance(node, ast.BinOp):
        return _constant_binop_is_safe(node)
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "len"
        and len(node.args) == 1
        and not node.keywords
    ):
        return _literal_items(node.args[0]) is not None
    return False


def _constant_binop_is_safe(node: ast.BinOp) -> bool:
    left = _literal_value(node.left)
    right = _literal_value(node.right)
    if left is _UNKNOWN or right is _UNKNOWN:
        return False
    operations = {
        ast.Add: operator.add,
        ast.Sub: operator.sub,
        ast.Mult: operator.mul,
        ast.Div: operator.truediv,
        ast.FloorDiv: operator.floordiv,
        ast.Mod: operator.mod,
        ast.Pow: operator.pow,
        ast.LShift: operator.lshift,
        ast.RShift: operator.rshift,
        ast.BitOr: operator.or_,
        ast.BitXor: operator.xor,
        ast.BitAnd: operator.and_,
    }
    operation = operations.get(type(node.op))
    if operation is None:
        return False
    try:
        operation(left, right)
    except (ArithmeticError, TypeError, ValueError):
        return False
    return True


def _simple_target(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Name)
        or isinstance(node, (ast.Tuple, ast.List))
        and all(_simple_target(item) for item in node.elts)
    )


def _function_arguments(arguments: ast.arguments) -> tuple[ast.arg, ...]:
    items = [*arguments.posonlyargs, *arguments.args, *arguments.kwonlyargs]
    if arguments.vararg is not None:
        items.append(arguments.vararg)
    if arguments.kwarg is not None:
        items.append(arguments.kwarg)
    return tuple(items)


_UNKNOWN = object()

__all__: list[str] = []
