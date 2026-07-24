"""re-export 增量固定点的正确性与扫描上界。"""

from __future__ import annotations

from collections.abc import Callable
from typing import cast

import pytest

import ai_sdlc.core.lean_code_caller_binding_resolution as binding_resolution
import ai_sdlc.core.lean_code_caller_evidence as caller_evidence
import ai_sdlc.core.lean_code_caller_module_state as module_state
import ai_sdlc.core.lean_code_caller_scope_semantics as scope_semantics
import ai_sdlc.core.lean_code_caller_target_semantics as target_semantics
from ai_sdlc.core.lean_code_caller_models import _CallableNode


def test_callable_reexport_worklist_scans_only_affected_modules(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    depth = 40
    sources = {
        "src/consumer.py": f"from src.layer{depth} import build\nbuild()\n".encode(),
        **{
            f"src/layer{index}.py": (
                "def build():\n    return 1\n"
                if index == 0
                else f"from src.layer{index - 1} import build\n"
            ).encode()
            for index in range(depth, -1, -1)
        },
    }
    parsed = caller_evidence._parsed_product_sources(sources)
    scans = 0
    original = cast(
        Callable[..., dict[str, _CallableNode]],
        module_state._module_reexported_callables,
    )

    def counted(*args: object, **kwargs: object) -> dict[str, _CallableNode]:
        nonlocal scans
        scans += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(module_state, "_module_reexported_callables", counted)
    imported = target_semantics._imported_callable_index(parsed)["src/consumer.py"]

    assert imported["build"].target_key == ("src/layer0.py", "build")
    assert scans <= 3 * len(sources)


def test_target_alias_worklist_converges_through_long_cycle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    depth = 30
    sources = {
        "src/base.py": b"def build():\n    return 1\n",
        **{
            f"src/facade{index}.py": (
                "from src.base import build as exported\n"
                if index == 0
                else f"from src.facade{index - 1} import exported\n"
            ).encode()
            for index in range(depth)
        },
        "src/cycle.py": f"from src.facade{depth - 1} import exported\n".encode(),
    }
    parsed = caller_evidence._parsed_product_sources(sources)
    candidates = module_state._reexport_candidate_index(parsed)
    scans = 0
    original = cast(
        Callable[..., set[str]],
        scope_semantics._resolved_reexport_aliases,
    )

    def counted(*args: object, **kwargs: object) -> set[str]:
        nonlocal scans
        scans += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(scope_semantics, "_resolved_reexport_aliases", counted)
    exports = binding_resolution._target_reexports(
        parsed, candidates, ("src/base.py", "build")
    )

    assert exports["src/cycle.py"] == frozenset({"exported"})
    assert scans <= 3 * len(sources)
