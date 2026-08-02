"""解析类引用、成员能力与 C3 方法解析顺序。"""

from __future__ import annotations

import ast
from collections.abc import Collection
from dataclasses import dataclass, field
from itertools import product

from ai_sdlc.core.lean_code_import_binding_flow import _contained_dynamic_kinds
from ai_sdlc.core.lean_code_import_class_members import _Member
from ai_sdlc.core.lean_code_static_values import _constant_subscript_value


@dataclass(frozen=True)
class _ClassCandidate:
    node: ast.ClassDef
    parent_scope: tuple[int, ...]
    class_ancestors: tuple[int, ...]
    class_names: tuple[str, ...]


@dataclass
class _ClassInfo:
    candidate: _ClassCandidate
    mros: tuple[tuple[int, ...], ...]
    metaclass_refs: frozenset[int] = frozenset()
    members: dict[str, _Member] = field(default_factory=dict)


@dataclass
class _ScopeState:
    bindings: dict[str, set[int]]

    def fork(self) -> _ScopeState:
        return _ScopeState(
            {name: set(class_ids) for name, class_ids in self.bindings.items()}
        )


def _delete_scope_target(
    target: ast.expr,
    state: _ScopeState,
    infos: dict[int, _ClassInfo],
) -> None:
    if isinstance(target, ast.Name):
        state.bindings.pop(target.id, None)
    elif isinstance(target, ast.Attribute):
        for class_id in _class_refs(target.value, state.bindings, infos):
            info = infos.get(class_id)
            if info is not None:
                info.members.pop(target.attr, None)


def _class_refs(
    value: ast.expr,
    bindings: dict[str, set[int]],
    infos: dict[int, _ClassInfo],
) -> set[int]:
    if isinstance(value, ast.NamedExpr):
        return _class_refs(value.value, bindings, infos)
    if isinstance(value, ast.Name):
        return set(bindings.get(value.id, set()))
    if isinstance(value, ast.Call):
        return _class_refs(value.func, bindings, infos)
    if isinstance(value, ast.IfExp):
        return {
            *_class_refs(value.body, bindings, infos),
            *_class_refs(value.orelse, bindings, infos),
        }
    if isinstance(value, ast.Subscript):
        selected = _constant_subscript_value(value)
        if selected is not None:
            return _class_refs(selected, bindings, infos)
    if isinstance(value, ast.Attribute):
        refs: set[int] = set()
        for class_id in _class_refs(value.value, bindings, infos):
            member = _resolve_member(infos.get(class_id), value.attr, infos)
            if member is not None:
                refs.update(member.class_refs)
        return refs
    return set()


def _member_value(
    value: ast.expr,
    bindings: dict[str, set[int]],
    infos: dict[int, _ClassInfo],
    dynamic_names: Collection[str],
) -> _Member:
    refs = _class_refs(value, bindings, infos)
    if refs:
        member = _Member(class_refs=frozenset(refs))
        return _Member(
            bound_dynamic=_dynamic_descriptor_get(member, infos),
            class_refs=member.class_refs,
        )
    if isinstance(value, ast.Attribute):
        members = [
            _resolve_member(infos.get(class_id), value.attr, infos)
            for class_id in _class_refs(value.value, bindings, infos)
        ]
        present = [member for member in members if member is not None]
        if present:
            return _Member(
                dynamic=any(member.dynamic for member in present),
                raw_dynamic=any(member.raw_dynamic for member in present),
                bound_dynamic=any(member.bound_dynamic for member in present),
                class_refs=frozenset().union(
                    *(member.class_refs for member in present)
                ),
            )
    dynamic = "callable" in _contained_dynamic_kinds(
        value,
        frozenset(),
        dynamic_names,
    )
    return _Member(
        dynamic=dynamic,
        raw_dynamic=dynamic,
        bound_dynamic=dynamic,
    )


def _possible_mros(
    class_id: int,
    base_choices: list[set[int]],
    infos: dict[int, _ClassInfo],
) -> tuple[tuple[int, ...], ...]:
    if not base_choices:
        return ((class_id,),)
    choices = [
        tuple(items) if items else (_unknown_base_id(class_id, index),)
        for index, items in enumerate(base_choices)
    ]
    mros: set[tuple[int, ...]] = set()
    for bases in product(*choices):
        parent_choices = [
            infos[base].mros if base in infos else ((base,),) for base in bases
        ]
        for parent_mros in product(*parent_choices):
            mros.add(_c3_mro(class_id, bases, parent_mros))
    return tuple(sorted(mros))


def _unknown_base_id(class_id: int, index: int) -> int:
    return -(class_id * 1024 + index + 1)


def _c3_mro(
    class_id: int,
    bases: tuple[int, ...],
    parent_mros: tuple[tuple[int, ...], ...],
) -> tuple[int, ...]:
    sequences = [list(mro) for mro in parent_mros]
    sequences.append(list(bases))
    result = [class_id]
    while any(sequences):
        sequences = [items for items in sequences if items]
        candidate = next(
            (
                items[0]
                for items in sequences
                if not any(items[0] in other[1:] for other in sequences)
            ),
            None,
        )
        if candidate is None:
            result.extend(
                item for items in sequences for item in items if item not in result
            )
            break
        result.append(candidate)
        for items in sequences:
            if items and items[0] == candidate:
                items.pop(0)
    return tuple(result)


