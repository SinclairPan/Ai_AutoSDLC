"""以顺序敏感的导入证据识别框架拥有的 Python callable。"""

from __future__ import annotations

import ast

from ai_sdlc.core.lean_code_framework_aliases import (
    _assignment_owner_bindings,
    _bind_live_contract_owner_aliases,
    _indirect_target_roots,
)
from ai_sdlc.core.lean_code_framework_effects import (
    _bound_names,
    _contains_dynamic_rebinding,
    _contains_unproved_helper_effect,
    _definition_time_effects,
    _invalidate_bound_names,
    _is_main_guard,
    _target_root_name,
    _TopLevelStoreFinder,
    _unknown_call_proof_names,
)
from ai_sdlc.core.lean_code_framework_flow import _main_guard_contract_names
from ai_sdlc.core.lean_code_framework_owner_flow import (
    _constant_print_call,
    _invalidate_contract_owners,
    _statement_owner_effects,
)
from ai_sdlc.core.lean_code_framework_resolution import (
    Contract,
    _apply_class,
    _qualified_name,
    _typer_command_contract,
)
from ai_sdlc.core.lean_code_framework_state import _BindingState


def _framework_owned_contracts(
    tree: ast.Module,
    trusted_frameworks: frozenset[str],
) -> dict[str, Contract]:
    contracts: dict[str, Contract] = {}
    state = _BindingState(trusted_frameworks)
    for statement in tree.body:
        _apply_framework_statement(statement, state, contracts)
    return contracts


def _apply_framework_statement(
    statement: ast.stmt,
    state: _BindingState,
    contracts: dict[str, Contract],
) -> None:
    _invalidate_contract_mutations(statement, contracts, state)
    if isinstance(statement, ast.Import):
        _apply_import(statement, state)
        return
    if isinstance(statement, ast.ImportFrom):
        _apply_import_from(statement, state)
        return
    if isinstance(statement, (ast.Assign, ast.AnnAssign)):
        _apply_assignment(statement, state, contracts)
        return
    if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
        _apply_function(statement, state, contracts)
        return
    if isinstance(statement, ast.ClassDef):
        _apply_class_definition(statement, state, contracts)
        return
    if (
        isinstance(statement, ast.If)
        and state.main_name_valid
        and _is_main_guard(statement.test)
    ):
        _apply_main_guard(statement, state, contracts)
        return
    _apply_unknown_statement(statement, state, contracts)


def _apply_function(
    statement: ast.FunctionDef | ast.AsyncFunctionDef,
    state: _BindingState,
    contracts: dict[str, Contract],
) -> None:
    contract = _function_contract(statement, state, contracts)
    state.invalidate(statement.name)
    if contract:
        contracts[statement.name] = contract
    state.functions.add(statement.name)
    body = ast.Module(body=statement.body, type_ignores=[])
    if _contains_dynamic_rebinding(body) or _contains_unproved_helper_effect(body):
        state.dynamic_functions.add(statement.name)


def _apply_class_definition(
    statement: ast.ClassDef,
    state: _BindingState,
    contracts: dict[str, Contract],
) -> None:
    dynamic, touched = _definition_time_effects(
        statement,
        state.proof_names(),
        state.safe_call_names(),
    )
    if dynamic:
        contracts.clear()
    _apply_proof_effects(state, dynamic, touched)
    _apply_class(statement, state, contracts)
    if any(
        symbol.startswith(f"{statement.name}.") or symbol == statement.name
        for symbol in contracts
    ):
        state.bind_contract_owner_alias(statement.name, {statement.name})


def _apply_main_guard(
    statement: ast.If,
    state: _BindingState,
    contracts: dict[str, Contract],
) -> None:
    for name in _main_guard_contract_names(
        statement.body,
        state.functions,
        state.dynamic_functions,
        state.safe_system_exit_names(),
    ):
        contracts[name] = ("guarded-main", ("guard=__main__",))
    _invalidate_bound_names(statement.orelse, state)


