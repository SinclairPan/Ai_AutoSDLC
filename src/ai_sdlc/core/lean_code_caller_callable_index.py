"""Private callable index responsibility for Lean caller analysis."""

from __future__ import annotations

import ast
from collections.abc import Sequence

from ai_sdlc.core.lean_code_caller_models import (
    _ComprehensionScope,
)
from ai_sdlc.core.lean_code_caller_primitives import (
    _assignment_value_targets,
    _enclosing_class_node,
    _enclosing_function_or_lambda,
    _is_ancestor,
    _OuterDeclarationFinder,
    _star_value_active,
)
from ai_sdlc.core.lean_code_caller_source_index import (
    _ImportlibAttributeMutationFinder,
)
from ai_sdlc.core.lean_code_execution_order import _lineage_item_key
from ai_sdlc.core.lean_code_flow import _pattern_bound_names
from ai_sdlc.core.lean_code_scope import _bound_names


def _star_assignment_states(
    statement: ast.stmt,
    states: dict[str, bool],
) -> dict[str, bool]:
    value: ast.expr | None = None
    targets: Sequence[ast.expr] = ()
    if isinstance(statement, ast.Assign):
        value, targets = statement.value, statement.targets
    elif isinstance(statement, ast.AnnAssign) and statement.value is not None:
        value, targets = statement.value, (statement.target,)
    if value is None:
        return {}
    captured = {
        name: _star_value_active(value, states)
        for target in targets
        for name in _bound_names(target)
    }
    if len(targets) == 1 and isinstance(targets[0], ast.Name):
        for index, item in enumerate(
            value.elts if isinstance(value, (ast.List, ast.Tuple)) else ()
        ):
            captured[_lineage_item_key(targets[0].id, index)] = _star_value_active(
                item, states
            )
    return captured

class _ModuleEffectBindingFinder(ast.NodeVisitor):
    def __init__(self) -> None:
        self.names: set[str] = set()

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, (ast.Store, ast.Del)):
            self.names.add(node.id)

    def visit_Import(self, node: ast.Import) -> None:
        self.names.update(
            alias.asname or alias.name.split(".", 1)[0] for alias in node.names
        )

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        self.names.update(
            alias.asname or alias.name for alias in node.names if alias.name != "*"
        )

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if node.value is not None:
            self.visit(node.target)
            self.visit(node.value)
        self.visit(node.annotation)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.names.add(node.name)
        self._visit_function_header(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.names.add(node.name)
        self._visit_function_header(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.names.add(node.name)
        for decorator in node.decorator_list:
            self.visit(decorator)
        for base in node.bases:
            self.visit(base)
        for keyword in node.keywords:
            self.visit(keyword.value)
        for type_param in getattr(node, "type_params", ()):
            self.visit(type_param)
        self.names.update(_declared_outer_effect_names(node.body, ast.Global))

    def visit_Lambda(self, node: ast.Lambda) -> None:
        self._visit_defaults(node.args)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if node.name:
            self.names.add(node.name)
        if node.type is not None:
            self.visit(node.type)
        for statement in node.body:
            self.visit(statement)

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

    def _visit_function_header(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
    ) -> None:
        for decorator in node.decorator_list:
            self.visit(decorator)
        self._visit_defaults(node.args)
        arguments = [
            *node.args.posonlyargs,
            *node.args.args,
            *node.args.kwonlyargs,
            node.args.vararg,
            node.args.kwarg,
        ]
        for argument in arguments:
            if argument is not None and argument.annotation is not None:
                self.visit(argument.annotation)
        if node.returns is not None:
            self.visit(node.returns)
        for type_param in getattr(node, "type_params", ()):
            self.visit(type_param)

    def _visit_defaults(self, arguments: ast.arguments) -> None:
        for default in (*arguments.defaults, *arguments.kw_defaults):
            if default is not None:
                self.visit(default)

    def _visit_comprehension(
        self,
        generators: list[ast.comprehension],
        *outputs: ast.expr,
    ) -> None:
        for generator in generators:
            self.visit(generator.iter)
            for condition in generator.ifs:
                self.visit(condition)
        for output in outputs:
            self.visit(output)

def _declared_outer_effect_names(
    statements: Sequence[ast.stmt],
    declaration_type: type[ast.Global] | type[ast.Nonlocal],
) -> set[str]:
    declarations = _OuterDeclarationFinder(declaration_type)
    for statement in statements:
        declarations.visit(statement)
    if not declarations.names:
        return set()
    effects = _ModuleEffectBindingFinder()
    for statement in statements:
        effects.visit(statement)
    return effects.names & declarations.names

def _generator_inherits_outer(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> bool:
    return (
        _enclosing_function_or_lambda(node, parents) is not None
        or _enclosing_class_node(node, parents) is not None
    )

def _statements_before_node(
    statements: Sequence[ast.stmt],
    node: ast.AST,
    parents: dict[ast.AST, ast.AST],
) -> list[ast.stmt]:
    for index, statement in enumerate(statements):
        if _is_ancestor(statement, node, parents):
            return list(statements[:index])
    return list(statements)

def _in_comprehension_body(
    node: ast.AST,
    parents: dict[ast.AST, ast.AST],
) -> bool:
    current = parents.get(node)
    while current is not None:
        if isinstance(current, _ComprehensionScope):
            return not _is_ancestor(current.generators[0].iter, node, parents)
        current = parents.get(current)
    return False

def _comprehension_shadows(
    node: ast.AST,
    target_name: str,
    parents: dict[ast.AST, ast.AST],
) -> bool:
    current = parents.get(node)
    while current is not None:
        if isinstance(
            current, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)
        ):
            first_iter = current.generators[0].iter
            if not _is_ancestor(first_iter, node, parents):
                bound = set().union(
                    *(
                        _bound_names(generator.target)
                        for generator in current.generators
                    )
                )
                if target_name in bound:
                    return True
        current = parents.get(current)
    return False

def _captured_importlib_functions(
    statement: ast.stmt,
    module_names: set[str],
    function_names: set[str],
    attribute_intact: bool,
) -> set[str]:
    value, targets = _assignment_value_targets(statement)
    if value is None:
        return set()
    is_module_attribute = (
        isinstance(value, ast.Attribute)
        and value.attr == "import_module"
        and isinstance(value.value, ast.Name)
        and value.value.id in module_names
        and attribute_intact
    )
    is_function_alias = isinstance(value, ast.Name) and value.id in function_names
    if not (is_module_attribute or is_function_alias):
        return set()
    return set().union(*(_bound_names(target) for target in targets))

def _importlib_attribute_assignment(
    statement: ast.stmt,
    module_names: set[str],
) -> ast.expr | None:
    value, targets = _assignment_value_targets(statement)
    if value is None or len(targets) != 1:
        return None
    target = targets[0]
    if (
        isinstance(target, ast.Attribute)
        and target.attr == "import_module"
        and isinstance(target.value, ast.Name)
        and target.value.id in module_names
    ):
        return value
    return None

def _captured_importlib_modules(
    statement: ast.stmt,
    module_names: set[str],
) -> set[str]:
    value, targets = _assignment_value_targets(statement)
    if not isinstance(value, ast.Name) or value.id not in module_names:
        return set()
    return set().union(*(_bound_names(target) for target in targets))

def _mutates_importlib_attribute(
    statement: ast.stmt,
    module_names: set[str],
) -> bool:
    finder = _ImportlibAttributeMutationFinder(module_names)
    finder.visit(statement)
    return finder.mutated

__all__: list[str] = []
