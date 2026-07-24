from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError
from tests.unit.stage_review.test_resources import _now

from ai_sdlc.core.stage_review.binding_builders import build_host_capability_snapshot
from ai_sdlc.core.stage_review.resource_builders import utc_iso


def test_adapter_self_reported_enforced_cannot_create_trust() -> None:
    api = _api()
    host = _host(api, trusted=False)
    manifest = _manifest(api, host_snapshot_digest=host.snapshot_digest)

    grade = api.TrustedIsolationBackendRegistry.default().derive_grade(
        manifest,
        host,
        adapter_grade="enforced",
        now=_now(),
    )

    assert grade == "unproven"


def test_manifest_tampering_is_rejected() -> None:
    api = _api()
    host = _host(api)
    manifest = _manifest(api, host_snapshot_digest=host.snapshot_digest)
    tampered = manifest.model_copy(update={"backend_instance_id": "instance.swapped"})

    with pytest.raises(ValidationError, match="manifest digest"):
        api.IsolationEvidenceManifest.model_validate(tampered.model_dump(mode="json"))


def test_manifest_and_permit_bind_runtime_layout() -> None:
    api = _api()
    host = _host(api)
    manifest = _manifest(api, host_snapshot_digest=host.snapshot_digest)
    permit = _permit(api)

    assert manifest.layout_digest == "sha256:layout"
    assert permit.layout_digest == manifest.layout_digest


@pytest.mark.parametrize(
    ("contract_version", "backend_version"),
    (("unknown-contract", "0.138.0"), ("2026-07-01", "0.137.0")),
)
def test_unknown_contract_or_old_backend_is_unproven(
    contract_version: str,
    backend_version: str,
) -> None:
    api = _api()
    host = _host(api)
    manifest = _manifest(
        api,
        host_snapshot_digest=host.snapshot_digest,
        contract_version=contract_version,
        backend_version=backend_version,
    )

    assert (
        api.TrustedIsolationBackendRegistry.default().derive_grade(
            manifest,
            host,
            adapter_grade="enforced",
            now=_now(),
        )
        == "unproven"
    )


def test_expired_host_snapshot_is_unproven() -> None:
    api = _api()
    host = _host(api, expires_delta=timedelta(seconds=-1))
    manifest = _manifest(api, host_snapshot_digest=host.snapshot_digest)

    assert (
        api.TrustedIsolationBackendRegistry.default().derive_grade(
            manifest,
            host,
            adapter_grade="enforced",
            now=_now(),
        )
        == "unproven"
    )


def test_environment_variable_cannot_forge_enforced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = _api()
    monkeypatch.setenv("AI_SDLC_ISOLATION_ENFORCED", "1")
    host = _host(api, trusted=False)
    manifest = _manifest(api, host_snapshot_digest=host.snapshot_digest)

    grade = api.TrustedIsolationBackendRegistry.default().derive_grade(
        manifest,
        host,
        adapter_grade="enforced",
        now=_now(),
    )

    assert grade == "unproven"


def test_detected_only_never_becomes_enforced() -> None:
    api = _api()
    host = _host(api)
    manifest = _manifest(api, host_snapshot_digest=host.snapshot_digest)

    grade = api.TrustedIsolationBackendRegistry.default().derive_grade(
        manifest,
        host,
        adapter_grade="detected_only",
        now=_now(),
    )

    assert grade == "detected_only"


def test_self_reported_detected_only_without_trusted_manifest_is_unproven() -> None:
    api = _api()
    host = _host(api, trusted=False)
    manifest = _manifest(api, host_snapshot_digest=host.snapshot_digest)

    grade = api.TrustedIsolationBackendRegistry.default().derive_grade(
        manifest,
        host,
        adapter_grade="detected_only",
        now=_now(),
    )

    assert grade == "unproven"


def test_registry_multiple_backend_order_is_irrelevant() -> None:
    api = _api()
    codex = api.codex_permission_profile_contract()
    second = replace(
        codex,
        backend_id="sandbox.second",
        contract_version="contract.second",
    )
    registry = api.TrustedIsolationBackendRegistry((second, codex))
    host = _host(api)
    manifest = _manifest(api, host_snapshot_digest=host.snapshot_digest)

    assert registry.derive_grade(
        manifest,
        host,
        adapter_grade="enforced",
        now=_now(),
    ) == "enforced"


