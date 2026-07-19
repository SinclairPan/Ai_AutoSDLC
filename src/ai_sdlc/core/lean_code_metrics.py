"""Deterministic file classification and Python AST metrics for Lean Code."""

from __future__ import annotations

import ast
import difflib
import fnmatch
import hashlib
from collections import Counter, defaultdict
from pathlib import Path

from ai_sdlc.core.lean_code_callers import attach_python_callers
from ai_sdlc.core.lean_code_classification import classify_file
from ai_sdlc.core.lean_code_dynamic_refs import _invocation_boundary
from ai_sdlc.core.lean_code_models import (
    FileClassification,
    FileMetric,
    FunctionMetric,
    LeanMetrics,
    MetricCapability,
)
from ai_sdlc.core.source_snapshot import SourceSnapshot
from ai_sdlc.core.source_snapshot_view import lean_metric_source


def collect_lean_metrics(
    root: Path,
    snapshot: SourceSnapshot,
    declared_scope: tuple[str, ...],
) -> LeanMetrics:
    """Collect repeatable metrics without treating unsupported syntax as zero risk."""

    with lean_metric_source(root, snapshot) as (versions, source_loader):
        files = [
            _file_metric(
                path,
                *versions[path],
                is_deleted=path in snapshot.deleted_files,
                is_binary=path in snapshot.binary_files,
            )
            for path in snapshot.changed_files
        ]
        duplicate_candidates = _mark_duplicate_candidates(files)
        attach_python_callers(
            root,
            snapshot,
            files,
            source_loader=source_loader,
        )
    return _assemble_metrics(files, duplicate_candidates, snapshot, declared_scope)


def _assemble_metrics(
    files: list[FileMetric],
    duplicate_candidates: list[str],
    snapshot: SourceSnapshot,
    declared_scope: tuple[str, ...],
) -> LeanMetrics:
    counts = Counter(str(item.classification) for item in files)
    product = [
        item
        for item in files
        if item.classification == FileClassification.HANDWRITTEN_PRODUCT
    ]
    tests = [
        item
        for item in files
        if item.classification == FileClassification.HANDWRITTEN_TEST
    ]
    return LeanMetrics(
        product_added_lines=sum(item.added_lines for item in product),
        product_deleted_lines=sum(item.deleted_lines for item in product),
        product_net_lines=sum(
            item.added_lines - item.deleted_lines for item in product
        ),
        test_added_lines=sum(item.added_lines for item in tests),
        test_deleted_lines=sum(item.deleted_lines for item in tests),
        test_net_lines=sum(item.added_lines - item.deleted_lines for item in tests),
        new_file_count=sum(item.is_new for item in files),
        changed_file_count=len(files),
        classification_counts=dict(sorted(counts.items())),
        unknown_files=[
            item.path
            for item in files
            if item.classification == FileClassification.UNKNOWN
        ],
        unsupported_semantic_files=_unsupported_semantic_files(files),
        duplicate_candidates=duplicate_candidates,
        scope_drift=[
            path
            for path in snapshot.changed_files
            if not _in_scope(path, declared_scope)
        ],
        files=files,
    )


def _unsupported_semantic_files(files: list[FileMetric]) -> list[str]:
    return [
        item.path
        for item in files
        if item.classification == FileClassification.HANDWRITTEN_PRODUCT
        and item.capability == MetricCapability.UNSUPPORTED
    ]


def _file_metric(
    path: str,
    before: bytes,
    after: bytes,
    *,
    is_deleted: bool,
    is_binary: bool,
) -> FileMetric:
    provenance = before if is_deleted else after
    classification = classify_file(path, provenance, is_binary)
    before_text, before_error = _decode_source(before)
    after_text, after_error = _decode_source(after)
    added_lines, deleted_lines = _line_delta(before_text, after_text)
    language = _language(path)
    functions: list[FunctionMetric] = []
    fan_out = base_fan_out = 0
    errors = [item for item in (before_error, after_error) if item]
    capability = MetricCapability.UNSUPPORTED
    if language == "python" and classification in {
        FileClassification.HANDWRITTEN_PRODUCT,
        FileClassification.HANDWRITTEN_TEST,
    }:
        functions, fan_out, base_fan_out, parse_errors = _python_metrics(
            before_text, after_text, path
        )
        errors.extend(parse_errors)
        capability = (
            MetricCapability.EXACT if not parse_errors else MetricCapability.UNSUPPORTED
        )
    elif classification not in {
        FileClassification.UNKNOWN,
        FileClassification.HANDWRITTEN_PRODUCT,
    }:
        capability = MetricCapability.CONSERVATIVE
    return FileMetric(
        path=path,
        classification=classification,
        language=language,
        capability=capability,
        base_lines=len(before_text.splitlines()),
        head_lines=len(after_text.splitlines()),
        added_lines=added_lines,
        deleted_lines=deleted_lines,
        import_fan_out=fan_out,
        base_import_fan_out=base_fan_out,
        functions=functions,
        parse_errors=errors,
    )


def _python_metrics(
    before: str,
    after: str,
    path: str,
) -> tuple[list[FunctionMetric], int, int, list[str]]:
    base_tree, base_error = _parse_python(before, path)
    head_tree, head_error = _parse_python(after, path)
    errors = [item for item in (base_error, head_error) if item]
    if head_tree is None:
        return [], 0, 0, errors
    base_functions = _function_nodes(base_tree) if base_tree else {}
    functions = [
        _function_metric(symbol, node, base_functions.get(symbol))
        for symbol, node in _function_nodes(head_tree).items()
    ]
    return functions, _import_fan_out(head_tree), _import_fan_out(base_tree), errors


def _function_nodes(
    tree: ast.AST | None,
) -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef]:
    if tree is None:
        return {}
    found: dict[str, ast.FunctionDef | ast.AsyncFunctionDef] = {}
    for node in tree.body if isinstance(tree, ast.Module) else []:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            found[node.name] = node
        elif isinstance(node, ast.ClassDef):
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    found[f"{node.name}.{child.name}"] = child
    return found


def _function_metric(
    symbol: str,
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    base: ast.FunctionDef | ast.AsyncFunctionDef | None,
) -> FunctionMetric:
    return FunctionMetric(
        symbol=symbol,
        logical_lines=_span(node),
        base_logical_lines=_span(base),
        complexity=_complexity(node),
        base_complexity=_complexity(base),
        max_nesting=_max_nesting(node),
        base_max_nesting=_max_nesting(base),
        public=not symbol.split(".")[-1].startswith("_"),
        is_new=base is None,
        capability=MetricCapability.EXACT,
        invocation_boundary=_invocation_boundary(node),
        fingerprint=_function_fingerprint(node),
    )


def _function_fingerprint(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    body = ast.Module(body=node.body, type_ignores=[])
    normalized = ast.dump(body, annotate_fields=True, include_attributes=False)
    return f"sha256:{hashlib.sha256(normalized.encode('utf-8')).hexdigest()}"


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
    return max(int(getattr(node, "end_lineno", node.lineno)) - int(node.lineno) + 1, 0)


def _language(path: str) -> str:
    suffix = Path(path).suffix.lower()
    if suffix == ".py":
        return "python"
    if suffix in {".ts", ".tsx"}:
        return "typescript"
    return {".java": "java", ".go": "go"}.get(suffix, "unknown")


def _in_scope(path: str, scope: tuple[str, ...]) -> bool:
    normalized = path.replace("\\", "/")
    return any(
        fnmatch.fnmatchcase(normalized, item.replace("\\", "/")) for item in scope
    )


__all__ = ["collect_lean_metrics"]
