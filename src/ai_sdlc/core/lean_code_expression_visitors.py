"""表达式动态调用与定义期绑定访问器。"""

from __future__ import annotations

import ast
from collections.abc import Collection

from ai_sdlc.core.lean_code_control_flow import _static_truth, _statically_empty
from ai_sdlc.core.lean_code_import_binding_flow import _dynamic_dependency_call


class _DynamicCallFinder(ast.NodeVisitor):
    def __init__(
        self,
        modules: Collection[str],
        callables: Collection[str],
    ) -> None:
        self.modules = modules
        self.callables = callables
        self.found = False

    def visit_Call(self, node: ast.Call) -> None:
        if _dynamic_dependency_call(node, self.modules, self.callables):
            self.found = True
        if not self.found:
            self.generic_visit(node)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        for default in (*node.args.defaults, *node.args.kw_defaults):
            if default is not None:
                self.visit(default)


class _NamedExpressionFinder(ast.NodeVisitor):
    def __init__(self) -> None:
        self.bindings: list[tuple[ast.expr, ast.expr]] = []

    def visit_NamedExpr(self, node: ast.NamedExpr) -> None:
        self.visit(node.value)
        self.bindings.append((node.target, node.value))

    def visit_Lambda(self, node: ast.Lambda) -> None:
        for default in (*node.args.defaults, *node.args.kw_defaults):
            if default is not None:
                self.visit(default)

    def visit_GeneratorExp(self, node: ast.GeneratorExp) -> None:
        if node.generators:
            self.visit(node.generators[0].iter)

    def visit_ListComp(self, node: ast.ListComp) -> None:
        self._visit_comprehension(node.generators, (node.elt,))

    def visit_SetComp(self, node: ast.SetComp) -> None:
        self._visit_comprehension(node.generators, (node.elt,))

    def visit_DictComp(self, node: ast.DictComp) -> None:
        self._visit_comprehension(node.generators, (node.key, node.value))

    def _visit_comprehension(
        self,
        generators: Collection[ast.comprehension],
        terminal: Collection[ast.expr],
    ) -> None:
        for generator in generators:
            self.visit(generator.iter)
            if _statically_empty(generator.iter):
                return
            for condition in generator.ifs:
                self.visit(condition)
                if _static_truth(condition) is False:
                    return
        for expression in terminal:
            self.visit(expression)


__all__: list[str] = []
