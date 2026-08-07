"""Aggregate POSIX release smoke verdicts with an authoritative jobs fallback."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

_PLATFORMS = ("macos", "linux")
_VALID_VERDICTS = {"passed", "failed", "unavailable"}
_SMOKE_STEP = "Install release tar and run CLI smoke"
_RECORD_STEP = "Record explicit release tar smoke verdict"
_ENFORCE_STEP = "Enforce explicit release tar smoke failure"


class VerdictAggregationError(ValueError):
    """Verdict evidence is malformed or ambiguous."""


def _read_artifact_verdict(root: Path, platform: str) -> str | None:
    path = root / f"{platform}.txt"
    if not path.is_file():
        return None
    verdict = path.read_text(encoding="utf-8").strip()
    if verdict not in _VALID_VERDICTS:
        raise VerdictAggregationError(f"invalid {platform} artifact verdict")
    return verdict


def _read_jobs(path: Path) -> list[dict[str, Any]]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise VerdictAggregationError("cannot read workflow jobs fallback") from exc
    pages = raw if isinstance(raw, list) else [raw]
    jobs: list[dict[str, Any]] = []
    for page in pages:
        if not isinstance(page, dict) or not isinstance(page.get("jobs"), list):
            raise VerdictAggregationError("workflow jobs fallback shape is invalid")
        for job in page["jobs"]:
            if isinstance(job, dict):
                jobs.append(job)
    return jobs


def _job_verdict(jobs: Sequence[dict[str, Any]], platform: str) -> str:
    expected_name = f"{platform} tar.gz"
    matches = [job for job in jobs if job.get("name") == expected_name]
    if len(matches) != 1:
        raise VerdictAggregationError(
            f"expected one authoritative {platform} smoke job, found {len(matches)}"
        )
    raw_steps = matches[0].get("steps")
    if not isinstance(raw_steps, list):
        raise VerdictAggregationError(f"{platform} smoke job steps are invalid")
    conclusions = {
        str(step.get("name")): str(step.get("conclusion") or "")
        for step in raw_steps
        if isinstance(step, dict)
    }
    if conclusions.get(_RECORD_STEP) != "success":
        return "unavailable"
    if conclusions.get(_ENFORCE_STEP) == "failure":
        return "failed"
    if (
        conclusions.get(_SMOKE_STEP) == "success"
        and conclusions.get(_ENFORCE_STEP) == "skipped"
    ):
        return "passed"
    return "unavailable"


def aggregate_verdicts(artifact_root: Path, jobs_json: Path) -> dict[str, str]:
    """Prefer independent artifacts and recover missing verdicts from run metadata."""

    jobs: list[dict[str, Any]] | None = None
    verdicts: dict[str, str] = {}
    for platform in _PLATFORMS:
        verdict = _read_artifact_verdict(artifact_root, platform)
        if verdict is None:
            jobs = jobs if jobs is not None else _read_jobs(jobs_json)
            verdict = _job_verdict(jobs, platform)
        verdicts[platform] = verdict
    return verdicts


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--jobs-json", type=Path, required=True)
    parser.add_argument("--github-output", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        verdicts = aggregate_verdicts(args.artifact_root, args.jobs_json)
    except VerdictAggregationError as exc:
        print(str(exc))
        return 1
    with args.github_output.open("a", encoding="utf-8") as output:
        for platform in _PLATFORMS:
            output.write(f"{platform}_smoke_verdict={verdicts[platform]}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
