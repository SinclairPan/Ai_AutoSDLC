"""提取语句执行时可能抛出异常的有序绑定前缀。"""

from __future__ import annotations

import ast
from collections.abc import Iterable, Sequence

from ai_sdlc.core.lean_code_control_flow import (
    _function_annotation_expressions,
    _known_safe_iterator_protocol,
    _known_safe_mapping_protocol,
    _static_truth,
    _statically_empty,
    _statically_nonempty,
    _unpack_analysis,
    _unpack_exception_points,
)
from ai_sdlc.core.lean_code_exception_generator_state import (
    _generator_exception_plans,
    _visit_consumed_generator,
)
from ai_sdlc.core.lean_code_exception_handlers import _raised_name
from ai_sdlc.core.lean_code_exception_points import (
    _deduplicate_prefixes,
    _ExceptionPoint,
)
from ai_sdlc.core.lean_code_exception_protocol_flow import (
    _ProtocolExceptionVisitor,
)
from ai_sdlc.core.lean_code_generator_identity import (
    _consumer_generator_lineage,
    _direct_generator_consumer,
    _generator_consumer_effect,
)
from ai_sdlc.core.lean_code_static_values import _known_hash_safe


def _statement_exception_points(statement: ast.stmt) -> tuple[_ExceptionPoint, ...]:
    finder = _ExceptionPointFinder()
    finder.visit(statement)
    return tuple(finder.points)


