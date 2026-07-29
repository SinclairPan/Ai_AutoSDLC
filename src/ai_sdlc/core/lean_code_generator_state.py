"""保存生成器消费器的词法身份、lineage 与游标状态。"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field

from ai_sdlc.core.lean_code_context_manager_lineage import (
    _ContextManagerProtocols,
    _merge_protocols,
)
from ai_sdlc.core.lean_code_generator_identity import _merge_lineages


@dataclass
class _ConsumerState:
    identities: dict[str, str] = field(default_factory=dict)
    builtin_modules: set[str] = field(default_factory=set)
    generators: dict[str, tuple[ast.GeneratorExp, ...]] = field(
        default_factory=dict
    )
    context_manager_protocols: dict[str, _ContextManagerProtocols] = field(
        default_factory=dict
    )
    generator_offsets: dict[int, int | None] = field(default_factory=dict)
    evaluate_annotations: bool = True

    def fork(self) -> _ConsumerState:
        return _ConsumerState(
            identities=dict(self.identities),
            builtin_modules=set(self.builtin_modules),
            generators=dict(self.generators),
            context_manager_protocols=dict(self.context_manager_protocols),
            generator_offsets=dict(self.generator_offsets),
            evaluate_annotations=self.evaluate_annotations,
        )


def _bind_names(
    names: set[str],
    identity: str,
    state: _ConsumerState,
) -> None:
    for name in names:
        state.identities[name] = identity
        state.builtin_modules.discard(name)
        state.generators.pop(name, None)
        state.context_manager_protocols.pop(name, None)


def _bind_generator_names(
    names: set[str],
    lineage: tuple[ast.GeneratorExp, ...],
    state: _ConsumerState,
) -> None:
    for name in names:
        if lineage:
            state.generators[name] = lineage
        else:
            state.generators.pop(name, None)


def _bind_context_manager_names(
    names: set[str],
    protocols: _ContextManagerProtocols,
    state: _ConsumerState,
) -> None:
    for name in names:
        if (
            protocols.sync.defined
            or protocols.sync.uncertain
            or protocols.async_.defined
            or protocols.async_.uncertain
        ):
            state.context_manager_protocols[name] = protocols
        else:
            state.context_manager_protocols.pop(name, None)


def _invalidate_non_consumers(state: _ConsumerState) -> None:
    for name, identity in tuple(state.identities.items()):
        if identity == "no-consume":
            state.identities[name] = "unknown"


def _merge_consumer_states(
    target: _ConsumerState,
    branches: list[_ConsumerState],
) -> None:
    names = set().union(*(branch.identities for branch in branches))
    target.identities = {
        name: next(iter(identities)) if len(identities) == 1 else "unknown"
        for name in names
        if (
            identities := {
                branch.identities.get(name, "unknown")
                for branch in branches
            }
        )
    }
    target.builtin_modules = set.intersection(
        *(branch.builtin_modules for branch in branches)
    )
    generator_names = set().union(*(branch.generators for branch in branches))
    target.generators = {
        name: _merge_lineages(
            *(branch.generators.get(name, ()) for branch in branches)
        )
        for name in generator_names
    }
    target.context_manager_protocols = _merged_manager_protocols(branches)
    generator_ids = set().union(
        *(branch.generator_offsets for branch in branches)
    )
    target.generator_offsets = {
        generator_id: next(iter(offsets)) if len(offsets) == 1 else None
        for generator_id in generator_ids
        if (
            offsets := {
                branch.generator_offsets.get(generator_id)
                for branch in branches
            }
        )
    }


def _merged_manager_protocols(
    branches: list[_ConsumerState],
) -> dict[str, _ContextManagerProtocols]:
    names = set().union(*(branch.context_manager_protocols for branch in branches))
    return {
        name: _merge_protocols(
            tuple(
                branch.context_manager_protocols.get(
                    name,
                    _ContextManagerProtocols(),
                )
                for branch in branches
            )
        )
        for name in names
    }


def _advance_generator_offsets(
    generators: tuple[ast.GeneratorExp, ...],
    mode: str,
    state: _ConsumerState,
) -> None:
    for generator in generators:
        key = id(generator)
        current = state.generator_offsets.get(key, 0)
        state.generator_offsets[key] = (
            current + 1
            if mode == "one" and current is not None
            else None
        )


__all__: list[str] = []
