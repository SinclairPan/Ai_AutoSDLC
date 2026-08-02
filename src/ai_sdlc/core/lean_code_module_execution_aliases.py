"""计算函数节点可见的动态导入别名。"""

from __future__ import annotations

import ast

from ai_sdlc.core.lean_code_import_binding_flow import (
    _default_dynamic_aliases,
)
from ai_sdlc.core.lean_code_import_class_aliases import (
    _default_dynamic_class_callables,
)
from ai_sdlc.core.lean_code_import_factories import _dynamic_import_aliases
from ai_sdlc.core.lean_code_scope import (
    _local_bindings,
    _scope_imports,
)

_FunctionNode = ast.FunctionDef | ast.AsyncFunctionDef


def _function_dynamic_aliases(
    function: _FunctionNode,
    module_aliases: set[str],
    callable_aliases: set[str],
) -> tuple[set[str], set[str]]:
    local_names = _local_bindings(function)
    modules = set(module_aliases) - local_names
    callables = set(callable_aliases) - local_names
    local_modules, local_callables = _dynamic_import_aliases(_scope_imports(function))
    default_modules, default_callables = _default_dynamic_aliases(
        function.args,
        module_aliases,
        callable_aliases,
    )
    default_callables.update(
        _default_dynamic_class_callables(function.args, callable_aliases)
    )
    modules.update({*local_modules, *default_modules})
    callables.update({*local_callables, *default_callables})
    return modules, callables


def _function_call_dynamic_aliases(
    function: _FunctionNode,
    module_aliases: set[str],
    callable_aliases: set[str],
) -> tuple[set[str], set[str]]:
    local_names = _local_bindings(function)
    modules = set(module_aliases) - local_names
    callables = set(callable_aliases) - local_names
    local_modules, local_callables = _dynamic_import_aliases(_scope_imports(function))
    modules.update(local_modules)
    callables.update(local_callables)
    return modules, callables


__all__: list[str] = []
