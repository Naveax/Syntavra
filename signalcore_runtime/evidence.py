from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import sqlite3
import struct
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, BinaryIO, Iterable, Iterator

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from .util import atomic_write_bytes, atomic_write_json, sha256_bytes


_MAGIC = b"SCEV1\x00"
_NONCE_BYTES = 12
_TAG_BYTES = 16
_KEY_BYTES = 32
_CHUNK_BYTES = 1024 * 1024


class EvidenceError(RuntimeError):
    pass


class EvidenceKeyring:
    """Versioned local keyring with per-project HKDF-derived data keys.

    Keys may be injected through ``SIGNALCORE_EVIDENCE_KEY`` (base64 or hex) for
    managed deployments. Otherwise a local 256-bit master key is created with
    mode 0600. The master key is never written into evidence metadata.
    """

    def __init__(self, root: Path, *, project_id: str, master_key: bytes | None = None):
        self.root = root
        self.project_id = project_id
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._managed_key = master_key or self._key_from_env()
        if self._managed_key is not None and len(self._managed_key) != _KEY_BYTES:
            raise EvidenceError("managed evidence key must decode to exactly 32 bytes")
        self._active_version = self._load_active_version()
        if self._managed_key is None:
            self._ensure_local_key(self._active_version)

    @staticmethod
    def _key_from_env() -> bytes | None:
        raw = os.environ.get("SIGNALCORE_EVIDENCE_KEY", "").strip()
        if not raw:
            return None
        try:
            if len(raw) == 64 and all(ch in "0123456789abcdefABCDEF" for ch in raw):
                return bytes.fromhex(raw)
            return base64.urlsafe_b64decode(raw + "=" * ((4 - len(raw) % 4) % 4))
        except (ValueError, TypeError) as exc:
            raise EvidenceError("SIGNALCORE_EVIDENCE_KEY is not valid hex/base64") from exc

    def _load_active_version(self) -> int:
        marker = self.root / "active.json"
        if not marker.is_file():
            atomic_write_json(marker, {"schema_version": 1, "active_version": 1}, mode=0o600)
            return 1
        try:
            payload = json.loads(marker.read_text(encoding="utf-8"))
            version = int(payload["active_version"])
        except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
            raise EvidenceError("evidence keyring active marker is invalid") from exc
        if version < 1:
            raise EvidenceError("evidence key version must be positive")
        return version

    def _key_path(self, version: int) -> Path:
        return self.root / f"master-v{version}.key"

    def _ensure_local_key(self, version: int) -> None:
        path = self._key_path(version)
        if path.exists():
            if path.stat().st_size != _KEY_BYTES:
                raise EvidenceError(f"evidence key has invalid size: {path}")
            return
        try:
            atomic_write_bytes(path, secrets.token_bytes(_KEY_BYTES), mode=0o600)
        except OSError as exc:
            raise EvidenceError("unable to create evidence encryption key") from exc

    @property
    def active_version(self) -> int:
        return self._active_version

    def master_key(self, version: int) -> bytes:
        if version < 1:
            raise EvidenceError("invalid evidence key version")
        if self._managed_key is not None:
            if version != 1:
                raise EvidenceError("managed key mode does not expose historical local keys")
            return self._managed_key
        path = self._key_path(version)
        if not path.is_file():
            raise EvidenceError(f"evidence key version unavailable: {version}")
        key = path.read_bytes()
        if len(key) != _KEY_BYTES:
            raise EvidenceError("evidence key file is corrupt")
        return key

    def data_key(self, version: int) -> bytes:
        salt = hashlib.sha256(("signalcore:evidence:" + self.project_id).encode("utf-8")).digest()
        return HKDF(
            algorithm=hashes.SHA256(),
            length=_KEY_BYTES,
            salt=salt,
            info=f"signalcore-evidence-v{version}".encode("ascii"),
        ).derive(self.master_key(version))

    def rotate(self) -> int:
        if self._managed_key is not None:
            raise EvidenceError("managed evidence keys must be rotated by the deployment key provider")
        with self._lock:
            next_version = self._active_version + 1
            self._ensure_local_key(next_version)
            atomic_write_json(
                self.root / "active.json",
                {"schema_version": 1, "active_version": next_version, "rotated_at": time.time()},
                mode=0o600,
            )
            self._active_version = next_version
            return next_version


