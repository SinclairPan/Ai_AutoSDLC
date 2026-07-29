"""把生成器消费元数据转换为异常前缀的游标计划。"""

from __future__ import annotations

import ast
from collections.abc import Sequence
from typing import Protocol

from ai_sdlc.core.lean_code_generator_identity import (
    _generator_consumer_mode,
    _generator_consumer_offsets,
)


class _ComprehensionVisitor(Protocol):
    def _visit_comprehension(
        self,
        generators: Sequence[ast.comprehension],
        terminal: Sequence[ast.expr],
        index: int = 0,
        *,
        hash_expression: ast.expr | None = None,
        maximum_iterations: int | None = None,
        skip_iterations: int = 0,
    ) -> None: ...


def _generator_exception_plans(
    node: ast.Call,
    generators: tuple[ast.GeneratorExp, ...],
) -> tuple[tuple[ast.GeneratorExp, int | None, int, bool], ...]:
    maximum_iterations = 1 if _generator_consumer_mode(node) == "one" else None
    offsets = _generator_consumer_offsets(node)
    return tuple(
        (
            generator,
            maximum_iterations,
            offset or 0,
            offset is None,
        )
        for index, generator in enumerate(generators)
        for offset in (offsets[index] if index < len(offsets) else 0,)
    )


def _visit_consumed_generator(
    visitor: _ComprehensionVisitor,
    node: ast.GeneratorExp,
    *,
    maximum_iterations: int | None = None,
    skip_iterations: int = 0,
) -> None:
    visitor._visit_comprehension(
        node.generators,
        (node.elt,),
        maximum_iterations=maximum_iterations,
        skip_iterations=skip_iterations,
    )


__all__: list[str] = []
