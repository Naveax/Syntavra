from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Iterable

from .util import atomic_write_json


class MigrationError(RuntimeError):
    pass


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    apply: Callable[[sqlite3.Connection], None]
    checksum: str = ""

    def identity(self) -> str:
        raw = f"{self.version}:{self.name}:{self.checksum}".encode("utf-8")
        return hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True)
class MigrationResult:
    database: str
    before_version: int
    after_version: int
    applied: tuple[str, ...]
    backup_path: str
    duration_ms: float
    ok: bool


class MigrationManager:
    """Transactional SQLite schema migration with backup-before-change rollback."""

    def __init__(self, path: Path, migrations: Iterable[Migration]):
        self.path = Path(path)
        ordered = sorted(migrations, key=lambda item: item.version)
        if any(item.version < 1 for item in ordered):
            raise ValueError("migration versions must be positive")
        if len({item.version for item in ordered}) != len(ordered):
            raise ValueError("duplicate migration version")
        self.migrations = tuple(ordered)
        self.backup_root = self.path.parent / "migration-backups"

    def current_version(self) -> int:
        if not self.path.exists():
            return 0
        db = sqlite3.connect(self.path)
        try:
            self._ensure_table(db)
            row = db.execute("SELECT COALESCE(MAX(version),0) FROM signalcore_schema_migrations").fetchone()
            return int(row[0] or 0)
        finally:
            db.close()

    def plan(self, target_version: int | None = None) -> dict[str, object]:
        current = self.current_version()
        target = target_version if target_version is not None else (self.migrations[-1].version if self.migrations else current)
        pending = [item for item in self.migrations if current < item.version <= target]
        return {
            "database": str(self.path),
            "current_version": current,
            "target_version": target,
            "pending": [{"version": item.version, "name": item.name, "identity": item.identity()} for item in pending],
        }

    def apply(self, *, target_version: int | None = None, dry_run: bool = False) -> MigrationResult:
        plan = self.plan(target_version)
        current = int(plan["current_version"])
        target = int(plan["target_version"])
        pending = [item for item in self.migrations if current < item.version <= target]
        if dry_run or not pending:
            return MigrationResult(str(self.path), current, current if not pending else target, tuple(item.name for item in pending), "", 0.0, True)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        backup = self._backup()
        started = time.perf_counter()
        try:
            db = sqlite3.connect(self.path, isolation_level=None)
            try:
                db.execute("PRAGMA foreign_keys=ON")
                db.execute("PRAGMA journal_mode=WAL")
                db.execute("BEGIN IMMEDIATE")
                self._ensure_table(db)
                for migration in pending:
                    existing = db.execute(
                        "SELECT identity FROM signalcore_schema_migrations WHERE version=?",
                        (migration.version,),
                    ).fetchone()
                    if existing:
                        if str(existing[0]) != migration.identity():
                            raise MigrationError(f"migration identity mismatch at version {migration.version}")
                        continue
                    migration.apply(db)
                    db.execute(
                        "INSERT INTO signalcore_schema_migrations(version,name,identity,applied_at) VALUES(?,?,?,?)",
                        (migration.version, migration.name, migration.identity(), time.time()),
                    )
                db.execute(f"PRAGMA user_version={target}")
                db.execute("COMMIT")
                integrity = db.execute("PRAGMA integrity_check").fetchone()
                if not integrity or integrity[0] != "ok":
                    raise MigrationError("database integrity check failed after migration")
            finally:
                db.close()
        except Exception as exc:
            self._restore(backup)
            raise MigrationError(f"migration failed and backup was restored: {exc}") from exc
        duration = (time.perf_counter() - started) * 1000
        result = MigrationResult(
            str(self.path), current, target, tuple(item.name for item in pending), str(backup), duration, True,
        )
        atomic_write_json(self.path.with_suffix(self.path.suffix + ".migration.json"), asdict(result), mode=0o600)
        return result

    def rollback(self, backup_path: Path) -> None:
        backup = Path(backup_path)
        if not backup.is_file():
            raise MigrationError("migration backup does not exist")
        self._restore(backup)

    def _backup(self) -> Path:
        self.backup_root.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        backup = self.backup_root / f"{self.path.name}.{stamp}.{os.getpid()}.bak"
        if self.path.exists():
            source = sqlite3.connect(self.path)
            target = sqlite3.connect(backup)
            try:
                source.backup(target)
                target.commit()
            finally:
                target.close()
                source.close()
        else:
            sqlite3.connect(backup).close()
        try:
            os.chmod(backup, 0o600)
        except OSError:
            pass
        return backup

    def _restore(self, backup: Path) -> None:
        for suffix in ("-wal", "-shm"):
            Path(str(self.path) + suffix).unlink(missing_ok=True)
        temp = self.path.with_name(self.path.name + ".restore")
        shutil.copy2(backup, temp)
        os.replace(temp, self.path)

    @staticmethod
    def _ensure_table(db: sqlite3.Connection) -> None:
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS signalcore_schema_migrations(
                version INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                identity TEXT NOT NULL,
                applied_at REAL NOT NULL
            )
            """
        )
