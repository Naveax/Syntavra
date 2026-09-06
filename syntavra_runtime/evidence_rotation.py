from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from .evidence import _CHUNK_BYTES, EvidenceError, EvidenceStore
from .util import atomic_write_json


def _wipe(path: Path) -> None:
    try:
        if path.exists():
            with path.open("r+b") as handle:
                remaining = path.stat().st_size
                zeroes = b"\x00" * min(_CHUNK_BYTES, max(1, remaining))
                while remaining > 0:
                    chunk = zeroes[: min(len(zeroes), remaining)]
                    handle.write(chunk)
                    remaining -= len(chunk)
                handle.flush()
                os.fsync(handle.fileno())
    except OSError:
        pass
    path.unlink(missing_ok=True)


def _reencrypt_object(
    store: EvidenceStore,
    digest: str,
    *,
    target_version: int,
) -> tuple[bool, int]:
    object_path = store._object_path(digest)
    metadata_path = store._metadata_path(digest)
    if not object_path.is_file() or not metadata_path.is_file():
        raise EvidenceError(f"evidence object is incomplete: {digest}")

    try:
        original_metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        source_version = int(original_metadata["encryption"]["key_version"])
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise EvidenceError(f"evidence metadata is invalid: {digest}") from exc
    if source_version == target_version:
        return False, object_path.stat().st_size

    plaintext_fd, plaintext_name = tempfile.mkstemp(
        prefix=".evidence-rotate-",
        dir=store.root,
    )
    plaintext_path = Path(plaintext_name)
    staging_path = object_path.with_name(f".{object_path.name}.rotate-{target_version}")
    backup_path = object_path.with_name(f".{object_path.name}.rotate-backup")
    restore_path = object_path.with_name(f".{object_path.name}.rotate-restore")
    original_stored_size = object_path.stat().st_size
    backup_created = False
    preserve_backup = False
    try:
        with os.fdopen(plaintext_fd, "w+b") as plaintext:
            store._decrypt_to(digest, plaintext)
            plaintext.flush()
            os.fsync(plaintext.fileno())
        stored_size = store._encrypt_file(
            plaintext_path,
            staging_path,
            digest=digest,
            version=target_version,
        )

        backup_path.unlink(missing_ok=True)
        os.replace(object_path, backup_path)
        backup_created = True
        os.replace(staging_path, object_path)

        updated_metadata = json.loads(json.dumps(original_metadata))
        updated_metadata["stored_bytes"] = stored_size
        updated_metadata["encryption"] = {
            **dict(updated_metadata.get("encryption") or {}),
            "algorithm": "AES-256-GCM",
            "key_version": target_version,
            "mode": "encrypted",
        }
        atomic_write_json(metadata_path, updated_metadata, mode=0o600)
        with store._transaction() as db:
            changed = db.execute(
                "UPDATE evidence_objects SET stored_bytes=?,key_version=? WHERE digest=?",
                (stored_size, target_version, digest),
            ).rowcount
            if changed != 1:
                raise EvidenceError(f"evidence index object missing: {digest}")
        backup_path.unlink(missing_ok=True)
        return True, stored_size
    except Exception as exc:
        rollback_error: Exception | None = None
        if backup_created and backup_path.exists():
            try:
                restore_path.unlink(missing_ok=True)
                # Keep the original encrypted backup intact until every recovery
                # surface (object, metadata and index) has been restored. Recovery
                # is a cold failure path, so one extra local copy is preferable to
                # destroying the last known-good ciphertext on a partial rollback.
                shutil.copy2(backup_path, restore_path)
                os.replace(restore_path, object_path)
                atomic_write_json(metadata_path, original_metadata, mode=0o600)
                with store._transaction() as db:
                    changed = db.execute(
                        "UPDATE evidence_objects SET stored_bytes=?,key_version=? WHERE digest=?",
                        (original_stored_size, source_version, digest),
                    ).rowcount
                    if changed != 1:
                        raise EvidenceError(f"evidence index object missing during rollback: {digest}")
                backup_path.unlink(missing_ok=True)
            except Exception as rollback_exc:
                rollback_error = rollback_exc
                preserve_backup = backup_path.exists()

        if rollback_error is not None:
            detail = f"{type(rollback_error).__name__}: {rollback_error}"
            backup_detail = (
                f"; backup preserved at {backup_path}"
                if preserve_backup
                else "; rollback backup unavailable"
            )
            raise EvidenceError(
                f"evidence key rotation failed for {digest}; rollback failed: {detail}{backup_detail}"
            ) from exc
        if isinstance(exc, EvidenceError):
            raise
        raise EvidenceError(f"evidence key rotation failed for {digest}") from exc
    finally:
        staging_path.unlink(missing_ok=True)
        restore_path.unlink(missing_ok=True)
        if not preserve_backup:
            backup_path.unlink(missing_ok=True)
        _wipe(plaintext_path)


def rotate_evidence_key(
    store: EvidenceStore,
    *,
    reencrypt: bool = True,
) -> dict[str, Any]:
    with store._lock:
        previous_version = store.keys.active_version
        active_version = store.keys.rotate()
        db = store._connect()
        try:
            rows = db.execute(
                "SELECT digest,key_version,stored_bytes "
                "FROM evidence_objects ORDER BY digest"
            ).fetchall()
        finally:
            db.close()

        reencrypted = 0
        skipped = 0
        stored_bytes = 0
        if reencrypt:
            for row in rows:
                changed, size = _reencrypt_object(
                    store,
                    str(row["digest"]),
                    target_version=active_version,
                )
                stored_bytes += size
                if changed:
                    reencrypted += 1
                else:
                    skipped += 1
        else:
            stored_bytes = sum(int(row["stored_bytes"]) for row in rows)
            skipped = len(rows)

        return {
            "ok": True,
            "previous_key_version": previous_version,
            "active_key_version": active_version,
            "reencrypt": bool(reencrypt),
            "objects": len(rows),
            "reencrypted": reencrypted,
            "skipped": skipped,
            "stored_bytes": stored_bytes,
        }
