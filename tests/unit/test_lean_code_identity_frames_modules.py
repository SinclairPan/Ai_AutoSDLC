"""帧、闭包单元和模块环境的对抗回归。"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.unit.lean_identity_truth import assert_python_truth_and_lineage


@pytest.mark.parametrize(
    ("case_name", "caller_source", "expected_calls"),
    (
        (
            "closure-does-not-read-caller-frame",
            """def make():
    import importlib as loader
    def callback():
        return loader.import_module('src.api').build_value()
    return callback

callback = make()
loader = object()
callback()
""",
            1,
        ),
        (
            "caller-frame-does-not-repair-fake-closure",
            """def make():
    loader = object()
    def callback():
        return loader.import_module('src.api').build_value()
    return callback

callback = make()
import importlib as loader
try:
    callback()
except AttributeError:
    pass
""",
            0,
        ),
        (
            "closure-cell-observes-late-fake-rebind",
            """def outer():
    from src.api import build_value as fn
    callback = lambda: fn()
    fn = lambda: 2
    return callback

outer()()
""",
            0,
        ),
        (
            "closure-cell-observes-late-target-rebind",
            """def outer():
    fn = lambda: 2
    callback = lambda: fn()
    from src.api import build_value as fn
    return callback

outer()()
""",
            1,
        ),
    ),
    ids=(
        "closure-does-not-read-caller-frame",
        "caller-frame-does-not-repair-fake-closure",
        "closure-cell-observes-late-fake-rebind",
        "closure-cell-observes-late-target-rebind",
    ),
)
def test_closure_uses_lexical_cells_not_caller_values(
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


def test_imported_global_effect_stays_in_owner_module(tmp_path: Path) -> None:
    assert_python_truth_and_lineage(
        tmp_path,
        supporting_files={
            "src/helpers.py": """values = iter(())

def reset():
    global values
    values = iter(())
"""
        },
        caller_source="""from src.api import build_value
from src.helpers import reset

values = (build_value() for _ in [0])
reset()
list(values)
""",
        expected_calls=1,
    )


@pytest.mark.parametrize(
    ("case_name", "helper_source", "caller_source", "expected_calls"),
    (
        (
            "dotted-module-hierarchy",
            "def invoke(callback):\n    return callback()\n",
            """import src.helpers

def outer():
    import importlib as loader
    def callback():
        return loader.import_module('src.api').build_value()
    return src.helpers.invoke(callback)

outer()
""",
            1,
        ),
        (
            "module-global-controls-callable",
            """ENABLED = False

def invoke(callback):
    if ENABLED:
        return callback()
    return None
""",
            """from src.api import build_value
from src.helpers import invoke

invoke(build_value)
""",
            0,
        ),
        (
            "statement-qualified-conditional-import",
            "def consume(items):\n    return list(items)\n",
            """from src.api import build_value
if True:
    from src.helpers import consume
else:
    from src.noop import consume

values = (build_value() for _ in [0])
consume(values)
""",
            1,
        ),
        (
            "unaliased-dotted-consumer",
            "def consume(items):\n    return list(items)\n",
            """from src.api import build_value
import src.helpers

values = (build_value() for _ in [0])
src.helpers.consume(values)
""",
            1,
        ),
    ),
    ids=(
        "dotted-module-hierarchy",
        "module-global-controls-callable",
        "statement-qualified-conditional-import",
        "unaliased-dotted-consumer",
    ),
)
def test_imports_preserve_canonical_module_environment(
    tmp_path: Path,
    case_name: str,
    helper_source: str,
    caller_source: str,
    expected_calls: int,
) -> None:
    del case_name
    assert_python_truth_and_lineage(
        tmp_path,
        supporting_files={
            "src/helpers.py": helper_source,
            "src/noop.py": "def consume(items):\n    return None\n",
        },
        caller_source=caller_source,
        expected_calls=expected_calls,
    )
