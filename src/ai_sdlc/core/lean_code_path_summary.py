"""在路径数量受限时保留最终绑定、别名依赖和覆盖顺序。"""

from __future__ import annotations

import ast
import copy
from collections.abc import Iterable, Sequence

_Binding = tuple[ast.expr, ast.expr]
_BindingPath = tuple[_Binding, ...]
_Prefix = tuple[ast.stmt, ...]
_SUMMARY_COMPONENT_LIMIT = 16
_SUMMARY_PATH_BINDING_LIMIT = 64
_SUMMARY_OVERFLOW_TARGET = "__ai_sdlc_summary_overflow__"


def _overflow_binding_paths(
    paths: Sequence[_BindingPath],
    *,
    sample_limit: int = 32,
) -> list[_BindingPath]:
    bounded = tuple(_bounded_binding_path(path) for path in paths)
    sampled = list(bounded[:sample_limit])
    summary = _binding_summary(bounded)
    candidates = [*sampled, *((summary,) if summary else ())]
    return _unique_binding_paths(candidates)


def _overflow_joined_binding_paths(
    left: Sequence[_BindingPath],
    right: Sequence[_BindingPath],
    sampled: Sequence[_BindingPath],
) -> list[_BindingPath]:
    bounded_left = tuple(_bounded_binding_path(path) for path in left)
    bounded_right = tuple(_bounded_binding_path(path) for path in right)
    bounded_sampled = tuple(_bounded_binding_path(path) for path in sampled)
    combined = _binding_summary(
        (
            (
                *_binding_summary(bounded_left),
                *_binding_summary(bounded_right),
            ),
        )
    )
    return _unique_binding_paths([*bounded_sampled, combined])


def _overflow_prefixes(
    prefixes: Sequence[_Prefix],
    *,
    sample_limit: int = 64,
) -> list[_Prefix]:
    sampled = list(prefixes[:sample_limit])
    binding_paths = tuple(_prefix_bindings(prefix) for prefix in prefixes)
    summary = tuple(
        ast.Assign(targets=[target], value=value)
        for target, value in _binding_summary(binding_paths)
    )
    side_effects = _prefix_side_effects(prefixes)
    candidates = [*sampled, (*side_effects, *summary)]
    return _unique_prefixes(candidates)


def _binding_summary(paths: Sequence[_BindingPath]) -> _BindingPath:
    if not paths:
        return ()
    finals = tuple(_final_bindings(path) for path in paths)
    keys = set().union(*(state.keys() for state in finals))
    summarized: list[tuple[int, str, ast.expr, ast.expr]] = []
    for key in keys:
        entries = [state[key] for state in finals if key in state]
        order = min(item[0] for item in entries)
        target = entries[0][1]
        values = [item[2] for item in entries]
        if len(entries) != len(finals) and isinstance(target, ast.Name):
            values.append(ast.Name(id=target.id, ctx=ast.Load()))
        summarized.append(
            (order, key, target, _merged_expression(values))
        )
    summarized.sort(key=lambda item: (item[0], item[1]))
    result = tuple((target, value) for _, _, target, value in summarized)
    return _bounded_binding_path(result)


def _bounded_binding_path(path: _BindingPath) -> _BindingPath:
    if len(path) <= _SUMMARY_PATH_BINDING_LIMIT:
        return path
    retained = path[-(_SUMMARY_PATH_BINDING_LIMIT - 1) :]
    return (*retained, _summary_overflow_binding())


def _summary_overflow_binding() -> _Binding:
    return (
        ast.Name(id=_SUMMARY_OVERFLOW_TARGET, ctx=ast.Store()),
        _unknown_dependency(),
    )


def _binding_path_overflowed(path: _BindingPath) -> bool:
    return any(
        isinstance(target, ast.Name)
        and target.id == _SUMMARY_OVERFLOW_TARGET
        for target, _ in path
    )


def _final_bindings(
    path: _BindingPath,
) -> dict[str, tuple[int, ast.expr, ast.expr]]:
    final: dict[str, tuple[int, ast.expr, ast.expr]] = {}
    names: dict[str, tuple[ast.expr, ...]] = {}
    for index, (target, value) in enumerate(path):
        components = _value_with_prior_dependencies(value, names)
        resolved = _merged_expression(components)
        final[ast.dump(target)] = (index, target, resolved)
        if isinstance(target, ast.Name):
            names[target.id] = components
    return final


def _value_with_prior_dependencies(
    value: ast.expr,
    names: dict[str, tuple[ast.expr, ...]],
) -> tuple[ast.expr, ...]:
    resolver = _PriorNameResolver(names)
    resolved = resolver.visit(copy.deepcopy(value))
    assert isinstance(resolved, ast.expr)
    residual = () if resolver.dependencies and _neutral_expression(resolved) else (resolved,)
    values = (*resolver.dependencies, *residual)
    if resolver.overflowed:
        values = (*values, _unknown_dependency())
    return _bounded_expressions(values)


