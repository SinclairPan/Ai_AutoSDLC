"""按 Python 词法与定义期执行语义度量函数导入扇出。"""

from __future__ import annotations

import ast
from dataclasses import dataclass

from ai_sdlc.core.lean_code_import_binding_flow import _default_dynamic_aliases
from ai_sdlc.core.lean_code_import_class_aliases import (
    _default_dynamic_class_callables,
)
from ai_sdlc.core.lean_code_import_factories import (
    _dynamic_factory_bindings,
    _dynamic_import_aliases,
)
from ai_sdlc.core.lean_code_import_function_flow import _FunctionLoadNameFinder
from ai_sdlc.core.lean_code_import_module_bindings import (
    _has_star_import,
    _import_bindings,
    _ImportBindings,
)
from ai_sdlc.core.lean_code_metric_models import MetricCapability
from ai_sdlc.core.lean_code_scope import (
    _local_bindings,
    _scope_imports,
)


@dataclass(frozen=True)
class _FunctionMetricFlow:
    local_import_nodes: list[ast.Import | ast.ImportFrom]
    bindings: dict[str, str]
    header: _FunctionLoadNameFinder
    body: _FunctionLoadNameFinder


def _function_import_metric(
    node: ast.FunctionDef | ast.AsyncFunctionDef | None,
    module_bindings: _ImportBindings,
) -> tuple[int, MetricCapability]:
    if node is None:
        return 0, MetricCapability.EXACT
    flow = _function_metric_flow(node, module_bindings)
    uncertain = (
        module_bindings.uncertain
        or id(node) in module_bindings.historically_dynamic_function_nodes
        or bool((flow.header.names | flow.body.names) & module_bindings.uncertain_names)
        or _has_star_import(flow.local_import_nodes)
        or flow.header.uncertain
        or flow.body.uncertain
    )
    capability = MetricCapability.CONSERVATIVE if uncertain else MetricCapability.EXACT
    modules = {
        module_bindings.values[name]
        for name in flow.header.names
        if name in module_bindings.values
    }
    modules.update(
        flow.bindings[name] for name in flow.body.names if name in flow.bindings
    )
    return len(modules), capability


def _function_metric_flow(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    module_bindings: _ImportBindings,
) -> _FunctionMetricFlow:
    local_import_nodes = _scope_imports(node)
    local_imports = _import_bindings(local_import_nodes)
    local_modules, local_callables = _dynamic_import_aliases(local_import_nodes)
    local_names = _local_bindings(node)
    dynamic_modules = (module_bindings.dynamic_modules - local_names) | local_modules
    dynamic_callables = (
        module_bindings.dynamic_callables - local_names
    ) | local_callables
    default_modules, default_callables = _default_dynamic_aliases(
        node.args,
        dynamic_modules,
        dynamic_callables,
    )
    default_callables.update(
        _default_dynamic_class_callables(node.args, dynamic_callables)
    )
    local_factories = _dynamic_factory_bindings(
        ast.Module(body=node.body, type_ignores=[]),
        dynamic_modules | default_modules,
        dynamic_callables | default_callables,
    )
    bindings = {
        name: module
        for name, module in module_bindings.values.items()
        if name not in _local_bindings(node) or name in local_imports
    }
    bindings.update(local_imports)
    header_finder = _FunctionLoadNameFinder(dynamic_modules, dynamic_callables)
    header_finder.visit_function_header(node)
    body_finder = _FunctionLoadNameFinder(
        dynamic_modules | default_modules,
        dynamic_callables | default_callables,
        local_factories,
    )
    body_finder._visit_statements(node.body)
    return _FunctionMetricFlow(
        local_import_nodes,
        bindings,
        header_finder,
        body_finder,
    )


__all__: list[str] = []
