"""合并描述符分析的分支状态并保留参数化调用效果。"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from ai_sdlc.core.lean_code_callback_effects import (
    _CallbackSummary,
    _merge_callback_summaries,
)


class _DescriptorStateLike(Protocol):
    bindings: dict[str, str]
    builtin_modules: set[str]
    mutators: dict[str, frozenset[str]]
    callback_parameters: dict[str, _CallbackSummary]
    latent_mutators: dict[str, frozenset[str]]
    typing_modules: set[str]
    type_hint_functions: set[str]


def _merge_descriptor_states(
    target: _DescriptorStateLike,
    states: Sequence[_DescriptorStateLike],
) -> None:
    names = set().union(*(state.bindings for state in states))
    target.bindings = {
        name: next(iter(kinds)) if len(kinds) == 1 else ""
        for name in names
        if (kinds := {state.bindings.get(name, "") for state in states})
    }
    target.builtin_modules = set.intersection(
        *(state.builtin_modules for state in states)
    )
    target.mutators = _merge_effect_maps(states, "mutators")
    target.callback_parameters = _merge_callbacks(states)
    target.latent_mutators = _merge_effect_maps(states, "latent_mutators")
    target.typing_modules = set.intersection(
        *(state.typing_modules for state in states)
    )
    target.type_hint_functions = set().union(
        *(state.type_hint_functions for state in states)
    )


def _merge_effect_maps(
    states: Sequence[_DescriptorStateLike],
    attribute: str,
) -> dict[str, frozenset[str]]:
    mappings = [getattr(state, attribute) for state in states]
    names = set().union(*mappings)
    return {
        name: frozenset().union(
            *(mapping.get(name, frozenset()) for mapping in mappings)
        )
        for name in names
    }


def _merge_callbacks(
    states: Sequence[_DescriptorStateLike],
) -> dict[str, _CallbackSummary]:
    names = set().union(*(state.callback_parameters for state in states))
    return {
        name: _merge_callback_summaries(
            [
                state.callback_parameters[name]
                for state in states
                if name in state.callback_parameters
            ],
            missing=any(name not in state.callback_parameters for state in states),
        )
        for name in names
    }


__all__: list[str] = []
