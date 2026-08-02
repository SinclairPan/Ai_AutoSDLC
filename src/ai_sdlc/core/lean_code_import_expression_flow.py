"""提取表达式中的动态调用与海象绑定。"""

from __future__ import annotations

import ast
from collections.abc import Collection

from ai_sdlc.core.lean_code_comprehension_depth import (
    _deep_comprehension_summary,
)
from ai_sdlc.core.lean_code_comprehension_values import (
    _bounded_iteration_values,
    _known_iteration_values,
)
from ai_sdlc.core.lean_code_control_flow import (
    _static_truth,
    _statically_empty,
    _statically_nonempty,
)
from ai_sdlc.core.lean_code_expression_visitors import (
    _DynamicCallFinder,
    _NamedExpressionFinder,
)
from ai_sdlc.core.lean_code_generator_identity import (
    _call_generator_lineage,
    _direct_generator_consumer,
    _generator_consumer_effect,
    _generator_consumer_mode,
    _generator_consumer_offsets,
)
from ai_sdlc.core.lean_code_path_summary import (
    _bounded_binding_path,
    _overflow_binding_paths,
    _overflow_joined_binding_paths,
)

_PATH_SAMPLE_LIMIT = 32
_PATH_LIMIT = _PATH_SAMPLE_LIMIT + 1
_COMPREHENSION_DEPTH_LIMIT = 64
def _expression_calls_dynamic(
    expression: ast.expr,
    modules: Collection[str],
    callables: Collection[str],
) -> bool:
    finder = _DynamicCallFinder(modules, callables)
    finder.visit(expression)
    return finder.found


def _named_expression_bindings(
    expression: ast.expr,
) -> tuple[tuple[ast.expr, ast.expr], ...]:
    finder = _NamedExpressionFinder()
    finder.visit(expression)
    return tuple(finder.bindings)


def _named_expression_binding_paths(
    expression: ast.expr,
) -> tuple[tuple[tuple[ast.expr, ast.expr], ...], ...]:
    return tuple(_binding_paths(expression))


def _binding_paths(
    expression: ast.expr,
) -> list[tuple[tuple[ast.expr, ast.expr], ...]]:
    deferred = _deferred_expression_paths(expression)
    if deferred is not None:
        return deferred
    if isinstance(expression, (ast.ListComp, ast.SetComp)):
        return _comprehension_paths(expression.generators, (expression.elt,))
    if isinstance(expression, ast.DictComp):
        return _comprehension_paths(
            expression.generators,
            (expression.key, expression.value),
        )
    if isinstance(expression, ast.NamedExpr):
        return [
            (*path, (expression.target, expression.value))
            for path in _binding_paths(expression.value)
        ]
    if isinstance(expression, ast.IfExp):
        return _if_expression_paths(expression)
    if isinstance(expression, ast.BoolOp):
        return _bool_paths(expression)
    paths: list[tuple[tuple[ast.expr, ast.expr], ...]] = [()]
    for child in ast.iter_child_nodes(expression):
        if isinstance(child, ast.expr):
            paths = _join_path_groups(paths, _binding_paths(child))
    return paths


def _deferred_expression_paths(
    expression: ast.expr,
) -> list[tuple[tuple[ast.expr, ast.expr], ...]] | None:
    if isinstance(expression, ast.Lambda):
        return _expression_sequence_paths(
            (
                *expression.args.defaults,
                *(item for item in expression.args.kw_defaults if item is not None),
            )
        )
    if isinstance(expression, ast.GeneratorExp):
        if not expression.generators:
            return [()]
        return _binding_paths(expression.generators[0].iter)
    if isinstance(expression, ast.Starred):
        if isinstance(expression.value, ast.GeneratorExp):
            return _generator_consumption_paths(expression.value)
        return _binding_paths(expression.value)
    if (
        isinstance(expression, ast.Call)
        and _direct_generator_consumer(expression)
        and _call_generator_lineage(expression)
    ):
        return _consumer_call_paths(expression)
    return None


def _if_expression_paths(
    expression: ast.IfExp,
) -> list[tuple[tuple[ast.expr, ast.expr], ...]]:
    truth = _static_truth(expression.test)
    branches = (
        (expression.body,)
        if truth is True
        else (expression.orelse,)
        if truth is False
        else (expression.body, expression.orelse)
    )
    return _join_path_groups(
        _binding_paths(expression.test),
        [path for branch in branches for path in _binding_paths(branch)],
    )


