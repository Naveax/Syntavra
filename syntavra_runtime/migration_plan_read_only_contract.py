from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sqlite3
import stat
from pathlib import Path
from typing import Any, Final

CONTRACT_VERSION: Final = 1
SCHEMA_VERSION: Final = 1
CAPABILITY: Final = "migration.plan"
ROUTE: Final = "migration.plan"
MAXIMUM_DATABASE_BYTES: Final = 64 * 1024 * 1024
MAXIMUM_PATH_BYTES: Final = 4096
INPUT_PROFILE: Final = "project-bound-quiescent-migration-sqlite-v1"
INPUT_FORMAT: Final = "canonical-project-relative-path"

_SIDECAR_SUFFIXES: Final = ("-journal", "-shm", "-wal")
_IDENTITY_RE: Final = re.compile(r"^[0-9a-f]{64}$")
_EXPECTED_COLUMNS: Final = (
    ("version", "INTEGER", 0, 1),
    ("name", "TEXT", 1, 0),
    ("identity", "TEXT", 1, 0),
    ("applied_at", "REAL", 1, 0),
)


class MigrationPlanReadOnlyError(RuntimeError):
    pass


def _error(code: str, cause: BaseException | None = None) -> MigrationPlanReadOnlyError:
    error = MigrationPlanReadOnlyError(code)
    if cause is not None:
        error.__cause__ = cause
    return error


def _metadata(path: Path, prefix: str) -> os.stat_result | None:
    try:
        value = path.lstat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise _error(f"{prefix}_METADATA_FAILED", exc)
    if stat.S_ISLNK(value.st_mode):
        raise _error(f"{prefix}_SYMLINK")
    return value


def _project_root(project_root: Path) -> Path:
    lexical = Path(os.path.abspath(os.fspath(project_root)))
    metadata = _metadata(lexical, "MIGRATION_PLAN_PROJECT_ROOT")
    if metadata is not None and not stat.S_ISDIR(metadata.st_mode):
        raise _error("MIGRATION_PLAN_PROJECT_ROOT_NOT_DIRECTORY")
    return lexical.resolve(strict=False)


def resolve_database(project_root: Path, database_input: str | Path) -> tuple[Path, str]:
    root = _project_root(project_root)
    raw = str(database_input).strip()
    encoded = raw.encode("utf-8", errors="strict")
    if not raw or "\x00" in raw or len(encoded) > MAXIMUM_PATH_BYTES:
        raise _error("MIGRATION_PLAN_DATABASE_PATH_INVALID")
    candidate = Path(raw)
    joined = candidate if candidate.is_absolute() else root / candidate
    lexical = Path(os.path.abspath(os.fspath(joined)))
    try:
        relative = lexical.relative_to(root)
    except ValueError as exc:
        raise _error("MIGRATION_PLAN_DATABASE_PATH_ESCAPE", exc)
    if relative == Path(".") or not relative.parts:
        raise _error("MIGRATION_PLAN_DATABASE_PATH_INVALID")

    current = root
    for part in relative.parts[:-1]:
        current /= part
        metadata = _metadata(current, "MIGRATION_PLAN_DATABASE_PARENT")
        if metadata is None:
            break
        if not stat.S_ISDIR(metadata.st_mode):
            raise _error("MIGRATION_PLAN_DATABASE_PARENT_NOT_DIRECTORY")
    _metadata(lexical, "MIGRATION_PLAN_DATABASE")
    selected = lexical.resolve(strict=False)
    try:
        selected.relative_to(root)
    except ValueError as exc:
        raise _error("MIGRATION_PLAN_DATABASE_PATH_ESCAPE", exc)
    logical = relative.as_posix()
    return selected, logical


