"""表达式状态、模式匹配与内建参数角色的对抗回归。"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.unit.lean_identity_truth import assert_python_truth_and_lineage


@pytest.mark.parametrize(
    ("case_name", "caller_source", "expected_calls"),
    (
        (
            "if-expression-commits-target-rebind",
            """from src.api import build_value
flag = object()
values = (0 for _ in [0])
(values := (build_value() for _ in [0])) if flag else None
next(values)
""",
            1,
        ),
        (
            "if-expression-commits-safe-rebind",
            """from src.api import build_value
flag = object()
values = (build_value() for _ in [0])
(values := (0 for _ in [0])) if flag else None
next(values)
""",
            0,
        ),
        (
            "bool-op-uses-evaluated-false-value",
            """from src.api import build_value
flag = False
values = (build_value() for _ in [0])
flag and next(values)
""",
            0,
        ),
        (
            "comprehension-commits-target-walrus",
            """from src.api import build_value
values = (0 for _ in [0])
[(values := (build_value() for _ in [0])) for _ in [0]]
next(values)
""",
            1,
        ),
        (
            "comprehension-commits-safe-walrus",
            """from src.api import build_value
values = (build_value() for _ in [0])
[(values := (0 for _ in [0])) for _ in [0]]
next(values)
""",
            0,
        ),
    ),
    ids=(
        "if-expression-commits-target-rebind",
        "if-expression-commits-safe-rebind",
        "bool-op-uses-evaluated-false-value",
        "comprehension-commits-target-walrus",
        "comprehension-commits-safe-walrus",
    ),
)
def test_expression_transfer_commits_only_executed_state(
    tmp_path: Path,
    case_name: str,
    caller_source: str,
    expected_calls: int,
) -> None:
    del case_name
    assert_python_truth_and_lineage(
        tmp_path,
        caller_source=caller_source,
        expected_calls=expected_calls,
    )


@pytest.mark.parametrize(
    ("case_name", "caller_source", "expected_calls"),
    (
        (
            "nested-dict-unpack-keeps-items",
            """from src.api import build_value
from src.consumer import consume

values = (build_value() for _ in [0])
consume(**{**{'items': values}})
""",
            1,
        ),
        (
            "dict-keyword-does-not-iterate-value",
            """from src.api import build_value
values = (build_value() for _ in [0])
dict(items=values)
""",
            0,
        ),
        (
            "next-default-is-not-consumed",
            """from src.api import build_value
values = (build_value() for _ in [0])
next(iter([1]), values)
""",
            0,
        ),
    ),
    ids=(
        "nested-dict-unpack-keeps-items",
        "dict-keyword-does-not-iterate-value",
        "next-default-is-not-consumed",
    ),
)
def test_builtin_and_mapping_arguments_follow_runtime_roles(
    tmp_path: Path,
    case_name: str,
    caller_source: str,
    expected_calls: int,
) -> None:
    del case_name
    assert_python_truth_and_lineage(
        tmp_path,
        supporting_files={
            "src/consumer.py": "def consume(*, items):\n    return list(items)\n"
        },
        caller_source=caller_source,
        expected_calls=expected_calls,
    )


@pytest.mark.parametrize(
    ("case_name", "caller_source", "expected_calls"),
    (
        (
            "mapping-pattern-captures-target",
            """from src.api import build_value
values = (build_value() for _ in [0])
match {'items': values}:
    case {'items': captured}:
        list(captured)
""",
            1,
        ),
        (
            "impossible-mapping-pattern-is-not-executed",
            """from src.api import build_value
values = (build_value() for _ in [0])
match {'other': 1}:
    case {'items': captured}:
        list(values)
""",
            0,
        ),
    ),
    ids=(
        "mapping-pattern-captures-target",
        "impossible-mapping-pattern-is-not-executed",
    ),
)
def test_match_mapping_uses_subject_keys_and_captures(
    tmp_path: Path,
    case_name: str,
    caller_source: str,
    expected_calls: int,
) -> None:
    del case_name
    assert_python_truth_and_lineage(
        tmp_path,
        caller_source=caller_source,
        expected_calls=expected_calls,
    )


@pytest.mark.parametrize(
    ("case_name", "safe_expression"),
    (
        ("constant-binop", "marker = 1 + 2"),
        ("safe-len", "marker = len(())"),
    ),
)
def test_safe_expression_does_not_invent_exception_handler(
    tmp_path: Path,
    case_name: str,
    safe_expression: str,
) -> None:
    del case_name
    assert_python_truth_and_lineage(
        tmp_path,
        caller_source=f"""from src.api import build_value
values = (build_value() for _ in [0])
try:
    {safe_expression}
except Exception:
    list(values)
""",
        expected_calls=0,
    )


def test_state_cap_join_keeps_possible_target_lineage(tmp_path: Path) -> None:
    flags = "".join(
        f"unknown_{index} = bool(int('0'))\n" for index in range(10)
    )
    branches = "".join(
        (
            f"if unknown_{index}:\n"
            "    values = safe\n"
            f"    alias_{index} = values\n"
        )
        for index in range(10)
    )
    assert_python_truth_and_lineage(
        tmp_path,
        caller_source=(
            "from src.api import build_value\n"
            "values = (build_value() for _ in [0])\n"
            "safe = (0 for _ in [0])\n"
            + flags
            + branches
            + "list(values)\n"
        ),
        expected_calls=1,
    )
