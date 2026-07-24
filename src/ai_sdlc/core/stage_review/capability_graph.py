"""Capability 生命周期归一化后的唯一依赖与冲突图。"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from ai_sdlc.core.stage_review.registry_models import (
    CapabilityDefinition,
    ReviewerCapabilityRegistry,
)


@dataclass(frozen=True, slots=True)
class CanonicalCapabilityGraph:
    """deprecated 已解析、可供 Registry 与 Role 共用的能力图。"""

    by_id: dict[str, CapabilityDefinition]
    dependencies: dict[str, frozenset[str]]
    conflicts: dict[str, frozenset[str]]


def capabilities_by_id(
    capabilities: Iterable[CapabilityDefinition],
) -> dict[str, CapabilityDefinition]:
    items = list(capabilities)
    by_id = {item.capability_id: item for item in items}
    if len(by_id) != len(items):
        raise ValueError("duplicate capability identity")
    return by_id


def direct_dependencies(item: CapabilityDefinition) -> set[str]:
    return set(item.implies) | ({item.parent} if item.parent else set())


def resolve_capability_ids(
    registry: ReviewerCapabilityRegistry,
    requested_ids: Iterable[str],
    *,
    allow_shadow: bool = False,
) -> tuple[str, ...]:
    """把 deprecated 能力解析到有效终点，并默认拒绝 shadow。"""

    by_id = capabilities_by_id(registry.capabilities)
    return resolved_reference_ids(
        requested_ids,
        by_id=by_id,
        allow_shadow=allow_shadow,
    )


def expand_capability_ids(
    registry: ReviewerCapabilityRegistry,
    requested_ids: Iterable[str],
    *,
    allow_shadow: bool = False,
) -> tuple[str, ...]:
    """在 canonical 图上解析生命周期、依赖闭包和冲突。"""

    graph = build_canonical_graph(
        capabilities_by_id(registry.capabilities),
        allow_shadow=allow_shadow,
    )
    requested = set(
        resolved_reference_ids(
            requested_ids,
            by_id=graph.by_id,
            allow_shadow=allow_shadow,
        )
    )
    expanded = dependency_closure(requested, graph.dependencies)
    _reject_graph_conflicts(expanded, graph.conflicts)
    return tuple(sorted(expanded))


def build_canonical_graph(
    by_id: dict[str, CapabilityDefinition],
    *,
    allow_shadow: bool,
) -> CanonicalCapabilityGraph:
    dependencies = normalized_dependency_graph(by_id, allow_shadow=allow_shadow)
    conflicts = _normalized_conflict_graph(by_id, allow_shadow=allow_shadow)
    verify_acyclic(dependencies, label="capability dependency cycle")
    for capability_id, conflict_ids in conflicts.items():
        if any(capability_id not in conflicts[item] for item in conflict_ids):
            raise ValueError("capability conflicts must be symmetric")
    for capability_id in sorted(dependencies):
        closure = dependency_closure({capability_id}, dependencies)
        _reject_graph_conflicts(closure, conflicts)
    return CanonicalCapabilityGraph(
        by_id=by_id,
        dependencies=dependencies,
        conflicts=conflicts,
    )


def normalized_dependency_graph(
    by_id: dict[str, CapabilityDefinition],
    *,
    allow_shadow: bool,
) -> dict[str, frozenset[str]]:
    included = _included_ids(by_id, allow_shadow=allow_shadow)
    return {
        capability_id: frozenset(
            resolved_reference_ids(
                direct_dependencies(by_id[capability_id]),
                by_id=by_id,
                allow_shadow=allow_shadow,
            )
        )
        for capability_id in sorted(included)
    }


def resolved_dependency_closure(
    item: CapabilityDefinition,
    *,
    by_id: dict[str, CapabilityDefinition],
) -> set[str]:
    graph = normalized_dependency_graph(by_id, allow_shadow=True)
    seeds = set(
        resolved_reference_ids(
            direct_dependencies(item),
            by_id=by_id,
            allow_shadow=True,
        )
    )
    return dependency_closure(seeds, graph)


def resolved_reference_ids(
    references: Iterable[str],
    *,
    by_id: dict[str, CapabilityDefinition],
    allow_shadow: bool,
) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                _resolve_capability(
                    by_id,
                    item,
                    allow_shadow=allow_shadow,
                ).capability_id
                for item in references
            }
        )
    )


def dependency_closure(
    seeds: set[str],
    graph: Mapping[str, Iterable[str]],
) -> set[str]:
    closure = set(seeds)
    pending = list(sorted(seeds))
    while pending:
        current = pending.pop()
        for dependency in sorted(graph.get(current, ())):
            if dependency not in closure:
                closure.add(dependency)
                pending.append(dependency)
    return closure


def verify_acyclic(
    graph: Mapping[str, Iterable[str]],
    *,
    label: str,
) -> None:
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str) -> None:
        if node in visiting:
            raise ValueError(f"{label}: {node}")
        if node in visited:
            return
        visiting.add(node)
        for dependency in sorted(graph.get(node, ())):
            visit(dependency)
        visiting.remove(node)
        visited.add(node)

    for node in sorted(graph):
        visit(node)


def _normalized_conflict_graph(
    by_id: dict[str, CapabilityDefinition],
    *,
    allow_shadow: bool,
) -> dict[str, frozenset[str]]:
    included = _included_ids(by_id, allow_shadow=allow_shadow)
    return {
        capability_id: frozenset(
            set(
                resolved_reference_ids(
                    by_id[capability_id].conflicts,
                    by_id=by_id,
                    allow_shadow=True,
                )
            )
            & included
        )
        for capability_id in sorted(included)
    }


def _included_ids(
    by_id: dict[str, CapabilityDefinition],
    *,
    allow_shadow: bool,
) -> set[str]:
    return {
        item.capability_id
        for item in by_id.values()
        if item.maturity == "active" or (allow_shadow and item.maturity == "shadow")
    }


def _resolve_capability(
    by_id: dict[str, CapabilityDefinition],
    capability_id: str,
    *,
    allow_shadow: bool,
) -> CapabilityDefinition:
    item = by_id.get(capability_id)
    if item is None:
        raise ValueError(f"unknown capability in reviewer role: {capability_id}")
    while item.maturity == "deprecated":
        item = by_id[item.superseded_by]
    if item.maturity == "shadow" and not allow_shadow:
        raise ValueError(f"reviewer role requires active capability: {item.capability_id}")
    return item


def _reject_graph_conflicts(
    capability_ids: set[str],
    conflicts: dict[str, frozenset[str]],
) -> None:
    for capability_id in sorted(capability_ids):
        found = capability_ids & set(conflicts[capability_id])
        if found:
            raise ValueError(
                f"capability conflict closure: {capability_id} conflicts with {sorted(found)}"
            )
