"""运行固定的 SnapshotControl CI 稳定性哨兵。"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

SENTINEL_NODE = "tests/unit/test_lean_code_pr_review.py::test_closed_scope_blocks_risk_disposition_tamper[True--False]"
SENTINEL_ROUNDS = 5


def run_snapshot_control_sentinel(runner=subprocess.run) -> dict[str, object]:
    """执行固定节点，首个失败即停止并保留每次执行证据。"""
    command = [sys.executable, "-m", "pytest", "-q", "--no-cov", SENTINEL_NODE]
    attempts: list[dict[str, object]] = []

    for attempt in range(1, SENTINEL_ROUNDS + 1):
        result = runner(command)
        returncode = int(result.returncode)
        attempts.append(
            {
                "attempt": attempt,
                "command": command,
                "returncode": returncode,
            }
        )
        if returncode != 0:
            return {
                "attempts": attempts,
                "exit_code": returncode,
                "node": SENTINEL_NODE,
                "rounds": SENTINEL_ROUNDS,
                "success": False,
            }

    return {
        "attempts": attempts,
        "exit_code": 0,
        "node": SENTINEL_NODE,
        "rounds": SENTINEL_ROUNDS,
        "success": True,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)

    report = run_snapshot_control_sentinel()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        + "\n",
        encoding="utf-8",
    )
    return int(report["exit_code"])


if __name__ == "__main__":
    raise SystemExit(main())
