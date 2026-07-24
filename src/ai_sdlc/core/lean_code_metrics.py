"""Deterministic file classification and Python AST metrics for Lean Code."""

from __future__ import annotations

import ast
import fnmatch
import hashlib
from collections import Counter
from pathlib import Path

from ai_sdlc.core.lean_code_callers import attach_python_callers
from ai_sdlc.core.lean_code_classification import classify_file
from ai_sdlc.core.lean_code_dynamic_refs import _invocation_boundary
from ai_sdlc.core.lean_code_metric_calculations import (
    _complexity,
    _decode_source,
    _import_fan_out,
    _line_delta,
    _mark_duplicate_candidates,
    _max_nesting,
    _parse_python,
    _span,
)
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
    task_scopes: dict[str, tuple[str, ...]] | None = None,
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
    return _assemble_metrics(
        files,
        duplicate_candidates,
        snapshot,
        declared_scope,
        task_scopes or {},
    )


def _assemble_metrics(
    files: list[FileMetric],
    duplicate_candidates: list[str],
    snapshot: SourceSnapshot,
    declared_scope: tuple[str, ...],
    task_scopes: dict[str, tuple[str, ...]],
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
        task_scope_matches=_task_scope_matches(snapshot, task_scopes),
        files=files,
    )


def _task_scope_matches(
    snapshot: SourceSnapshot,
    task_scopes: dict[str, tuple[str, ...]],
) -> dict[str, list[str]]:
    return {
        path: sorted(
            task_id for task_id, scope in task_scopes.items() if _in_scope(path, scope)
        )
        for path in snapshot.changed_files
    }


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
    framework_owned = _framework_owned_symbols(head_tree)
    functions = [
        _function_metric(
            symbol,
            node,
            base_functions.get(symbol),
            framework_owned=symbol in framework_owned,
        )
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
    *,
    framework_owned: bool,
) -> FunctionMetric:
    return FunctionMetric(
        symbol=symbol,
        logical_lines=_span(node),
        base_logical_lines=_span(base),
        complexity=_complexity(node),
        base_complexity=_complexity(base),
        max_nesting=_max_nesting(node),
        base_max_nesting=_max_nesting(base),
        public=(
            not framework_owned
            and all(not part.startswith("_") for part in symbol.split("."))
        ),
        is_new=base is None,
        capability=MetricCapability.EXACT,
        invocation_boundary=_invocation_boundary(node),
        fingerprint=_function_fingerprint(node),
    )


def _framework_owned_symbols(tree: ast.Module) -> set[str]:
    symbols = _guarded_main_entrypoints(tree)
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        symbols.update(
            f"{node.name}.model_post_init"
            for child in node.body
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
            and child.name == "model_post_init"
        )
        base_names = {_base_name(base).rsplit(".", 1)[-1] for base in node.bases}
        if "Protocol" in base_names:
            symbols.update(
                f"{node.name}.{child.name}"
                for child in node.body
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
            )
    return symbols


def _guarded_main_entrypoints(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in tree.body:
        if not isinstance(node, ast.If) or not _is_main_guard(node.test):
            continue
        names.update(
            call.func.id
            for statement in node.body
            for call in ast.walk(statement)
            if isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
        )
    return names


def _is_main_guard(node: ast.expr) -> bool:
    if (
        not isinstance(node, ast.Compare)
        or len(node.ops) != 1
        or len(node.comparators) != 1
    ):
        return False
    values = (node.left, node.comparators[0])
    return any(
        isinstance(value, ast.Name) and value.id == "__name__" for value in values
    ) and any(
        isinstance(value, ast.Constant) and value.value == "__main__" for value in values
    )


def _base_name(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _base_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return ""


def _function_fingerprint(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    body = ast.Module(body=node.body, type_ignores=[])
    normalized = ast.dump(body, annotate_fields=True, include_attributes=False)
    return f"sha256:{hashlib.sha256(normalized.encode('utf-8')).hexdigest()}"


def _language(path: str) -> str:
    suffix = Path(path).suffix.lower()
    if suffix == ".py":
        return "python"
    if suffix in {".ts", ".tsx"}:
        return "typescript"
    return {".java": "java", ".go": "go"}.get(suffix, "unknown")


def _in_scope(path: str, scope: tuple[str, ...]) -> bool:
    normalized = path.replace("\\", "/")
    for item in scope:
        pattern = item.strip().strip("`").replace("\\", "/").rstrip("/")
        if not pattern:
            continue
        if fnmatch.fnmatchcase(normalized, pattern):
            return True
        if not any(token in pattern for token in "*?[") and normalized.startswith(
            f"{pattern}/"
        ):
            return True
    return False


__all__ = ["collect_lean_metrics"]
