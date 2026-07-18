"""Snapshot-bound Python caller analysis for new public Lean Code symbols."""

from __future__ import annotations

import ast
from collections.abc import Sequence
from pathlib import Path

from ai_sdlc.core.lean_code_call_finder import (
    _enclosing_target_instances,
    _function_calls_target,
)
from ai_sdlc.core.lean_code_classification import classify_file
from ai_sdlc.core.lean_code_flow import (
    ReferenceState,
    _merge_reference_states,
    _pattern_bound_names,
    _references_target_class,
)
from ai_sdlc.core.lean_code_models import (
    FileClassification,
    FileMetric,
    MetricCapability,
)
from ai_sdlc.core.lean_code_scope import (
    _bound_names,
    _enclosing_class,
    _scope_imports,
    _without_local_roots,
)
from ai_sdlc.core.source_snapshot import SourceSnapshot
from ai_sdlc.core.source_snapshot_view import python_sources

_ModuleTarget = tuple[str, str, str, str]
_ExceptionStateStacks = tuple[list[ReferenceState], ...]


def attach_python_callers(
    root: Path, snapshot: SourceSnapshot, files: list[FileMetric]
) -> None:
    """Count only symbol-resolved callers from the frozen snapshot after-view."""

    targets = {
        (file.path, function.symbol): function
        for file in files
        if file.capability == MetricCapability.EXACT
        for function in file.functions
        if function.public and function.is_new
    }
    if not targets:
        return
    callers: dict[tuple[str, str], set[str]] = {target: set() for target in targets}
    for path, payload in python_sources(root, snapshot).items():
        if (
            classify_file(path, payload, False)
            != FileClassification.HANDWRITTEN_PRODUCT
        ):
            continue
        try:
            tree = ast.parse(payload.decode("utf-8", errors="strict"), filename=path)
        except (SyntaxError, UnicodeDecodeError):
            continue
        for target in targets:
            _collect_target_callers(path, tree, target, callers[target])
    for target, function in targets.items():
        function.caller_count = len(callers[target])


def _collect_target_callers(
    path: str,
    tree: ast.Module,
    target: tuple[str, str],
    callers: set[str],
) -> None:
    target_path, target_symbol = target
    target_class, _, target_name = target_symbol.rpartition(".")
    direct_names, module_names, class_names = _target_module_names(
        tree.body, path, target_path, target_class, target_name
    )
    parents = {
        child: parent
        for parent in ast.walk(tree)
        for child in ast.iter_child_nodes(parent)
    }
    # 嵌套函数作为独立 caller 评估，避免把同一次调用重复计入外层函数。
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        owner_class = _enclosing_class(node, parents)
        inherited_instances = _enclosing_target_instances(
            node, parents, class_names, target_class
        )
        local_imports = {
            id(import_node): _target_import_names(
                [import_node], path, target_path, target_class, target_name
            )
            for import_node in _scope_imports(node)
        }
        caller = f"{path}:{node.name}:{node.lineno}"
        if _function_calls_target(
            node,
            direct_names,
            module_names,
            class_names,
            target_name,
            owner_class == target_class and bool(target_class),
            inherited_instances,
            local_imports,
        ) and not _is_target_definition(
            path, target_path, node.name, target_name, owner_class, target_class
        ):
            callers.add(caller)


def _target_module_names(
    nodes: Sequence[ast.stmt],
    caller_path: str,
    target_path: str,
    target_class: str,
    target_name: str,
) -> tuple[set[str], set[str], set[str]]:
    """解析模块最终绑定；函数嵌套作用域由独立 finder 负责。"""
    state = _resolve_module_statements(
        nodes,
        (caller_path, target_path, target_class, target_name),
        (set(), set(), set(), set()),
    )
    return state[0], state[1], state[2]


def _resolve_module_statements(
    nodes: Sequence[ast.stmt],
    target: _ModuleTarget,
    state: ReferenceState,
    exception_stacks: _ExceptionStateStacks = (),
) -> ReferenceState:
    for node in nodes:
        for states in exception_stacks:
            states.append(state)
        state = _resolve_module_statement(node, target, state, exception_stacks)
    return state


def _resolve_module_statement(
    node: ast.stmt,
    target: _ModuleTarget,
    state: ReferenceState,
    exception_stacks: _ExceptionStateStacks,
) -> ReferenceState:
    if isinstance(node, ast.If):
        return _merge_reference_states(
            _resolve_module_statements(node.body, target, state, exception_stacks),
            _resolve_module_statements(node.orelse, target, state, exception_stacks),
        )
    if isinstance(node, (ast.Try, ast.TryStar)):
        return _resolve_module_try(node, target, state, exception_stacks)
    if isinstance(node, ast.Match):
        cases = [
            _resolve_module_statements(
                case.body,
                target,
                _discard_module_names(state, _pattern_bound_names(case.pattern)),
                exception_stacks,
            )
            for case in node.cases
        ]
        return _merge_reference_states(state, *cases)
    if isinstance(node, (ast.For, ast.AsyncFor)):
        iteration = _discard_module_names(state, _bound_names(node.target))
        return _merge_reference_states(
            _resolve_module_statements(node.orelse, target, state, exception_stacks),
            _resolve_module_statements(
                [*node.body, *node.orelse], target, iteration, exception_stacks
            ),
        )
    if isinstance(node, ast.While):
        return _merge_reference_states(
            _resolve_module_statements(node.orelse, target, state, exception_stacks),
            _resolve_module_statements(
                [*node.body, *node.orelse], target, state, exception_stacks
            ),
        )
    return _apply_module_statement(node, target, state)


