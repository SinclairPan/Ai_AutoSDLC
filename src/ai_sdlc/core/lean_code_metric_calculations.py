"""Low-level deterministic calculations used by Lean file metrics."""

from __future__ import annotations

import ast
import difflib
from collections import defaultdict

from ai_sdlc.core.lean_code_models import FileMetric, FunctionMetric


def _mark_duplicate_candidates(files: list[FileMetric]) -> list[str]:
    groups: dict[str, list[tuple[str, FunctionMetric]]] = defaultdict(list)
    for file in files:
        for function in file.functions:
            if function.fingerprint:
                groups[function.fingerprint].append((file.path, function))
    candidates: list[str] = []
    for fingerprint, members in sorted(groups.items()):
        if len(members) < 2:
            continue
        for _path, function in members:
            function.duplicate_count = len(members)
        locations = ",".join(sorted(f"{path}:{item.symbol}" for path, item in members))
        candidates.append(f"{fingerprint}:{locations}")
    return candidates


def _complexity(node: ast.AST | None) -> int:
    if node is None:
        return 0
    value = 1
    for child in ast.walk(node):
        if isinstance(
            child, (ast.If, ast.For, ast.AsyncFor, ast.While, ast.IfExp, ast.Match)
        ):
            value += 1
        elif isinstance(child, ast.BoolOp):
            value += max(len(child.values) - 1, 1)
        elif isinstance(child, ast.Try):
            value += len(child.handlers) + bool(child.orelse) + bool(child.finalbody)
    return value


def _max_nesting(node: ast.AST | None, depth: int = 0) -> int:
    if node is None:
        return 0
    branch = isinstance(
        node, (ast.If, ast.For, ast.AsyncFor, ast.While, ast.Try, ast.Match)
    )
    current = depth + int(branch)
    return max(
        [
            current,
            *(_max_nesting(child, current) for child in ast.iter_child_nodes(node)),
        ]
    )


def _import_fan_out(tree: ast.AST | None) -> int:
    if tree is None:
        return 0
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module.split(".")[0])
    return len(modules)


def _parse_python(source: str, path: str) -> tuple[ast.Module | None, str]:
    if not source:
        return ast.Module(body=[], type_ignores=[]), ""
    try:
        return ast.parse(source, filename=path), ""
    except SyntaxError as exc:
        return None, f"python_parse_error:{exc.msg}:{exc.lineno}"


def _line_delta(before: str, after: str) -> tuple[int, int]:
    matcher = difflib.SequenceMatcher(
        a=before.splitlines(), b=after.splitlines(), autojunk=False
    )
    added = deleted = 0
    for tag, start_a, end_a, start_b, end_b in matcher.get_opcodes():
        if tag in {"insert", "replace"}:
            added += end_b - start_b
        if tag in {"delete", "replace"}:
            deleted += end_a - start_a
    return added, deleted


def _decode_source(payload: bytes) -> tuple[str, str]:
    try:
        return payload.decode("utf-8", errors="strict"), ""
    except UnicodeDecodeError as exc:
        return "", f"utf8_decode_error:{exc.start}"


def _span(node: ast.AST | None) -> int:
    if node is None or not hasattr(node, "lineno"):
        return 0
    start = int(node.lineno)
    return max(int(getattr(node, "end_lineno", start)) - start + 1, 0)


__all__: list[str] = []
