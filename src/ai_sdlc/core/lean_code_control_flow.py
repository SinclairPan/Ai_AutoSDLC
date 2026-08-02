"""提供轻量、确定性的静态控制流判定。"""

from __future__ import annotations

import ast
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from ai_sdlc.core.lean_code_static_values import (
    _literal_dict_values,
    _literal_key_node,
)


def _static_truth(node: ast.expr | None) -> bool | None:
    if isinstance(node, ast.Constant):
        return bool(node.value)
    return None


def _statically_empty(node: ast.expr) -> bool:
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return all(
            isinstance(item, ast.Starred) and _statically_empty(item.value)
            for item in node.elts
        )
    if isinstance(node, ast.Dict):
        return all(
            key is None and _statically_empty(value)
            for key, value in zip(node.keys, node.values, strict=True)
        )
    return isinstance(node, ast.Constant) and isinstance(
        node.value,
        (str, bytes, tuple, frozenset),
    ) and not node.value


def _statically_nonempty(node: ast.expr) -> bool:
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return any(
            not isinstance(item, ast.Starred)
            or _statically_nonempty(item.value)
            for item in node.elts
        )
    if isinstance(node, ast.Dict):
        return any(
            key is not None or _statically_nonempty(value)
            for key, value in zip(node.keys, node.values, strict=True)
        )
    return isinstance(node, ast.Constant) and isinstance(
        node.value,
        (str, bytes, tuple, frozenset),
    ) and bool(node.value)


def _known_safe_iterator_protocol(
    value: ast.expr,
    *,
    is_async: bool = False,
) -> bool:
    if is_async:
        return False
    if isinstance(value, (ast.List, ast.Tuple, ast.Set, ast.Dict)):
        return True
    return isinstance(value, ast.Constant) and _constant_is_iterable(value.value)


def _known_safe_mapping_protocol(value: ast.expr) -> bool:
    if not isinstance(value, ast.Dict):
        return False
    return all(
        key is not None or _known_safe_mapping_protocol(item)
        for key, item in zip(value.keys, value.values, strict=True)
    )


@dataclass(frozen=True)
class _UnpackPoint:
    raised: str | None
    prefix: tuple[ast.Assign, ...] = ()


@dataclass(frozen=True)
class _UnpackAnalysis:
    points: tuple[_UnpackPoint, ...] = ()
    bindings: tuple[ast.Assign, ...] = ()


def _unpack_exceptions(
    target: ast.expr,
    value: ast.expr | None,
) -> tuple[str | None, ...]:
    return tuple(
        dict.fromkeys(point.raised for point in _unpack_exception_points(target, value))
    )


def _unpack_exception_points(
    target: ast.expr,
    value: ast.expr | None,
) -> tuple[_UnpackPoint, ...]:
    return _unpack_analysis(target, value).points


def _unpack_analysis(
    target: ast.expr,
    value: ast.expr | None,
) -> _UnpackAnalysis:
    if not isinstance(target, (ast.Tuple, ast.List)):
        fallback = value or ast.Name(
            id="__ai_sdlc_unknown_unpack_value__",
            ctx=ast.Load(),
        )
        return _UnpackAnalysis(bindings=(_binding_assignment(target, fallback),))
    values = _known_iterable_items(value)
    if values is not None:
        pairs = _static_unpack_pairs(target.elts, values)
        if pairs is None:
            return _UnpackAnalysis(points=(_UnpackPoint("ValueError"),))
        completed: list[ast.Assign] = []
        points: list[_UnpackPoint] = []
        for child_target, child_value in pairs:
            child = _unpack_analysis(child_target, child_value)
            points.extend(
                _UnpackPoint(point.raised, (*completed, *point.prefix))
                for point in child.points
            )
            completed.extend(child.bindings)
        return _UnpackAnalysis(
            points=tuple(dict.fromkeys(points)),
            bindings=tuple(completed),
        )
    if isinstance(value, ast.Constant) and not _constant_is_iterable(value.value):
        return _UnpackAnalysis(points=(_UnpackPoint("TypeError"),))
    fallback = value or ast.Name(id="__ai_sdlc_unknown_unpack_value__", ctx=ast.Load())
    return _UnpackAnalysis(
        points=(_UnpackPoint(None),),
        bindings=(_binding_assignment(target, fallback),),
    )


def _binding_assignment(target: ast.expr, value: ast.expr) -> ast.Assign:
    return ast.Assign(targets=[target], value=value)


def _constant_is_iterable(value: object) -> bool:
    return isinstance(value, (str, bytes, tuple, frozenset))


def _known_iterable_items(value: ast.expr | None) -> tuple[ast.expr, ...] | None:
    if isinstance(value, (ast.List, ast.Tuple)):
        if any(isinstance(item, ast.Starred) for item in value.elts):
            return None
        return tuple(value.elts)
    if isinstance(value, ast.Dict):
        values = _literal_dict_values(value)
        if values is None:
            return None
        keys = tuple(_literal_key_node(key) for key in values)
        if any(key is None for key in keys):
            return None
        return tuple(key for key in keys if key is not None)
    if isinstance(value, ast.Constant) and isinstance(value.value, (str, bytes)):
        return tuple(ast.Constant(item) for item in value.value)
    return None


