"""传播类工厂方法的限定名与类别名。"""

from __future__ import annotations

import ast
from collections.abc import Collection

from ai_sdlc.core.lean_code_control_flow import _unpack_analysis
from ai_sdlc.core.lean_code_import_reflection import (
    _attribute_path,
    _reflection_alias_markers,
    _reflection_callable_kind,
)
from ai_sdlc.core.lean_code_scope import _bound_names
from ai_sdlc.core.lean_code_static_values import (
    _constant_subscript_value,
    _constant_value,
)


def _parallel_sequence(target: ast.expr, value: ast.expr) -> bool:
    return (
        isinstance(target, (ast.Tuple, ast.List))
        and isinstance(value, (ast.Tuple, ast.List))
        and len(target.elts) == len(value.elts)
        and not any(isinstance(item, ast.Starred) for item in target.elts)
    )


def _assigned_dynamic_class_callables(
    statement: ast.stmt,
    callable_aliases: Collection[str],
) -> set[str]:
    if isinstance(statement, ast.Assign):
        targets = statement.targets
        value = statement.value
    elif isinstance(statement, ast.AnnAssign) and statement.value is not None:
        targets = [statement.target]
        value = statement.value
    elif isinstance(statement, ast.Expr) and isinstance(statement.value, ast.NamedExpr):
        targets = [statement.value.target]
        value = statement.value.value
    else:
        return set()
    callables: set[str] = set()
    for target in targets:
        callables.update(_dynamic_class_alias_bindings(target, value, callable_aliases))
    return callables


def _default_dynamic_class_callables(
    arguments: ast.arguments,
    callable_aliases: Collection[str],
) -> set[str]:
    positional = [*arguments.posonlyargs, *arguments.args]
    pairs = [
        *(
            zip(
                positional[-len(arguments.defaults) :],
                arguments.defaults,
                strict=True,
            )
            if arguments.defaults
            else ()
        ),
        *(
            (argument, default)
            for argument, default in zip(
                arguments.kwonlyargs,
                arguments.kw_defaults,
                strict=True,
            )
            if default is not None
        ),
    ]
    return set().union(
        *(
            _dynamic_class_alias_bindings(
                ast.Name(id=argument.arg, ctx=ast.Store()),
                default,
                callable_aliases,
            )
            for argument, default in pairs
        )
    )


def _dynamic_class_alias_bindings(
    target: ast.expr,
    value: ast.expr,
    callable_aliases: Collection[str],
) -> set[str]:
    if isinstance(target, (ast.Tuple, ast.List)):
        unpack = _unpack_analysis(target, value)
        if not unpack.points:
            return set().union(
                *(
                    _dynamic_class_alias_bindings(
                        binding.targets[0],
                        binding.value,
                        callable_aliases,
                    )
                    for binding in unpack.bindings
                )
            )
    target_paths = _binding_paths(target)
    reflection_bindings = _reflection_alias_bindings(
        target_paths,
        value,
        callable_aliases,
    )
    if reflection_bindings:
        return reflection_bindings
    if _dynamic_class_callable(value, callable_aliases):
        return target_paths
    descriptor_suffixes = _namespace_member_suffixes(value, callable_aliases)
    if descriptor_suffixes:
        return {
            f"{name}.{suffix}"
            for name in target_paths
            for suffix in descriptor_suffixes
        }
    sources = _contained_dynamic_class_sources(value, callable_aliases)
    suffixes = {
        _source_alias_suffix(value, alias.removeprefix(f"{source}."))
        for source in sources
        for alias in callable_aliases
        if alias.startswith(f"{source}.")
        and not (
            isinstance(value, ast.Call)
            and alias.removeprefix(f"{source}.").startswith("__dict__.")
        )
    }
    return {f"{name}.{suffix}" for name in target_paths for suffix in suffixes}


def _reflection_alias_bindings(
    target_paths: set[str],
    value: ast.expr,
    callable_aliases: Collection[str],
) -> set[str]:
    return {
        f"{name}.{reflection_alias}"
        for reflection_alias in _reflection_alias_markers(
            value,
            callable_aliases,
        )
        for name in target_paths
    }


def _source_alias_suffix(value: ast.expr, suffix: str) -> str:
    marker = "__instance__."
    selected = (
        _constant_subscript_value(value)
        if isinstance(value, ast.Subscript)
        else None
    )
    normalized = selected or value
    if _contains_call_result(normalized) and suffix.startswith(marker):
        return suffix.removeprefix(marker)
    return suffix


def _contains_call_result(value: ast.expr) -> bool:
    if isinstance(value, ast.NamedExpr):
        return _contains_call_result(value.value)
    if isinstance(value, ast.Call):
        return True
    if isinstance(value, ast.IfExp):
        return _contains_call_result(value.body) or _contains_call_result(
            value.orelse
        )
    if isinstance(value, ast.BoolOp):
        return any(_contains_call_result(item) for item in value.values)
    return False


