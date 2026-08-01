"""以受保护密钥和单调头锚定 SnapshotControl Event。"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from pathlib import Path

from ai_sdlc.core.stage_review.artifacts import (
    SharedStateIntegrityError,
    _resolve_trusted_project_state,
    serialized_json_bytes,
)
from ai_sdlc.core.stage_review.canonical import (
    CanonicalizationPolicy,
    canonical_digest,
)
from ai_sdlc.core.stage_review.optimization.snapshot_models import (
    SnapshotControlEvent,
)
from ai_sdlc.core.stage_review.optimization.snapshot_trust_epoch import (
    _SnapshotTrustEpochStore,
)
from ai_sdlc.core.stage_review.optimization.snapshot_trusted_files import (
    _create_secure_file,
    _prepare_trusted_directory,
    _read_secure_file,
    _replace_secure_file,
    _secure_file_lock,
    _unlink_secure_file,
)

SNAPSHOT_CONTROL_TRUST_MAC_EXTENSION = "snapshot_control_trust_mac"
_KEY_BYTES = 32
_HEAD_SCHEMA = "snapshot-control-trusted-head.v1"


class _TrustedHeadRefreshError(SharedStateIntegrityError):
    """读取快照落后于已提交可信头时要求从存储重新取流。"""


class SnapshotControlTrustAnchor:
    def __init__(self, root: Path, *, project_id: str) -> None:
        trusted_root = _resolve_trusted_project_state(root, project_id)
        self.project_id = project_id
        self.trusted_root = _prepare_trusted_directory(trusted_root)
        self.epoch_store = _SnapshotTrustEpochStore(
            self.trusted_root,
            project_id=project_id,
        )
        self.root = _prepare_trusted_directory(
            self.trusted_root / "snapshot-control"
        )
        self.key_path = self.root / "event-anchor.key"
        self.head_path = self.root / "trusted-head.json"
        self._key = _load_or_create_key(
            self.root,
            require_existing=self.epoch_store._exists(),
        )

    def _sign(self, event: SnapshotControlEvent) -> SnapshotControlEvent:
        extensions = dict(event.extensions)
        extensions.pop(SNAPSHOT_CONTROL_TRUST_MAC_EXTENSION, None)
        unsigned = event.model_copy(update={"extensions": extensions})
        extensions[SNAPSHOT_CONTROL_TRUST_MAC_EXTENSION] = self._event_mac(unsigned)
        payload = event.model_dump(mode="json")
        payload.update({"extensions": extensions, "event_digest": ""})
        return SnapshotControlEvent.model_validate(payload)

    def _verify(self, event: SnapshotControlEvent) -> None:
        actual = event.extensions.get(SNAPSHOT_CONTROL_TRUST_MAC_EXTENSION)
        extensions = dict(event.extensions)
        extensions.pop(SNAPSHOT_CONTROL_TRUST_MAC_EXTENSION, None)
        unsigned = event.model_copy(update={"extensions": extensions})
        expected = self._event_mac(unsigned)
        if not isinstance(actual, str) or not hmac.compare_digest(actual, expected):
            raise SharedStateIntegrityError("snapshot control trust anchor diverged")

    def _verify_event_authentication(
        self,
        events: tuple[SnapshotControlEvent, ...],
    ) -> int:
        legacy_sequence = 0
        signed_seen = False
        for event in events:
            if self._is_authenticated(event):
                signed_seen = True
                self._verify(event)
                continue
            if signed_seen:
                raise SharedStateIntegrityError(
                    "unsigned snapshot event follows trusted history"
                )
            legacy_sequence = event.sequence
        return legacy_sequence

    def _reconcile_head(
        self,
        events: tuple[SnapshotControlEvent, ...],
        *,
        legacy_sequence: int,
    ) -> None:
        lock = _secure_file_lock(
            self.root,
            ".trusted-head.lock",
            timeout_seconds=2,
        )
        with lock:
            current = _read_head(self.root, self._key, self.project_id)
            legacy_digest = _event_digest_at(events, legacy_sequence)
            head_sequence = events[-1].sequence if events else 0
            head_digest = events[-1].event_digest if events else ""
            commitment = _epoch_commitment(
                self._key,
                legacy_sequence=legacy_sequence,
                legacy_digest=legacy_digest,
                head_sequence=head_sequence,
                head_digest=head_digest,
            )
            epoch = self.epoch_store._read()
            _verify_epoch_matches_stream(epoch, events, commitment)
            if current is None:
                if epoch is not None:
                    raise SharedStateIntegrityError(
                        "snapshot trust anchor head is invalid"
                    )
                _write_committed_head(
                    self.root, self._key, self.project_id, commitment
                )
                self.epoch_store._reconcile(commitment)
                return
            _verify_head_matches_stream(
                current,
                events,
                legacy_sequence=legacy_sequence,
                legacy_digest=legacy_digest,
            )
            current_sequence = _trusted_sequence(current, "head_sequence")
            if head_sequence > current_sequence:
                _write_committed_head(
                    self.root, self._key, self.project_id, commitment
                )
            self.epoch_store._reconcile(commitment)

    def _is_authenticated(self, event: SnapshotControlEvent) -> bool:
        return isinstance(
            event.extensions.get(SNAPSHOT_CONTROL_TRUST_MAC_EXTENSION),
            str,
        )

    def _event_mac(self, event: SnapshotControlEvent) -> str:
        payload = event.model_dump(mode="json", exclude={"event_digest"})
        digest = canonical_digest(payload, CanonicalizationPolicy())
        value = hmac.new(self._key, digest.encode("utf-8"), hashlib.sha256)
        return f"hmac-sha256:{value.hexdigest()}"


def _verify_head_matches_stream(
    head: dict[str, object],
    events: tuple[SnapshotControlEvent, ...],
    *,
    legacy_sequence: int,
    legacy_digest: str,
) -> None:
    expected_legacy_sequence = _trusted_sequence(head, "legacy_sequence")
    expected_head_sequence = _trusted_sequence(head, "head_sequence")
    if len(events) < expected_head_sequence:
        raise _TrustedHeadRefreshError("snapshot control trusted head advanced")
    if (
        legacy_sequence != expected_legacy_sequence
        or legacy_digest != str(head["legacy_digest"])
        or _event_digest_at(events, expected_head_sequence)
        != str(head["head_digest"])
    ):
        raise SharedStateIntegrityError("snapshot control trusted head diverged")


def _write_committed_head(
    directory: Path,
    key: bytes,
    project_id: str,
    commitment: dict[str, object],
) -> None:
    _write_head(
        directory,
        key,
        _head_payload(
            project_id,
            legacy_sequence=_trusted_sequence(commitment, "legacy_sequence"),
            legacy_digest=str(commitment["legacy_digest"]),
            head_sequence=_trusted_sequence(commitment, "head_sequence"),
            head_digest=str(commitment["head_digest"]),
        ),
    )


def _event_digest_at(events: tuple[SnapshotControlEvent, ...], sequence: int) -> str:
    if sequence == 0:
        return ""
    if sequence > len(events) or events[sequence - 1].sequence != sequence:
        return ""
    return events[sequence - 1].event_digest


def _epoch_commitment(
    key: bytes,
    *,
    legacy_sequence: int,
    legacy_digest: str,
    head_sequence: int,
    head_digest: str,
) -> dict[str, object]:
    return {
        "event_key_digest": f"sha256:{hashlib.sha256(key).hexdigest()}",
        "legacy_sequence": legacy_sequence,
        "legacy_digest": legacy_digest,
        "head_sequence": head_sequence,
        "head_digest": head_digest,
    }


def _verify_epoch_matches_stream(
    epoch: dict[str, object] | None,
    events: tuple[SnapshotControlEvent, ...],
    commitment: dict[str, object],
) -> None:
    if epoch is None:
        return
    for name in ("event_key_digest", "legacy_sequence", "legacy_digest"):
        if epoch.get(name) != commitment.get(name):
            raise SharedStateIntegrityError("snapshot trust epoch diverged")
    sequence = _trusted_sequence(epoch, "head_sequence")
    if len(events) < sequence:
        raise _TrustedHeadRefreshError("snapshot trust epoch advanced")
    if _event_digest_at(events, sequence) != epoch.get("head_digest"):
        raise SharedStateIntegrityError("snapshot trust epoch diverged")


def _trusted_sequence(head: dict[str, object], name: str) -> int:
    value = head.get(name)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise SharedStateIntegrityError("snapshot control trusted head is invalid")
    return value


def _head_payload(
    project_id: str,
    *,
    legacy_sequence: int,
    legacy_digest: str,
    head_sequence: int,
    head_digest: str,
) -> dict[str, object]:
    return {
        "schema_version": _HEAD_SCHEMA,
        "project_id": project_id,
        "legacy_sequence": legacy_sequence,
        "legacy_digest": legacy_digest,
        "head_sequence": head_sequence,
        "head_digest": head_digest,
    }


def _read_head(
    directory: Path,
    key: bytes,
    project_id: str,
) -> dict[str, object] | None:
    try:
        raw = _read_secure_file(directory, "trusted-head.json")
    except FileNotFoundError:
        return None
    try:
        value = json.loads(raw.decode("utf-8"))
        if not isinstance(value, dict):
            raise ValueError("trusted head must be an object")
        mac = str(value.pop("head_mac"))
        required = {
            "schema_version": _HEAD_SCHEMA,
            "project_id": project_id,
        }
        if any(value.get(name) != expected for name, expected in required.items()):
            raise ValueError("trusted head identity diverged")
        for name in ("legacy_sequence", "head_sequence"):
            _trusted_sequence(value, name)
        if _trusted_sequence(value, "legacy_sequence") > _trusted_sequence(
            value,
            "head_sequence",
        ):
            raise ValueError("trusted head legacy boundary is invalid")
        expected_mac = _head_mac(key, value)
        if not hmac.compare_digest(mac, expected_mac):
            raise ValueError("trusted head MAC diverged")
        return value
    except (KeyError, TypeError, ValueError, UnicodeError, json.JSONDecodeError) as exc:
        raise SharedStateIntegrityError("snapshot control trusted head is invalid") from exc


def _write_head(directory: Path, key: bytes, payload: dict[str, object]) -> None:
    trusted = dict(payload)
    trusted["head_mac"] = _head_mac(key, payload)
    _replace_secure_file(
        directory,
        "trusted-head.json",
        serialized_json_bytes(trusted),
    )


def _head_mac(key: bytes, payload: dict[str, object]) -> str:
    digest = canonical_digest(payload, CanonicalizationPolicy())
    value = hmac.new(key, digest.encode("utf-8"), hashlib.sha256)
    return f"hmac-sha256:{value.hexdigest()}"


def _load_or_create_key(directory: Path, *, require_existing: bool) -> bytes:
    with _secure_file_lock(
        directory,
        ".key-init.lock",
        timeout_seconds=2,
    ):
        try:
            stored = _read_secure_file(directory, "event-anchor.key")
        except FileNotFoundError:
            stored = b""
        key = _decode_key(stored) if stored else b""
        if len(key) == _KEY_BYTES:
            return key
        try:
            _read_secure_file(directory, "trusted-head.json")
        except FileNotFoundError:
            pass
        else:
            raise SharedStateIntegrityError("snapshot trust anchor key is invalid")
        if stored or require_existing:
            raise SharedStateIntegrityError("snapshot trust anchor key is invalid")
        _unlink_secure_file(directory, "event-anchor.key")
        key = secrets.token_bytes(_KEY_BYTES)
        _create_secure_file(directory, "event-anchor.key", _encode_key(key))
        created = _read_secure_file(directory, "event-anchor.key")
        if not hmac.compare_digest(created, key):
            raise SharedStateIntegrityError("snapshot trust anchor key is invalid")
        return created


def _encode_key(key: bytes) -> bytes:
    return key


def _decode_key(stored: bytes) -> bytes:
    return stored