def _resolve_member(
    info: _ClassInfo | None,
    name: str,
    infos: dict[int, _ClassInfo],
) -> _Member | None:
    resolved = _resolve_raw_member(info, name, infos)
    if resolved is None:
        return None
    if _dynamic_descriptor_get(resolved, infos):
        return _Member(
            dynamic=True,
            raw_dynamic=resolved.raw_dynamic,
            bound_dynamic=resolved.bound_dynamic,
            class_refs=resolved.class_refs,
        )
    return resolved


def _resolve_raw_member(
    info: _ClassInfo | None,
    name: str,
    infos: dict[int, _ClassInfo],
) -> _Member | None:
    if info is None:
        return None
    resolved = []
    for mro in info.mros:
        value = None
        for class_id in mro:
            if class_id not in infos:
                value = _Member(
                    dynamic=True,
                    raw_dynamic=True,
                    bound_dynamic=True,
                )
                break
            if name in infos[class_id].members:
                value = infos[class_id].members[name]
                break
        if value is not None:
            resolved.append(value)
    if not resolved:
        return None
    return _Member(
        dynamic=any(item.dynamic for item in resolved),
        raw_dynamic=any(item.raw_dynamic for item in resolved),
        bound_dynamic=any(item.bound_dynamic for item in resolved),
        class_refs=frozenset().union(*(item.class_refs for item in resolved)),
    )


def _dynamic_descriptor_get(
    member: _Member,
    infos: dict[int, _ClassInfo],
) -> bool:
    return any(
        descriptor is not None and descriptor.dynamic
        for class_id in member.class_refs
        if class_id in infos
        for descriptor in (
            _resolve_raw_member(infos[class_id], "__get__", infos),
        )
    )


def _dynamic_member_paths(
    class_id: int,
    infos: dict[int, _ClassInfo],
    queried_names: frozenset[str] = frozenset(),
    seen: frozenset[int] = frozenset(),
) -> set[str]:
    if class_id in seen or class_id not in infos:
        return set()
    info = infos[class_id]
    names = queried_names | {
        name
        for mro in info.mros
        for item in mro
        if item in infos
        for name in infos[item].members
    }
    paths: set[str] = set()
    for name in names:
        member = _resolve_member(info, name, infos)
        metaclass_dynamic = _metaclass_lookup_dynamic(info, infos)
        if metaclass_dynamic:
            paths.add(name)
        if member is None:
            if _instance_fallback_dynamic(info, infos):
                paths.add(f"__instance__.{name}")
            continue
        if member.dynamic:
            paths.add(name)
        for ref in member.class_refs:
            paths.update(
                f"{name}.{suffix}"
                for suffix in _dynamic_member_paths(
                    ref,
                    infos,
                    queried_names,
                    seen | {class_id},
                )
            )
    return paths


def _instance_fallback_dynamic(
    info: _ClassInfo,
    infos: dict[int, _ClassInfo],
) -> bool:
    return any(
        member is not None and member.dynamic
        for member in (
            _resolve_raw_member(info, "__getattribute__", infos),
            _resolve_raw_member(info, "__getattr__", infos),
        )
    )


def _metaclass_lookup_dynamic(
    info: _ClassInfo,
    infos: dict[int, _ClassInfo],
) -> bool:
    return any(
        member is not None and member.dynamic
        for class_id in info.metaclass_refs
        if class_id in infos
        for member in (
            _resolve_raw_member(infos[class_id], "__getattribute__", infos),
        )
    )


def _qualified_paths(
    classes: Collection[_ClassCandidate],
    infos: dict[int, _ClassInfo],
    queried_names: frozenset[str] = frozenset(),
) -> dict[int, frozenset[str]]:
    paths: dict[int, set[str]] = {}
    for candidate in classes:
        info = infos.get(id(candidate.node))
        direct_dynamic = {
            name
            for name, member in (info.members.items() if info is not None else ())
            if member.raw_dynamic
        }
        bound_dynamic = {
            name
            for name, member in (info.members.items() if info is not None else ())
            if member.bound_dynamic
        }
        for member in _dynamic_member_paths(
            id(candidate.node),
            infos,
            queried_names,
        ):
            for index, class_id in enumerate(candidate.class_ancestors):
                path = ".".join((*candidate.class_names[index:], member))
                paths.setdefault(class_id, set()).add(path)
        for member in direct_dynamic:
            for index, class_id in enumerate(candidate.class_ancestors):
                path = ".".join(
                    (*candidate.class_names[index:], "__dict__", member)
                )
                paths.setdefault(class_id, set()).add(path)
        for member in bound_dynamic:
            for index, class_id in enumerate(candidate.class_ancestors):
                path = ".".join(
                    (
                        *candidate.class_names[index:],
                        "__dict__",
                        member,
                        "__get__",
                    )
                )
                paths.setdefault(class_id, set()).add(path)
    return {class_id: frozenset(class_paths) for class_id, class_paths in paths.items()}


def _merge_scope_states(
    target: _ScopeState,
    states: Collection[_ScopeState],
) -> None:
    names = set().union(*(state.bindings for state in states))
    target.bindings = {
        name: set().union(*(state.bindings.get(name, set()) for state in states))
        for name in names
        if any(state.bindings.get(name) for state in states)
    }


def _nested_blocks(statement: ast.stmt) -> tuple[list[ast.stmt], ...]:
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
