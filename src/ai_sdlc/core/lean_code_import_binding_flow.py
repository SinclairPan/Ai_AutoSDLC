"""追踪模块导入绑定、动态别名与定义期不确定性。"""

from __future__ import annotations

import ast
from collections.abc import Collection

from ai_sdlc.core.lean_code_context_manager_metadata import (
    _CONTEXT_MANAGER_UNCERTAIN_ATTRIBUTE,
)
from ai_sdlc.core.lean_code_import_class_aliases import _dynamic_class_callable
from ai_sdlc.core.lean_code_import_mutation import _lineage_mutation_names
from ai_sdlc.core.lean_code_import_uncertainty import (
    _StoreNameFinder,
    _UncertainBindingFinder,
)
from ai_sdlc.core.lean_code_scope import _bound_names, _local_bindings

_DYNAMIC_CALLABLE_NAMES = frozenset(
    {
        "import_module",
        "__import__",
        "load_module",
        "exec_module",
        "eval",
        "exec",
        "globals",
        "locals",
    }
)
_DYNAMIC_EXTRACTOR_NAMES = frozenset({"getattr", "vars", "__getattribute__"})
_UNKNOWN_CALLABLE_VALUE = "__ai_sdlc_unknown_unpack_value__"


def _assigned_dynamic_aliases(
    statement: ast.stmt,
    module_aliases: set[str],
    callable_aliases: set[str],
) -> tuple[set[str], set[str]]:
    if isinstance(statement, ast.Assign):
        targets = statement.targets
        value = statement.value
    elif isinstance(statement, ast.AnnAssign) and statement.value is not None:
        targets = [statement.target]
        value = statement.value
    else:
        return set(), set()
    modules: set[str] = set()
    callables: set[str] = set()
    for target in targets:
        assigned_modules, assigned_callables = _dynamic_alias_bindings(
            target,
            value,
            module_aliases,
            callable_aliases,
        )
        modules.update(assigned_modules)
        callables.update(assigned_callables)
    return modules, callables


def _dynamic_alias_bindings(
    target: ast.expr,
    value: ast.expr,
    module_aliases: Collection[str],
    callable_aliases: Collection[str],
) -> tuple[set[str], set[str]]:
    if (
        isinstance(target, (ast.Tuple, ast.List))
        and isinstance(value, (ast.Tuple, ast.List))
        and len(target.elts) == len(value.elts)
        and not any(isinstance(item, ast.Starred) for item in target.elts)
    ):
        modules: set[str] = set()
        callables: set[str] = set()
        for child_target, child_value in zip(target.elts, value.elts, strict=True):
            child_modules, child_callables = _dynamic_alias_bindings(
                child_target,
                child_value,
                module_aliases,
                callable_aliases,
            )
            modules.update(child_modules)
            callables.update(child_callables)
        return modules, callables
    names = _bound_names(target)
    kind = _dynamic_expression_kind(value, module_aliases, callable_aliases)
    if kind == "module":
        return names, set()
    if kind == "callable":
        return set(), names
    # 无法精确求值的包装、星号或不等长解构必须传播已知动态来源。
    contained = _contained_dynamic_kinds(value, module_aliases, callable_aliases)
    return (
        names if "module" in contained else set(),
        names if "callable" in contained else set(),
    )


def _contained_dynamic_kinds(
    value: ast.expr,
    module_aliases: Collection[str],
    callable_aliases: Collection[str],
) -> set[str]:
    direct = _dynamic_expression_kind(value, module_aliases, callable_aliases)
    if direct:
        return {direct}
    kinds: set[str] = set()
    for child in _dynamic_value_children(value):
        kinds.update(
            _contained_dynamic_kinds(
                child,
                module_aliases,
                callable_aliases,
            )
        )
    return kinds


def _dynamic_value_children(value: ast.expr) -> tuple[ast.expr, ...]:
    if isinstance(value, ast.Lambda):
        return (
            *value.args.defaults,
            *(item for item in value.args.kw_defaults if item is not None),
            value.body,
        )
    if isinstance(value, ast.GeneratorExp):
        return (
            value.elt,
            *(
                expression
                for generator in value.generators
                for expression in (generator.iter, *generator.ifs)
            ),
        )
    if isinstance(value, (ast.ListComp, ast.SetComp)):
        terminal: tuple[ast.expr, ...] = (value.elt,)
    elif isinstance(value, ast.DictComp):
        terminal = (value.key, value.value)
    else:
        return tuple(
            child
            for child in ast.iter_child_nodes(value)
            if isinstance(child, ast.expr)
        )
    return (
        *terminal,
        *(
            expression
            for generator in value.generators
            for expression in (generator.iter, *generator.ifs)
        ),
    )


def _default_dynamic_aliases(
    arguments: ast.arguments,
    module_aliases: Collection[str],
    callable_aliases: Collection[str],
) -> tuple[set[str], set[str]]:
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
    modules: set[str] = set()
    callables: set[str] = set()
    for argument, default in pairs:
        target = ast.Name(id=argument.arg, ctx=ast.Store())
        assigned_modules, assigned_callables = _dynamic_alias_bindings(
            target,
            default,
            module_aliases,
            callable_aliases,
        )
        modules.update(assigned_modules)
        callables.update(assigned_callables)
    return modules, callables