def _apply_unknown_statement(
    statement: ast.stmt,
    state: _BindingState,
    contracts: dict[str, Contract],
) -> None:
    owner_effects = _statement_owner_effects(
        statement,
        state,
        owners={symbol.split(".", 1)[0] for symbol in contracts},
        safe_calls=state.safe_call_names(),
    )
    owner_bindings = owner_effects.aliases
    _bind_live_contract_owner_aliases(owner_bindings, state, contracts)
    if _calls_dynamic_function(statement, state):
        state.clear_proofs()
        contracts.clear()
        return
    if _has_unknown_call(statement, state):
        state.clear_entrypoints()
    _invalidate_unknown_call_proofs(statement, state)
    _invalidate_contract_owners(owner_effects.touched_owners, contracts, state)
    _invalidate_unknown_statement_bindings(statement, state)
    _bind_live_contract_owner_aliases(owner_bindings, state, contracts)
    if _contains_dynamic_rebinding(statement):
        state.clear_proofs()
        contracts.clear()


def _apply_import(node: ast.Import, state: _BindingState) -> None:
    for alias in node.names:
        name = alias.asname or alias.name.split(".", 1)[0]
        state.invalidate(name)
        if alias.name == "typer" and "typer" in state.trusted_frameworks:
            state.typer_modules.add(name)
        elif alias.name == "builtins":
            state.builtin_decorator_modules.add(name)
            state.property_decorator_modules.add(name)
            state.system_exit_modules.add(name)
        elif alias.name == "abc":
            state.abc_decorator_modules.add(name)
        elif (
            alias.name in {"typing", "typing_extensions"}
            and alias.name in state.trusted_frameworks
        ):
            state.typing_modules.add(name)
        elif alias.name == "pydantic" and "pydantic" in state.trusted_frameworks:
            state.pydantic_modules.add(name)


def _apply_import_from(node: ast.ImportFrom, state: _BindingState) -> None:
    for alias in node.names:
        if alias.name == "*":
            state.clear_proofs()
            continue
        name = alias.asname or alias.name
        state.invalidate(name)
        if (
            node.level == 0
            and node.module == "typer"
            and "typer" in state.trusted_frameworks
            and alias.name == "Typer"
        ):
            state.typer_constructors.add(name)
        elif (
            node.level == 0
            and node.module in {"typing", "typing_extensions"}
            and node.module in state.trusted_frameworks
            and alias.name == "Protocol"
        ):
            state.protocol_aliases.add(name)
        elif (
            node.level == 0
            and node.module == "pydantic"
            and "pydantic" in state.trusted_frameworks
            and alias.name == "BaseModel"
        ):
            state.pydantic_bases.add(name)
        elif (
            node.level == 0
            and node.module == "builtins"
            and alias.name in {"property", "classmethod", "staticmethod"}
        ) or (
            node.level == 0 and node.module == "abc" and alias.name == "abstractmethod"
        ):
            state.identity_method_decorators.add(name)
            if alias.name == "property":
                state.property_decorators.add(name)
        elif (
            node.level == 0 and node.module == "builtins" and alias.name == "SystemExit"
        ):
            state.system_exit_names.add(name)


def _apply_assignment(
    node: ast.Assign | ast.AnnAssign,
    state: _BindingState,
    contracts: dict[str, Contract],
) -> None:
    value = node.value
    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
    owner_bindings = (
        _assignment_owner_bindings(targets, value, state) if value is not None else {}
    )
    if value is not None:
        if _calls_dynamic_function(value, state):
            state.clear_proofs()
            contracts.clear()
        if _has_unknown_call(value, state):
            state.clear_entrypoints()
        _invalidate_unknown_call_proofs(value, state)
        _invalidate_contract_call_effects(
            value,
            contracts,
            state,
        )
    for target in targets:
        root = _target_root_name(target)
        if root and not isinstance(target, ast.Name):
            state.invalidate_preserving_contract_owner_alias(root)
        if isinstance(target, ast.Subscript) and root in {"globals", "locals"}:
            state.clear_proofs()
    if value is not None and _contains_dynamic_rebinding(value):
        state.clear_proofs()
        contracts.clear()
    names = set().union(*(_bound_names(target) for target in targets))
    is_typer_app = isinstance(value, ast.Call) and _qualified_name(value.func) in {
        *state.typer_constructors,
        *(f"{name}.Typer" for name in state.typer_modules),
    }
    for name in names:
        state.invalidate(name)
        if is_typer_app:
            state.typer_apps.add(name)
        owners = owner_bindings.get(name, set())
        if owners and any(
            symbol == owner or symbol.startswith(f"{owner}.")
            for owner in owners
            for symbol in contracts
        ):
            state.bind_contract_owner_alias(name, owners)


