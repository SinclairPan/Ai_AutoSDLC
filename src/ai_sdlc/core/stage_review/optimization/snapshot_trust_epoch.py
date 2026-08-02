"""在 SnapshotControl 子树之外保存项目级信任代际。"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from pathlib import Path

from ai_sdlc.core.stage_review.artifacts import (
    SharedStateIntegrityError,
    serialized_json_bytes,
)
from ai_sdlc.core.stage_review.canonical import (
    CanonicalizationPolicy,
    canonical_digest,
)
from ai_sdlc.core.stage_review.optimization.snapshot_trusted_files import (
    _create_secure_file,
    _read_secure_file,
    _replace_secure_file,
    _secure_file_lock,
)

_EPOCH_KEY_BYTES = 32
_EPOCH_SCHEMA = "snapshot-control-trust-epoch.v1"
_ANCHOR_SCHEMA = "snapshot-project-anchor.v2"
_EPOCH_KEY_NAME = "snapshot-project-anchor.key"
_EPOCH_STATE_NAME = "snapshot-control-epoch.json"
_COMMITMENT_FIELDS = (
    "event_key_digest",
    "legacy_sequence",
    "legacy_digest",
    "head_sequence",
    "head_digest",
)


class _SnapshotTrustEpochStore:
    def __init__(self, root: Path, *, project_id: str) -> None:
        self.root = root
        self.project_id = project_id
        self._key, self._anchor = _load_or_create_project_anchor(
            root,
            project_id=project_id,
        )

    def _exists(self) -> bool:
        self._refresh_anchor()
        if _anchor_generation(self._anchor) < 1:
            return False
        if not _secure_file_exists(self.root, _EPOCH_STATE_NAME):
            raise SharedStateIntegrityError("snapshot trust epoch is missing")
        return True

    def _read(self) -> dict[str, object] | None:
        self._refresh_anchor()
        value = _read_epoch(self.root, self._key, self.project_id)
        if value is None:
            if _anchor_generation(self._anchor) > 0:
                raise SharedStateIntegrityError("snapshot trust epoch is missing")
            return None
        _verify_epoch_against_anchor(value, self._anchor)
        return value

    def _reconcile(self, commitment: dict[str, object]) -> None:
        with _secure_file_lock(
            self.root,
            ".snapshot-trust-epoch.lock",
            timeout_seconds=2,
        ):
            current = self._read()
            if current is None:
                payload = {
                    **commitment,
                    "schema_version": _EPOCH_SCHEMA,
                    "project_id": self.project_id,
                    "epoch_id": f"snapshot-trust-epoch.{secrets.token_hex(16)}",
                    "generation": 1,
                }
                _write_epoch(self.root, self._key, payload)
                self._commit_anchor(payload)
                return
            if _positive_int(current, "generation") > _anchor_generation(
                self._anchor
            ):
                self._commit_anchor(current)
            _verify_epoch_identity(current, commitment)
            current_sequence = _non_negative_int(current, "head_sequence")
            next_sequence = _non_negative_int(commitment, "head_sequence")
            if next_sequence < current_sequence:
                raise SharedStateIntegrityError("snapshot trust epoch moved backwards")
            if next_sequence == current_sequence:
                if current.get("head_digest") != commitment.get("head_digest"):
                    raise SharedStateIntegrityError("snapshot trust epoch diverged")
                return
            payload = {
                **commitment,
                "schema_version": _EPOCH_SCHEMA,
                "project_id": self.project_id,
                "epoch_id": current["epoch_id"],
                "generation": _positive_int(current, "generation") + 1,
            }
            _write_epoch(self.root, self._key, payload)
            self._commit_anchor(payload)

    def _commit_anchor(self, epoch: dict[str, object]) -> None:
        payload = {
            "schema_version": _ANCHOR_SCHEMA,
            "project_id": self.project_id,
            "key_hex": self._key.hex(),
            "epoch_id": epoch["epoch_id"],
            "generation": _positive_int(epoch, "generation"),
            **{name: epoch[name] for name in _COMMITMENT_FIELDS},
        }
        _replace_secure_file(
            self.root,
            _EPOCH_KEY_NAME,
            serialized_json_bytes(payload),
        )
        self._anchor = payload

    def _refresh_anchor(self) -> None:
        key, anchor = _read_project_anchor(self.root, self.project_id)
        if not hmac.compare_digest(key, self._key):
            raise SharedStateIntegrityError("snapshot project anchor key diverged")
        self._anchor = anchor


def _verify_epoch_identity(
    current: dict[str, object],
    commitment: dict[str, object],
) -> None:
    for name in ("event_key_digest", "legacy_sequence", "legacy_digest"):
        if current.get(name) != commitment.get(name):
            raise SharedStateIntegrityError("snapshot trust epoch diverged")


def _verify_epoch_against_anchor(
    epoch: dict[str, object],
    anchor: dict[str, object],
) -> None:
    anchor_generation = _anchor_generation(anchor)
    epoch_generation = _positive_int(epoch, "generation")
    if epoch_generation < anchor_generation:
        raise SharedStateIntegrityError("snapshot trust epoch moved backwards")
    if epoch_generation > anchor_generation + 1:
        raise SharedStateIntegrityError("snapshot trust epoch diverged")
    if anchor_generation == 0:
        if epoch_generation != 1:
            raise SharedStateIntegrityError("snapshot trust epoch diverged")
        return
    if epoch.get("epoch_id") != anchor.get("epoch_id"):
        raise SharedStateIntegrityError("snapshot trust epoch diverged")
    if epoch_generation == anchor_generation:
        for name in _COMMITMENT_FIELDS:
            if epoch.get(name) != anchor.get(name):
                raise SharedStateIntegrityError("snapshot trust epoch diverged")
        return
    if _non_negative_int(epoch, "head_sequence") < _non_negative_int(
        anchor,
        "head_sequence",
    ):
        raise SharedStateIntegrityError("snapshot trust epoch moved backwards")
    for name in ("event_key_digest", "legacy_sequence", "legacy_digest"):
        if epoch.get(name) != anchor.get(name):
            raise SharedStateIntegrityError("snapshot trust epoch diverged")


def _load_or_create_project_anchor(
    directory: Path,
    *,
    project_id: str,
) -> tuple[bytes, dict[str, object]]:
    with _secure_file_lock(
        directory,
        ".snapshot-epoch-key.lock",
        timeout_seconds=2,
    ):
        try:
            return _read_project_anchor(directory, project_id)
        except FileNotFoundError:
            pass
        if _secure_file_exists(directory, _EPOCH_STATE_NAME):
            raise SharedStateIntegrityError("snapshot project anchor is missing")
        key = secrets.token_bytes(_EPOCH_KEY_BYTES)
        payload: dict[str, object] = {
            "schema_version": _ANCHOR_SCHEMA,
            "project_id": project_id,
            "key_hex": key.hex(),
            "generation": 0,
        }
        _create_secure_file(
            directory,
            _EPOCH_KEY_NAME,
            serialized_json_bytes(payload),
        )
        created_key, created = _read_project_anchor(directory, project_id)
        if not hmac.compare_digest(created_key, key):
            raise SharedStateIntegrityError("snapshot project anchor key diverged")
        return created_key, created


def _read_project_anchor(
    directory: Path,
    project_id: str,
) -> tuple[bytes, dict[str, object]]:
    try:
        raw = _read_secure_file(directory, _EPOCH_KEY_NAME)
        value = json.loads(raw.decode("utf-8"))
        if not isinstance(value, dict):
            raise ValueError("snapshot project anchor must be an object")
        if value.get("schema_version") != _ANCHOR_SCHEMA:
            raise ValueError("snapshot project anchor schema diverged")
        if value.get("project_id") != project_id:
            raise ValueError("snapshot project anchor project diverged")
        key = bytes.fromhex(str(value["key_hex"]))
        if len(key) != _EPOCH_KEY_BYTES:
            raise ValueError("snapshot project anchor key is invalid")
        generation = _anchor_generation(value)
        if generation > 0:
            _positive_int(value, "generation")
            _positive_text(value, "epoch_id")
            for name in _COMMITMENT_FIELDS:
                if name not in value:
                    raise ValueError("snapshot project anchor is incomplete")
            _non_negative_int(value, "legacy_sequence")
            _non_negative_int(value, "head_sequence")
        return key, value
    except FileNotFoundError:
        raise
    except (KeyError, TypeError, ValueError, UnicodeError, json.JSONDecodeError) as exc:
        raise SharedStateIntegrityError("snapshot project anchor is invalid") from exc


def _read_epoch(
    directory: Path,
    key: bytes,
    project_id: str,
) -> dict[str, object] | None:
    try:
        raw = _read_secure_file(directory, _EPOCH_STATE_NAME)
    except FileNotFoundError:
        return None
    try:
        value = json.loads(raw.decode("utf-8"))
        if not isinstance(value, dict):
            raise ValueError("snapshot trust epoch must be an object")
        mac = str(value.pop("epoch_mac"))
        if value.get("schema_version") != _EPOCH_SCHEMA:
            raise ValueError("snapshot trust epoch schema diverged")
        if value.get("project_id") != project_id:
            raise ValueError("snapshot trust epoch project diverged")
        _positive_int(value, "generation")
        _positive_text(value, "epoch_id")
        _non_negative_int(value, "legacy_sequence")
        _non_negative_int(value, "head_sequence")
        if not hmac.compare_digest(mac, _epoch_mac(key, value)):
            raise ValueError("snapshot trust epoch MAC diverged")
        return value
    except (KeyError, TypeError, ValueError, UnicodeError, json.JSONDecodeError) as exc:
        raise SharedStateIntegrityError("snapshot trust epoch is invalid") from exc


def _secure_file_exists(directory: Path, name: str) -> bool:
    try:
        _read_secure_file(directory, name)
    except FileNotFoundError:
        return False
    return True


def _write_epoch(directory: Path, key: bytes, payload: dict[str, object]) -> None:
    encoded = dict(payload)
    encoded["epoch_mac"] = _epoch_mac(key, payload)
    _replace_secure_file(directory, _EPOCH_STATE_NAME, serialized_json_bytes(encoded))


def _epoch_mac(key: bytes, payload: dict[str, object]) -> str:
    digest = canonical_digest(payload, CanonicalizationPolicy())
    value = hmac.new(key, digest.encode("utf-8"), hashlib.sha256)
    return f"hmac-sha256:{value.hexdigest()}"


def _anchor_generation(value: dict[str, object]) -> int:
    parsed = value.get("generation")
    if not isinstance(parsed, int) or isinstance(parsed, bool) or parsed < 0:
        raise SharedStateIntegrityError("snapshot project anchor is invalid")
    return parsed


def _positive_text(value: dict[str, object], name: str) -> str:
    parsed = value.get(name)
    if not isinstance(parsed, str) or not parsed:
        raise ValueError(f"snapshot trust epoch {name} is invalid")
    return parsed


def _positive_int(value: dict[str, object], name: str) -> int:
    parsed = value.get(name)
    if not isinstance(parsed, int) or isinstance(parsed, bool) or parsed < 1:
        raise ValueError(f"snapshot trust epoch {name} is invalid")
    return parsed


def _non_negative_int(value: dict[str, object], name: str) -> int:
    parsed = value.get(name)
    if not isinstance(parsed, int) or isinstance(parsed, bool) or parsed < 0:
        raise ValueError(f"snapshot trust epoch {name} is invalid")
    return parsed
