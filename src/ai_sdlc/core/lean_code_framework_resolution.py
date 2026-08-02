"""解析可证明保持身份的 framework callable contract。"""

from __future__ import annotations

import ast
from typing import Protocol

from ai_sdlc.core.lean_code_dynamic_refs import _BUILTIN_PUBLIC_METHODS
from ai_sdlc.core.lean_code_framework_effects import _TopLevelStoreFinder
from ai_sdlc.core.lean_code_framework_properties import (
    _protocol_decorators_preserve_contract,
    _protocol_property_decorator,
    _protocol_property_lifecycle_preserves_contract,
    _update_class_property_bindings,
)

Contract = tuple[str, tuple[str, ...]]


class _FrameworkClassState(Protocol):
    protocol_aliases: set[str]
    typing_modules: set[str]
    pydantic_bases: set[str]
    pydantic_classes: set[str]
    pydantic_modules: set[str]
    builtin_names: set[str]
    identity_method_decorators: set[str]
    builtin_decorator_modules: set[str]
    property_decorators: set[str]
    property_decorator_modules: set[str]
    abc_decorator_modules: set[str]

    def invalidate(self, name: str) -> None: ...


def _apply_class(
    node: ast.ClassDef,
    state: _FrameworkClassState,
    contracts: dict[str, Contract],
) -> None:
    # 类装饰器可替换整个类对象；未证明保持身份时不能授予框架豁免。
    if node.decorator_list or node.keywords:
        state.invalidate(node.name)
        return
    base_names = [_qualified_name(base) for base in node.bases]
    base_name_set = set(base_names)
    is_protocol = _is_protocol(base_name_set, state)
    is_pydantic = _is_pydantic(base_name_set, state)
    builtin_bases = base_name_set & state.builtin_names
    if len(base_names) != 1 or not (is_protocol or is_pydantic or builtin_bases):
        state.invalidate(node.name)
        return
    property_members: set[str] = set()
    property_decorators = set(state.property_decorators)
    property_modules = set(state.property_decorator_modules)
    for child in node.body:
        _apply_class_member(
            node.name,
            child,
            state,
            contracts,
            property_members,
            property_decorators,
            property_modules,
            is_protocol=is_protocol,
            is_pydantic=is_pydantic,
            builtin_bases=builtin_bases,
        )
        _update_class_property_bindings(
            child,
            property_decorators,
            property_modules,
        )
    state.invalidate(node.name)
    if is_pydantic:
        state.pydantic_classes.add(node.name)


def _apply_class_member(
    class_name: str,
    child: ast.stmt,
    state: _FrameworkClassState,
    contracts: dict[str, Contract],
    property_members: set[str],
    property_decorators: set[str],
    property_modules: set[str],
    *,
    is_protocol: bool,
    is_pydantic: bool,
    builtin_bases: set[str],
) -> None:
    lifecycle, rebound = _class_member_lifecycle(
        class_name,
        child,
        contracts,
        property_members,
    )
    if not isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
        property_members.difference_update(rebound.names)
        return
    if not _class_member_decorators_allowed(
        child,
        state,
        property_decorators,
        property_modules,
        is_protocol=is_protocol,
        lifecycle=lifecycle,
    ):
        property_members.discard(child.name)
        return
    _record_class_method_contract(
        class_name,
        child,
        state,
        contracts,
        property_members,
        property_decorators,
        property_modules,
        lifecycle=lifecycle,
        is_protocol=is_protocol,
        is_pydantic=is_pydantic,
        builtin_bases=builtin_bases,
    )


def _class_member_lifecycle(
    class_name: str,
    child: ast.stmt,
    contracts: dict[str, Contract],
    property_members: set[str],
) -> tuple[bool, _TopLevelStoreFinder]:
    previous_contract, previous_property = _previous_class_member_state(
        class_name,
        child,
        contracts,
        property_members,
    )
    rebound = _rebind_class_members(class_name, child, contracts)
    lifecycle = isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and (
        _protocol_property_lifecycle_preserves_contract(
            child,
            previous_contract,
            previous_property=previous_property,
        )
    )
    return lifecycle, rebound