class _PriorNameResolver(ast.NodeTransformer):
    def __init__(self, names: dict[str, tuple[ast.expr, ...]]) -> None:
        self.names = names
        self.dependencies: list[ast.expr] = []
        self.overflowed = False

    def visit_Name(self, node: ast.Name) -> ast.expr:
        if isinstance(node.ctx, ast.Load) and node.id in self.names:
            for dependency in self.names[node.id]:
                if len(self.dependencies) == _SUMMARY_COMPONENT_LIMIT:
                    self.overflowed = True
                    break
                self.dependencies.append(dependency)
            return ast.copy_location(ast.Constant(value=None), node)
        return node

    def visit_Lambda(self, node: ast.Lambda) -> ast.Lambda:
        node.args.defaults = [
            self.visit(default)
            for default in node.args.defaults
        ]
        node.args.kw_defaults = [
            self.visit(default) if default is not None else None
            for default in node.args.kw_defaults
        ]
        return node

    def visit_GeneratorExp(self, node: ast.GeneratorExp) -> ast.GeneratorExp:
        if node.generators:
            node.generators[0].iter = self.visit(node.generators[0].iter)
        return node

    def visit_ListComp(self, node: ast.ListComp) -> ast.ListComp:
        result = self.generic_visit(node)
        assert isinstance(result, ast.ListComp)
        return result

    def visit_SetComp(self, node: ast.SetComp) -> ast.SetComp:
        result = self.generic_visit(node)
        assert isinstance(result, ast.SetComp)
        return result

    def visit_DictComp(self, node: ast.DictComp) -> ast.DictComp:
        result = self.generic_visit(node)
        assert isinstance(result, ast.DictComp)
        return result


def _neutral_expression(value: ast.expr) -> bool:
    if isinstance(value, ast.Constant):
        return value.value is None
    if isinstance(value, (ast.Tuple, ast.List, ast.Set)):
        return all(_neutral_expression(item) for item in value.elts)
    if isinstance(value, ast.Dict):
        return all(
            key is None or _neutral_expression(key)
            for key in value.keys
        ) and all(_neutral_expression(item) for item in value.values)
    return False


def _bounded_expressions(values: Iterable[ast.expr]) -> tuple[ast.expr, ...]:
    unique: dict[str, ast.expr] = {}
    overflowed = False
    for value in values:
        key = ast.dump(value)
        if key in unique:
            continue
        if len(unique) >= _SUMMARY_COMPONENT_LIMIT:
            overflowed = True
            break
        unique[key] = value
    if overflowed:
        marker = _unknown_dependency()
        unique.setdefault(ast.dump(marker), marker)
    return tuple(unique.values())


def _unknown_dependency() -> ast.expr:
    return ast.Call(
        func=ast.Name(id="__import__", ctx=ast.Load()),
        args=[ast.Constant(value="__ai_sdlc_unknown_dependency__")],
        keywords=[],
    )


def _merged_expression(values: Iterable[ast.expr]) -> ast.expr:
    unique = _bounded_expressions(values)
    if len(unique) == 1:
        return unique[0]
    return ast.Tuple(elts=list(unique), ctx=ast.Load())


def _prefix_bindings(prefix: _Prefix) -> _BindingPath:
    bindings: list[_Binding] = []
    for statement in prefix:
        if isinstance(statement, ast.Expr) and isinstance(
            statement.value,
            ast.NamedExpr,
        ):
            bindings.append((statement.value.target, statement.value.value))
        elif isinstance(statement, ast.Assign) and len(statement.targets) == 1:
            bindings.append((statement.targets[0], statement.value))
        elif isinstance(statement, ast.AnnAssign) and statement.value is not None:
            bindings.append((statement.target, statement.value))
    return tuple(bindings)


def _prefix_side_effects(prefixes: Sequence[_Prefix]) -> tuple[ast.stmt, ...]:
    side_effects: dict[str, ast.stmt] = {}
    for prefix in prefixes:
        for statement in prefix:
            if _binding_statement(statement):
                continue
            side_effects.setdefault(ast.dump(statement), statement)
    return tuple(side_effects.values())


def _binding_statement(statement: ast.stmt) -> bool:
    return (
        isinstance(statement, ast.Expr)
        and isinstance(statement.value, ast.NamedExpr)
    ) or isinstance(statement, (ast.Assign, ast.AnnAssign))


def _unique_binding_paths(paths: Iterable[_BindingPath]) -> list[_BindingPath]:
    unique: dict[tuple[tuple[str, str], ...], _BindingPath] = {}
    for path in paths:
        key = tuple((ast.dump(target), ast.dump(value)) for target, value in path)
        unique.setdefault(key, path)
    return list(unique.values())


def _unique_prefixes(prefixes: Iterable[_Prefix]) -> list[_Prefix]:
    unique: dict[tuple[str, ...], _Prefix] = {}
    for prefix in prefixes:
        key = tuple(ast.dump(statement) for statement in prefix)
        unique.setdefault(key, prefix)
    return list(unique.values())


__all__: list[str] = []
