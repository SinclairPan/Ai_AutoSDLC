from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from ai_sdlc.core.stage_review.ci_certificate_export import _selected_proof


def test_selected_proof_is_bound_to_exact_session_and_certificate(tmp_path) -> None:
    proof_root = (
        tmp_path
        / "stage-review-sessions"
        / "sessions"
        / "work-item"
        / "stage"
        / "session"
        / "certificate-proofs"
    )
    proof_root.mkdir(parents=True)
    for name in ("current", "other"):
        (proof_root / f"{name}.json").write_text("{}\n", encoding="utf-8")
    current = SimpleNamespace(
        certificate=SimpleNamespace(
            close_kind="local-pr-review-attest",
            stage_instance_id="review.current",
            scope=SimpleNamespace(session_id="session.current"),
            issued_at="2026-07-25T09:00:00Z",
            certificate_id="certificate.current",
        )
    )
    other = SimpleNamespace(
        certificate=SimpleNamespace(
            close_kind="local-pr-review-attest",
            stage_instance_id="review.current",
            scope=SimpleNamespace(session_id="session.concurrent"),
            issued_at="2026-07-25T10:00:00Z",
            certificate_id="certificate.concurrent",
        )
    )
    proofs = {"current": current, "other": other}

    with (
        patch(
            "ai_sdlc.core.stage_review.ci_certificate_export.read_json_object",
            side_effect=lambda path: {"name": path.stem},
        ),
        patch(
            "ai_sdlc.core.stage_review.ci_certificate_export."
            "CiCertificateAuthorityProof.model_validate",
            side_effect=lambda payload: proofs[payload["name"]],
        ),
    ):
        selected = _selected_proof(
            tmp_path,
            "local-pr-review-attest",
            stage_instance_id="review.current",
            review_session_id="session.current",
            certificate_id="certificate.current",
        )

    assert selected is current


def test_selected_proof_fails_closed_when_exact_identity_is_missing(tmp_path) -> None:
    with pytest.raises(ValueError, match="exact CI certificate authority proof"):
        _selected_proof(
            tmp_path,
            "local-pr-review-attest",
            stage_instance_id="review.missing",
            review_session_id="session.missing",
            certificate_id="certificate.missing",
        )