class EvidenceStore:
    """Encrypted, project-scoped, content-addressed exact evidence store.

    Plaintext SHA-256 remains the stable external handle. Object bytes are stored
    as AES-256-GCM ciphertext with project/digest-bound AAD. Metadata maintains a
    reference graph and retention timestamps; raw evidence is never persisted in
    plaintext by this implementation.
    """

    schema_version = 3

    def __init__(
        self,
        root: Path,
        *,
        project_id: str,
        master_key: bytes | None = None,
        default_ttl_seconds: int | None = None,
    ):
        self.root = Path(root)
        self.project_id = str(project_id)
        self.objects = self.root / "objects"
        self.metadata = self.root / "metadata"
        self.keys = EvidenceKeyring(self.root / "keys", project_id=self.project_id, master_key=master_key)
        self.index_path = self.root / "evidence.sqlite3"
        self.default_ttl_seconds = default_ttl_seconds
        self._lock = threading.RLock()
        self.objects.mkdir(parents=True, exist_ok=True)
        self.metadata.mkdir(parents=True, exist_ok=True)
        self._initialize_index()

    @staticmethod
    def _parse_handle(handle: str) -> str:
        prefix = "sc://sha256/"
        if not handle.startswith(prefix):
            raise EvidenceError("invalid evidence handle")
        digest = handle[len(prefix):]
        if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
            raise EvidenceError("invalid evidence digest")
        return digest

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.index_path, timeout=30, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA busy_timeout=30000")
        db.execute("PRAGMA synchronous=FULL")
        return db

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def _initialize_index(self) -> None:
        with self._transaction() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS evidence_objects(
                    digest TEXT PRIMARY KEY,
                    plaintext_bytes INTEGER NOT NULL,
                    stored_bytes INTEGER NOT NULL,
                    key_version INTEGER NOT NULL,
                    created_at REAL NOT NULL,
                    last_accessed_at REAL NOT NULL,
                    expires_at REAL,
                    ref_count INTEGER NOT NULL DEFAULT 0,
                    legal_hold INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS evidence_references(
                    digest TEXT NOT NULL,
                    reference TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    PRIMARY KEY(digest, reference),
                    FOREIGN KEY(digest) REFERENCES evidence_objects(digest) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS evidence_expiry_idx ON evidence_objects(expires_at);
                """
            )

    def _object_path(self, digest: str) -> Path:
        return self.objects / digest[:2] / digest[2:]

    def _metadata_path(self, digest: str) -> Path:
        return self.metadata / f"{digest}.json"

    def _aad(self, digest: str, version: int) -> bytes:
        return json.dumps(
            {"schema": self.schema_version, "project_id": self.project_id, "digest": digest, "key_version": version},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")

    def _encrypt_file(self, plaintext: Path, destination: Path, *, digest: str, version: int) -> int:
        nonce = secrets.token_bytes(_NONCE_BYTES)
        encryptor = Cipher(algorithms.AES(self.keys.data_key(version)), modes.GCM(nonce)).encryptor()
        encryptor.authenticate_additional_data(self._aad(digest, version))
        destination.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=".encrypted-", dir=destination.parent)
        temp = Path(temp_name)
        try:
            with os.fdopen(fd, "wb") as output, plaintext.open("rb") as source:
                output.write(_MAGIC)
                output.write(struct.pack(">I", version))
                output.write(nonce)
                while chunk := source.read(_CHUNK_BYTES):
                    output.write(encryptor.update(chunk))
                output.write(encryptor.finalize())
                output.write(encryptor.tag)
                output.flush()
                os.fsync(output.fileno())
            try:
                os.chmod(temp, 0o600)
            except OSError:
                pass
            os.replace(temp, destination)
            return destination.stat().st_size
        except OSError as exc:
            raise EvidenceError("evidence encryption/write failed; object was not committed") from exc
        finally:
            temp.unlink(missing_ok=True)

    def _decrypt_to(self, digest: str, destination: BinaryIO) -> int:
        path = self._object_path(digest)
        if not path.is_file():
            raise EvidenceError("evidence object missing")
        total_size = path.stat().st_size
        minimum = len(_MAGIC) + 4 + _NONCE_BYTES + _TAG_BYTES
        if total_size < minimum:
            raise EvidenceError("evidence object is truncated")
        with path.open("rb") as source:
            magic = source.read(len(_MAGIC))
            if magic != _MAGIC:
                raise EvidenceError("plaintext/legacy evidence object rejected; migration required")
            version_raw = source.read(4)
            if len(version_raw) != 4:
                raise EvidenceError("evidence key version missing")
            version = struct.unpack(">I", version_raw)[0]
            nonce = source.read(_NONCE_BYTES)
            if len(nonce) != _NONCE_BYTES:
                raise EvidenceError("evidence nonce missing")
            source.seek(-_TAG_BYTES, os.SEEK_END)
            tag = source.read(_TAG_BYTES)
            ciphertext_start = len(_MAGIC) + 4 + _NONCE_BYTES
            ciphertext_end = total_size - _TAG_BYTES
            source.seek(ciphertext_start)
            decryptor = Cipher(algorithms.AES(self.keys.data_key(version)), modes.GCM(nonce, tag)).decryptor()
            decryptor.authenticate_additional_data(self._aad(digest, version))
            remaining = ciphertext_end - ciphertext_start
            calculated = hashlib.sha256()
            written = 0
            try:
                while remaining:
                    chunk = source.read(min(_CHUNK_BYTES, remaining))
                    if not chunk:
                        raise EvidenceError("evidence ciphertext ended unexpectedly")
                    remaining -= len(chunk)
                    plain = decryptor.update(chunk)
                    destination.write(plain)
                    calculated.update(plain)
                    written += len(plain)
                tail = decryptor.finalize()
                if tail:
                    destination.write(tail)
                    calculated.update(tail)
                    written += len(tail)
            except Exception as exc:
                raise EvidenceError("evidence authentication failed") from exc
        if calculated.hexdigest() != digest:
            raise EvidenceError("evidence plaintext digest mismatch")
        return written

    def _metadata_payload(
        self,
        digest: str,
        size: int,
        stored_size: int,
        *,
        kind: str,
        metadata: dict[str, Any] | None,
        key_version: int,
        expires_at: float | None,
    ) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "digest": digest,
            "bytes": size,
            "stored_bytes": stored_size,
            "project_id": self.project_id,
            "kind": kind,
            "created_at": time.time(),
            "expires_at": expires_at,
            "encryption": {"algorithm": "AES-256-GCM", "key_version": key_version, "mode": "encrypted"},
            "provenance": [metadata or {}],
        }

    def put(
        self,
        data: bytes,
        *,
        kind: str = "generic",
        metadata: dict[str, Any] | None = None,
        ttl_seconds: int | None = None,
        reference: str = "",
    ) -> str:
        return self.put_stream((data,), kind=kind, metadata=metadata, ttl_seconds=ttl_seconds, reference=reference)

    def put_stream(
        self,
        chunks: Iterable[bytes],
        *,
        kind: str = "generic",
        metadata: dict[str, Any] | None = None,
        ttl_seconds: int | None = None,
        reference: str = "",
    ) -> str:
        self.root.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=".evidence-plain-", dir=self.root)
        plaintext = Path(temp_name)
        digest = hashlib.sha256()
        size = 0
        try:
            with os.fdopen(fd, "wb") as handle:
                for chunk in chunks:
                    if not isinstance(chunk, (bytes, bytearray, memoryview)):
                        raise TypeError("evidence stream must yield bytes")
                    value = bytes(chunk)
                    if not value:
                        continue
                    digest.update(value)
                    handle.write(value)
                    size += len(value)
                handle.flush()
                os.fsync(handle.fileno())
            hexdigest = digest.hexdigest()
            object_path = self._object_path(hexdigest)
            key_version = self.keys.active_version
            resolved_ttl = self.default_ttl_seconds if ttl_seconds is None else ttl_seconds
            expires_at = None if resolved_ttl is None or resolved_ttl <= 0 else time.time() + resolved_ttl
            with self._lock:
                if object_path.exists():
                    with tempfile.TemporaryFile("w+b") as verification:
                        self._decrypt_to(hexdigest, verification)
                    stored_size = object_path.stat().st_size
                else:
                    stored_size = self._encrypt_file(
                        plaintext, object_path, digest=hexdigest, version=key_version
                    )
                meta_path = self._metadata_path(hexdigest)
                if meta_path.exists():
                    payload = json.loads(meta_path.read_text(encoding="utf-8"))
                    if payload.get("project_id") != self.project_id:
                        raise EvidenceError("evidence scope mismatch")
                    provenance = list(payload.get("provenance") or [])
                    candidate = metadata or {}
                    if candidate not in provenance:
                        provenance.append(candidate)
                    payload["provenance"] = provenance[-128:]
                    if expires_at is not None:
                        current = payload.get("expires_at")
                        payload["expires_at"] = max(float(current or 0), expires_at)
                    atomic_write_json(meta_path, payload, mode=0o600)
                else:
                    atomic_write_json(
                        meta_path,
                        self._metadata_payload(
                            hexdigest,
                            size,
                            stored_size,
                            kind=kind,
                            metadata=metadata,
                            key_version=key_version,
                            expires_at=expires_at,
                        ),
                        mode=0o600,
                    )
                now = time.time()
                with self._transaction() as db:
                    db.execute(
                        """
                        INSERT INTO evidence_objects(
                            digest,plaintext_bytes,stored_bytes,key_version,created_at,last_accessed_at,expires_at,ref_count
                        ) VALUES(?,?,?,?,?,?,?,?)
                        ON CONFLICT(digest) DO UPDATE SET
                            last_accessed_at=excluded.last_accessed_at,
                            expires_at=CASE
                                WHEN evidence_objects.expires_at IS NULL THEN excluded.expires_at
                                WHEN excluded.expires_at IS NULL THEN evidence_objects.expires_at
                                ELSE MAX(evidence_objects.expires_at, excluded.expires_at)
                            END
                        """,
                        (hexdigest, size, stored_size, key_version, now, now, expires_at, 0),
                    )
                    if reference:
                        inserted = db.execute(
                            "INSERT OR IGNORE INTO evidence_references(digest,reference,created_at) VALUES(?,?,?)",
                            (hexdigest, reference, now),
                        ).rowcount
                        if inserted:
                            db.execute(
                                "UPDATE evidence_objects SET ref_count=ref_count+1 WHERE digest=?",
                                (hexdigest,),
                            )
            return f"sc://sha256/{hexdigest}"
        finally:
            try:
                if plaintext.exists():
                    with plaintext.open("r+b") as handle:
                        length = plaintext.stat().st_size
                        handle.write(b"\x00" * min(length, 1024 * 1024))
                        handle.flush()
                        os.fsync(handle.fileno())
            except OSError:
                pass
            plaintext.unlink(missing_ok=True)

    @staticmethod
    def file_chunks(path: Path, *, chunk_size: int = _CHUNK_BYTES) -> Iterator[bytes]:
        with path.open("rb") as handle:
            while chunk := handle.read(chunk_size):
                yield chunk

    def put_files(
        self,
        paths: Iterable[Path],
        *,
        separator: bytes = b"\n",
        kind: str = "files",
        metadata: dict[str, Any] | None = None,
        ttl_seconds: int | None = None,
        reference: str = "",
    ) -> str:
        ordered = tuple(Path(path) for path in paths)

        def chunks() -> Iterator[bytes]:
            emitted = False
            for path in ordered:
                if not path.is_file():
                    continue
                if emitted and separator:
                    yield separator
                yield from self.file_chunks(path)
                emitted = True

        merged_metadata = {"source_path_hashes": [sha256_bytes(str(path).encode("utf-8")) for path in ordered], **(metadata or {})}
        return self.put_stream(
            chunks(), kind=kind, metadata=merged_metadata, ttl_seconds=ttl_seconds, reference=reference
        )

    def put_file(
        self,
        path: Path,
        *,
        kind: str = "file",
        metadata: dict[str, Any] | None = None,
        ttl_seconds: int | None = None,
        reference: str = "",
    ) -> str:
        return self.put_stream(
            self.file_chunks(path),
            kind=kind,
            metadata={"source_path_hash": sha256_bytes(str(path).encode("utf-8")), **(metadata or {})},
            ttl_seconds=ttl_seconds,
            reference=reference,
        )

    def get(self, handle: str, *, max_bytes: int | None = None) -> bytes:
        digest = self._parse_handle(handle)
        description = self.describe(handle)
        size = int(description.get("bytes", -1))
        if max_bytes is not None and size > max_bytes:
            raise EvidenceError(f"evidence exceeds max_bytes: {size} > {max_bytes}")
        with tempfile.TemporaryFile("w+b") as output:
            written = self._decrypt_to(digest, output)
            if max_bytes is not None and written > max_bytes:
                raise EvidenceError(f"evidence exceeds max_bytes: {written} > {max_bytes}")
            output.seek(0)
            data = output.read()
        with self._transaction() as db:
            db.execute("UPDATE evidence_objects SET last_accessed_at=? WHERE digest=?", (time.time(), digest))
        return data

    def open(self, handle: str) -> BinaryIO:
        digest = self._parse_handle(handle)
        output = tempfile.TemporaryFile("w+b")
        try:
            self._decrypt_to(digest, output)
            output.seek(0)
            with self._transaction() as db:
                db.execute("UPDATE evidence_objects SET last_accessed_at=? WHERE digest=?", (time.time(), digest))
            return output
        except Exception:
            output.close()
            raise

    def describe(self, handle: str) -> dict[str, Any]:
        digest = self._parse_handle(handle)
        meta_path = self._metadata_path(digest)
        if not meta_path.is_file():
            raise EvidenceError("evidence metadata missing")
        value = json.loads(meta_path.read_text(encoding="utf-8"))
        if value.get("project_id") != self.project_id:
            raise EvidenceError("evidence scope mismatch")
        if int(value.get("schema_version", 0)) != self.schema_version:
            raise EvidenceError("unsupported evidence metadata schema; migrate first")
        return value

    def retain(self, handle: str, reference: str) -> bool:
        digest = self._parse_handle(handle)
        if not reference:
            raise EvidenceError("evidence reference must not be empty")
        with self._transaction() as db:
            if db.execute("SELECT 1 FROM evidence_objects WHERE digest=?", (digest,)).fetchone() is None:
                raise EvidenceError("evidence object missing")
            inserted = db.execute(
                "INSERT OR IGNORE INTO evidence_references(digest,reference,created_at) VALUES(?,?,?)",
                (digest, reference, time.time()),
            ).rowcount
            if inserted:
                db.execute("UPDATE evidence_objects SET ref_count=ref_count+1 WHERE digest=?", (digest,))
        return bool(inserted)

    def release(self, handle: str, reference: str) -> bool:
        digest = self._parse_handle(handle)
        with self._transaction() as db:
            deleted = db.execute(
                "DELETE FROM evidence_references WHERE digest=? AND reference=?", (digest, reference)
            ).rowcount
            if deleted:
                db.execute(
                    "UPDATE evidence_objects SET ref_count=MAX(0,ref_count-1) WHERE digest=?", (digest,)
                )
        return bool(deleted)

    def set_legal_hold(self, handle: str, enabled: bool) -> None:
        digest = self._parse_handle(handle)
        with self._transaction() as db:
            changed = db.execute(
                "UPDATE evidence_objects SET legal_hold=? WHERE digest=?", (int(enabled), digest)
            ).rowcount
            if not changed:
                raise EvidenceError("evidence object missing")

    def pin(self, handle: str, reference: str) -> bool:
        return self.retain(handle, reference)

    def unpin(self, handle: str, reference: str) -> bool:
        return self.release(handle, reference)

    def gc(
        self,
        *,
        now: float | None = None,
        ttl_seconds: float | None = None,
        max_delete_bytes: int | None = None,
        dry_run: bool = True,
        limit: int = 1000,
    ) -> dict[str, Any]:
        cutoff_now = time.time() if now is None else float(now)
        age_cutoff = None if ttl_seconds is None else cutoff_now - max(0.0, float(ttl_seconds))
        sql = """
            SELECT digest,plaintext_bytes,stored_bytes FROM evidence_objects
            WHERE ref_count=0 AND legal_hold=0 AND (
                (expires_at IS NOT NULL AND expires_at<=?) OR
                (? IS NOT NULL AND last_accessed_at<=?)
            )
            ORDER BY COALESCE(expires_at,last_accessed_at),last_accessed_at LIMIT ?
        """
        with self._transaction() as db:
            rows = db.execute(sql, (cutoff_now, age_cutoff, age_cutoff, max(1, int(limit)))).fetchall()
            selected = []
            consumed = 0
            for row in rows:
                size = int(row["stored_bytes"])
                if max_delete_bytes is not None and selected and consumed + size > max_delete_bytes:
                    break
                if max_delete_bytes is not None and not selected and size > max_delete_bytes:
                    continue
                selected.append(row)
                consumed += size
            if not dry_run:
                for row in selected:
                    digest = str(row["digest"])
                    self._object_path(digest).unlink(missing_ok=True)
                    self._metadata_path(digest).unlink(missing_ok=True)
                    db.execute("DELETE FROM evidence_objects WHERE digest=?", (digest,))
        return {
            "ok": True,
            "dry_run": dry_run,
            "objects": len(selected),
            "deleted": 0 if dry_run else len(selected),
            "plaintext_bytes": sum(int(row["plaintext_bytes"]) for row in selected),
            "stored_bytes": consumed,
            "bytes_reclaimed": 0 if dry_run else consumed,
        }

    def stats(self) -> dict[str, Any]:
        with self._connect() as db:
            row = db.execute(
                "SELECT COUNT(*) objects,COALESCE(SUM(plaintext_bytes),0) plaintext_bytes,COALESCE(SUM(stored_bytes),0) stored_bytes,COALESCE(SUM(ref_count),0) AS [references] FROM evidence_objects"
            ).fetchone()
            expired = db.execute(
                "SELECT COUNT(*) FROM evidence_objects WHERE expires_at IS NOT NULL AND expires_at<=? AND ref_count=0 AND legal_hold=0",
                (time.time(),),
            ).fetchone()[0]
        return {**dict(row), "collectable": int(expired), "encrypted": True, "active_key_version": self.keys.active_version}

    def verify(self, handle: str) -> bool:
        try:
            with self.open(handle):
                pass
            self.describe(handle)
            return True
        except (OSError, ValueError, EvidenceError, json.JSONDecodeError):
            return False
