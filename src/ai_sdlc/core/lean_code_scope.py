"""Lexical-scope helpers for deterministic Python caller resolution."""

from __future__ import annotations

import ast

from ai_sdlc.core.lean_code_flow import _pattern_bound_names


def _argument_names(node: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    return {argument.arg for argument in _arguments(node.args)}


def _arguments(arguments: ast.arguments) -> list[ast.arg]:
    items = [*arguments.posonlyargs, *arguments.args, *arguments.kwonlyargs]
    if arguments.vararg is not None:
        items.append(arguments.vararg)
    if arguments.kwarg is not None:
        items.append(arguments.kwarg)
    return items


def _without_local_roots(names: set[str], local_names: set[str]) -> set[str]:
    return {name for name in names if name.split(".", 1)[0] not in local_names}


def _bound_names(node: ast.AST | None) -> set[str]:
    if isinstance(node, ast.Name):
        return {node.id}
    if isinstance(node, (ast.Tuple, ast.List)):
        return set().union(*(_bound_names(item) for item in node.elts))
    if isinstance(node, ast.Starred):
        return _bound_names(node.value)
    return set()


def _local_bindings(node: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    finder = _LocalBindingFinder()
    finder.names.update(_argument_names(node))
    for statement in node.body:
        finder.visit(statement)
    return finder.names - finder.global_names - finder.nonlocal_names


def _scope_declarations(
    node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef,
) -> tuple[set[str], set[str]]:
    finder = _ScopeDeclarationFinder()
    for statement in node.body:
        finder.visit(statement)
    return finder.global_names, finder.nonlocal_names


def _scope_imports(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> list[ast.Import | ast.ImportFrom]:
    finder = _ScopeImportFinder()
    for statement in node.body:
        finder.visit(statement)
    return finder.imports


def _enclosing_class(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> str:
    current = parents.get(node)
    while current is not None:
        if isinstance(current, ast.ClassDef):
            return current.name
        current = parents.get(current)
    return ""


class _LocalBindingFinder(ast.NodeVisitor):
    def __init__(self) -> None:
        self.names: set[str] = set()
        self.global_names: set[str] = set()
        self.nonlocal_names: set[str] = set()

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, (ast.Store, ast.Del)):
            self.names.add(node.id)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.names.add(node.name)
        self._visit_function_header(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.names.add(node.name)
        self._visit_function_header(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.names.add(node.name)
        self._visit_class_header(node)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        self._visit_defaults(node.args)

    def visit_Import(self, node: ast.Import) -> None:
        self.names.update(
            alias.asname or alias.name.split(".", 1)[0] for alias in node.names
        )

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        self.names.update(alias.asname or alias.name for alias in node.names)

    def visit_Global(self, node: ast.Global) -> None:
        self.global_names.update(node.names)

    def visit_Nonlocal(self, node: ast.Nonlocal) -> None:
        self.nonlocal_names.update(node.names)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if node.name:
            self.names.add(node.name)
        self.generic_visit(node)

    def visit_match_case(self, node: ast.match_case) -> None:
        self.names.update(_pattern_bound_names(node.pattern))
        if node.guard is not None:
            self.visit(node.guard)
        for statement in node.body:
            self.visit(statement)

    def visit_ListComp(self, node: ast.ListComp) -> None:
        self._visit_comprehension(node.generators, node.elt)

    def visit_SetComp(self, node: ast.SetComp) -> None:
        self._visit_comprehension(node.generators, node.elt)

    def visit_GeneratorExp(self, node: ast.GeneratorExp) -> None:
        self._visit_comprehension(node.generators, node.elt)

    def visit_DictComp(self, node: ast.DictComp) -> None:
        self._visit_comprehension(node.generators, node.key, node.value)

    def _visit_comprehension(
        self, generators: list[ast.comprehension], *outputs: ast.expr
    ) -> None:
        for generator in generators:
            self.visit(generator.iter)
            for condition in generator.ifs:
                self.visit(condition)
        for output in outputs:
            self.visit(output)

    def _visit_function_header(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
    ) -> None:
        for decorator in node.decorator_list:
            self.visit(decorator)
        self._visit_defaults(node.args)
        for argument in _arguments(node.args):
            if argument.annotation is not None:
                self.visit(argument.annotation)
        if node.returns is not None:
            self.visit(node.returns)
        for type_param in getattr(node, "type_params", ()):
            self.visit(type_param)

    def _visit_class_header(self, node: ast.ClassDef) -> None:
        for decorator in node.decorator_list:
            self.visit(decorator)
        for base in node.bases:
            self.visit(base)
        for keyword in node.keywords:
            self.visit(keyword.value)
        for type_param in getattr(node, "type_params", ()):
            self.visit(type_param)

    def _visit_defaults(self, arguments: ast.arguments) -> None:
        for default in (*arguments.defaults, *arguments.kw_defaults):
            if default is not None:
                self.visit(default)


class _ScopeImportFinder(ast.NodeVisitor):
    def __init__(self) -> None:
        self.imports: list[ast.Import | ast.ImportFrom] = []

    def visit_Import(self, node: ast.Import) -> None:
        self.imports.append(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        self.imports.append(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        return

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        return

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        return


class _ScopeDeclarationFinder(ast.NodeVisitor):
    def __init__(self) -> None:
        self.global_names: set[str] = set()
        self.nonlocal_names: set[str] = set()

    def visit_Global(self, node: ast.Global) -> None:
        self.global_names.update(node.names)

    def visit_Nonlocal(self, node: ast.Nonlocal) -> None:
        self.nonlocal_names.update(node.names)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        return

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        return

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        return

    def visit_Lambda(self, node: ast.Lambda) -> None:
        return


__all__: list[str] = []
