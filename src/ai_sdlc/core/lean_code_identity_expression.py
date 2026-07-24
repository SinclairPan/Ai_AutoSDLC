"""延迟对象身份的表达式求值与调用传播。"""

from __future__ import annotations

import ast
import builtins
from collections.abc import Sequence
from dataclasses import replace
from typing import cast

from ai_sdlc.core.lean_code_identity_calls import _IdentityCallMixin
from ai_sdlc.core.lean_code_identity_join import _join_states, _join_values
from ai_sdlc.core.lean_code_identity_models import (
    _EMPTY_VALUE,
    _IMPORTLIB_INTACT_KEY,
    _IdentityState,
    _IdentityValue,
)
from ai_sdlc.core.lean_code_identity_scalar import _binary_scalar
from ai_sdlc.core.lean_code_identity_semantics import (
    _comparison_truth,
    _literal_value,
    _predicate_truth,
)


class _IdentityExpressionMixin(_IdentityCallMixin):
    origin: ast.AST
    mode: str
    generator: ast.GeneratorExp | None
    anchors: list[ast.AST]
    anchor_paths: list[
        tuple[
            ast.AST,
            tuple[ast.stmt, ...],
            tuple[tuple[str, _IdentityValue], ...],
        ]
    ]
    escaped: bool
    call_stack: dict[int, int]
    call_sites: list[ast.AST]
    exception_states: list[_IdentityState]
    target_anchors: list[tuple[tuple[str, str], ast.AST]]
    target_reference_anchors: list[tuple[tuple[str, str], ast.AST]]

    def _expression(self, state: _IdentityState, node: ast.expr) -> _IdentityValue:
        if node is self.origin or self.mode == "factory" and node is self.generator:
            return self._origin_value(state, node)
        if isinstance(node, ast.Constant):
            return self._constant_value(node)
        if isinstance(node, ast.Name):
            if node.id not in state.values and not hasattr(builtins, node.id):
                self._record_exception(state, "NameError")
            value = self._hydrate_module_callable(state, state.read(node.id))
            self._record_target_reference(value, node)
            return value
        if isinstance(node, ast.Attribute):
            value = self._attribute(state, node)
            self._record_target_reference(value, node)
            return value
        if isinstance(node, (ast.Tuple, ast.List, ast.Dict, ast.Subscript)):
            return self._container_expression(state, node)
        if isinstance(node, ast.Starred):
            return self._expression(state, node.value)
        if isinstance(node, ast.NamedExpr):
            value = self._expression(state, node.value)
            self._bind(state, node.target, value)
            return value
        if isinstance(node, ast.Call):
            return self._call(state, node)
        if isinstance(node, ast.GeneratorExp):
            return self._deferred_generator_value(state, node)
        if isinstance(node, ast.Lambda):
            return self._callable_value(state, node)
        if isinstance(node, ast.Await):
            value = self._expression(state, node.value)
            self._consume_deferred_callable(state, value)
            return _EMPTY_VALUE
        if isinstance(node, ast.BinOp):
            return self._binary_expression(state, node)
        if isinstance(node, ast.BoolOp):
            return self._bool_op(state, node)
        if isinstance(node, ast.IfExp):
            return self._if_expression(state, node)
        if isinstance(node, ast.Compare):
            self._compare(state, node)
            return _IdentityValue("literal", truth=_predicate_truth(node))
        if isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp)):
            return self._eager_comprehension(state, node)
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.expr):
                self._expression(state, child)
        return _EMPTY_VALUE

    def _eager_comprehension(
        self,
        state: _IdentityState,
        node: ast.ListComp | ast.SetComp | ast.DictComp,
    ) -> _IdentityValue:
        branch = state.clone()
        for generator in node.generators:
            iterable = self._expression(branch, generator.iter)
            if iterable.truth is False:
                return _IdentityValue("container", truth=False)
            item = iterable.items[0] if iterable.items else _EMPTY_VALUE
            self._bind(branch, generator.target, item)
            for condition in generator.ifs:
                predicate = self._expression(branch, condition)
                if self._truth(condition, predicate) is False:
                    return _IdentityValue("container", truth=False)
        if isinstance(node, ast.DictComp):
            self._expression(branch, node.key)
            self._expression(branch, node.value)
        else:
            self._expression(branch, node.elt)
        self._adopt_state(state, branch)
        return _IdentityValue("container", truth=True)

    @staticmethod
    def _constant_value(node: ast.Constant) -> _IdentityValue:
        scalar_known = isinstance(
            node.value, (str, bytes, int, float, bool, type(None))
        )
        scalar = (
            cast(str | bytes | int | float | bool | None, node.value)
            if scalar_known
            else None
        )
        return _IdentityValue(
            "literal",
            truth=bool(node.value),
            scalar=scalar,
            scalar_known=scalar_known,
        )

    def _deferred_generator_value(
        self, state: _IdentityState, node: ast.GeneratorExp
    ) -> _IdentityValue:
        iterable = self._expression(state, node.generators[0].iter)
        return _IdentityValue(
            "deferred-generator",
            items=iterable.items,
            closure_cells=tuple(
                sorted((name, state.ensure_cell(name)) for name in state.values)
            ),
            generator_node=node,
            generator_truth=iterable.truth,
        )

    def _attribute(self, state: _IdentityState, node: ast.Attribute) -> _IdentityValue:
        owner = self._expression(state, node.value)
        if owner.kind == "module":
            value = dict(owner.entries).get(node.attr, _EMPTY_VALUE)
            if value.callable_node is not None and not value.module_entries:
                module_entries = tuple(
                    (name, item)
                    for name, item in owner.entries
                    if isinstance(name, str)
                )
                return replace(value, module_entries=module_entries)
            return value
        if owner.kind == "instance":
            value = dict(owner.entries).get(node.attr, _EMPTY_VALUE)
            return replace(value, bound_method=True) if value.callable_node else value
        if owner.kind == "asyncio-module" and node.attr == "run":
            return _IdentityValue("asyncio-run")
        if node.attr != "import_module" or owner.kind != "importlib-module":
            return _EMPTY_VALUE
        intact = state.read(_IMPORTLIB_INTACT_KEY)
        if intact == _EMPTY_VALUE:
            intact = _IdentityValue("literal", truth=True)
        return _IdentityValue("importlib-function") if intact.truth else _EMPTY_VALUE

    @staticmethod
    def _hydrate_module_callable(
        state: _IdentityState, value: _IdentityValue
    ) -> _IdentityValue:
        if (
            value.callable_node is not None
            and not value.module_entries
            and value.module_id == state.module_id
            and state.module_entries
        ):
            return replace(value, module_entries=state.module_entries)
        return value

    def _container_expression(
        self,
        state: _IdentityState,
        node: ast.Tuple | ast.List | ast.Dict | ast.Subscript,
    ) -> _IdentityValue:
        if isinstance(node, (ast.Tuple, ast.List)):
            items = tuple(self._expression(state, item) for item in node.elts)
            return _IdentityValue(
                "container",
                items=items,
                truth=bool(items),
            )
        if isinstance(node, ast.Dict):
            return self._dict_expression(state, node)
        value = self._expression(state, node.value)
        index = _literal_value(node.slice)
        if isinstance(index, int) and value.items:
            normalized = index if index >= 0 else len(value.items) + index
            if 0 <= normalized < len(value.items):
                return value.items[normalized]
        if isinstance(index, (str, int)):
            return dict(value.entries).get(index, _EMPTY_VALUE)
        return _EMPTY_VALUE

    def _dict_expression(self, state: _IdentityState, node: ast.Dict) -> _IdentityValue:
        entries: list[tuple[str | int, _IdentityValue]] = []
        for key, item in zip(node.keys, node.values, strict=True):
            if key is None:
                value = self._expression(state, item)
                entries.extend(value.entries)
                continue
            self._expression(state, key)
            literal = _literal_value(key)
            value = self._expression(state, item)
            if isinstance(literal, (str, int)):
                entries.append((literal, value))
        return _IdentityValue("container", entries=tuple(entries), truth=bool(entries))

    def _if_expression(self, state: _IdentityState, node: ast.IfExp) -> _IdentityValue:
        test = self._expression(state, node.test)
        truth = self._truth(node.test, test)
        if truth is not None:
            return self._expression(state, node.body if truth else node.orelse)
        left_state = state.clone()
        right_state = state.clone()
        left = self._expression(left_state, node.body)
        right = self._expression(right_state, node.orelse)
        self._adopt_state(state, _join_states([left_state, right_state]))
        return _join_values([left, right])

    def _origin_value(
        self,
        state: _IdentityState,
        node: ast.expr,
    ) -> _IdentityValue:
        if isinstance(node, ast.GeneratorExp):
            self._expression(state, node.generators[0].iter)
        if self.mode == "factory" and node is self.generator:
            return _IdentityValue("product")
        if isinstance(node, ast.Lambda):
            return self._callable_value(state, node, "object")
        return _IdentityValue("object")

    def _bool_op(self, state: _IdentityState, node: ast.BoolOp) -> _IdentityValue:
        result = _EMPTY_VALUE
        for value in node.values:
            result = self._expression(state, value)
            truth = self._truth(value, result)
            if isinstance(node.op, ast.And) and truth is False:
                break
            if isinstance(node.op, ast.Or) and truth is True:
                break
        return result

    @staticmethod
    def _adopt_state(target: _IdentityState, source: _IdentityState) -> None:
        target.values = dict(source.values)
        target.cells = dict(source.cells)
        target.bindings = dict(source.bindings)
        target.completion = source.completion
        target.result = source.result
        target.exception = source.exception
        target.path = source.path
        target.frame_serial = source.frame_serial
        target.module_id = source.module_id
        target.module_entries = source.module_entries

    def _binary_expression(
        self, state: _IdentityState, node: ast.BinOp
    ) -> _IdentityValue:
        left = self._expression(state, node.left)
        right = self._expression(state, node.right)
        if not left.scalar_known or not right.scalar_known:
            self._record_exception(state, None)
            return _EMPTY_VALUE
        result, error = _binary_scalar(node.op, left.scalar, right.scalar)
        if error is not None:
            self._record_exception(state, error)
            return _EMPTY_VALUE
        known = isinstance(result, (str, bytes, int, float, bool, type(None)))
        return _IdentityValue(
            "literal",
            truth=bool(result),
            scalar=(
                cast(str | bytes | int | float | bool | None, result) if known else None
            ),
            scalar_known=known,
        )

    def _compare(self, state: _IdentityState, node: ast.Compare) -> None:
        left = node.left
        self._expression(state, left)
        for operator, comparator in zip(node.ops, node.comparators, strict=True):
            self._expression(state, comparator)
            if _comparison_truth(left, operator, comparator) is False:
                return
            left = comparator

    def _bind(
        self, state: _IdentityState, target: ast.expr, value: _IdentityValue
    ) -> None:
        if isinstance(target, ast.Name):
            state.write(target.id, value)
            return
        if isinstance(target, ast.Attribute) and target.attr == "import_module":
            owner = self._expression(state, target.value)
            if owner.kind == "importlib-module":
                state.write(
                    _IMPORTLIB_INTACT_KEY,
                    _IdentityValue("literal", truth=value.kind == "importlib-function"),
                )
            return
        if isinstance(target, ast.Starred):
            self._bind(state, target.value, _EMPTY_VALUE)
            return
        if isinstance(target, (ast.Tuple, ast.List)):
            values = value.items if len(value.items) == len(target.elts) else ()
            for index, item in enumerate(target.elts):
                self._bind(state, item, values[index] if values else _EMPTY_VALUE)
            return
        if value.tracked:
            self.escaped = True

    def _consumes(self, value: _IdentityValue) -> bool:
        expected = "product" if self.mode == "factory" else "object"
        return value.kind == expected or value.may_tracked

    def _record_consumption(self, state: _IdentityState, node: ast.AST) -> None:
        if self.generator is None or self._generator_may_execute(state, self.generator):
            self._record_anchor(state, node)

    def _record_target(
        self, state: _IdentityState, target: tuple[str, str], node: ast.AST
    ) -> None:
        del state
        anchor = self.call_sites[0] if self.call_sites else node
        self.target_anchors.append((target, anchor))

    def _record_target_reference(self, value: _IdentityValue, node: ast.AST) -> None:
        raise NotImplementedError

    def _generator_may_execute(
        self, state: _IdentityState, node: ast.GeneratorExp
    ) -> bool:
        for generator in node.generators:
            iterable = self._expression(state, generator.iter)
            if iterable.truth is False:
                return False
            for condition in generator.ifs:
                value = self._expression(state, condition)
                if self._truth(condition, value) is False:
                    return False
        return True

    def _record_anchor(self, state: _IdentityState, node: ast.AST) -> None:
        anchor = self.call_sites[0] if self.call_sites else node
        self.anchors.append(anchor)
        self.anchor_paths.append(
            (anchor, state.path, tuple(sorted(state.resolved_values().items())))
        )

    def _record_exception(self, state: _IdentityState, name: str | None) -> None:
        raised = state.clone()
        raised.completion = "raise"
        raised.exception = name
        self.exception_states.append(raised)

    @staticmethod
    def _truth(node: ast.AST, value: _IdentityValue) -> bool | None:
        return value.truth if value.truth is not None else _predicate_truth(node)

    @staticmethod
    def _exception_name(node: ast.expr | None) -> str | None:
        if isinstance(node, ast.Call):
            node = node.func
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            return node.attr
        return None

    def process(
        self, states: list[_IdentityState], statements: Sequence[ast.stmt]
    ) -> list[_IdentityState]:
        raise NotImplementedError


__all__: list[str] = []
