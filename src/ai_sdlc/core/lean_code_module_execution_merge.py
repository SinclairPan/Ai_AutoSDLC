"""合并模块定义期执行状态与类成员。"""

from __future__ import annotations

import ast
from collections.abc import Mapping
from typing import TypeVar

from ai_sdlc.core.lean_code_scope import _bound_names

_Key = TypeVar("_Key")


def _copy_members(members: Mapping[str, set[int]]) -> dict[str, set[int]]:
    return {name: set(refs) for name, refs in members.items()}


def _merge_members(
    candidates: list[dict[str, set[int]]],
) -> dict[str, set[int]]:
    names = set().union(*(members.keys() for members in candidates))
    return {
        name: set().union(*(members.get(name, set()) for members in candidates))
        for name in names
    }


def _ordered_members(
    candidates: list[dict[str, set[int]]],
) -> dict[str, set[int]]:
    members: dict[str, set[int]] = {}
    for candidate in reversed(candidates):
        members.update(_copy_members(candidate))
    return members


def _copy_sets(values: Mapping[_Key, set[int]]) -> dict[_Key, set[int]]:
    return {name: set(refs) for name, refs in values.items()}


def _merge_set_maps(
    candidates: tuple[dict[str, set[int]], ...],
) -> dict[str, set[int]]:
    names = set().union(*(candidate.keys() for candidate in candidates))
    return {
        name: set().union(*(candidate.get(name, set()) for candidate in candidates))
        for name in names
    }


def _merge_class_maps(
    candidates: tuple[dict[str, dict[str, set[int]]], ...],
) -> dict[str, dict[str, set[int]]]:
    names = set().union(*(candidate.keys() for candidate in candidates))
    return {
        name: _merge_members(
            [candidate[name] for candidate in candidates if name in candidate]
        )
        for name in names
    }


def _merge_containers(
    candidates: tuple[dict[str, tuple[set[int], ...]], ...],
) -> dict[str, tuple[set[int], ...]]:
    names = set().union(*(candidate.keys() for candidate in candidates))
    merged: dict[str, tuple[set[int], ...]] = {}
    for name in names:
        values = [candidate[name] for candidate in candidates if name in candidate]
        lengths = {len(items) for items in values}
        if len(lengths) == 1:
            merged[name] = tuple(
                set().union(*(items[index] for items in values))
                for index in range(lengths.pop())
            )
    return merged


def _merge_mapping_states(
    candidates: tuple[dict[str, dict[object, set[int]]], ...],
) -> dict[str, dict[object, set[int]]]:
    names = set().union(*(candidate.keys() for candidate in candidates))
    return {
        name: _merge_object_set_maps(
            tuple(candidate[name] for candidate in candidates if name in candidate)
        )
        for name in names
    }


def _merge_object_set_maps(
    candidates: tuple[dict[object, set[int]], ...],
) -> dict[object, set[int]]:
    keys = set().union(*(candidate.keys() for candidate in candidates))
    return {
        key: set().union(*(candidate.get(key, set()) for candidate in candidates))
        for key in keys
    }


def _class_bound_names(statements: list[ast.stmt]) -> set[str]:
    names: set[str] = set()
    for statement in statements:
        if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(statement.name)
        elif isinstance(statement, ast.Assign):
            names.update(
                set().union(*(_bound_names(target) for target in statement.targets))
            )
        elif isinstance(statement, (ast.AnnAssign, ast.For, ast.AsyncFor)):
            names.update(_bound_names(statement.target))
        elif isinstance(statement, (ast.With, ast.AsyncWith)):
            names.update(
                set().union(
                    *(
                        _bound_names(item.optional_vars)
                        for item in statement.items
                        if item.optional_vars is not None
                    ),
                    set(),
                )
            )
        elif isinstance(statement, ast.Match):
            names.update(
                node.name
                for case in statement.cases
                for node in ast.walk(case.pattern)
                if isinstance(node, (ast.MatchAs, ast.MatchStar)) and node.name
            )
        for block in _nested_statement_blocks(statement):
            names.update(_class_bound_names(block))
    return names


def _nested_statement_blocks(statement: ast.stmt) -> tuple[list[ast.stmt], ...]:
    if isinstance(statement, ast.If):
        return statement.body, statement.orelse
    if isinstance(statement, (ast.For, ast.AsyncFor, ast.While)):
        return statement.body, statement.orelse
    if isinstance(statement, (ast.With, ast.AsyncWith)):
        return (statement.body,)
    if isinstance(statement, (ast.Try, ast.TryStar)):
        return (
            statement.body,
            *(handler.body for handler in statement.handlers),
            statement.orelse,
            statement.finalbody,
        )
    if isinstance(statement, ast.Match):
        return tuple(case.body for case in statement.cases)
    return ()


__all__: list[str] = []