def _dynamic_expression_kind(
    value: ast.expr,
    module_aliases: Collection[str],
    callable_aliases: Collection[str],
) -> str:
    if isinstance(value, ast.Name):
        if value.id == _UNKNOWN_CALLABLE_VALUE:
            return "callable"
        if value.id in module_aliases:
            return "module"
        if value.id in {
            *_DYNAMIC_CALLABLE_NAMES,
            *callable_aliases,
        }:
            return "callable"
        return ""
    if _dynamic_class_callable(value, callable_aliases):
        return "callable"
    if _dynamic_module_attribute(value, module_aliases):
        return "callable"
    if _runtime_module_loader(value):
        return "callable"
    if (
        isinstance(value, ast.Call)
        and _dynamic_expression_kind(
            value.func,
            module_aliases,
            callable_aliases,
        )
        == "callable"
    ):
        return "callable"
    if (
        isinstance(value, ast.Call)
        and isinstance(value.func, ast.Name)
        and value.func.id == "getattr"
        and len(value.args) >= 2
        and isinstance(value.args[0], ast.Name)
        and value.args[0].id in module_aliases
        and isinstance(value.args[1], ast.Constant)
        and value.args[1].value in _DYNAMIC_CALLABLE_NAMES
    ):
        return "callable"
    if _dynamic_module_dict_access(value, module_aliases):
        return "callable"
    if _contains_dynamic_extraction(value, module_aliases):
        return "callable"
    return ""


def _dynamic_module_attribute(
    value: ast.expr,
    module_aliases: Collection[str],
) -> bool:
    return (
        isinstance(value, ast.Attribute)
        and isinstance(value.value, ast.Name)
        and value.value.id in module_aliases
        and value.attr in _DYNAMIC_CALLABLE_NAMES
    )


def _dynamic_module_dict_access(
    value: ast.expr,
    module_aliases: Collection[str],
) -> bool:
    if not isinstance(value, ast.Subscript):
        return False
    direct_module = (
        isinstance(value.value, ast.Name) and value.value.id in module_aliases
    )
    module_dict = (
        isinstance(value.value, ast.Attribute)
        and isinstance(value.value.value, ast.Name)
        and value.value.value.id in module_aliases
        and value.value.attr == "__dict__"
    )
    if not (direct_module or module_dict):
        return False
    # 只有已证明为普通名称的字面量键才能排除动态入口抽取。
    return not (
        isinstance(value.slice, ast.Constant)
        and isinstance(value.slice.value, str)
        and value.slice.value not in _DYNAMIC_CALLABLE_NAMES
    )


def _runtime_module_loader(value: ast.expr) -> bool:
    if not (
        isinstance(value, ast.Attribute)
        and value.attr in _DYNAMIC_CALLABLE_NAMES
        and isinstance(value.value, ast.Subscript)
        and isinstance(value.value.value, ast.Attribute)
        and value.value.value.attr == "modules"
    ):
        return False
    key = value.value.slice
    return isinstance(key, ast.Constant) and key.value in {"builtins", "importlib"}


def _contains_dynamic_extraction(
    value: ast.expr,
    module_aliases: Collection[str],
) -> bool:
    nodes = tuple(ast.walk(value))
    if not any(
        isinstance(node, ast.Name) and node.id in module_aliases for node in nodes
    ):
        return False
    if any(
        isinstance(node, ast.Constant) and node.value in _DYNAMIC_CALLABLE_NAMES
        for node in nodes
    ):
        return True
    if any(
        isinstance(node, ast.Call) and _is_dynamic_extractor_call(node)
        for node in nodes
    ):
        return True
    # 未知包装器可以从动态模块返回任意可调用入口，静态分析不得降级为 module。
    return any(isinstance(node, ast.Call) for node in nodes)


def _is_dynamic_extractor_call(node: ast.Call) -> bool:
    if isinstance(node.func, ast.Name):
        return node.func.id in _DYNAMIC_EXTRACTOR_NAMES
    return (
        isinstance(node.func, ast.Attribute)
        and node.func.attr in _DYNAMIC_EXTRACTOR_NAMES
    )


def _module_rebound_names(statement: ast.stmt) -> set[str]:
    if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return {statement.name}
    finder = _StoreNameFinder()
    finder.visit(statement)
    return finder.names


def _nested_binding_uncertainty(
    statement: ast.stmt,
    module_aliases: Collection[str] = frozenset(),
    callable_aliases: Collection[str] = frozenset(),
) -> tuple[set[str], bool]:
    finder = _UncertainBindingFinder()
    finder.visit(statement)
    lineage_mutations = _dynamic_lineage_mutation_names(
        statement,
        module_aliases,
        callable_aliases,
    )
    if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
        lineage_mutations.difference_update(_local_bindings(statement))
    protocol_uncertain = (
        isinstance(statement, (ast.With, ast.AsyncWith))
        and getattr(statement, _CONTEXT_MANAGER_UNCERTAIN_ATTRIBUTE, False)
    )
    return finder.names | lineage_mutations, finder.unbounded or protocol_uncertain


def _dynamic_lineage_mutation_names(
    statement: ast.AST,
    module_aliases: Collection[str],
    callable_aliases: Collection[str],
) -> set[str]:
    """返回可能接收动态加载能力的模块或容器根名称。"""

    return _lineage_mutation_names(
        statement,
        module_aliases,
        callable_aliases,
        _contained_dynamic_kinds,
    )


def _dynamic_dependency_call(
    node: ast.Call,
    module_aliases: Collection[str] = frozenset(),
    callable_aliases: Collection[str] = frozenset(),
) -> bool:
    return "callable" in _contained_dynamic_kinds(
        node.func,
        module_aliases,
        callable_aliases,
    )


__all__: list[str] = []
