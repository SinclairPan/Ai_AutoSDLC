"""Pipeline 外部调用与不可变写入的统一 fencing 边界。"""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar, cast

from pydantic import BaseModel

from ai_sdlc.core.stage_review.optimization.controller_models import (
    OptimizationEpoch,
    OptimizationEpochEffectReceipt,
    OptimizationEpochLeaseClaim,
)
from ai_sdlc.core.stage_review.optimization.controller_store import (
    OptimizationControllerStore,
)
from ai_sdlc.core.stage_review.optimization.maintenance_window import (
    EpochLeaseGuard,
)
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
        result = operation()
        self.authorize()
        return result

    def write(self, epoch_id: str, stage: str, value: M) -> M:
        return commit_effect(
            self.authorize,
            lambda: self.store.write(epoch_id, stage, value),
        )

    def commit(self, operation: Callable[[], T]) -> T:
        return commit_effect(self.authorize, operation)

    def epoch_fencing_identity(self) -> tuple[int, str]:
        return _epoch_fencing_identity(self.authorize)


class EpochRuntimeAuthorizer:
    """把 epoch lease 与其冻结的 runtime manifest 合并为同一副作用栅栏。"""

    def __init__(
        self,
        factory_token: object,
        lease_authorizer: Callable[[], None],
        validate_runtime: Callable[[], object],
        epoch: OptimizationEpoch,
    ) -> None:
        if factory_token is not _EPOCH_AUTHORIZER_FACTORY_TOKEN:
            raise TypeError("epoch runtime authorizer must be factory-created")
        self._lease_authorizer = lease_authorizer
        self._validate_runtime = validate_runtime
        self._epoch = epoch.model_copy(deep=True)
        self._lease_guard = (
            lease_authorizer
            if isinstance(lease_authorizer, EpochLeaseGuard)
            else None
        )

    @classmethod
    def for_epoch(
        cls,
        lease_authorizer: Callable[[], None],
        validate_runtime: Callable[[], object],
        epoch: OptimizationEpoch,
    ) -> EpochRuntimeAuthorizer:
        return cls(
            _EPOCH_AUTHORIZER_FACTORY_TOKEN,
            lease_authorizer,
            validate_runtime,
            epoch,
        )

    @property
    def epoch_fencing_epoch(self) -> int:
        return int(getattr(self._lease_authorizer, "epoch_fencing_epoch", 0))

    @property
    def epoch_claim_digest(self) -> str:
        return str(getattr(self._lease_authorizer, "epoch_claim_digest", ""))

    @property
    def epoch_id(self) -> str:
        return self._epoch.epoch_id

    @property
    def runtime_bundle_manifest_digest(self) -> str:
        return self._epoch.runtime_bundle_manifest_digest

    def shadow_observed_at(self, epoch: OptimizationEpoch) -> str:
        guard = self._bound_epoch_lease_guard(epoch)
        return guard.claim.acquired_at

    def __call__(self) -> None:
        self._lease_authorizer()
        self._validate_runtime()

    def commit(self, operation: Callable[[], T]) -> T:
        commit = getattr(self._lease_authorizer, "commit", None)
        if not callable(commit):
            raise TypeError("fenced authorizer requires atomic commit")

        def guarded() -> T:
            # 运行时校验是不可变发布的线性化点；发布内容必须提前完成 staging，
            # 避免不可逆写入后再以异常伪装成回滚。
            self._validate_runtime()
            return operation()

        return cast(T, commit(guarded))

    def commit_shadow_observation(
        self,
        *,
        epoch: OptimizationEpoch,
        publication_digest: str,
        provider_journal_last_event_digest: str,
        operation: Callable[[], T],
    ) -> tuple[T, OptimizationEpochEffectReceipt]:
        guard = self._bound_epoch_lease_guard(epoch)

        def guarded() -> tuple[T, OptimizationEpochEffectReceipt]:
            self._validate_runtime()
            receipt = guard.store._prepare_effect_receipt(  # noqa: SLF001
                self._epoch,
                guard.claim,
                effect_kind="shadow_observation",
                effect_digest=publication_digest,
                provider_journal_last_event_digest=(
                    provider_journal_last_event_digest
                ),
            )
            result = operation()
            return result, guard.store._persist_effect_receipt(receipt)  # noqa: SLF001

        return cast(tuple[T, OptimizationEpochEffectReceipt], guard.commit(guarded))

    def _bound_epoch_lease_guard(
        self,
        epoch: OptimizationEpoch,
    ) -> EpochLeaseGuard:
        guard = self._lease_guard
        if (
            type(guard) is not EpochLeaseGuard
            or type(guard.store) is not OptimizationControllerStore
            or type(guard.claim) is not OptimizationEpochLeaseClaim
            or epoch != self._epoch
            or guard.claim.epoch_id != self._epoch.epoch_id
        ):
            raise TypeError(
                "shadow publication requires a bound EpochLeaseGuard"
            )
        return guard


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


_EPOCH_AUTHORIZER_FACTORY_TOKEN = object()
