from ai_sdlc.core.stage_review.optimization.shadow_observations import (
    OptimizationShadowObservation,
)


class InMemoryShadowObservationStore:
    """统计单测使用的内存端口，不模拟生产可信发布边界。"""

    def __init__(self) -> None:
        self._values: dict[str, OptimizationShadowObservation] = {}

    def add(
        self,
        observation: OptimizationShadowObservation,
    ) -> OptimizationShadowObservation:
        existing = self._values.setdefault(
            observation.assignment_id,
            observation,
        )
        if existing != observation:
            raise ValueError("shadow fixture identity collided")
        return existing

    def read_assignment(
        self,
        assignment_id: str,
    ) -> OptimizationShadowObservation | None:
        return self._values.get(assignment_id)
