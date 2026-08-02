"""隔离函数流中的推导式词法作用域处理。"""

from __future__ import annotations

import ast

from ai_sdlc.core.lean_code_scope import _bound_names


class _FunctionComprehensionFlow:
    shadowed: list[tuple[set[str], bool]]
    comprehension_depth: int

    def visit(self, node: ast.AST) -> None:
        raise NotImplementedError

    def visit_function_header(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
    ) -> None:
        for decorator in node.decorator_list:
            self.visit(decorator)
        self._visit_defaults(node.args)
        arguments = (
            *node.args.posonlyargs,
            *node.args.args,
            *node.args.kwonlyargs,
        )
        for argument in arguments:
            if argument.annotation is not None:
                self.visit(argument.annotation)
        if node.returns is not None:
            self.visit(node.returns)
        for type_param in getattr(node, "type_params", ()):
            self.visit(type_param)

    def _visit_defaults(self, arguments: ast.arguments) -> None:
        for default in (*arguments.defaults, *arguments.kw_defaults):
            if default is not None:
                self.visit(default)

    def visit_ListComp(self, node: ast.ListComp) -> None:  # noqa: N802
        self._visit_comprehension(node.generators, node.elt)

    def visit_SetComp(self, node: ast.SetComp) -> None:  # noqa: N802
        self._visit_comprehension(node.generators, node.elt)

    def visit_GeneratorExp(self, node: ast.GeneratorExp) -> None:  # noqa: N802
        self._visit_comprehension(node.generators, node.elt)

    def visit_DictComp(self, node: ast.DictComp) -> None:  # noqa: N802
        self._visit_comprehension(node.generators, node.key, node.value)

    def _visit_comprehension(
        self,
        generators: list[ast.comprehension],
        *outputs: ast.expr,
    ) -> None:
        first, *remaining = generators
        self.visit(first.iter)
        scope: set[str] = _bound_names(first.target)
        self.shadowed.append((scope, False))
        self.comprehension_depth += 1
        try:
            self._visit_comprehension_tail(first, remaining, outputs, scope)
        finally:
            self.comprehension_depth -= 1
            self.shadowed.pop()

    def _visit_comprehension_tail(
        self,
        first: ast.comprehension,
        remaining: list[ast.comprehension],
        outputs: tuple[ast.expr, ...],
        scope: set[str],
    ) -> None:
        for condition in first.ifs:
            self.visit(condition)
        for generator in remaining:
            self.visit(generator.iter)
            scope.update(_bound_names(generator.target))
            for condition in generator.ifs:
                self.visit(condition)
        for output in outputs:
            self.visit(output)


__all__: list[str] = []
