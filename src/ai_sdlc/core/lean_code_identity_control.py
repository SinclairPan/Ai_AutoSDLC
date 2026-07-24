"""身份流的循环、异常与完成态转移。"""

from __future__ import annotations

import ast
from collections.abc import Sequence

from ai_sdlc.core.lean_code_identity_expression import _IdentityExpressionMixin
from ai_sdlc.core.lean_code_identity_join import _deduplicate_states
from ai_sdlc.core.lean_code_identity_models import (
    _EMPTY_VALUE,
    _IdentityState,
    _IdentityValue,
)
from ai_sdlc.core.lean_code_identity_semantics import _statement_may_raise


class _IdentityControlMixin(_IdentityExpressionMixin):
    def _known_for(
        self,
        state: _IdentityState,
        node: ast.For | ast.AsyncFor,
        items: tuple[_IdentityValue, ...],
    ) -> list[_IdentityState]:
        current = [state]
        broken: list[_IdentityState] = []
        for item in items:
            iteration: list[_IdentityState] = []
            for candidate in current:
                self._bind(candidate, node.target, item)
                outcomes = self.process([candidate], node.body)
                broken.extend(self._complete_as_normal(outcomes, "break"))
                iteration.extend(self._complete_as_normal(outcomes, "continue"))
                iteration.extend(
                    outcome for outcome in outcomes if outcome.completion == "normal"
                )
            current = _deduplicate_states(iteration)
        return broken + self.process(current, node.orelse)

    def _unknown_for(
        self, state: _IdentityState, node: ast.For | ast.AsyncFor
    ) -> list[_IdentityState]:
        body_state = state.clone()
        self._bind(body_state, node.target, _EMPTY_VALUE)
        outcomes = self.process([body_state], node.body)
        broken = self._complete_as_normal(outcomes, "break")
        continuing = self._complete_as_normal(outcomes, "continue")
        continuing.extend(item for item in outcomes if item.completion == "normal")
        return broken + self.process([state, *continuing], node.orelse)

    def _try_body(
        self, state: _IdentityState, statements: Sequence[ast.stmt]
    ) -> list[_IdentityState]:
        current = [state]
        terminal: list[_IdentityState] = []
        for statement in statements:
            current, completed = self._try_statement(current, statement)
            terminal.extend(completed)
        return current + terminal

    def _try_statement(
        self, states: list[_IdentityState], statement: ast.stmt
    ) -> tuple[list[_IdentityState], list[_IdentityState]]:
        continuing: list[_IdentityState] = []
        completed: list[_IdentityState] = []
        for candidate in states:
            exception_start = len(self.exception_states)
            results = self.process([candidate], (statement,))
            expression_raises = self.exception_states[exception_start:]
            if expression_raises:
                completed.extend(expression_raises)
            elif _statement_may_raise(statement) and not isinstance(
                statement, ast.Raise
            ):
                raised = candidate.clone()
                raised.completion = "raise"
                completed.append(raised)
            continuing.extend(
                result for result in results if result.completion == "normal"
            )
            completed.extend(
                result for result in results if result.completion != "normal"
            )
        return continuing, completed

    def _handle_raise(
        self, state: _IdentityState, handlers: list[ast.ExceptHandler]
    ) -> list[_IdentityState]:
        outcomes: list[_IdentityState] = []
        for handler in handlers:
            match = self._handler_matches(handler.type, state.exception)
            if match is False:
                continue
            branch = state.clone()
            branch.completion = "normal"
            branch.exception = None
            if handler.name:
                branch.write(handler.name, _EMPTY_VALUE)
            outcomes.extend(self.process([branch], handler.body))
            if match is True:
                return outcomes
        return outcomes or [state]

    def _apply_finally(
        self, states: list[_IdentityState], statements: list[ast.stmt]
    ) -> list[_IdentityState]:
        if not statements:
            return states
        outcomes: list[_IdentityState] = []
        for state in states:
            saved = state.completion, state.result, state.exception
            state.completion, state.exception = "normal", None
            final = self.process([state], statements)
            for item in final:
                if item.completion == "normal":
                    item.completion, item.result, item.exception = saved
                outcomes.append(item)
        return outcomes

    @staticmethod
    def _complete_as_normal(
        states: list[_IdentityState], completion: str
    ) -> list[_IdentityState]:
        selected = [item.clone() for item in states if item.completion == completion]
        for item in selected:
            item.completion = "normal"
        return selected

    @staticmethod
    def _handler_matches(node: ast.expr | None, exception: str | None) -> bool | None:
        if node is None:
            return True
        if exception is None:
            return None
        if isinstance(node, ast.Name):
            return node.id in {exception, "Exception", "BaseException"}
        if isinstance(node, ast.Tuple):
            outcomes = [
                _IdentityControlMixin._handler_matches(item, exception)
                for item in node.elts
            ]
            return any(item is True for item in outcomes)
        return None


__all__: list[str] = []
