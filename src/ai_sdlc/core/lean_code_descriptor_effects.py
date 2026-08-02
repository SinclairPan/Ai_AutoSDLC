"""解析会在定义期改变内建描述符绑定的调用效果。"""

from __future__ import annotations

import ast
from typing import Protocol

from ai_sdlc.core.lean_code_call_arguments import _bound_call_arguments
from ai_sdlc.core.lean_code_callback_effects import (
    _CallbackSummary,
    _expression_callback_summary,
)
from ai_sdlc.core.lean_code_callback_projection import _is_type_hints_call
from ai_sdlc.core.lean_code_control_flow import _function_annotation_expressions
from ai_sdlc.core.lean_code_static_values import _constant_subscript_value

_DESCRIPTORS = frozenset({"classmethod", "property", "staticmethod"})


class _DescriptorEffectState(Protocol):
    mutators: dict[str, frozenset[str]]
    callback_parameters: dict[str, _CallbackSummary]
    latent_mutators: dict[str, frozenset[str]]
    typing_modules: set[str]
    type_hint_functions: set[str]
    evaluate_annotations: bool


def _expression_mutations(
    expression: ast.expr | None,
    state: _DescriptorEffectState,
) -> frozenset[str]:
    if isinstance(expression, ast.Name):
        return state.mutators.get(expression.id, frozenset())
    if isinstance(expression, ast.NamedExpr):
        return _expression_mutations(expression.value, state)
    if isinstance(expression, ast.Subscript):
        selected = _constant_subscript_value(expression)
        return (
            _expression_mutations(selected, state)
            if selected is not None
            else frozenset()
        )
    if isinstance(expression, ast.IfExp):
        return _expression_mutations(
            expression.body,
            state,
        ) | _expression_mutations(expression.orelse, state)
    if isinstance(expression, ast.BoolOp):
        return frozenset().union(
            *(_expression_mutations(value, state) for value in expression.values)
        )
    if isinstance(expression, ast.Call):
        return _call_mutations(expression, state)
    return frozenset()


def _function_descriptor_mutations(
    statement: ast.FunctionDef | ast.AsyncFunctionDef,
    state: _DescriptorEffectState,
) -> frozenset[str]:
    finder = _DefinitionTimeEffectFinder(state)
    for child in statement.body:
        finder.visit(child)
    return frozenset(finder.mutations)


def _annotation_descriptor_mutations(
    statement: ast.FunctionDef | ast.AsyncFunctionDef,
    state: _DescriptorEffectState,
) -> frozenset[str]:
    finder = _DefinitionTimeEffectFinder(state)
    for expression in _function_annotation_expressions(statement):
        finder.visit(expression)
    return frozenset(finder.mutations)


def _call_mutations(
    node: ast.Call,
    state: _DescriptorEffectState,
) -> frozenset[str]:
    mutations = set(_expression_mutations(node.func, state))
    summary = _expression_callback_summary(node.func, state)
    if summary is not None:
        bound, complete = _bound_call_arguments(summary.arguments, node)
        if summary.uncertain:
            mutations.update(_DESCRIPTORS)
        for name in summary.invoked_parameters:
            argument = bound.get(name)
            if argument is not None:
                mutations.update(_invoked_expression_mutations(argument, state))
            elif not complete:
                mutations.update(_DESCRIPTORS)
        for name in summary.type_hint_parameters:
            argument = bound.get(name)
            if argument is not None:
                mutations.update(_latent_expression_mutations(argument, state))
            elif not complete:
                mutations.update(frozenset().union(*state.latent_mutators.values()))
    if _is_type_hints_call(node, state):
        positional = ast.arguments(
            posonlyargs=[],
            args=[ast.arg(arg="obj")],
            vararg=None,
            kwonlyargs=[],
            kw_defaults=[],
            kwarg=None,
            defaults=[],
        )
        bound, complete = _bound_call_arguments(positional, node)
        argument = bound.get("obj")
        if argument is not None:
            mutations.update(_latent_expression_mutations(argument, state))
        elif not complete:
            mutations.update(frozenset().union(*state.latent_mutators.values()))
    return frozenset(mutations)


def _invoked_expression_mutations(
    expression: ast.expr,
    state: _DescriptorEffectState,
) -> frozenset[str]:
    if isinstance(expression, ast.Lambda):
        finder = _DefinitionTimeEffectFinder(state)
        finder.visit(expression.body)
        return frozenset(finder.mutations)
    mutations = _expression_mutations(expression, state)
    if mutations or isinstance(
        expression,
        (ast.Name, ast.Subscript, ast.IfExp, ast.BoolOp),
    ):
        return mutations
    return _DESCRIPTORS


def _latent_expression_mutations(
    expression: ast.expr,
    state: _DescriptorEffectState,
) -> frozenset[str]:
    if isinstance(expression, ast.Name):
        return state.latent_mutators.get(expression.id, frozenset())
    if isinstance(expression, ast.NamedExpr):
        return _latent_expression_mutations(expression.value, state)
    if isinstance(expression, ast.Subscript):
        selected = _constant_subscript_value(expression)
        return (
            _latent_expression_mutations(selected, state)
            if selected is not None
            else frozenset().union(*state.latent_mutators.values())
        )
    if isinstance(expression, ast.IfExp):
        return _latent_expression_mutations(
            expression.body, state
        ) | _latent_expression_mutations(expression.orelse, state)
    if isinstance(expression, ast.BoolOp):
        return frozenset().union(
            *(_latent_expression_mutations(value, state) for value in expression.values)
        )
    return frozenset().union(*state.latent_mutators.values())


class _DefinitionTimeEffectFinder(ast.NodeVisitor):
    def __init__(self, state: _DescriptorEffectState) -> None:
        self.state = state
        self.mutations: set[str] = set()

    def visit_Call(self, node: ast.Call) -> None:
        self.mutations.update(_call_mutations(node, self.state))
        self.generic_visit(node)

    def visit_Global(self, node: ast.Global) -> None:
        self.mutations.update(_DESCRIPTORS.intersection(node.names))

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function_header(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function_header(node)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        self._visit_arguments(node.args)

    def _visit_function_header(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
    ) -> None:
        for decorator in node.decorator_list:
            self.mutations.update(_expression_mutations(decorator, self.state))
            self.visit(decorator)
        self._visit_arguments(node.args)
        if self.state.evaluate_annotations and node.returns is not None:
            self.visit(node.returns)

    def _visit_arguments(self, arguments: ast.arguments) -> None:
        for default in (*arguments.defaults, *arguments.kw_defaults):
            if default is not None:
                self.visit(default)
        if not self.state.evaluate_annotations:
            return
        annotations = (
            *arguments.posonlyargs,
            *arguments.args,
            *arguments.kwonlyargs,
        )
        for argument in annotations:
            if argument.annotation is not None:
                self.visit(argument.annotation)
        if arguments.vararg is not None and arguments.vararg.annotation is not None:
            self.visit(arguments.vararg.annotation)
        if arguments.kwarg is not None and arguments.kwarg.annotation is not None:
            self.visit(arguments.kwarg.annotation)


__all__: list[str] = []
