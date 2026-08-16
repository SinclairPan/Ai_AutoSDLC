"""Negative architecture contract for the minimal review kernel."""

from __future__ import annotations

import ast
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_KERNEL = _ROOT / "src" / "ai_sdlc" / "core" / "review_kernel.py"


def test_review_kernel_has_no_runtime_or_persistence_dependencies() -> None:
    tree = ast.parse(_KERNEL.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)

    forbidden_fragments = {
        "loop_artifacts",
        "requirement_loop",
        "design_contract_loop",
        "implementation_loop",
        "frontend_evidence_loop",
        "pr_review_service",
        "stage_review",
        "lean_code",
        "subprocess",
        "urllib",
        "requests",
        "provider",
        "model",
    }
    assert not {
        dependency
        for dependency in imported
        if any(fragment in dependency for fragment in forbidden_fragments)
    }


def test_review_kernel_exposes_no_close_or_persistence_symbols() -> None:
    tree = ast.parse(_KERNEL.read_text(encoding="utf-8"))
    public_symbols = {
        node.name
        for node in tree.body
        if isinstance(node, (ast.ClassDef, ast.FunctionDef))
        and not node.name.startswith("_")
    }

    forbidden_fragments = {
        "close",
        "persist",
        "store",
        "save",
        "record",
        "authorize",
        "certificate",
        "attest",
        "session",
    }
    assert not {
        symbol
        for symbol in public_symbols
        if any(fragment in symbol.lower() for fragment in forbidden_fragments)
    }