def _generator_consumption_paths(
    expression: ast.GeneratorExp,
    *,
    mode: str = "all",
    offset: int | None = 0,
) -> list[tuple[tuple[ast.expr, ast.expr], ...]]:
    if offset is None:
        mode = "unknown"
        offset = 0
    limit = 1 if mode == "one" else None
    paths = _comprehension_paths(
        expression.generators,
        (expression.elt,),
        maximum_iterations=limit,
        skip_iterations=offset,
    )
    if mode != "unknown":
        return paths
    prefixes = [*paths]
    values = (
        _known_iteration_values(expression.generators[0].iter)
        if expression.generators
        else None
    )
    maximum = (
        min(len(values) - offset, _PATH_SAMPLE_LIMIT)
        if values is not None
        else 1
    )
    for count in range(1, max(1, maximum) + 1):
        prefixes.extend(
            _comprehension_paths(
                expression.generators,
                (expression.elt,),
                maximum_iterations=count,
                skip_iterations=offset,
            )
        )
    return _deduplicate_paths(prefixes)


def _consumer_call_paths(
    expression: ast.Call,
) -> list[tuple[tuple[ast.expr, ast.expr], ...]]:
    paths = _binding_paths(expression.func)
    for argument in expression.args:
        paths = _join_path_groups(paths, _binding_paths(argument))
    for keyword in expression.keywords:
        paths = _join_path_groups(paths, _binding_paths(keyword.value))
    effect = _generator_consumer_effect(expression) or "consume"
    if effect == "no-consume":
        return paths
    consumed = list(paths)
    mode = _generator_consumer_mode(expression) or (
        "unknown" if effect == "unknown" else "all"
    )
    generators = _call_generator_lineage(expression)
    offsets = _generator_consumer_offsets(expression)
    for index, generator in enumerate(generators):
        consumed = _join_path_groups(
            consumed,
            _generator_consumption_paths(
                generator,
                mode=mode,
                offset=offsets[index] if index < len(offsets) else 0,
            ),
        )
    return (
        _deduplicate_paths([*paths, *consumed])
        if effect == "unknown"
        else consumed
    )


def _expression_sequence_paths(
    expressions: Collection[ast.expr],
) -> list[tuple[tuple[ast.expr, ast.expr], ...]]:
    paths: list[tuple[tuple[ast.expr, ast.expr], ...]] = [()]
    for expression in expressions:
        paths = _join_path_groups(paths, _binding_paths(expression))
    return paths


def _comprehension_paths(
    generators: Collection[ast.comprehension],
    terminal: Collection[ast.expr],
    *,
    maximum_iterations: int | None = None,
    skip_iterations: int = 0,
) -> list[tuple[tuple[ast.expr, ast.expr], ...]]:
    candidates = tuple(generators)
    if not candidates:
        return _expression_sequence_paths(terminal)
    if len(candidates) > _COMPREHENSION_DEPTH_LIMIT:
        return _deep_comprehension_summary(
            candidates,
            tuple(terminal),
            _binding_paths,
            _expression_sequence_paths,
        )
    return _comprehension_level_paths(
        candidates,
        tuple(terminal),
        0,
        maximum_iterations=maximum_iterations,
        skip_iterations=skip_iterations,
    )


def _comprehension_level_paths(
    generators: tuple[ast.comprehension, ...],
    terminal: tuple[ast.expr, ...],
    index: int,
    *,
    maximum_iterations: int | None = None,
    skip_iterations: int = 0,
) -> list[tuple[tuple[ast.expr, ast.expr], ...]]:
    generator = generators[index]
    values = _known_iteration_values(generator.iter)
    if values is not None:
        return _known_comprehension_paths(
            generators,
            terminal,
            index,
            values,
            maximum_iterations=maximum_iterations,
            skip_iterations=skip_iterations,
        )
    iter_paths = _binding_paths(generator.iter)
    if _statically_empty(generator.iter):
        return iter_paths
    stopped = [] if _statically_nonempty(generator.iter) else list(iter_paths)
    continuing = iter_paths
    for condition in generator.ifs:
        evaluated = _join_path_groups(continuing, _binding_paths(condition))
        truth = _static_truth(condition)
        if truth is not True:
            stopped.extend(evaluated)
        if truth is False:
            return _deduplicate_paths(stopped)
        continuing = evaluated
    suffixes = (
        _expression_sequence_paths(terminal)
        if index == len(generators) - 1
        else _comprehension_level_paths(
            generators,
            terminal,
            index + 1,
            maximum_iterations=maximum_iterations,
            skip_iterations=skip_iterations,
        )
    )
    completed = _join_path_groups(continuing, suffixes)
    return _deduplicate_paths([*stopped, *completed])


