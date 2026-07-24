"""Stage Review 工件共享的兼容字段与深度不可变 JSON 载荷。"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from math import isfinite
from typing import (
    TYPE_CHECKING,
    Literal,
    NoReturn,
    Self,
    SupportsIndex,
    TypeAlias,
    TypeVar,
)

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from typing_extensions import TypeAliasType

from ai_sdlc.core.stage_review.canonical import (
    CanonicalizationPolicy,
    canonical_digest,
)

_ModelT = TypeVar("_ModelT", bound=BaseModel)
JsonPrimitive: TypeAlias = str | int | float | bool | None
if TYPE_CHECKING:
    JsonValue: TypeAlias = JsonPrimitive | dict[str, "JsonValue"] | list["JsonValue"]
else:
    JsonValue = TypeAliasType(
        "JsonValue",
        JsonPrimitive | dict[str, "JsonValue"] | list["JsonValue"],
    )


class FrozenJsonDict(dict[str, object]):
    """保持 JSON object 序列化形状，同时拒绝验证后的原地修改。"""

    def _immutable(self) -> NoReturn:
        raise TypeError("frozen JSON object does not support mutation")

    def __setitem__(self, _key: str, _value: object) -> None:
        self._immutable()

    def __delitem__(self, _key: str) -> None:
        self._immutable()

    def clear(self) -> None:
        self._immutable()

    def pop(self, _key: str, _default: object = None) -> object:
        self._immutable()

    def popitem(self) -> tuple[str, object]:
        self._immutable()

    def setdefault(self, _key: str, _default: object = None) -> object:
        self._immutable()

    def update(self, *_args: object, **_kwargs: object) -> None:
        self._immutable()

    def __ior__(self, _value: object) -> Self:  # type: ignore[misc, override]
        self._immutable()


class FrozenJsonArray(list[object]):
    """保持 JSON array 序列化形状，同时拒绝验证后的原地修改。"""

    def _immutable(self) -> NoReturn:
        raise TypeError("frozen JSON array does not support mutation")

    def __setitem__(self, _key: object, _value: object) -> None:
        self._immutable()

    def __delitem__(self, _key: object) -> None:
        self._immutable()

    def append(self, _value: object) -> None:
        self._immutable()

    def clear(self) -> None:
        self._immutable()

    def extend(self, _value: Iterable[object]) -> None:
        self._immutable()

    def insert(self, _index: SupportsIndex, _value: object) -> None:
        self._immutable()

    def pop(self, _index: SupportsIndex = -1) -> object:
        self._immutable()

    def remove(self, _value: object) -> None:
        self._immutable()

    def reverse(self) -> None:
        self._immutable()

    def sort(self, *_args: object, **_kwargs: object) -> None:
        self._immutable()

    def __iadd__(self, _value: Iterable[object]) -> Self:  # type: ignore[misc]
        self._immutable()

    def __imul__(self, _value: SupportsIndex) -> Self:
        self._immutable()


def freeze_json_mapping(value: Mapping[str, object]) -> dict[str, object]:
    validate_json_mapping(value)
    return FrozenJsonDict(
        {key: _freeze_json_value(item) for key, item in value.items()}
    )


def validate_json_mapping(value: object) -> None:
    if not isinstance(value, Mapping):
        raise ValueError("value must be a JSON object")
    _validate_json_value(value)


def _freeze_json_value(value: object) -> object:
    if isinstance(value, Mapping):
        return freeze_json_mapping(value)
    if isinstance(value, list):
        return FrozenJsonArray(_freeze_json_value(item) for item in value)
    return value


def _validate_json_value(value: object) -> None:
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if isfinite(value):
            return
        raise ValueError("JSON number must be finite")
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise ValueError("JSON object keys must be strings")
        for item in value.values():
            _validate_json_value(item)
        return
    if isinstance(value, list):
        for item in value:
            _validate_json_value(item)
        return
    raise ValueError("value must be recursive JSON data")


class ArtifactCompatibility(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    canonicalization_version: Literal["canonical-json.v1"] = "canonical-json.v1"
    compatibility_mode: Literal["strict", "read-only-legacy"] = "strict"
    extensions: dict[str, JsonValue] = Field(default_factory=dict)

    @field_validator("extensions", mode="before")
    @classmethod
    def _validate_extensions(cls, value: object) -> object:
        _validate_json_value(value)
        return value

    @model_validator(mode="after")
    def _freeze_extensions(self) -> Self:
        object.__setattr__(self, "extensions", freeze_json_mapping(self.extensions))
        return self


def fill_artifact_digest(value: _ModelT, digest_field: str) -> _ModelT:
    current = getattr(value, digest_field)
    if getattr(value, "compatibility_mode", "strict") == "read-only-legacy":
        extensions = getattr(value, "extensions", {})
        if current and extensions.get("source_digest") == current:
            return value
        raise ValueError(f"{digest_field} lacks verified legacy source digest")
    payload = value.model_dump(exclude={digest_field}, mode="json")
    expected = canonical_digest(payload, CanonicalizationPolicy())
    if current and current != expected:
        raise ValueError(f"{digest_field} does not match content")
    if not current:
        object.__setattr__(value, digest_field, expected)
    return value