def _dynamic_class_callable(
    value: ast.expr,
    callable_aliases: Collection[str],
) -> bool:
    reflective = _reflective_member(value, callable_aliases)
    if reflective is not None:
        owner, member, mode = reflective
        root = _attribute_path(owner)
        namespace = ".__dict__" if mode == "namespace" else ""
        member_roots = (
            (f"{root}.__instance__", root)
            if mode == "attribute" and isinstance(owner, ast.Call)
            else (f"{root}{namespace}",)
        )
        if root and member == "*":
            return any(
                alias.startswith(f"{member_root}.")
                for member_root in member_roots
                for alias in callable_aliases
            )
        return bool(
            root
            and any(
                f"{member_root}.{member}" in callable_aliases
                for member_root in member_roots
            )
        )
    candidate = value.func if isinstance(value, ast.Call) else value
    if isinstance(candidate, ast.Attribute):
        path = _attribute_path(candidate)
    else:
        return False
    if path in callable_aliases:
        return True
    if isinstance(candidate.value, ast.Call):
        root = _attribute_path(candidate.value.func)
        return bool(
            root
            and f"{root}.__instance__.{candidate.attr}" in callable_aliases
        )
    return False


def _namespace_member_suffixes(
    value: ast.expr,
    callable_aliases: Collection[str],
) -> set[str]:
    reflective = _reflective_member(value, callable_aliases)
    if reflective is None:
        return set()
    owner, member, mode = reflective
    root = _attribute_path(owner)
    if mode != "namespace" or not root or member == "*":
        return set()
    prefix = f"{root}.__dict__.{member}."
    return {
        alias.removeprefix(prefix)
        for alias in callable_aliases
        if alias.startswith(prefix)
    }


def _discard_callable_binding(callables: set[str], name: str) -> None:
    callables.difference_update(
        {alias for alias in callables if alias == name or alias.startswith(f"{name}.")}
    )


def _dynamic_class_source(
    value: ast.expr,
    callable_aliases: Collection[str],
) -> str:
    candidate = value.func if isinstance(value, ast.Call) else value
    path = _attribute_path(candidate)
    if path and any(alias.startswith(f"{path}.") for alias in callable_aliases):
        return path
    return ""


def _contained_dynamic_class_sources(
    value: ast.expr,
    callable_aliases: Collection[str],
) -> set[str]:
    if isinstance(value, ast.Subscript):
        selected = _constant_subscript_value(value)
        if selected is not None:
            return _contained_dynamic_class_sources(selected, callable_aliases)
    if isinstance(value, ast.IfExp):
        return {
            *_contained_dynamic_class_sources(value.body, callable_aliases),
            *_contained_dynamic_class_sources(value.orelse, callable_aliases),
        }
    direct = _dynamic_class_source(value, callable_aliases)
    if direct:
        return {direct}
    sources: set[str] = set()
    for child in ast.iter_child_nodes(value):
        if isinstance(child, ast.expr):
            sources.update(_contained_dynamic_class_sources(child, callable_aliases))
    return sources


def _binding_paths(target: ast.expr) -> set[str]:
    names = _bound_names(target)
    if names:
        return set(names)
    path = _attribute_path(target)
    return {path} if path else set()


def _reflective_member(
    value: ast.expr,
    callable_aliases: Collection[str] = (),
) -> tuple[ast.expr, str, str] | None:
    if (
        isinstance(value, ast.Call)
        and _reflection_callable_kind(value.func, callable_aliases) == "getattr"
        and len(value.args) >= 2
    ):
        member = _constant_value(value.args[1])
        return value.args[0], member if isinstance(member, str) else "*", "attribute"
    direct = _direct_getattribute_member(value)
    if direct is not None:
        return direct
    mapping = _vars_mapping_member(value, callable_aliases)
    if mapping is not None:
        return mapping
    return None


def _direct_getattribute_member(
    value: ast.expr,
) -> tuple[ast.expr, str, str] | None:
    if not (
        isinstance(value, ast.Call)
        and isinstance(value.func, ast.Attribute)
        and value.func.attr == "__getattribute__"
    ):
        return None
    if len(value.args) >= 2:
        owner, key = value.args[0], value.args[1]
    elif len(value.args) == 1:
        owner, key = value.func.value, value.args[0]
    else:
        return None
    member = _constant_value(key)
    return owner, member if isinstance(member, str) else "*", "attribute"


def _vars_mapping_member(
    value: ast.expr,
    callable_aliases: Collection[str],
) -> tuple[ast.expr, str, str] | None:
    container: ast.expr
    key: ast.expr
    if isinstance(value, ast.Subscript):
        container, key = value.value, value.slice
    elif (
        isinstance(value, ast.Call)
        and isinstance(value.func, ast.Attribute)
        and value.func.attr == "get"
    ):
        container = value.func.value
        if not value.args:
            return None
        key = value.args[0]
    else:
        return None
    owner = _mapping_owner(container, callable_aliases)
    if owner is None:
        return None
    member = _constant_value(key)
    return owner, member if isinstance(member, str) else "*", "namespace"


def _mapping_owner(
    container: ast.expr,
    callable_aliases: Collection[str],
) -> ast.expr | None:
    if (
        isinstance(container, ast.Call)
        and _reflection_callable_kind(container.func, callable_aliases) == "vars"
        and len(container.args) == 1
    ):
        return container.args[0]
    if isinstance(container, ast.Attribute) and container.attr == "__dict__":
        return container.value
    return None


def _reflective_member_name(value: ast.expr) -> str:
    member = _reflective_member(value)
    return member[1] if member is not None else ""


__all__: list[str] = []
