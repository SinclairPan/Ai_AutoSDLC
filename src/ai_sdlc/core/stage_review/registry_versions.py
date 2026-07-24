"""Registry 使用的稳定身份、日期与版本范围规则。"""

from __future__ import annotations

import re
from datetime import date

_MACHINE_ID = re.compile(r"^[a-z][a-z0-9]*(?:[._/-][a-z0-9]+)*$")
_VERSION = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
_RANGE_PART = re.compile(
    r"^(>=|<=|==|>|<)(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$"
)


def require_machine_id(value: str, label: str, *, optional: bool = False) -> str:
    text = value.strip()
    if not text and optional:
        return ""
    if not _MACHINE_ID.fullmatch(text):
        raise ValueError(f"{label} must be a stable machine identity")
    return text


def normalize_machine_ids(value: object) -> list[str]:
    return sorted(
        {
            require_machine_id(str(item), "machine identity")
            for item in normalize_collection(value)
        }
    )


def normalize_text_set(value: object) -> list[str]:
    return sorted({str(item).strip() for item in normalize_collection(value)})


def normalize_collection(value: object) -> list[object]:
    if not isinstance(value, (list, tuple, set, frozenset)):
        raise ValueError("value must be a collection")
    return list(value)


def require_version(value: str) -> str:
    text = value.strip()
    if not _VERSION.fullmatch(text):
        raise ValueError("version must use MAJOR.MINOR.PATCH")
    return text


def require_iso_date(value: str) -> str:
    text = value.strip()
    try:
        parsed = date.fromisoformat(text)
    except ValueError as exc:
        raise ValueError("review_date must use YYYY-MM-DD") from exc
    if parsed.isoformat() != text:
        raise ValueError("review_date must use YYYY-MM-DD")
    return text


def validate_version_range(value: str) -> None:
    parts = [item.strip() for item in value.split(",") if item.strip()]
    if not parts or any(not _RANGE_PART.fullmatch(item) for item in parts):
        raise ValueError("compatibility range is invalid")


def version_in_range(version: str, value: str) -> bool:
    current = _version_tuple(require_version(version))
    validate_version_range(value)
    return all(_matches(current, item.strip()) for item in value.split(","))


def _version_tuple(value: str) -> tuple[int, int, int]:
    major, minor, patch = value.split(".")
    return int(major), int(minor), int(patch)


def _matches(current: tuple[int, int, int], expression: str) -> bool:
    match = _RANGE_PART.fullmatch(expression)
    if match is None:  # pragma: no cover - validate_version_range 已先验证。
        return False
    target = (int(match[2]), int(match[3]), int(match[4]))
    return {
        ">=": current >= target,
        "<=": current <= target,
        "==": current == target,
        ">": current > target,
        "<": current < target,
    }[match[1]]
