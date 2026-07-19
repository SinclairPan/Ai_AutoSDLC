"""Disable repository-defined content filters during read-only source capture."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

_FILTER_KEY = re.compile(r"^filter\.(.+)\.(?:clean|process|required)$")
_GIT_TIMEOUT_SECONDS = 30


def external_filter_overrides(root: Path) -> tuple[str, ...]:
    """Return Git config arguments that prevent project filter execution."""
    try:
        result = subprocess.run(
            [
                "git",
                "config",
                "--name-only",
                "--get-regexp",
                r"^filter\..*\.(clean|process|required)$",
            ],
            cwd=root,
            capture_output=True,
            check=False,
            timeout=_GIT_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise ValueError("git filter discovery timed out") from exc
    except OSError as exc:
        raise ValueError(f"git filter discovery is unavailable: {exc}") from exc
    if result.returncode not in {0, 1}:
        message = result.stderr.decode("utf-8", errors="replace").strip()
        raise ValueError(f"git filter discovery failed: {message}")
    drivers: set[str] = set()
    for raw_key in result.stdout.decode("utf-8", errors="strict").splitlines():
        match = _FILTER_KEY.fullmatch(raw_key.strip())
        if match:
            drivers.add(match.group(1))
    arguments: list[str] = []
    for driver in sorted(drivers):
        arguments.extend(
            (
                "-c",
                f"filter.{driver}.clean=",
                "-c",
                f"filter.{driver}.process=",
                "-c",
                f"filter.{driver}.required=false",
            )
        )
    return tuple(arguments)


__all__ = ["external_filter_overrides"]