def _rebind_class_members(
    class_name: str,
    child: ast.stmt,
    contracts: dict[str, Contract],
) -> _TopLevelStoreFinder:
    rebound = _TopLevelStoreFinder()
    rebound.visit(child)
    for member_name in rebound.names:
        contracts.pop(f"{class_name}.{member_name}", None)
    return rebound


def _previous_class_member_state(
    class_name: str,
    child: ast.stmt,
    contracts: dict[str, Contract],
    property_members: set[str],
) -> tuple[Contract | None, bool]:
    if not isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return None, False
    return (
        contracts.get(f"{class_name}.{child.name}"),
        child.name in property_members,
    )


def _class_member_decorators_allowed(
    child: ast.FunctionDef | ast.AsyncFunctionDef,
    state: _FrameworkClassState,
    property_decorators: set[str],
    property_modules: set[str],
    *,
    is_protocol: bool,
    lifecycle: bool,
) -> bool:
    return not child.decorator_list or (
        is_protocol
        and (
            _protocol_decorators_preserve_contract(
                child,
                state,
                property_decorators,
                property_modules,
            )
            or lifecycle
        )
    )


def _record_class_method_contract(
    class_name: str,
    child: ast.FunctionDef | ast.AsyncFunctionDef,
    state: _FrameworkClassState,
    contracts: dict[str, Contract],
    property_members: set[str],
    property_decorators: set[str],
    property_modules: set[str],
    *,
    lifecycle: bool,
    is_protocol: bool,
    is_pydantic: bool,
    builtin_bases: set[str],
) -> None:
    contract = _class_method_contract(
        class_name,
        child.name,
        is_protocol=is_protocol,
        is_pydantic=is_pydantic,
        builtin_bases=builtin_bases,
    )
    if contract:
        contracts[f"{class_name}.{child.name}"] = contract
        if is_protocol and (
            lifecycle
            or _protocol_property_decorator(
                child,
                property_decorators,
                property_modules,
            )
        ):
            property_members.add(child.name)
        else:
            property_members.discard(child.name)
    else:
        property_members.discard(child.name)


def _is_protocol(
    base_names: set[str],
    state: _FrameworkClassState,
) -> bool:
    return any(
        name in state.protocol_aliases
        or any(name == f"{module}.Protocol" for module in state.typing_modules)
        for name in base_names
    )


def _is_pydantic(
    base_names: set[str],
    state: _FrameworkClassState,
) -> bool:
    return any(
        name in state.pydantic_bases
        or name in state.pydantic_classes
        or any(name == f"{module}.BaseModel" for module in state.pydantic_modules)
        for name in base_names
    )


def _class_method_contract(
    class_name: str,
    method_name: str,
    *,
    is_protocol: bool,
    is_pydantic: bool,
    builtin_bases: set[str],
) -> Contract | None:
    if is_protocol:
        return "protocol-member", (f"base={class_name}:Protocol",)
    if is_pydantic and method_name == "model_post_init":
        return "pydantic-lifecycle", ("hook=model_post_init",)
    return _builtin_override_contract(builtin_bases, method_name)


def _typer_command_contract(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    typer_apps: set[str],
) -> Contract | None:
    for index, decorator in enumerate(node.decorator_list):
        target = decorator.func if isinstance(decorator, ast.Call) else decorator
        if (
            isinstance(target, ast.Attribute)
            and isinstance(target.value, ast.Name)
            and target.value.id in typer_apps
            and target.attr in {"callback", "command"}
            and index == len(node.decorator_list) - 1
        ):
            app_name = target.value.id
            return (
                "typer-command",
                (f"typer-app={app_name}", f"decorator={app_name}.{target.attr}"),
            )
    return None


def _builtin_override_contract(
    base_names: set[str],
    method_name: str,
) -> Contract | None:
    for base_name in sorted(base_names):
        if method_name in _BUILTIN_PUBLIC_METHODS[base_name]:
            return (
                "builtin-override",
                (f"base={base_name}", f"method={method_name}"),
            )
    return None


def _qualified_name(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _qualified_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return ""


__all__: list[str] = []
