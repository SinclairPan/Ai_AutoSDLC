"""维护 framework callable 的顺序绑定证明状态。"""

from __future__ import annotations

from dataclasses import dataclass, field

from ai_sdlc.core.lean_code_dynamic_refs import _BUILTIN_PUBLIC_METHODS


@dataclass
class _BindingState:
    trusted_frameworks: frozenset[str]
    typer_modules: set[str] = field(default_factory=set)
    typer_constructors: set[str] = field(default_factory=set)
    typer_apps: set[str] = field(default_factory=set)
    typing_modules: set[str] = field(default_factory=set)
    protocol_aliases: set[str] = field(default_factory=set)
    pydantic_modules: set[str] = field(default_factory=set)
    pydantic_bases: set[str] = field(default_factory=set)
    pydantic_classes: set[str] = field(default_factory=set)
    builtin_names: set[str] = field(
        default_factory=lambda: set(_BUILTIN_PUBLIC_METHODS)
    )
    identity_method_decorators: set[str] = field(
        default_factory=lambda: {"property", "classmethod", "staticmethod"}
    )
    builtin_decorator_modules: set[str] = field(default_factory=set)
    property_decorators: set[str] = field(default_factory=lambda: {"property"})
    property_decorator_modules: set[str] = field(default_factory=set)
    abc_decorator_modules: set[str] = field(default_factory=set)
    contract_owner_aliases: dict[str, set[str]] = field(default_factory=dict)
    functions: set[str] = field(default_factory=set)
    dynamic_functions: set[str] = field(default_factory=set)
    main_name_valid: bool = True
    system_exit_names: set[str] = field(default_factory=lambda: {"SystemExit"})
    system_exit_modules: set[str] = field(default_factory=set)

    def invalidate(self, name: str) -> None:
        if name == "__name__":
            self.main_name_valid = False
        for names in self._binding_sets():
            names.discard(name)
        self.invalidate_contract_owner_alias(name)

    def invalidate_preserving_contract_owner_alias(self, name: str) -> None:
        owners = set(self.contract_owner_aliases.get(name, set()))
        self.invalidate(name)
        if owners:
            self.contract_owner_aliases[name] = owners

    def clear_proofs(self) -> None:
        for names in self._binding_sets():
            names.clear()
        self.contract_owner_aliases.clear()
        self.main_name_valid = False

    def clear_entrypoints(self) -> None:
        self.functions.clear()
        self.dynamic_functions.clear()

    def proof_names(self) -> set[str]:
        return set().union(*self._framework_binding_sets())

    def bind_contract_owner_alias(self, name: str, owners: set[str]) -> None:
        self.invalidate_contract_owner_alias(name)
        if owners:
            self.contract_owner_aliases[name] = set(owners)

    def invalidate_contract_owner_alias(self, name: str) -> None:
        self.contract_owner_aliases.pop(name, None)
        for alias, owners in tuple(self.contract_owner_aliases.items()):
            if name in owners:
                self.contract_owner_aliases.pop(alias, None)

    def clear_contract_owner_aliases(self) -> None:
        self.contract_owner_aliases.clear()

    def safe_call_names(self) -> set[str]:
        return {
            *self.typer_constructors,
            *(f"{name}.Typer" for name in self.typer_modules),
            *(
                f"{name}.{method}"
                for name in self.typer_apps
                for method in ("callback", "command")
            ),
        }

    def safe_system_exit_names(self) -> set[str]:
        return {
            *self.system_exit_names,
            *(f"{name}.SystemExit" for name in self.system_exit_modules),
        }

    def _framework_binding_sets(self) -> tuple[set[str], ...]:
        return (
            self.typer_modules,
            self.typer_constructors,
            self.typer_apps,
            self.typing_modules,
            self.protocol_aliases,
            self.pydantic_modules,
            self.pydantic_bases,
            self.pydantic_classes,
            self.builtin_names,
            self.identity_method_decorators,
            self.builtin_decorator_modules,
            self.property_decorators,
            self.property_decorator_modules,
            self.abc_decorator_modules,
        )

    def _binding_sets(self) -> tuple[set[str], ...]:
        return (
            *self._framework_binding_sets(),
            self.functions,
            self.dynamic_functions,
            self.system_exit_names,
            self.system_exit_modules,
        )


__all__: list[str] = []