class _ExceptionPointFinder(_ProtocolExceptionVisitor, ast.NodeVisitor):
    def __init__(self) -> None:
        self.prefixes: list[tuple[ast.stmt, ...]] = [()]
        self.points: list[_ExceptionPoint] = []

    def visit_NamedExpr(self, node: ast.NamedExpr) -> None:
        self.visit(node.value)
        binding = ast.Expr(value=node)
        self.prefixes = [(*prefix, binding) for prefix in self.prefixes]

    def visit_Assign(self, node: ast.Assign) -> None:
        self.visit(node.value)
        for target in node.targets:
            self._record_unpack_points(target, node.value)
            self.visit(target)
            binding = ast.Assign(targets=[target], value=node.value)
            self.prefixes = [(*prefix, binding) for prefix in self.prefixes]

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if node.value is not None:
            self.visit(node.value)
            self._record_unpack_points(node.target, node.value)
        self.visit(node.target)
        if node.value is not None:
            binding = ast.Assign(targets=[node.target], value=node.value)
            self.prefixes = [(*prefix, binding) for prefix in self.prefixes]

    def visit_Call(self, node: ast.Call) -> None:
        self.visit(node.func)
        for argument in node.args:
            self.visit(argument)
        for keyword in node.keywords:
            self.visit(keyword.value)
        generators = _consumer_generator_lineage(node) or tuple(
            value
            for value in (
                *node.args,
                *(keyword.value for keyword in node.keywords),
            )
            if isinstance(value, ast.GeneratorExp)
        )
        effect = _generator_consumer_effect(node) or (
            "consume" if _direct_generator_consumer(node) else ""
        )
        if generators and effect in {"consume", "unknown"}:
            initial = list(self.prefixes)
            for generator, maximum, offset, unknown in _generator_exception_plans(
                node,
                generators,
            ):
                before_unknown_offset = list(self.prefixes)
                _visit_consumed_generator(
                    self,
                    generator,
                    maximum_iterations=maximum,
                    skip_iterations=offset,
                )
                if unknown:
                    self.prefixes = _deduplicate_prefixes(
                        [*before_unknown_offset, *self.prefixes]
                    )
            if effect == "unknown":
                self.prefixes = _deduplicate_prefixes(
                    [*initial, *self.prefixes]
                )
        self._record()

    def visit_Attribute(self, node: ast.Attribute) -> None:
        self.visit(node.value)
        self._record()

    def visit_UnaryOp(self, node: ast.UnaryOp) -> None:
        self.visit(node.operand)
        self._record()

    def visit_Compare(self, node: ast.Compare) -> None:
        self.visit(node.left)
        for comparator in node.comparators:
            self.visit(comparator)
            self._record()

    def visit_Dict(self, node: ast.Dict) -> None:
        for key, value in zip(node.keys, node.values, strict=True):
            if key is None:
                self.visit(value)
                if not _known_safe_mapping_protocol(value):
                    self._record()
                continue
            self.visit(key)
            self.visit(value)
            if not _known_hash_safe(key):
                self._record()

    def visit_List(self, node: ast.List) -> None:
        if isinstance(node.ctx, ast.Store):
            self.generic_visit(node)
            return
        self._visit_sequence_display(node.elts)

    def visit_Tuple(self, node: ast.Tuple) -> None:
        if isinstance(node.ctx, ast.Store):
            self.generic_visit(node)
            return
        self._visit_sequence_display(node.elts)

    def visit_Set(self, node: ast.Set) -> None:
        for item in node.elts:
            if isinstance(item, ast.Starred):
                self.visit(item.value)
                if not _known_safe_iterator_protocol(item.value):
                    self._record()
                continue
            self.visit(item)
            if not _known_hash_safe(item):
                self._record()

    def visit_For(self, node: ast.For) -> None:
        self._visit_for(node)

    def visit_AsyncFor(self, node: ast.AsyncFor) -> None:
        self._visit_for(node)

    def visit_ListComp(self, node: ast.ListComp) -> None:
        self._visit_comprehension(node.generators, (node.elt,))

    def visit_SetComp(self, node: ast.SetComp) -> None:
        self._visit_comprehension(
            node.generators,
            (node.elt,),
            hash_expression=node.elt,
        )

    def visit_GeneratorExp(self, node: ast.GeneratorExp) -> None:
        if node.generators:
            outer_generator = node.generators[0]
            outer_iter = outer_generator.iter
            self.visit(outer_iter)
            if not _known_safe_iterator_protocol(
                outer_iter,
                is_async=bool(outer_generator.is_async),
            ):
                self._record()

    def visit_DictComp(self, node: ast.DictComp) -> None:
        self._visit_comprehension(
            node.generators,
            (node.key, node.value),
            hash_expression=node.key,
        )

    def visit_Await(self, node: ast.Await) -> None:
        self.visit(node.value)
        self._record()

    def visit_Assert(self, node: ast.Assert) -> None:
        self.visit(node.test)
        self._record_truth_protocol(node.test)
        if node.msg is not None:
            self.visit(node.msg)
        self._record("AssertionError")

    def visit_Raise(self, node: ast.Raise) -> None:
        if node.exc is not None:
            self.visit(node.exc)
        if node.cause is not None:
            self.visit(node.cause)
        self._record(_raised_name(node.exc))

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function_header(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function_header(node)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        for default in (*node.args.defaults, *node.args.kw_defaults):
            if default is not None:
                self.visit(default)

    def _visit_function_header(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
    ) -> None:
        for expression in (
            *node.decorator_list,
            *getattr(node, "type_params", ()),
            *node.args.defaults,
            *(item for item in node.args.kw_defaults if item is not None),
            *_function_annotation_expressions(node),
        ):
            self.visit(expression)

    def _record(self, raised: str | None = None) -> None:
        for prefix in self.prefixes:
            point = _ExceptionPoint(prefix, raised)
            if point not in self.points:
                self.points.append(point)

    def _visit_sequence_display(self, items: Sequence[ast.expr]) -> None:
        for item in items:
            if not isinstance(item, ast.Starred):
                self.visit(item)
                continue
            if isinstance(item.value, ast.GeneratorExp):
                _visit_consumed_generator(self, item.value)
            else:
                self.visit(item.value)
            if not _known_safe_iterator_protocol(item.value):
                self._record()

    def _visit_for(self, node: ast.For | ast.AsyncFor) -> None:
        if isinstance(node.iter, ast.GeneratorExp):
            _visit_consumed_generator(self, node.iter)
        else:
            self.visit(node.iter)
        unsafe_protocol = not _known_safe_iterator_protocol(
            node.iter,
            is_async=isinstance(node, ast.AsyncFor),
        )
        if unsafe_protocol:
            self._record()
        values: tuple[ast.expr | None, ...]
        if isinstance(node.iter, (ast.List, ast.Tuple)):
            values = tuple(node.iter.elts)
        else:
            values = (None,)
        for value in values:
            self._record_unpack_points(node.target, value)
            self._apply_unpack_bindings(node.target, value)
            self.visit(node.target)
            for statement in node.body:
                self.visit(statement)
        if unsafe_protocol:
            self._record()
        for statement in node.orelse:
            self.visit(statement)

    def _visit_comprehension(
        self,
        generators: Sequence[ast.comprehension],
        terminal: Sequence[ast.expr],
        index: int = 0,
        *,
        hash_expression: ast.expr | None = None,
        maximum_iterations: int | None = None,
        skip_iterations: int = 0,
    ) -> None:
        if index == len(generators):
            for expression in terminal:
                self.visit(expression)
            if hash_expression is not None and not _known_hash_safe(hash_expression):
                self._record()
            return
        generator = generators[index]
        self.visit(generator.iter)
        unsafe_protocol = not _known_safe_iterator_protocol(
            generator.iter,
            is_async=bool(generator.is_async),
        )
        if unsafe_protocol:
            self._record()
        if _statically_empty(generator.iter):
            return
        stopped = [] if _statically_nonempty(generator.iter) else list(self.prefixes)
        values: tuple[ast.expr | None, ...] = (
            tuple(generator.iter.elts)
            if isinstance(generator.iter, (ast.List, ast.Tuple))
            and not any(isinstance(item, ast.Starred) for item in generator.iter.elts)
            else (None,)
        )
        values = values[skip_iterations:] if index == 0 else values
        candidates = values[:maximum_iterations] if maximum_iterations else values
        for value in candidates:
            self._visit_comprehension_iteration(
                generator,
                value,
                stopped,
                generators,
                terminal,
                index,
                hash_expression,
                maximum_iterations,
                skip_iterations,
            )
        self.prefixes = _deduplicate_prefixes([*stopped, *self.prefixes])
        if unsafe_protocol:
            self._record()

    def _visit_comprehension_iteration(
        self,
        generator: ast.comprehension,
        value: ast.expr | None,
        stopped: list[tuple[ast.stmt, ...]],
        generators: Sequence[ast.comprehension],
        terminal: Sequence[ast.expr],
        index: int,
        hash_expression: ast.expr | None,
        maximum_iterations: int | None,
        skip_iterations: int,
    ) -> None:
        self._record_unpack_points(generator.target, value)
        self._apply_unpack_bindings(generator.target, value)
        self.visit(generator.target)
        for condition in generator.ifs:
            self.visit(condition)
            self._record_truth_protocol(condition)
            truth = _static_truth(condition)
            if truth is not True:
                stopped.extend(self.prefixes)
            if truth is False:
                return
        self._visit_comprehension(
            generators,
            terminal,
            index + 1,
            hash_expression=hash_expression,
            maximum_iterations=maximum_iterations,
            skip_iterations=skip_iterations,
        )

    def _visit_alternatives(self, expressions: Iterable[ast.expr]) -> None:
        initial = list(self.prefixes)
        outcomes: list[tuple[ast.stmt, ...]] = []
        for expression in expressions:
            self.prefixes = list(initial)
            self.visit(expression)
            outcomes.extend(self.prefixes)
        self.prefixes = _deduplicate_prefixes(outcomes)

    def _record_unpack_points(
        self,
        target: ast.expr,
        value: ast.expr | None,
    ) -> None:
        initial = list(self.prefixes)
        for point in _unpack_exception_points(target, value):
            self.prefixes = [
                (*prefix, *point.prefix)
                for prefix in initial
            ]
            self._record(point.raised)
        self.prefixes = initial

    def _apply_unpack_bindings(
        self,
        target: ast.expr,
        value: ast.expr | None,
    ) -> None:
        bindings = _unpack_analysis(target, value).bindings
        self.prefixes = [
            (*prefix, *bindings)
            for prefix in self.prefixes
        ]

__all__: list[str] = []
