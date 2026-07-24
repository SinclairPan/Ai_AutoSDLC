"""延迟对象身份分析的有界值域。"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field

from ai_sdlc.core.lean_code_scope import _local_bindings

_CallableNode = ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda
_TargetKey = tuple[str, str]


@dataclass(frozen=True)
class _IdentityValue:
    kind: str = ""
    callable_node: _CallableNode | None = None
    items: tuple[_IdentityValue, ...] = ()
    entries: tuple[tuple[str | int, _IdentityValue], ...] = ()
    truth: bool | None = None
    scalar: str | bytes | int | float | bool | None = None
    scalar_known: bool = False
    closure: tuple[tuple[str, _IdentityValue], ...] = ()
    closure_cells: tuple[tuple[str, str], ...] = ()
    generator_node: ast.GeneratorExp | None = None
    generator_truth: bool | None = None
    target_key: _TargetKey | None = None
    defaults: tuple[_IdentityValue, ...] = ()
    kw_defaults: tuple[_IdentityValue | None, ...] = ()
    execution_kind: str = "sync"
    module_id: str = ""
    module_entries: tuple[tuple[str, _IdentityValue], ...] = ()
    bound_arguments: tuple[_IdentityValue, ...] = ()
    bound_keywords: tuple[tuple[str, _IdentityValue], ...] = ()
    bound_method: bool = False
    may_tracked: bool = False
    alternatives: tuple[_IdentityValue, ...] = ()

    @property
    def tracked(self) -> bool:
        return self.kind in {"object", "product"} or self.may_tracked


@dataclass
class _IdentityState:
    values: dict[str, _IdentityValue] = field(default_factory=dict)
    cells: dict[str, _IdentityValue] = field(default_factory=dict)
    bindings: dict[str, str] = field(default_factory=dict)
    completion: str = "normal"
    result: _IdentityValue = field(default_factory=_IdentityValue)
    exception: str | None = None
    path: tuple[ast.stmt, ...] = ()
    frame_serial: int = 0
    module_id: str = ""
    module_entries: tuple[tuple[str, _IdentityValue], ...] = ()

    def clone(self) -> _IdentityState:
        return _IdentityState(
            values=dict(self.values),
            cells=dict(self.cells),
            bindings=dict(self.bindings),
            completion=self.completion,
            result=self.result,
            exception=self.exception,
            path=self.path,
            frame_serial=self.frame_serial,
            module_id=self.module_id,
            module_entries=self.module_entries,
        )

    def read(self, name: str) -> _IdentityValue:
        cell_id = self.bindings.get(name)
        return (
            self.cells.get(cell_id, _EMPTY_VALUE)
            if cell_id
            else self.values.get(name, _EMPTY_VALUE)
        )

    def write(self, name: str, value: _IdentityValue) -> None:
        self.values[name] = value
        cell_id = self.bindings.get(name)
        if cell_id is not None:
            self.cells[cell_id] = value

    def ensure_cell(self, name: str) -> str:
        cell_id = self.bindings.get(name)
        if cell_id is None:
            cell_id = f"{self.frame_serial}:{name}:{len(self.cells)}"
            self.bindings[name] = cell_id
            self.cells[cell_id] = self.values.get(name, _EMPTY_VALUE)
        return cell_id

    def sync_cells(self) -> None:
        for name, cell_id in self.bindings.items():
            self.values[name] = self.cells.get(cell_id, _EMPTY_VALUE)

    def resolved_values(self) -> dict[str, _IdentityValue]:
        return {name: self.read(name) for name in self.values}


_IdentityPath = tuple[
    ast.AST,
    tuple[ast.stmt, ...],
    tuple[tuple[str, _IdentityValue], ...],
]


@dataclass(frozen=True)
class _IdentityTrace:
    anchors: tuple[ast.AST, ...]
    returned: bool
    escaped: bool
    paths: tuple[_IdentityPath, ...] = ()


@dataclass(frozen=True)
class _CallableSummary:
    result: _IdentityValue = field(default_factory=_IdentityValue)
    effects: tuple[tuple[str, _IdentityValue], ...] = ()
    completion: str = "normal"
    exception: str | None = None
    escaped: bool = False
    cells: tuple[tuple[str, _IdentityValue], ...] = ()
    frame_serial: int = 0


_EMPTY_VALUE = _IdentityValue()
_UNKNOWN_VALUE = _IdentityValue("unknown")
_IMPORTLIB_INTACT_KEY = "\0importlib:intact"
_IMPORT_BINDING_PREFIX = "\0import-binding:"


def _import_binding_key(node: ast.Import | ast.ImportFrom, name: str) -> str:
    return f"{_import_binding_prefix(node)}{name}"


def _import_binding_prefix(node: ast.Import | ast.ImportFrom) -> str:
    return f"{_IMPORT_BINDING_PREFIX}{id(node)}:"


def _literal_identity(value: int) -> _IdentityValue:
    return _IdentityValue("literal", truth=bool(value), scalar=value, scalar_known=True)


def _initial_identity_state(
    scope: ast.Module | ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef,
) -> _IdentityState:
    if isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return _IdentityState({name: _EMPTY_VALUE for name in _local_bindings(scope)})
    return _IdentityState()


__all__: list[str] = []
