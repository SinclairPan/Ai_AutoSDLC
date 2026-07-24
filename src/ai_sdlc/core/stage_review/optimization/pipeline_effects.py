"""Pipeline 外部调用与不可变写入的统一 fencing 边界。"""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar, cast

from pydantic import BaseModel

from ai_sdlc.core.stage_review.optimization.pipeline_store import (
    OptimizationPipelineStore,
)

T = TypeVar("T")
M = TypeVar("M", bound=BaseModel)


class PipelineEffects:
    def __init__(
        self,
        store: OptimizationPipelineStore,
        authorize: Callable[[], None],
    ) -> None:
        self.store = store
        self.authorize = authorize

    def call(self, operation: Callable[[], T]) -> T:
        self.authorize()
        return operation()

    def write(self, epoch_id: str, stage: str, value: M) -> M:
        return commit_effect(
            self.authorize,
            lambda: self.store.write(epoch_id, stage, value),
        )


def commit_effect(authorizer: Callable[[], None], operation: Callable[[], T]) -> T:
    commit = getattr(authorizer, "commit", None)
    if not callable(commit):
        raise TypeError("fenced authorizer requires atomic commit")
    return cast(T, commit(operation))


def _epoch_fencing_identity(authorizer: Callable[[], None]) -> tuple[int, str]:
    fencing_epoch = getattr(authorizer, "epoch_fencing_epoch", 0)
    claim_digest = str(getattr(authorizer, "epoch_claim_digest", ""))
    if not isinstance(fencing_epoch, int) or fencing_epoch < 1 or not claim_digest:
        raise TypeError("fenced authorizer requires epoch claim identity")
    return fencing_epoch, claim_digest


class _AllowEffect:
    epoch_fencing_epoch = 1
    epoch_claim_digest = "sha256:test-epoch-claim"

    def __call__(self) -> None:
        return None

    def commit(self, operation: Callable[[], T]) -> T:
        return operation()


allow_effect = _AllowEffect()