def _static_unpack_pairs(
    targets: Sequence[ast.expr],
    values: Sequence[ast.expr],
) -> tuple[tuple[ast.expr, ast.expr], ...] | None:
    stars = tuple(
        index for index, target in enumerate(targets) if isinstance(target, ast.Starred)
    )
    if not stars:
        if len(targets) != len(values):
            return None
        return tuple(zip(targets, values, strict=True))
    star_index = stars[0]
    fixed = len(targets) - 1
    if len(stars) != 1 or len(values) < fixed:
        return None
    suffix_count = len(targets) - star_index - 1
    prefix_pairs = tuple(zip(targets[:star_index], values[:star_index], strict=True))
    suffix_pairs = (
        tuple(zip(targets[-suffix_count:], values[-suffix_count:], strict=True))
        if suffix_count
        else ()
    )
    middle_end = len(values) - suffix_count if suffix_count else len(values)
    starred = targets[star_index]
    assert isinstance(starred, ast.Starred)
    middle = ast.List(
        elts=list(values[star_index:middle_end]),
        ctx=ast.Load(),
    )
    return (*prefix_pairs, (starred.value, middle), *suffix_pairs)


def _function_annotation_expressions(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> tuple[ast.expr, ...]:
    arguments = (
        *node.args.posonlyargs,
        *node.args.args,
        *node.args.kwonlyargs,
        *((node.args.vararg,) if node.args.vararg is not None else ()),
        *((node.args.kwarg,) if node.args.kwarg is not None else ()),
    )
    annotations = tuple(
        argument.annotation
        for argument in arguments
        if argument.annotation is not None
    )
    return (
        *annotations,
        *((node.returns,) if node.returns is not None else ()),
    )


def _future_annotations_enabled(tree: ast.Module) -> bool:
    return any(
        isinstance(statement, ast.ImportFrom)
        and statement.module == "__future__"
        and any(alias.name == "annotations" for alias in statement.names)
        for statement in tree.body
    )


def _subscript_exception(node: ast.Subscript) -> str | None:
    if isinstance(node.value, (ast.List, ast.Tuple)):
        return "IndexError"
    if isinstance(node.value, ast.Dict):
        return "KeyError"
    return None


def _reachable_match_cases(
    subject: ast.expr,
    cases: Iterable[ast.match_case],
) -> tuple[tuple[ast.match_case, ...], bool]:
    candidates = tuple(cases)
    if not isinstance(subject, ast.Constant):
        reachable_cases = tuple(
            case for case in candidates if _guard_may_pass(case.guard)
        )
        return reachable_cases, not any(
            _is_irrefutable(case) for case in reachable_cases
        )
    reachable: list[ast.match_case] = []
    for case in candidates:
        matched = _literal_pattern_matches(subject.value, case.pattern)
        guard = True if case.guard is None else _static_truth(case.guard)
        if matched is False or guard is False:
            continue
        reachable.append(case)
        if matched is True and guard is True:
            return tuple(reachable), False
    return tuple(reachable), True


def _literal_pattern_matches(value: object, pattern: ast.pattern) -> bool | None:
    if isinstance(pattern, ast.MatchValue):
        if isinstance(pattern.value, ast.Constant):
            return value == pattern.value.value
        return None
    if isinstance(pattern, ast.MatchSingleton):
        return value is pattern.value
    if isinstance(pattern, ast.MatchAs) and pattern.pattern is None:
        return True
    if isinstance(pattern, ast.MatchOr):
        matches = tuple(
            _literal_pattern_matches(value, item) for item in pattern.patterns
        )
        if any(item is True for item in matches):
            return True
        return None if any(item is None for item in matches) else False
    return None


def _is_irrefutable(case: ast.match_case) -> bool:
    return (
        case.guard is None
        and isinstance(case.pattern, ast.MatchAs)
        and case.pattern.pattern is None
    )


def _guard_may_pass(guard: ast.expr | None) -> bool:
    return _static_truth(guard) is not False


def _statement_may_raise(statement: ast.stmt) -> bool:
    finder = _ExecutedRaiseFinder()
    finder.visit(statement)
    return finder.found


class _ExecutedRaiseFinder(ast.NodeVisitor):
    def __init__(self) -> None:
        self.found = False

    def visit_Call(self, node: ast.Call) -> None:
        self.found = True

    def visit_Await(self, node: ast.Await) -> None:
        self.found = True

    def visit_Raise(self, node: ast.Raise) -> None:
        self.found = True

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function_header(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function_header(node)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        for default in (*node.args.defaults, *node.args.kw_defaults):
            if default is not None:
                self.visit(default)

    def _visit_function_header(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
    ) -> None:
        for expression in (
            *node.decorator_list,
            *node.args.defaults,
            *(item for item in node.args.kw_defaults if item is not None),
        ):
            self.visit(expression)


def _assignment_parts(
    statement: ast.stmt,
) -> tuple[list[ast.expr], ast.expr | None]:
    if isinstance(statement, ast.Assign):
        return statement.targets, statement.value
    if isinstance(statement, ast.AnnAssign):
        return [statement.target], statement.value
    return [], None


def _match_pattern_names(pattern: ast.pattern) -> set[str]:
    return {
        node.name
        for node in ast.walk(pattern)
        if isinstance(node, (ast.MatchAs, ast.MatchStar)) and node.name
    }


def _must_iterate(statement: ast.stmt) -> bool:
    return isinstance(statement, (ast.For, ast.AsyncFor)) and _statically_nonempty(
        statement.iter
    )


def _parallel_sequence(target: ast.expr, value: ast.expr) -> bool:
    return (
        isinstance(target, (ast.Tuple, ast.List))
        and isinstance(value, (ast.Tuple, ast.List))
        and len(target.elts) == len(value.elts)
        and not any(isinstance(item, ast.Starred) for item in target.elts)
    )


__all__: list[str] = []