def test_failed_boundary_result_cannot_authorize_enforced() -> None:
    api = _api()
    host = _host(api)
    manifest = _manifest(api, host_snapshot_digest=host.snapshot_digest)
    failed = manifest.boundary_results[0].model_copy(
        update={"blocked_before_side_effect": False, "observed": "write-succeeded"}
    )
    changed = api.build_isolation_evidence_manifest(
        **{
            **manifest.model_dump(
                mode="json",
                exclude={"artifact_kind", "manifest_digest"},
            ),
            "boundary_results": (failed, *manifest.boundary_results[1:]),
        }
    )

    assert (
        api.TrustedIsolationBackendRegistry.default().derive_grade(
            changed,
            host,
            adapter_grade="enforced",
            now=_now(),
        )
        == "unproven"
    )


def test_manifest_cleanup_failure_cannot_authorize_enforced() -> None:
    api = _api()
    host = _host(api)
    manifest = _manifest(api, host_snapshot_digest=host.snapshot_digest)
    changed = api.build_isolation_evidence_manifest(
        **{
            **manifest.model_dump(
                mode="json",
                exclude={"artifact_kind", "manifest_digest"},
            ),
            "cleanup_succeeded": False,
        }
    )

    assert (
        api.TrustedIsolationBackendRegistry.default().derive_grade(
            changed,
            host,
            adapter_grade="enforced",
            now=_now(),
        )
        == "unproven"
    )
def test_permit_is_short_lived_and_single_use(tmp_path: Path) -> None:
    api = _api()
    permit = _permit(api)
    store = api.IsolationPermitStore(tmp_path)
    store.consume(permit, **_consume_identity(), now=_now())

    with pytest.raises(api.IsolationPermitRefused, match="already-consumed"):
        store.consume(permit, **_consume_identity(), now=_now())

    receipt = store.receipts()[-1]
    assert receipt.command_started is False
    assert receipt.reason_id == "isolation.permit-already-consumed"


def test_isolation_manifest_digest_uses_a_windows_safe_physical_filename(
    tmp_path: Path,
) -> None:
    api = _api()
    host = _host(api)
    manifest = _manifest(api, host_snapshot_digest=host.snapshot_digest)
    store = api.IsolationPermitStore(tmp_path)

    store.persist_manifest(manifest)

    paths = tuple((store.root / "manifests").glob("*.json"))
    assert [path.name for path in paths] == [
        f"{manifest.manifest_digest.removeprefix('sha256:')}.json"
    ]


def test_expired_permit_is_rejected_before_command(tmp_path: Path) -> None:
    api = _api()
    permit = _permit(api, expires_delta=timedelta(seconds=-1))
    store = api.IsolationPermitStore(tmp_path)

    with pytest.raises(api.IsolationPermitRefused, match="expired"):
        store.consume(permit, **_consume_identity(), now=_now())

    assert store.receipts()[-1].command_started is False


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    (
        ("assignment_digest", "sha256:assignment.swapped", "identity-mismatch"),
        ("candidate_digest", "sha256:candidate.swapped", "identity-mismatch"),
        ("host_snapshot_digest", "sha256:host.stale", "host-stale"),
        ("backend_instance_id", "instance.restarted", "backend-instance-stale"),
        ("backend_epoch", "epoch.restarted", "backend-epoch-stale"),
        ("layout_digest", "sha256:layout.swapped", "layout-stale"),
    ),
)
def test_permit_identity_exchange_fails_closed(
    tmp_path: Path,
    field: str,
    value: str,
    reason: str,
) -> None:
    api = _api()
    identity = _consume_identity()
    identity[field] = value

    with pytest.raises(api.IsolationPermitRefused, match=reason):
        api.IsolationPermitStore(tmp_path).consume(
            _permit(api),
            **identity,
            now=_now(),
        )


def _api():
    from ai_sdlc.core.stage_review import isolation_execution

    return isolation_execution


