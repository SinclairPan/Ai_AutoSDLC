"""阶段评审工件共享的确定性规范化与摘要。"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import PurePosixPath
from typing import Literal

from pydantic import BaseModel

PathCasePolicy = Literal["preserve", "lower"]


@dataclass(frozen=True, slots=True)
class CanonicalizationPolicy:
    """声明非语义字段、集合字段和仓库路径字段。"""

    excluded_fields: frozenset[str] = frozenset()
    set_like_fields: frozenset[str] = frozenset()
    path_fields: frozenset[str] = frozenset()
    path_case_policy: PathCasePolicy = "preserve"


def normalize_repo_path(
    value: str,
    *,
    case_policy: PathCasePolicy = "preserve",
) -> str:
    """把路径规范化为不可逃逸的仓库相对 POSIX 路径。"""

    normalized = value.strip().replace("\\", "/")
    if (
        not normalized
        or normalized.startswith("/")
        or re.match(r"^[A-Za-z]:/", normalized)
    ):
        raise ValueError("path must be repository-relative")
    parts = [part for part in PurePosixPath(normalized).parts if part != "."]
    if not parts or any(part == ".." for part in parts):
        raise ValueError("path must be repository-relative")
    result = "/".join(parts)
    if case_policy == "lower":
        return result.lower()
    if case_policy != "preserve":
        raise ValueError(f"unsupported path case policy: {case_policy}")
    return result


def canonical_payload(value: object, policy: CanonicalizationPolicy) -> object:
    """按显式策略生成只含 JSON 原语的确定性结构。"""

    return _canonicalize(value, policy, field_path=())


def canonical_bytes(value: object, policy: CanonicalizationPolicy) -> bytes:
    """生成无空白、UTF-8、稳定键顺序的规范字节。"""

    payload = canonical_payload(value, policy)
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def canonical_digest(value: object, policy: CanonicalizationPolicy) -> str:
    """返回带算法前缀的规范 SHA-256 摘要。"""

    return "sha256:" + hashlib.sha256(canonical_bytes(value, policy)).hexdigest()


def _canonicalize(
    value: object,
    policy: CanonicalizationPolicy,
    *,
    field_path: tuple[str, ...],
) -> object:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    if isinstance(value, Enum):
        value = value.value
    if isinstance(value, dict):
        return {
            str(key): _canonicalize(
                item,
                policy,
                field_path=(*field_path, str(key)),
            )
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
            if _field_rule((*field_path, str(key))) not in policy.excluded_fields
        }
    if isinstance(value, (list, tuple, set, frozenset)):
        items = [_canonicalize(item, policy, field_path=field_path) for item in value]
        field_rule = _field_rule(field_path)
        if field_rule in policy.path_fields:
            items = [
                normalize_repo_path(str(item), case_policy=policy.path_case_policy)
                for item in items
            ]
        if field_rule in policy.set_like_fields or isinstance(value, (set, frozenset)):
            by_encoding = {
                json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":")): item
                for item in items
            }
            return [by_encoding[key] for key in sorted(by_encoding)]
        return items
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("canonical payload does not allow NaN or Infinity")
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(f"unsupported canonical value: {type(value).__name__}")


def _field_rule(field_path: tuple[str, ...]) -> str:
    """把策略限制到根字段或显式点路径，避免扩展同名键被误解释。"""

    return ".".join(field_path)
