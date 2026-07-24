"""身份执行状态的去重与保守合并。"""

from __future__ import annotations

import ast

from ai_sdlc.core.lean_code_identity_models import (
    _EMPTY_VALUE,
    _IdentityState,
    _IdentityValue,
)

_MAX_IDENTITY_STATES = 256


def _deduplicate_states(states: list[_IdentityState]) -> list[_IdentityState]:
    unique = {_state_key(state): state for state in states}
    deduplicated = list(unique.values())
    if len(deduplicated) <= _MAX_IDENTITY_STATES:
        return deduplicated
    groups: dict[tuple[str, str | None], list[_IdentityState]] = {}
    for state in deduplicated:
        groups.setdefault((state.completion, state.exception), []).append(state)
    return [_join_states(group) for group in groups.values()]


def _state_key(state: _IdentityState) -> tuple[object, ...]:
    return (
        tuple(sorted(state.resolved_values().items())),
        tuple(sorted(state.cells.items())),
        tuple(sorted(state.bindings.items())),
        state.completion,
        state.result,
        state.exception,
        state.module_id,
        state.module_entries,
    )


def _join_states(states: list[_IdentityState]) -> _IdentityState:
    first = states[0]
    names = set().union(*(state.values for state in states))
    values = {
        name: _join_values([state.read(name) for state in states]) for name in names
    }
    cell_ids = set().union(*(state.cells for state in states))
    cells = {
        cell_id: _join_values(
            [state.cells.get(cell_id, _EMPTY_VALUE) for state in states]
        )
        for cell_id in cell_ids
    }
    return _joined_state(first, states, values, cells)


def _joined_state(
    first: _IdentityState,
    states: list[_IdentityState],
    values: dict[str, _IdentityValue],
    cells: dict[str, _IdentityValue],
) -> _IdentityState:
    bindings = {
        name: cell_ids_for_name.pop()
        for name in set.intersection(*(set(state.bindings) for state in states))
        if len(cell_ids_for_name := {state.bindings[name] for state in states}) == 1
    }
    return _IdentityState(
        values=values,
        cells=cells,
        bindings=bindings,
        completion=first.completion,
        result=_join_values([state.result for state in states]),
        exception=first.exception,
        path=_common_path([state.path for state in states]),
        frame_serial=max(state.frame_serial for state in states),
        module_id=first.module_id,
        module_entries=(
            first.module_entries
            if all(state.module_entries == first.module_entries for state in states)
            else ()
        ),
    )


def _join_values(values: list[_IdentityValue]) -> _IdentityValue:
    first = values[0]
    if all(item == first for item in values):
        return first
    alternatives = _joined_alternatives(values)
    targets = {item.target_key for item in alternatives if item.target_key is not None}
    return _IdentityValue(
        "unknown",
        target_key=targets.pop() if len(targets) == 1 else None,
        may_tracked=any(item.tracked for item in alternatives),
        alternatives=alternatives,
    )


def _joined_alternatives(values: list[_IdentityValue]) -> tuple[_IdentityValue, ...]:
    flattened = [
        alternative
        for value in values
        for alternative in (value.alternatives or (value,))
    ]
    unique: list[_IdentityValue] = []
    for value in flattened:
        if value not in unique:
            unique.append(value)
    return tuple(unique)


def _common_path(paths: list[tuple[ast.stmt, ...]]) -> tuple[ast.stmt, ...]:
    shortest = min(len(path) for path in paths)
    length = 0
    while length < shortest and all(
        path[length] is paths[0][length] for path in paths[1:]
    ):
        length += 1
    return paths[0][:length]


__all__: list[str] = []