def _resolve_module_try(
    node: ast.Try | ast.TryStar,
    target: _ModuleTarget,
    state: ReferenceState,
    exception_stacks: _ExceptionStateStacks,
) -> ReferenceState:
    prefixes = [state]
    current = _resolve_module_statements(
        node.body, target, state, (*exception_stacks, prefixes)
    )
    success = _resolve_module_statements(node.orelse, target, current, exception_stacks)
    handler_entry = _merge_reference_states(*prefixes)
    handlers = []
    for handler in node.handlers:
        entry = (
            _discard_module_names(handler_entry, {handler.name})
            if handler.name
            else handler_entry
        )
        handlers.append(
            _resolve_module_statements(handler.body, target, entry, exception_stacks)
        )
    merged = _merge_reference_states(success, *handlers)
    return _resolve_module_statements(node.finalbody, target, merged, exception_stacks)


def _apply_module_statement(
    node: ast.stmt,
    target: _ModuleTarget,
    state: ReferenceState,
) -> ReferenceState:
    caller_path, target_path, target_class, target_name = target
    direct, modules, classes = (set(state[0]), set(state[1]), set(state[2]))
    bound = _module_bound_names(node)
    value = node.value if isinstance(node, (ast.Assign, ast.AnnAssign)) else None
    class_alias = value is not None and _references_target_class(value, classes)
    direct.difference_update(bound)
    modules = _without_local_roots(modules, bound)
    classes = _without_local_roots(classes, bound)
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        additions = _target_import_names(
            [node], caller_path, target_path, target_class, target_name
        )
        direct.update(additions[0])
        modules.update(additions[1])
        classes.update(additions[2])
    elif class_alias:
        classes.update(bound)
    elif caller_path == target_path:
        _add_target_definition(node, direct, classes, target_class, target_name)
    return direct, modules, classes, set()


def _discard_module_names(state: ReferenceState, names: set[str]) -> ReferenceState:
    return (
        state[0] - names,
        _without_local_roots(state[1], names),
        _without_local_roots(state[2], names),
        set(),
    )


def _module_bound_names(node: ast.stmt) -> set[str]:
    if isinstance(node, ast.Import):
        return {alias.asname or alias.name.split(".", 1)[0] for alias in node.names}
    if isinstance(node, ast.ImportFrom):
        return {alias.asname or alias.name for alias in node.names if alias.name != "*"}
    if isinstance(node, ast.Assign):
        return {name for target in node.targets for name in _bound_names(target)}
    if isinstance(node, ast.AnnAssign):
        return _bound_names(node.target) if node.value is not None else set()
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return {node.name}
    if isinstance(node, ast.Delete):
        return set().union(*(_bound_names(target) for target in node.targets))
    return set()


def _add_target_definition(
    node: ast.stmt,
    direct: set[str],
    classes: set[str],
    target_class: str,
    target_name: str,
) -> None:
    if target_class and isinstance(node, ast.ClassDef) and node.name == target_class:
        classes.add(node.name)
    if (
        not target_class
        and isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == target_name
    ):
        direct.add(node.name)


def _target_import_names(
    nodes: Sequence[ast.stmt],
    caller_path: str,
    target_path: str,
    target_class: str,
    target_name: str,
) -> tuple[set[str], set[str], set[str]]:
    modules_for_target = _module_names(target_path)
    direct: set[str] = set()
    modules: set[str] = set()
    classes: set[str] = set()
    for node in nodes:
        if isinstance(node, ast.ImportFrom):
            imported_from = _import_from_modules(caller_path, node)
            if imported_from & modules_for_target:
                direct.update(
                    alias.asname or alias.name
                    for alias in node.names
                    if not target_class and alias.name == target_name
                )
                classes.update(
                    alias.asname or alias.name
                    for alias in node.names
                    if target_class and alias.name == target_class
                )
            modules.update(
                alias.asname or alias.name
                for alias in node.names
                if any(
                    f"{module}.{alias.name}" in modules_for_target
                    for module in imported_from
                )
            )
        elif isinstance(node, ast.Import):
            modules.update(
                alias.asname or alias.name
                for alias in node.names
                if alias.name in modules_for_target
            )
    if target_class:
        classes.update(f"{module}.{target_class}" for module in modules)
    call_modules = modules if not target_class else set()
    return direct, call_modules, classes


def _import_from_modules(path: str, node: ast.ImportFrom) -> set[str]:
    if node.level == 0:
        return {node.module or ""}
    resolved: set[str] = set()
    for caller_module in _module_names(path):
        package = caller_module.split(".")
        if not path.replace("\\", "/").endswith("/__init__.py"):
            package = package[:-1]
        keep = max(0, len(package) - node.level + 1)
        parts = [*package[:keep]]
        if node.module:
            parts.extend(node.module.split("."))
        resolved.add(".".join(parts))
    return resolved


def _module_names(path: str) -> set[str]:
    normalized = path.replace("\\", "/")
    if normalized.endswith("/__init__.py"):
        normalized = normalized[: -len("/__init__.py")]
    elif normalized.endswith(".py"):
        normalized = normalized[:-3]
    module = normalized.strip("/").replace("/", ".")
    names = {module}
    if module.startswith("src."):
        names.add(module.removeprefix("src."))
    return names


def _is_target_definition(
    path: str,
    target_path: str,
    node_name: str,
    target_name: str,
    owner_class: str,
    target_class: str,
) -> bool:
    return (
        path == target_path and node_name == target_name and owner_class == target_class
    )


__all__ = ["attach_python_callers"]