def canonical_request_bytes(project_root: Path, database_input: str | Path) -> bytes:
    _database, logical = resolve_database(project_root, database_input)
    return json.dumps(
        {"route": ROUTE, "database": logical},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def rust_argv(project_root: Path, database_input: str | Path) -> tuple[str, ...]:
    _database, logical = resolve_database(project_root, database_input)
    return ("migration", "plan", str(_project_root(project_root)), logical.encode("utf-8").hex())


def request_digest(project_root: Path, database_input: str | Path) -> str:
    return hashlib.sha256(canonical_request_bytes(project_root, database_input)).hexdigest()


def _reject_sidecars(database: Path) -> None:
    for suffix in _SIDECAR_SUFFIXES:
        sidecar = Path(f"{database}{suffix}")
        metadata = _metadata(sidecar, "MIGRATION_PLAN_SIDECAR")
        if metadata is not None:
            if not stat.S_ISREG(metadata.st_mode):
                raise _error("MIGRATION_PLAN_SIDECAR_NOT_FILE")
            raise _error("MIGRATION_PLAN_SIDECAR_PRESENT")


def _identity(database: Path) -> tuple[int, int, int, int]:
    metadata = _metadata(database, "MIGRATION_PLAN_DATABASE")
    if metadata is None:
        raise _error("MIGRATION_PLAN_DATABASE_DISAPPEARED")
    if not stat.S_ISREG(metadata.st_mode):
        raise _error("MIGRATION_PLAN_DATABASE_NOT_FILE")
    return (
        int(metadata.st_dev),
        int(metadata.st_ino),
        int(metadata.st_size),
        int(metadata.st_mtime_ns),
    )


def _open(database: Path) -> sqlite3.Connection:
    identity = _identity(database)
    if identity[2] > MAXIMUM_DATABASE_BYTES:
        raise _error("MIGRATION_PLAN_DATABASE_TOO_LARGE")
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(
            f"{database.as_uri()}?mode=ro&immutable=1",
            uri=True,
            timeout=0.0,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        connection.execute("PRAGMA trusted_schema=OFF")
        query_only = connection.execute("PRAGMA query_only").fetchone()
    except sqlite3.Error as exc:
        if connection is not None:
            connection.close()
        raise _error("MIGRATION_PLAN_DATABASE_OPEN_FAILED", exc)
    if query_only is None or int(query_only[0]) != 1:
        connection.close()
        raise _error("MIGRATION_PLAN_QUERY_ONLY_FAILED")
    return connection


def _table_exists(connection: sqlite3.Connection) -> bool:
    try:
        row = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='syntavra_schema_migrations'"
        ).fetchone()
    except sqlite3.Error as exc:
        raise _error("MIGRATION_PLAN_SCHEMA_QUERY_FAILED", exc)
    return row is not None


def _migration_rows(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    if not _table_exists(connection):
        return []
    try:
        columns = tuple(
            (str(row[1]), str(row[2]).upper(), int(row[3]), int(row[5]))
            for row in connection.execute("PRAGMA table_info(syntavra_schema_migrations)")
        )
        if columns != _EXPECTED_COLUMNS:
            raise _error("MIGRATION_PLAN_SCHEMA_COLUMNS_INVALID")
        rows = [
            {
                "version": int(row[0]),
                "name": str(row[1]),
                "identity": str(row[2]),
                "applied_at": float(row[3]),
            }
            for row in connection.execute(
                "SELECT version,name,identity,applied_at "
                "FROM syntavra_schema_migrations ORDER BY version"
            )
        ]
    except MigrationPlanReadOnlyError:
        raise
    except (sqlite3.Error, TypeError, ValueError, OverflowError) as exc:
        raise _error("MIGRATION_PLAN_ROWS_INVALID", exc)

    versions: set[int] = set()
    for row in rows:
        version = row["version"]
        name = row["name"]
        identity = row["identity"]
        applied_at = row["applied_at"]
        if version < 1 or version in versions:
            raise _error("MIGRATION_PLAN_VERSION_INVALID")
        versions.add(version)
        if not name or len(name.encode("utf-8")) > 1024:
            raise _error("MIGRATION_PLAN_NAME_INVALID")
        if _IDENTITY_RE.fullmatch(identity) is None:
            raise _error("MIGRATION_PLAN_IDENTITY_INVALID")
        if not math.isfinite(applied_at) or applied_at < 0:
            raise _error("MIGRATION_PLAN_APPLIED_AT_INVALID")
    return rows


def migration_plan_read_only_result(
    project_root: Path,
    database_input: str | Path,
) -> dict[str, Any]:
    database, logical = resolve_database(project_root, database_input)
    metadata = _metadata(database, "MIGRATION_PLAN_DATABASE")
    if metadata is None:
        _reject_sidecars(database)
        return {
            "database": logical,
            "current_version": 0,
            "target_version": 0,
            "pending": [],
        }
    if not stat.S_ISREG(metadata.st_mode):
        raise _error("MIGRATION_PLAN_DATABASE_NOT_FILE")

    _reject_sidecars(database)
    before = _identity(database)
    connection = _open(database)
    try:
        rows = _migration_rows(connection)
    finally:
        connection.close()
    _reject_sidecars(database)
    if _identity(database) != before:
        raise _error("MIGRATION_PLAN_SOURCE_CHANGED")

    current = max((int(row["version"]) for row in rows), default=0)
    return {
        "database": logical,
        "current_version": current,
        "target_version": current,
        "pending": [],
    }


__all__ = [
    "CAPABILITY",
    "CONTRACT_VERSION",
    "INPUT_FORMAT",
    "INPUT_PROFILE",
    "MAXIMUM_DATABASE_BYTES",
    "MAXIMUM_PATH_BYTES",
    "MigrationPlanReadOnlyError",
    "ROUTE",
    "SCHEMA_VERSION",
    "canonical_request_bytes",
    "migration_plan_read_only_result",
    "request_digest",
    "resolve_database",
    "rust_argv",
]
