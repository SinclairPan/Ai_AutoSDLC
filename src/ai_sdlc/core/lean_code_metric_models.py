"""Lean Code 的版本化度量值对象。"""

from __future__ import annotations

from enum import StrEnum
from typing import ClassVar, Literal, cast

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SerializerFunctionWrapHandler,
    model_serializer,
)

from ai_sdlc.core.loop_models import LoopArtifactModel

LEAN_ARTIFACT_SCHEMA_VERSION = "2"
V2_FUNCTION_METRIC_FIELDS = frozenset(
    {
        "base_capability",
        "base_import_fan_out",
        "callable_contract",
        "contract_evidence",
        "import_fan_out",
    }
)


class LeanVersionedArtifact(LoopArtifactModel):
    """Lean artifact codec that preserves v1 reads and emits explicit v2 writes."""

    schema_version: str = LEAN_ARTIFACT_SCHEMA_VERSION
    supported_schema_versions: ClassVar[frozenset[str]] = frozenset({"1", "2"})


class FileClassification(StrEnum):
    """Classification applied before maintainability budgets are interpreted."""

    HANDWRITTEN_PRODUCT = "handwritten_product"
    HANDWRITTEN_TEST = "handwritten_test"
    GENERATED = "generated"
    FIXTURE = "fixture"
    VENDORED = "vendored"
    SNAPSHOT = "snapshot"
    DECLARATIVE = "declarative"
    UNKNOWN = "unknown"


class MetricCapability(StrEnum):
    """Confidence contract for one language or metric adapter."""

    EXACT = "exact"
    CONSERVATIVE = "conservative"
    UNSUPPORTED = "unsupported"


class FunctionMetric(BaseModel):
    """Deterministic measurements for one source function."""

    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    symbol: str
    logical_lines: int = Field(ge=0)
    base_logical_lines: int = Field(default=0, ge=0)
    complexity: int = Field(default=0, ge=0)
    base_complexity: int = Field(default=0, ge=0)
    max_nesting: int = Field(default=0, ge=0)
    base_max_nesting: int = Field(default=0, ge=0)
    import_fan_out: int = Field(default=0, ge=0, exclude_if=lambda value: value == 0)
    base_import_fan_out: int = Field(
        default=0, ge=0, exclude_if=lambda value: value == 0
    )
    caller_count: int = Field(default=0, ge=0)
    caller_evidence: list[str] = Field(
        default_factory=list, exclude_if=lambda value: not value
    )
    public: bool = False
    is_new: bool = False
    capability: MetricCapability = MetricCapability.UNSUPPORTED
    base_capability: MetricCapability = Field(
        default=MetricCapability.UNSUPPORTED,
        exclude_if=lambda value: value == MetricCapability.UNSUPPORTED,
    )
    callable_contract: str = Field(default="", exclude_if=lambda value: not value)
    contract_evidence: list[str] = Field(
        default_factory=list, exclude_if=lambda value: not value
    )
    binding_state: Literal["exact", "plausible", "disproven"] = "disproven"
    execution_state: Literal[
        "executed", "contractual", "referenced_only", "unreachable", "unknown"
    ] = "unreachable"
    invocation_boundary: str = ""
    invocation_evidence: list[str] = Field(
        default_factory=list, exclude_if=lambda value: not value
    )
    reference_evidence: list[str] = Field(
        default_factory=list, exclude_if=lambda value: not value
    )
    unlinked_evidence: list[str] = Field(
        default_factory=list, exclude_if=lambda value: not value
    )
    fingerprint: str = ""
    duplicate_count: int = Field(default=1, ge=1)

    @model_serializer(mode="wrap")
    def _preserve_legacy_field_presence(
        self,
        handler: SerializerFunctionWrapHandler,
    ) -> dict[str, object]:
        payload = cast(dict[str, object], handler(self))
        for field_name in ("binding_state", "execution_state"):
            if field_name not in self.model_fields_set:
                payload.pop(field_name, None)
        return payload


class FileMetric(BaseModel):
    """Classification, diff, size, and semantic metrics for one changed file."""

    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    path: str
    classification: FileClassification
    language: str = "unknown"
    capability: MetricCapability = MetricCapability.UNSUPPORTED
    base_lines: int = Field(default=0, ge=0)
    head_lines: int = Field(default=0, ge=0)
    added_lines: int = Field(default=0, ge=0)
    deleted_lines: int = Field(default=0, ge=0)
    import_fan_out: int = Field(default=0, ge=0)
    base_import_fan_out: int = Field(default=0, ge=0)
    functions: list[FunctionMetric] = Field(default_factory=list)
    parse_errors: list[str] = Field(default_factory=list)


__all__ = [
    "FileClassification",
    "FileMetric",
    "FunctionMetric",
    "LEAN_ARTIFACT_SCHEMA_VERSION",
    "LeanVersionedArtifact",
    "MetricCapability",
    "V2_FUNCTION_METRIC_FIELDS",
]
