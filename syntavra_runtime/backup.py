from __future__ import annotations

import json
import os
import shutil
import sqlite3
import tarfile
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .crypto import KeyRing, open_sealed_file, seal_file
from .util import atomic_write_json, sha256_file


class BackupError(RuntimeError):
    pass


@dataclass(frozen=True)
class BackupResult:
    path: str
    files: int
    plaintext_bytes: int
    encrypted: bool
    created_at: float
    manifest_hash: str


class StateBackupManager:
    """Point-in-time local backup with SQLite backup API and optional encryption."""

    def __init__(self, state_root: Path, *, project_id: str):
        self.state_root = Path(state_root).resolve(strict=False)
        self.project_id = project_id
        self.backup_root = self.state_root / "backups"
        self.backup_root.mkdir(parents=True, exist_ok=True)
        self.keyring = KeyRing(self.state_root / "backup-keys", project_id=project_id)

    def create(self, destination: Path, *, encrypt: bool = True) -> BackupResult:
        destination = Path(destination).resolve(strict=False)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="syntavra-backup-") as temp_name:
            staging = Path(temp_name) / "state"
            staging.mkdir()
            manifest: dict[str, Any] = {"schema_version": 1, "project_id": self.project_id, "created_at": time.time(), "files": {}}
            for source in sorted(self.state_root.rglob("*")):
                if not source.is_file() or source.is_symlink():
                    continue
                try:
                    relative = source.relative_to(self.state_root)
                except ValueError:
                    continue
                if relative.parts and relative.parts[0] in {"backups", "backup-keys", "tmp"}:
                    continue
                target = staging / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                if source.suffix in {".sqlite", ".sqlite3", ".db"}:
                    self._backup_sqlite(source, target)
                else:
                    shutil.copy2(source, target)
                manifest["files"][relative.as_posix()] = {
                    "sha256": sha256_file(target),
                    "bytes": target.stat().st_size,
                }
            atomic_write_json(staging / "BACKUP_MANIFEST.json", manifest, mode=0o600)
            archive = Path(temp_name) / "state.tar"
            with tarfile.open(archive, "w") as handle:
                for path in sorted(staging.rglob("*")):
                    handle.add(path, arcname=path.relative_to(staging), recursive=False)
            if encrypt:
                key_id, key, _ = self.keyring.active()
                temp_destination = destination.with_name(destination.name + ".tmp")
                temp_destination.unlink(missing_ok=True)
                seal_file(archive, temp_destination, master_key=key, project_id=self.project_id, key_id=key_id)
                os.replace(temp_destination, destination)
            else:
                shutil.copy2(archive, destination)
            return BackupResult(
                str(destination),
                len(manifest["files"]),
                archive.stat().st_size,
                encrypt,
                float(manifest["created_at"]),
                sha256_file(staging / "BACKUP_MANIFEST.json"),
            )

    def verify(self, source: Path, *, encrypted: bool = True) -> dict[str, Any]:
        with tempfile.TemporaryDirectory(prefix="syntavra-verify-") as temp_name:
            archive = self._materialize(Path(source), Path(temp_name), encrypted)
            extracted = Path(temp_name) / "extracted"
            self._safe_extract(archive, extracted)
            manifest_path = extracted / "BACKUP_MANIFEST.json"
            if not manifest_path.is_file():
                raise BackupError("backup manifest missing")
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if manifest.get("project_id") != self.project_id:
                raise BackupError("backup project scope mismatch")
            failures: list[str] = []
            for relative, expected in (manifest.get("files") or {}).items():
                path = extracted / relative
                try:
                    path.resolve(strict=False).relative_to(extracted.resolve(strict=False))
                except ValueError:
                    failures.append(relative + ":escape")
                    continue
                if not path.is_file() or sha256_file(path) != expected.get("sha256"):
                    failures.append(relative)
            return {"ok": not failures, "files": len(manifest.get("files") or {}), "failures": failures}

    def restore(self, source: Path, *, encrypted: bool = True, dry_run: bool = True) -> dict[str, Any]:
        verification = self.verify(source, encrypted=encrypted)
        if not verification["ok"]:
            raise BackupError("backup verification failed")
        if dry_run:
            return {"ok": True, "dry_run": True, **verification}
        with tempfile.TemporaryDirectory(prefix="syntavra-restore-") as temp_name:
            temp = Path(temp_name)
            archive = self._materialize(Path(source), temp, encrypted)
            extracted = temp / "extracted"
            self._safe_extract(archive, extracted)
            rollback = self.backup_root / f"pre-restore-{int(time.time())}.scbackup"
            self.create(rollback, encrypt=True)
            manifest = json.loads((extracted / "BACKUP_MANIFEST.json").read_text(encoding="utf-8"))
            restored = 0
            for relative in sorted(manifest.get("files") or {}):
                source_path = extracted / relative
                target = self.state_root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                temporary = target.with_name(target.name + ".restore-tmp")
                shutil.copy2(source_path, temporary)
                os.replace(temporary, target)
                restored += 1
            return {"ok": True, "dry_run": False, "restored": restored, "rollback": str(rollback)}

    @staticmethod
    def _backup_sqlite(source: Path, target: Path) -> None:
        try:
            source_db = sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True, timeout=5)
            target_db = sqlite3.connect(target)
            try:
                source_db.backup(target_db)
                if target_db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise BackupError(f"SQLite backup failed integrity check: {source}")
            finally:
                target_db.close()
                source_db.close()
        except sqlite3.DatabaseError:
            shutil.copy2(source, target)

    def _materialize(self, source: Path, temp: Path, encrypted: bool) -> Path:
        if not source.is_file():
            raise BackupError("backup file missing")
        if not encrypted:
            return source
        from .crypto import inspect_sealed_file
        info = inspect_sealed_file(source)
        key = self.keyring.get(info.key_id)
        archive = temp / "state.tar"
        open_sealed_file(source, archive, master_key=key, project_id=self.project_id)
        return archive

    @staticmethod
    def _safe_extract(archive: Path, destination: Path) -> None:
        destination.mkdir(parents=True, exist_ok=True)
        with tarfile.open(archive, "r") as handle:
            root = destination.resolve(strict=False)
            for member in handle.getmembers():
                target = (destination / member.name).resolve(strict=False)
                try:
                    target.relative_to(root)
                except ValueError as exc:
                    raise BackupError("archive path traversal detected") from exc
                if member.issym() or member.islnk() or member.isdev():
                    raise BackupError("backup contains unsupported special file")
            for member in handle.getmembers():
                target = destination / member.name
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                if not member.isfile():
                    raise BackupError("backup contains unsupported non-file member")
                target.parent.mkdir(parents=True, exist_ok=True)
                source = handle.extractfile(member)
                if source is None:
                    raise BackupError("backup member could not be read")
                temporary = target.with_name(target.name + ".extract-tmp")
                try:
                    with temporary.open("wb") as output:
                        shutil.copyfileobj(source, output)
                        output.flush()
                        os.fsync(output.fileno())
                    os.replace(temporary, target)
                finally:
                    source.close()
                    temporary.unlink(missing_ok=True)
