"""共享的 framework 绑定、副作用与名称失效分析。"""

from __future__ import annotations

import ast
from typing import Protocol


class _ProofState(Protocol):
    def invalidate(self, name: str) -> None: ...

    def clear_proofs(self) -> None: ...


def _invalidate_bound_names(
    statements: list[ast.stmt] | tuple[ast.stmt, ...],
    state: _ProofState,
) -> None:
    finder = _TopLevelStoreFinder()
    for statement in statements:
        finder.visit(statement)
    if finder.unresolved:
        state.clear_proofs()
        return
    for name in finder.names:
        state.invalidate(name)


class _TopLevelStoreFinder(ast.NodeVisitor):
    def __init__(self) -> None:
        self.names: set[str] = set()
        self.unresolved = False

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, (ast.Store, ast.Del)):
            self.names.add(node.id)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if isinstance(node.ctx, (ast.Store, ast.Del)):
            root = _target_root_name(node)
            if root:
                self.names.add(root)
            else:
                self.unresolved = True
        self.generic_visit(node)

    def visit_Subscript(self, node: ast.Subscript) -> None:
        if isinstance(node.ctx, (ast.Store, ast.Del)):
            root = _target_root_name(node)
            if root:
                self.names.add(root)
            else:
                self.unresolved = True
        self.generic_visit(node)

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
        for alias in node.names:
            if alias.name == "*":
                self.unresolved = True
            else:
                self.names.add(alias.asname or alias.name)


def _bound_names(node: ast.AST | None) -> set[str]:
    if isinstance(node, ast.Name):
        return {node.id}
    if isinstance(node, (ast.Tuple, ast.List)):
        return set().union(*(_bound_names(item) for item in node.elts))
    if isinstance(node, ast.Starred):
        return _bound_names(node.value)
    return set()


def _target_root_name(node: ast.AST) -> str:
    current = node
    while isinstance(current, (ast.Attribute, ast.Subscript)):
        current = current.value
    return current.id if isinstance(current, ast.Name) else ""


def _contains_dynamic_rebinding(node: ast.AST) -> bool:
    if any(isinstance(child, (ast.Global, ast.Nonlocal)) for child in ast.walk(node)):
        return True
    if any(
        isinstance(child, ast.Name)
        and isinstance(child.ctx, ast.Load)
        and child.id in {"exec", "eval", "globals", "locals", "vars"}
        for child in ast.walk(node)
    ):
        return True
    for child in ast.walk(node):
        if not isinstance(child, ast.Call):
            continue
        if isinstance(child.func, ast.Name) and child.func.id in {
            "setattr",
            "delattr",
            "exec",
            "eval",
            "globals",
            "locals",
            "vars",
        }:
            return True
        if (
            isinstance(child.func, ast.Attribute)
            and isinstance(child.func.value, ast.Call)
            and isinstance(child.func.value.func, ast.Name)
            and child.func.value.func.id in {"globals", "locals", "vars"}
        ):
            return True
    return False


def _contains_unproved_helper_effect(node: ast.AST) -> bool:
    """未知调用或非局部写入都不能证明不会改写模块入口。"""

    for child in ast.walk(node):
        if isinstance(child, (ast.Global, ast.Nonlocal, ast.Call)):
            return True
        if isinstance(child, (ast.Attribute, ast.Subscript)) and isinstance(
            child.ctx, (ast.Store, ast.Del)
        ):
            return True
    return False


def _definition_time_effects(
    node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef,
    proof_names: set[str],
    safe_call_names: set[str],
) -> tuple[bool, set[str]]:
    finder = _DefinitionTimeEffectFinder(proof_names, safe_call_names)
    finder.visit(node)
    return finder.dynamic, finder.touched_proofs


class _DefinitionTimeEffectFinder(ast.NodeVisitor):
    def __init__(self, proof_names: set[str], safe_call_names: set[str]) -> None:
        self.proof_names = proof_names
        self.safe_call_names = safe_call_names
        self.dynamic = False
        self.touched_proofs: set[str] = set()

    def visit_Call(self, node: ast.Call) -> None:
        if (
            _contains_dynamic_rebinding(node)
            or _call_name(node.func) not in self.safe_call_names
        ):
            self.dynamic = True
        if _call_name(node.func) not in self.safe_call_names:
            self.touched_proofs.update(
                child.id
                for child in ast.walk(node)
                if isinstance(child, ast.Name) and child.id in self.proof_names
            )
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
        for statement in node.body:
            self.visit(statement)

    def _visit_function_header(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
    ) -> None:
        for decorator in node.decorator_list:
            self.visit(decorator)
        for default in (*node.args.defaults, *node.args.kw_defaults):
            if default is not None:
                self.visit(default)
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


def _unknown_call_proof_names(
    node: ast.AST,
    proof_names: set[str],
    safe_call_names: set[str],
) -> set[str]:
    touched: set[str] = set()
    for child in ast.walk(node):
        if not isinstance(child, ast.Call):
            continue
        if _call_name(child.func) in safe_call_names:
            continue
        touched.update(
            item.id
            for item in ast.walk(child)
            if isinstance(item, ast.Name) and item.id in proof_names
        )
    return touched


def _call_name(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _call_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return ""


def _is_main_guard(node: ast.expr) -> bool:
    if (
        not isinstance(node, ast.Compare)
        or len(node.ops) != 1
        or not isinstance(node.ops[0], ast.Eq)
        or len(node.comparators) != 1
    ):
        return False
    values = (node.left, node.comparators[0])
    return any(
        isinstance(value, ast.Name) and value.id == "__name__" for value in values
    ) and any(
        isinstance(value, ast.Constant) and value.value == "__main__"
        for value in values
    )


__all__: list[str] = []
