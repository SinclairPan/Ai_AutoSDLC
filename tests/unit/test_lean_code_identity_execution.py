"""可调用执行类型、完成状态和递归摘要的对抗回归。"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.unit.lean_identity_truth import assert_python_truth_and_lineage


@pytest.mark.parametrize(
    ("case_name", "helper_source", "caller_source"),
    (
        (
            "unconsumed-generator-function",
            "def invoke(callback):\n    yield callback()\n",
            """from src.api import build_value
from src.helpers import invoke

pending = invoke(build_value)
pending.close()
""",
        ),
        (
            "unawaited-coroutine-function",
            "async def invoke(callback):\n    return callback()\n",
            """from src.api import build_value
from src.helpers import invoke

pending = invoke(build_value)
pending.close()
""",
        ),
        (
            "unconsumed-async-generator-function",
            "async def invoke(callback):\n    yield callback()\n",
            """import asyncio
from src.api import build_value
from src.helpers import invoke

pending = invoke(build_value)
asyncio.run(pending.aclose())
""",
        ),
        (
            "raised-call-has-no-normal-product",
            """def invoke(callback):
    raise RuntimeError('stop')
    return (callback() for _ in [0])
""",
            """from src.api import build_value
from src.helpers import invoke

try:
    values = invoke(build_value)
except RuntimeError:
    values = iter(())
list(values)
""",
        ),
    ),
    ids=(
        "unconsumed-generator-function",
        "unawaited-coroutine-function",
        "unconsumed-async-generator-function",
        "raised-call-has-no-normal-product",
    ),
)
def test_callable_kind_and_completion_prevent_eager_execution(
    tmp_path: Path,
    case_name: str,
    helper_source: str,
    caller_source: str,
) -> None:
    del case_name
    assert_python_truth_and_lineage(
        tmp_path,
        supporting_files={"src/helpers.py": helper_source},
        caller_source=caller_source,
        expected_calls=0,
    )


@pytest.mark.parametrize(
    ("case_name", "helper_source", "caller_source"),
    (
        (
            "consumed-generator-function",
            "def invoke(callback):\n    yield callback()\n",
            """from src.api import build_value
from src.helpers import invoke

list(invoke(build_value))
""",
        ),
        (
            "awaited-coroutine-function",
            "async def invoke(callback):\n    return callback()\n",
            """import asyncio
from src.api import build_value
from src.helpers import invoke

async def main():
    await invoke(build_value)

asyncio.run(main())
""",
        ),
        (
            "consumed-async-generator-function",
            "async def invoke(callback):\n    yield callback()\n",
            """import asyncio
from src.api import build_value
from src.helpers import invoke

async def main():
    async for _ in invoke(build_value):
        pass

asyncio.run(main())
""",
        ),
    ),
    ids=(
        "consumed-generator-function",
        "awaited-coroutine-function",
        "consumed-async-generator-function",
    ),
)
def test_callable_body_executes_only_after_protocol_consumption(
    tmp_path: Path,
    case_name: str,
    helper_source: str,
    caller_source: str,
) -> None:
    del case_name
    assert_python_truth_and_lineage(
        tmp_path,
        supporting_files={"src/helpers.py": helper_source},
        caller_source=caller_source,
        expected_calls=1,
    )


@pytest.mark.parametrize(
    ("case_name", "caller_source", "expected_calls"),
    (
        (
            "default-captures-target-at-definition",
            """from src.api import build_value as invoke

def consume(callback=invoke):
    return callback()

invoke = lambda: 2
consume()
""",
            1,
        ),
        (
            "default-keeps-fake-after-target-rebind",
            """invoke = lambda: 2

def consume(callback=invoke):
    return callback()

from src.api import build_value as invoke
consume()
""",
            0,
        ),
        (
            "recursive-fixed-point-beyond-depth-eight",
            """def outer():
    import importlib as loader
    def callback():
        return loader.import_module('src.api').build_value()
    def invoke(function, depth):
        if depth:
            return invoke(function, depth - 1)
        return function()
    return invoke(callback, 8)

outer()
""",
            1,
        ),
    ),
    ids=(
        "default-captures-target-at-definition",
        "default-keeps-fake-after-target-rebind",
        "recursive-fixed-point-beyond-depth-eight",
    ),
)
def test_callable_defaults_and_recursion_follow_python_semantics(
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
    ("case_name", "caller_source"),
    (
        (
            "functools-wraps-decorator",
            """from functools import wraps
from src.api import build_value

def preserve(function):
    @wraps(function)
    def wrapper(*args, **kwargs):
        return function(*args, **kwargs)
    return wrapper

@preserve
def consume(items):
    return list(items)

values = (build_value() for _ in [0])
consume(values)
""",
        ),
        (
            "callable-object-consumer",
            """from src.api import build_value

class Consumer:
    def __call__(self, items):
        return list(items)

values = (build_value() for _ in [0])
Consumer()(values)
""",
        ),
        (
            "functools-partial-consumer",
            """from functools import partial
from src.api import build_value
from src.consumer import consume

values = (build_value() for _ in [0])
partial(consume, values)()
""",
        ),
    ),
    ids=(
        "functools-wraps-decorator",
        "callable-object-consumer",
        "functools-partial-consumer",
    ),
)
def test_common_callable_wrappers_preserve_consumption(
    tmp_path: Path,
    case_name: str,
    caller_source: str,
) -> None:
    del case_name
    assert_python_truth_and_lineage(
        tmp_path,
        supporting_files={
            "src/consumer.py": "def consume(items):\n    return list(items)\n"
        },
        caller_source=caller_source,
        expected_calls=1,
    )
