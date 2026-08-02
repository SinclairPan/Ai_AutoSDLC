"""证明用户函数不会消费或逃逸传入参数。"""

from __future__ import annotations

import ast


def _proven_non_consumer(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> bool:
    finder = _ParameterLoadFinder(_parameter_names(node.args))
    for statement in node.body:
        finder.visit(statement)
    return not finder.found


def _lambda_ignores_arguments(node: ast.Lambda) -> bool:
    finder = _ParameterLoadFinder(_parameter_names(node.args))
    finder.visit(node.body)
    return not finder.found


def _parameter_names(arguments: ast.arguments) -> set[str]:
    return {
        argument.arg
        for argument in (
            *arguments.posonlyargs,
            *arguments.args,
            *arguments.kwonlyargs,
            *((arguments.vararg,) if arguments.vararg is not None else ()),
            *((arguments.kwarg,) if arguments.kwarg is not None else ()),
        )
    }


class _ParameterLoadFinder(ast.NodeVisitor):
    def __init__(self, parameters: set[str]) -> None:
        self.parameters = parameters
        self.found = False

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, ast.Load) and node.id in self.parameters:
            self.found = True


__all__: list[str] = []
