"""验证 framework callable 的顺序绑定与 ``__main__`` 可达性。"""

from __future__ import annotations

import ast

from ai_sdlc.core.lean_code_framework_effects import (
    _call_name,
    _contains_dynamic_rebinding,
    _TopLevelStoreFinder,
)

_CONDITIONAL_STATEMENTS = (
    ast.If,
    ast.For,
    ast.AsyncFor,
    ast.While,
    ast.Try,
    ast.TryStar,
    ast.With,
    ast.AsyncWith,
    ast.Match,
    ast.Assert,
    ast.FunctionDef,
    ast.AsyncFunctionDef,
    ast.ClassDef,
)


def _main_guard_contract_names(
    statements: list[ast.stmt],
    function_names: set[str],
    dynamic_function_names: set[str],
    system_exit_names: set[str],
) -> set[str]:
    active = set(function_names)
    accepted: set[str] = set()
    for statement in statements:
        if _main_guard_statement_stops(
            statement,
            active,
            accepted,
            dynamic_function_names,
            system_exit_names,
        ):
            break
    return accepted


def _main_guard_statement_stops(
    statement: ast.stmt,
    active: set[str],
    accepted: set[str],
    dynamic_function_names: set[str],
    system_exit_names: set[str],
) -> bool:
    if isinstance(statement, ast.Raise):
        _record_raise_entrypoints(
            statement,
            active,
            accepted,
            dynamic_function_names,
            system_exit_names,
        )
        return True
    if isinstance(statement, (ast.Return, ast.Break, ast.Continue)):
        return True
    if _contains_dynamic_rebinding(statement):
        active.clear()
        return False
    if isinstance(statement, _CONDITIONAL_STATEMENTS):
        if _conditional_effect_invalidates(
            statement,
            active,
            dynamic_function_names,
        ):
            active.clear()
        else:
            _discard_rebound_names(active, statement)
        return False
    _record_direct_entrypoints(statement, active, accepted, dynamic_function_names)
    return False


def _record_raise_entrypoints(
    statement: ast.Raise,
    active: set[str],
    accepted: set[str],
    dynamic_function_names: set[str],
    system_exit_names: set[str],
) -> None:
    effects = _MayExecuteEffectFinder(system_exit_names)
    effects.visit(statement)
    if _call_effect_invalidates(
        effects,
        active,
        dynamic_function_names,
        system_exit_names,
    ):
        active.clear()
        return
    finder = _UnconditionalCallFinder(system_exit_names)
    finder.visit(statement)
    accepted.update(finder.names & active)


def _record_direct_entrypoints(
    statement: ast.stmt,
    active: set[str],
    accepted: set[str],
    dynamic_function_names: set[str],
) -> None:
    effects = _MayExecuteEffectFinder()
    effects.visit(statement)
    if _call_effect_invalidates(effects, active, dynamic_function_names):
        active.clear()
        return
    finder = _UnconditionalCallFinder()
    finder.visit(statement)
    if _call_effect_invalidates(finder, active, dynamic_function_names):
        active.clear()
        return
    accepted.update(finder.names & active)
    _discard_rebound_names(active, statement)


def _conditional_effect_invalidates(
    statement: ast.stmt,
    active: set[str],
    dynamic_function_names: set[str],
) -> bool:
    if isinstance(statement, ast.If) and _literal_if_is_inert(statement):
        return False
    if isinstance(
        statement,
        (
            ast.If,
            ast.For,
            ast.AsyncFor,
            ast.While,
            ast.Try,
            ast.TryStar,
            ast.With,
            ast.AsyncWith,
            ast.Match,
            ast.Assert,
            ast.ClassDef,
        ),
    ):
        return True
    finder = _MayExecuteEffectFinder()
    finder.visit(statement)
    return _call_effect_invalidates(finder, active, dynamic_function_names)


