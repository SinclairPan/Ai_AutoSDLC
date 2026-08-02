"""识别定义期导入绑定的不确定性。"""

from __future__ import annotations

import ast

from ai_sdlc.core.lean_code_scope import _arguments


class _StoreNameFinder(ast.NodeVisitor):
    def __init__(self) -> None:
        self.names: set[str] = set()

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, (ast.Store, ast.Del)):
            self.names.add(node.id)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.names.add(node.name)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.names.add(node.name)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.names.add(node.name)

    def visit_Import(self, node: ast.Import) -> None:
        self.names.update(
            alias.asname or alias.name.split(".", 1)[0] for alias in node.names
        )

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        self.names.update(
            alias.asname or alias.name for alias in node.names if alias.name != "*"
        )


class _UncertainBindingFinder(ast.NodeVisitor):
    def __init__(self) -> None:
        self.names: set[str] = set()
        self.unbounded = False
        self.class_depth = 0

    def visit_Import(self, node: ast.Import) -> None:
        if self.class_depth == 0:
            self.names.update(
                alias.asname or alias.name.split(".", 1)[0] for alias in node.names
            )

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if self.class_depth:
            return
        for alias in node.names:
            if alias.name == "*":
                self.unbounded = True
            else:
                self.names.add(alias.asname or alias.name)

    def visit_Call(self, node: ast.Call) -> None:
        if _unbounded_binding_call(node):
            self.unbounded = True
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if isinstance(node.ctx, (ast.Store, ast.Del)):
            self.unbounded = True
        self.generic_visit(node)

    def visit_Subscript(self, node: ast.Subscript) -> None:
        if isinstance(node.ctx, (ast.Store, ast.Del)):
            self.unbounded = True
        self.generic_visit(node)

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
        self.class_depth += 1
        try:
            for statement in node.body:
                self.visit(statement)
        finally:
            self.class_depth -= 1

    def _visit_function_header(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
    ) -> None:
        for decorator in node.decorator_list:
            self.visit(decorator)
        for default in (*node.args.defaults, *node.args.kw_defaults):
            if default is not None:
                self.visit(default)
        for argument in _arguments(node.args):
            if argument.annotation is not None:
                self.visit(argument.annotation)
        if node.returns is not None:
            self.visit(node.returns)
        for type_param in getattr(node, "type_params", ()):
            self.visit(type_param)


def _unbounded_binding_call(node: ast.Call) -> bool:
    return isinstance(node.func, ast.Name) and node.func.id in {
        "exec",
        "eval",
        "globals",
        "locals",
    }


__all__: list[str] = []