def _invalidate_unknown_statement_bindings(
    statement: ast.stmt,
    state: _BindingState,
) -> None:
    preserved = _indirect_target_roots(statement)
    aliases = {
        name: set(state.contract_owner_aliases.get(name, set()))
        for name in preserved
    }
    _invalidate_bound_names((statement,), state)
    for name, owners in aliases.items():
        if owners:
            state.contract_owner_aliases[name] = owners


def _function_contract(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    state: _BindingState,
    contracts: dict[str, Contract],
) -> Contract | None:
    dynamic, touched = _definition_time_effects(
        node,
        state.proof_names(),
        state.safe_call_names(),
    )
    if dynamic:
        contracts.clear()
    _apply_proof_effects(state, dynamic, touched)
    return _typer_command_contract(node, state.typer_apps)


def _invalidate_contract_mutations(
    statement: ast.stmt,
    contracts: dict[str, Contract],
    state: _BindingState,
) -> None:
    finder = _TopLevelStoreFinder()
    finder.visit(statement)
    if finder.unresolved:
        contracts.clear()
        state.clear_contract_owner_aliases()
        return
    for owner in finder.names:
        removed = False
        for symbol in tuple(contracts):
            if symbol == owner or symbol.startswith(f"{owner}."):
                contracts.pop(symbol, None)
                removed = True
        if removed:
            state.invalidate_contract_owner_alias(owner)


def _invalidate_contract_call_effects(
    node: ast.AST,
    contracts: dict[str, Contract],
    state: _BindingState,
) -> None:
    owners = {symbol.split(".", 1)[0] for symbol in contracts}
    touched: set[str] = set()
    for call in (child for child in ast.walk(node) if isinstance(child, ast.Call)):
        call_name = _qualified_name(call.func)
        if call_name in state.safe_call_names() or _constant_print_call(call):
            continue
        names = {child.id for child in ast.walk(call) if isinstance(child, ast.Name)}
        direct = names & owners
        aliased = set().union(
            *(state.contract_owner_aliases.get(name, set()) for name in names)
        )
        if not direct and not aliased:
            continue
        touched.update(direct | aliased)
    for owner in touched:
        _invalidate_contract_owners((owner,), contracts, state)


def _apply_proof_effects(
    state: _BindingState,
    dynamic: bool,
    touched: set[str],
) -> None:
    if dynamic:
        state.clear_proofs()
        return
    for name in touched:
        state.invalidate(name)


def _invalidate_unknown_call_proofs(
    node: ast.AST,
    state: _BindingState,
) -> None:
    for name in _unknown_call_proof_names(
        node,
        state.proof_names(),
        state.safe_call_names(),
    ):
        state.invalidate(name)


def _has_unknown_call(node: ast.AST, state: _BindingState) -> bool:
    return any(
        isinstance(child, ast.Call)
        and _qualified_name(child.func) not in state.safe_call_names()
        for child in ast.walk(node)
    )


def _calls_dynamic_function(node: ast.AST, state: _BindingState) -> bool:
    return any(
        isinstance(child, ast.Call)
        and isinstance(child.func, ast.Name)
        and child.func.id in state.dynamic_functions
        for child in ast.walk(node)
    )


__all__: list[str] = []