def _literal_if_is_inert(statement: ast.If) -> bool:
    if not isinstance(statement.test, ast.Constant) or not isinstance(
        statement.test.value, bool
    ):
        return False
    selected = statement.body if statement.test.value else statement.orelse
    return all(isinstance(child, ast.Pass) for child in selected)


def _call_effect_invalidates(
    finder: _UnconditionalCallFinder,
    active: set[str],
    dynamic_function_names: set[str],
    safe_call_names: set[str] | frozenset[str] = frozenset(),
) -> bool:
    return bool(
        finder.unresolved
        or finder.names - active - safe_call_names
        or finder.names & dynamic_function_names
    )


def _discard_rebound_names(active: set[str], statement: ast.stmt) -> None:
    rebound = _TopLevelStoreFinder()
    rebound.visit(statement)
    indirect_store = any(
        isinstance(node, (ast.Attribute, ast.Subscript))
        and isinstance(node.ctx, (ast.Store, ast.Del))
        for node in ast.walk(statement)
    )
    if rebound.unresolved or indirect_store:
        active.clear()
    else:
        active.difference_update(rebound.names)


class _UnconditionalCallFinder(ast.NodeVisitor):
    def __init__(self, safe_qualified_names: set[str] | None = None) -> None:
        self.names: set[str] = set()
        self.unresolved = False
        self.safe_qualified_names = safe_qualified_names or set()

    def visit_Call(self, node: ast.Call) -> None:
        name = _call_name(node.func)
        if name:
            self.names.add(name)
        else:
            # 属性、下标或调用结果都可能在目标入口执行前重绑定模块状态。
            self.unresolved = True
        self.generic_visit(node)

    def visit_BoolOp(self, node: ast.BoolOp) -> None:
        return

    def visit_IfExp(self, node: ast.IfExp) -> None:
        return

    def visit_Lambda(self, node: ast.Lambda) -> None:
        self._visit_defaults(node.args)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function_header(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function_header(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        for decorator in node.decorator_list:
            self.visit(decorator)
        for base in node.bases:
            self.visit(base)
        for keyword in node.keywords:
            self.visit(keyword.value)
        for type_param in getattr(node, "type_params", ()):
            self.visit(type_param)

    def _visit_function_header(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
    ) -> None:
        for decorator in node.decorator_list:
            self.visit(decorator)
        self._visit_defaults(node.args)
        for argument in (
            *node.args.posonlyargs,
            *node.args.args,
            *node.args.kwonlyargs,
        ):
            if argument.annotation is not None:
                self.visit(argument.annotation)
        if node.args.vararg and node.args.vararg.annotation is not None:
            self.visit(node.args.vararg.annotation)
        if node.args.kwarg and node.args.kwarg.annotation is not None:
            self.visit(node.args.kwarg.annotation)
        if node.returns is not None:
            self.visit(node.returns)
        for type_param in getattr(node, "type_params", ()):
            self.visit(type_param)

    def _visit_defaults(self, arguments: ast.arguments) -> None:
        for default in (*arguments.defaults, *arguments.kw_defaults):
            if default is not None:
                self.visit(default)

    def visit_ListComp(self, node: ast.ListComp) -> None:
        return

    def visit_SetComp(self, node: ast.SetComp) -> None:
        return

    def visit_DictComp(self, node: ast.DictComp) -> None:
        return

    def visit_GeneratorExp(self, node: ast.GeneratorExp) -> None:
        return


class _MayExecuteEffectFinder(_UnconditionalCallFinder):
    def visit_BoolOp(self, node: ast.BoolOp) -> None:
        self.unresolved = True

    def visit_IfExp(self, node: ast.IfExp) -> None:
        self.unresolved = True

    def visit_ListComp(self, node: ast.ListComp) -> None:
        self.unresolved = True

    def visit_SetComp(self, node: ast.SetComp) -> None:
        self.unresolved = True

    def visit_DictComp(self, node: ast.DictComp) -> None:
        self.unresolved = True

    def visit_GeneratorExp(self, node: ast.GeneratorExp) -> None:
        self.unresolved = True


__all__: list[str] = []
