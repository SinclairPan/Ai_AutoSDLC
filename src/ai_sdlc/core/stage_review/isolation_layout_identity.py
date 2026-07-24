"""隔离运行布局的稳定语义身份。"""

from __future__ import annotations

from ai_sdlc.core.stage_review.canonical import CanonicalizationPolicy, canonical_digest

_RUNTIME_METADATA = frozenset({"ai_sdlc_version", "created_at", "created_by"})


def _runtime_layout_digest(value: object) -> str:
    return canonical_digest(
        value,
        CanonicalizationPolicy(
            excluded_fields=_RUNTIME_METADATA | {"layout_digest"}
        ),
    )
