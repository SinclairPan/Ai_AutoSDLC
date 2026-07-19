"""Canonical identifiers for Lean Code artifact path segments."""

from __future__ import annotations

import re

_SAFE_ARTIFACT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_WINDOWS_DEVICE_NAMES = {
    "AUX",
    "CLOCK$",
    "CON",
    "NUL",
    "PRN",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}


def is_safe_artifact_id(value: str) -> bool:
    """Return whether a value is one portable, non-traversing path segment."""
    if not _SAFE_ARTIFACT_ID.fullmatch(value) or value.endswith((".", " ")):
        return False
    return value.split(".", 1)[0].upper() not in _WINDOWS_DEVICE_NAMES


__all__ = ["is_safe_artifact_id"]