def _known_comprehension_paths(
    generators: tuple[ast.comprehension, ...],
    terminal: tuple[ast.expr, ...],
    index: int,
    values: tuple[ast.expr, ...],
    *,
    maximum_iterations: int | None,
    skip_iterations: int,
) -> list[tuple[tuple[ast.expr, ast.expr], ...]]:
    generator = generators[index]
    continuing = _binding_paths(generator.iter)
    candidates = _bounded_iteration_values(
        values[skip_iterations:],
        remaining_levels=len(generators) - index,
        sample_limit=_PATH_SAMPLE_LIMIT,
    )
    if maximum_iterations:
        candidates = candidates[:maximum_iterations]
    if not candidates:
        return continuing
    for value in candidates:
        iteration = _join_path_groups(
            continuing,
            [((generator.target, value),)],
        )
        skipped: list[tuple[tuple[ast.expr, ast.expr], ...]] = []
        for condition in generator.ifs:
            evaluated = _join_path_groups(iteration, _binding_paths(condition))
            truth = _static_truth(condition)
            if truth is not True:
                skipped.extend(evaluated)
            iteration = [] if truth is False else evaluated
        suffix = (
            _expression_sequence_paths(terminal)
            if index == len(generators) - 1
            else _comprehension_level_paths(
                generators,
                terminal,
                index + 1,
                maximum_iterations=maximum_iterations,
                skip_iterations=0,
            )
        )
        continuing = _deduplicate_paths(
            [*skipped, *_join_path_groups(iteration, suffix)]
        )
    return continuing


def _bool_paths(
    expression: ast.BoolOp,
) -> list[tuple[tuple[ast.expr, ast.expr], ...]]:
    continuing: list[tuple[tuple[ast.expr, ast.expr], ...]] = [()]
    completed: list[tuple[tuple[ast.expr, ast.expr], ...]] = []
    for index, value in enumerate(expression.values):
        evaluated = _join_path_groups(continuing, _binding_paths(value))
        if index == len(expression.values) - 1:
            completed.extend(evaluated)
            break
        truth = _static_truth(value)
        stops = truth is False if isinstance(expression.op, ast.And) else truth is True
        continues = truth is True if isinstance(expression.op, ast.And) else truth is False
        if stops:
            completed.extend(evaluated)
            continuing = []
            break
        if truth is None:
            completed.extend(evaluated)
        continuing = evaluated if continues or truth is None else []
    return _deduplicate_paths(completed)


def _join_path_groups(
    left: list[tuple[tuple[ast.expr, ast.expr], ...]],
    right: list[tuple[tuple[ast.expr, ast.expr], ...]],
) -> list[tuple[tuple[ast.expr, ast.expr], ...]]:
    if len(left) * len(right) <= _PATH_LIMIT:
        return _deduplicate_paths(
            [(*prefix, *suffix) for prefix in left for suffix in right]
        )
    sampled: list[tuple[tuple[ast.expr, ast.expr], ...]] = []
    for prefix in left:
        for suffix in right:
            sampled.append((*prefix, *suffix))
            if len(sampled) == _PATH_SAMPLE_LIMIT:
                break
        if len(sampled) == _PATH_SAMPLE_LIMIT:
            break
    return _overflow_joined_binding_paths(left, right, sampled)


def _deduplicate_paths(
    paths: list[tuple[tuple[ast.expr, ast.expr], ...]],
) -> list[tuple[tuple[ast.expr, ast.expr], ...]]:
    unique: dict[tuple[tuple[str, str], ...], tuple[tuple[ast.expr, ast.expr], ...]] = {}
    for path in paths:
        bounded = _bounded_binding_path(path)
        key = tuple((ast.dump(target), ast.dump(value)) for target, value in bounded)
        unique.setdefault(key, bounded)
    if len(unique) > _PATH_LIMIT:
        return _overflow_binding_paths(tuple(unique.values()))
    return list(unique.values())


__all__: list[str] = []
