"""维护生成器消费分析所需的内建调用身份。"""

from __future__ import annotations

import ast

from ai_sdlc.core.lean_code_generator_state import _bind_names, _ConsumerState

_CONSUMERS = frozenset(
    {
        "all",
        "anext",
        "any",
        "dict",
        "frozenset",
        "list",
        "max",
        "min",
        "next",
        "set",
        "sorted",
        "sum",
        "tuple",
    }
)


def _apply_generator_import(node: ast.Import, state: _ConsumerState) -> None:
    for alias in node.names:
        name = alias.asname or alias.name.split(".", 1)[0]
        _bind_names({name}, "unknown", state)
        if alias.name == "builtins":
            state.builtin_modules.add(name)


def _apply_generator_import_from(
    node: ast.ImportFrom,
    state: _ConsumerState,
) -> None:
    for alias in node.names:
        if alias.name == "*":
            continue
        name = alias.asname or alias.name
        identity = (
            _builtin_consumer_identity(alias.name)
            if node.module == "builtins" and alias.name in _CONSUMERS
            else "unknown"
        )
        _bind_names({name}, identity, state)


def _builtin_consumer_identity(name: str) -> str:
    if name in {"next", "anext"}:
        return "consume-one"
    if name in {"all", "any"}:
        return "consume-unknown"
    return "consume-all" if name in _CONSUMERS else "unknown"


def _is_builtin_consumer(name: str) -> bool:
    return name in _CONSUMERS


__all__: list[str] = []