def _host(
    api,
    *,
    trusted: bool = True,
    expires_delta: timedelta = timedelta(minutes=5),
    release_manifest_digest: str = "sha256:release-manifest",
):
    contract = api.codex_permission_profile_contract()
    adapter_id = contract.host_adapter_id if trusted else "provider.self-report"
    source = contract.capability_source if trusted else "environment"
    return build_host_capability_snapshot(
        host_adapter_id=adapter_id,
        host_adapter_version="1.0.0",
        host_session_id="host.session",
        capability_ids=tuple(
            sorted(
                (
                    "agent_execution",
                    f"isolation.{contract.backend_id}",
                    f"network_enforcement.{contract.backend_id}",
                )
            )
        ),
        capability_source=source,
        evidence_digest="sha256:host.probe",
        backend_id=contract.backend_id if trusted else "",
        backend_contract_version=contract.contract_version if trusted else "",
        backend_release_manifest_digest=(
            release_manifest_digest if trusted else ""
        ),
        backend_runtime_identity_digest=(
            "sha256:runtime-identity" if trusted else ""
        ),
        previous_snapshot_digest="",
        authorization_transition="probe-confirmed",
        issued_at=utc_iso(_now() - timedelta(minutes=1)),
        expires_at=utc_iso(_now() + expires_delta),
    )


def _manifest(
    api,
    *,
    host_snapshot_digest: str,
    contract_version: str = "2026-07-01",
    backend_version: str = "0.138.0",
    release_manifest_digest: str = "sha256:release-manifest",
):
    return api.build_isolation_evidence_manifest(
        backend_id="codex.permission-profile",
        contract_version=contract_version,
        backend_version=backend_version,
        backend_instance_id="instance.one",
        backend_epoch="epoch.one",
        platform="macos",
        platform_mechanism="seatbelt",
        host_snapshot_digest=host_snapshot_digest,
        release_manifest_digest=release_manifest_digest,
        runtime_identity_digest="sha256:runtime-identity",
        allocation_digest="sha256:allocation",
        assignment_digest="sha256:assignment",
        candidate_digest="sha256:candidate",
        layout_digest="sha256:layout",
        policy_digest="sha256:policy",
        filesystem_policy_digest="sha256:filesystem-policy",
        network_policy_digest="sha256:network-policy",
        process_id=101,
        parent_process_id=100,
        boundary_results=_boundary_results(api),
        os_native_denials=(),
        cleanup_succeeded=True,
        issued_at=utc_iso(_now() - timedelta(minutes=2)),
        expires_at=utc_iso(_now() + timedelta(minutes=1)),
    )


def _permit(api, *, expires_delta: timedelta = timedelta(minutes=1)):
    return api.build_isolation_execution_permit(
        allocation_digest="sha256:allocation",
        assignment_digest="sha256:assignment",
        candidate_digest="sha256:candidate",
        host_snapshot_digest="sha256:host",
        backend_id="codex.permission-profile",
        contract_version="2026-07-01",
        backend_version="0.138.0",
        backend_instance_id="instance.one",
        backend_epoch="epoch.one",
        normalized_run_root="/tmp/reviewer-run",
        layout_digest="sha256:layout",
        filesystem_policy_digest="sha256:filesystem-policy",
        network_policy_digest="sha256:network-policy",
        manifest_digest="sha256:manifest",
        issued_at=utc_iso(_now() - timedelta(minutes=2)),
        expires_at=utc_iso(_now() + expires_delta),
        nonce="nonce.one",
    )


def _consume_identity() -> dict[str, str]:
    return {
        "allocation_digest": "sha256:allocation",
        "assignment_digest": "sha256:assignment",
        "candidate_digest": "sha256:candidate",
        "host_snapshot_digest": "sha256:host",
        "backend_instance_id": "instance.one",
        "backend_epoch": "epoch.one",
        "layout_digest": "sha256:layout",
    }


def _boundary_results(api):
    return tuple(
        api.IsolationBoundaryResult(
            action=action,
            target_kind=action,
            expected=("allowed" if action == "output-write-allowed" else "denied"),
            observed=("allowed" if action == "output-write-allowed" else "denied"),
            os_error="EPERM",
            blocked_before_side_effect=action != "output-write-allowed",
            before_digest=f"sha256:before.{action}",
            after_digest=f"sha256:before.{action}",
        )
        for action in api.REQUIRED_ENFORCED_BOUNDARIES
    )
