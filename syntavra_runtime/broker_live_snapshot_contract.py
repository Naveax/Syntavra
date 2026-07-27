from __future__ import annotations

import hashlib
import os
import sqlite3
import stat
import time
from pathlib import Path
from typing import Any, Final

from .broker_snapshot_contract import (
    BROKER_SCHEMA_VERSION,
    CONTRACT_VERSION,
    DATABASE_NAME,
    TABLE_SPECS,
    BrokerSnapshotError,
    _broker_schema_version,
    _canonical_json_bytes,
    _database_path,
    _foreign_keys,
    _indexes,
    _project_root,
    _schema_objects,
    _table_columns,
    _table_rows,
)

SNAPSHOT_SCHEMA_VERSION: Final = 1
SNAPSHOT_ID: Final = "syntavra-broker-live-snapshot-v1"
MAXIMUM_DATABASE_BYTES: Final = 64 * 1024 * 1024
MAXIMUM_DURATION_SECONDS: Final = 5.0
PAGES_PER_STEP: Final = 64
RETRY_SLEEP_SECONDS: Final = 0.010
_SIDECAR_SUFFIXES: Final = ("-journal", "-shm", "-wal")


class BrokerLiveSnapshotError(BrokerSnapshotError):
    pass


def _error(code: str, cause: BaseException | None = None) -> BrokerLiveSnapshotError:
    error = BrokerLiveSnapshotError(code)
    if cause is not None:
        error.__cause__ = cause
    return error


def _sidecar_path(database: Path, suffix: str) -> Path:
    return Path(f"{database}{suffix}")


def _regular_file_metadata(path: Path, code_prefix: str) -> os.stat_result | None:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise _error(f"{code_prefix}_METADATA_FAILED", exc)
    if stat.S_ISLNK(metadata.st_mode):
        raise _error(f"{code_prefix}_SYMLINK")
    if not stat.S_ISREG(metadata.st_mode):
        raise _error(f"{code_prefix}_NOT_FILE")
    return metadata


def _sidecar_state(database: Path) -> dict[str, bool]:
    state = {
        "journal": _regular_file_metadata(
            _sidecar_path(database, "-journal"),
            "BROKER_LIVE_SIDECAR",
        )
        is not None,
        "shm": _regular_file_metadata(
            _sidecar_path(database, "-shm"),
            "BROKER_LIVE_SIDECAR",
        )
        is not None,
        "wal": _regular_file_metadata(
            _sidecar_path(database, "-wal"),
            "BROKER_LIVE_SIDECAR",
        )
        is not None,
    }
    if state["journal"]:
        raise _error("BROKER_LIVE_ROLLBACK_JOURNAL_PRESENT")
    if state["wal"] != state["shm"]:
        raise _error("BROKER_LIVE_WAL_SHM_PAIR_INVALID")
    return state


def _observed_identity(database: Path, sidecars: dict[str, bool]) -> tuple[tuple[str, int, int], ...]:
    paths = [("database", database)]
    for name in ("shm", "wal"):
        if sidecars[name]:
            paths.append((name, _sidecar_path(database, f"-{name}")))
    output: list[tuple[str, int, int]] = []
    for name, path in paths:
        metadata = _regular_file_metadata(path, "BROKER_LIVE_SOURCE")
        if metadata is None:
            raise _error("BROKER_LIVE_SOURCE_DISAPPEARED")
        output.append((name, int(metadata.st_size), int(metadata.st_mtime_ns)))
    return tuple(output)


def _open_source(database: Path) -> sqlite3.Connection:
    uri = f"{database.as_uri()}?mode=ro"
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(
            uri,
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
        raise _error("BROKER_LIVE_SOURCE_OPEN_FAILED", exc)
    if query_only is None or int(query_only[0]) != 1:
        connection.close()
        raise _error("BROKER_LIVE_QUERY_ONLY_FAILED")
    return connection


def _positive_pragma(connection: sqlite3.Connection, name: str) -> int:
    try:
        row = connection.execute(f"PRAGMA {name}").fetchone()
    except sqlite3.Error as exc:
        raise _error("BROKER_LIVE_SOURCE_METADATA_FAILED", exc)
    if row is None:
        raise _error("BROKER_LIVE_SOURCE_METADATA_FAILED")
    try:
        value = int(row[0])
    except (TypeError, ValueError) as exc:
        raise _error("BROKER_LIVE_SOURCE_METADATA_FAILED", exc)
    if value <= 0:
        raise _error("BROKER_LIVE_SOURCE_METADATA_FAILED")
    return value


def _journal_mode(connection: sqlite3.Connection) -> str:
    try:
        row = connection.execute("PRAGMA journal_mode").fetchone()
    except sqlite3.Error as exc:
        raise _error("BROKER_LIVE_SOURCE_METADATA_FAILED", exc)
    if row is None or not isinstance(row[0], str):
        raise _error("BROKER_LIVE_SOURCE_METADATA_FAILED")
    value = row[0].casefold()
    if value not in {"delete", "memory", "off", "persist", "truncate", "wal"}:
        raise _error("BROKER_LIVE_JOURNAL_MODE_UNSUPPORTED")
    return value


def _validate_size(page_size: int, page_count: int) -> int:
    if page_size <= 0 or page_size > 65536 or page_size & (page_size - 1):
        raise _error("BROKER_LIVE_PAGE_SIZE_INVALID")
    if page_count <= 0:
        raise _error("BROKER_LIVE_PAGE_COUNT_INVALID")
    logical_bytes = page_size * page_count
    if logical_bytes > MAXIMUM_DATABASE_BYTES:
        raise _error("BROKER_LIVE_DATABASE_TOO_LARGE")
    return logical_bytes


def _logical_snapshot(
    connection: sqlite3.Connection,
    expected_project_id: str,
) -> tuple[int, dict[str, list[dict[str, Any]]]]:
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA query_only=ON")
        connection.execute("PRAGMA trusted_schema=OFF")
    except sqlite3.Error as exc:
        raise _error("BROKER_LIVE_DESTINATION_QUERY_ONLY_FAILED", exc)
    _schema_objects(connection)
    for table, spec in TABLE_SPECS.items():
        _table_columns(connection, table, spec[0])
    _indexes(connection)
    _foreign_keys(connection)
    schema_version = _broker_schema_version(connection)
    tables = {
        table: _table_rows(connection, table, spec, expected_project_id)
        for table, spec in TABLE_SPECS.items()
    }
    return schema_version, tables


def snapshot_live_broker_database(
    project_root: str | os.PathLike[str],
    database_path: str | os.PathLike[str],
    *,
    expected_project_id: str,
) -> dict[str, Any]:
    root, actual_project_id = _project_root(project_root, expected_project_id)
    database, relative_path = _database_path(root, database_path)
    sidecars_before = _sidecar_state(database)
    identity_before = _observed_identity(database, sidecars_before)

    source = _open_source(database)
    destination = sqlite3.connect(":memory:", isolation_level=None)
    started = time.monotonic()
    deadline = started + MAXIMUM_DURATION_SECONDS
    progress_state = {"steps": 0, "remaining": 0, "total": 0}
    try:
        journal_mode = _journal_mode(source)
        page_size = _positive_pragma(source, "page_size")
        initial_page_count = _positive_pragma(source, "page_count")
        initial_logical_bytes = _validate_size(page_size, initial_page_count)

        def progress(status: int, remaining: int, total: int) -> None:
            del status
            progress_state["steps"] += 1
            progress_state["remaining"] = int(remaining)
            progress_state["total"] = int(total)
            if time.monotonic() > deadline:
                raise _error("BROKER_LIVE_BACKUP_TIMEOUT")
            if total <= 0 or remaining < 0 or remaining > total:
                raise _error("BROKER_LIVE_BACKUP_PROGRESS_INVALID")
            _validate_size(page_size, int(total))

        try:
            source.backup(
                destination,
                pages=PAGES_PER_STEP,
                progress=progress,
                name="main",
                sleep=RETRY_SLEEP_SECONDS,
            )
        except BrokerLiveSnapshotError:
            raise
        except sqlite3.Error as exc:
            raise _error("BROKER_LIVE_BACKUP_FAILED", exc)
        if time.monotonic() > deadline:
            raise _error("BROKER_LIVE_BACKUP_TIMEOUT")
        if progress_state["steps"] <= 0 or progress_state["remaining"] != 0:
            raise _error("BROKER_LIVE_BACKUP_INCOMPLETE")

        final_page_count = _positive_pragma(destination, "page_count")
        final_logical_bytes = _validate_size(page_size, final_page_count)
        broker_schema_version, tables = _logical_snapshot(
            destination,
            expected_project_id,
        )
    finally:
        destination.close()
        source.close()

    sidecars_after = _sidecar_state(database)
    identity_after = _observed_identity(database, sidecars_after)
    source_changed = identity_after != identity_before or sidecars_after != sidecars_before

    row_counts = {table: len(rows) for table, rows in tables.items()}
    payload: dict[str, Any] = {
        "ok": True,
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "contract_version": CONTRACT_VERSION,
        "snapshot_id": SNAPSHOT_ID,
        "broker_schema_version": broker_schema_version,
        "project_id": actual_project_id,
        "project_binding": {
            "expected": expected_project_id,
            "actual": actual_project_id,
            "matched": True,
        },
        "database": {
            "relative_path": relative_path,
            "source_open_mode": "read-only-live",
            "source_query_only": True,
            "source_journal_mode": journal_mode,
            "wal_present": sidecars_before["wal"],
            "shm_present": sidecars_before["shm"],
            "rollback_journal_present": False,
            "source_changed_during_backup": source_changed,
            "backup_destination": "memory",
        },
        "backup": {
            "api": "sqlite-online-backup",
            "pages_per_step": PAGES_PER_STEP,
            "maximum_database_bytes": MAXIMUM_DATABASE_BYTES,
            "maximum_duration_milliseconds": int(MAXIMUM_DURATION_SECONDS * 1000),
            "retry_sleep_milliseconds": int(RETRY_SLEEP_SECONDS * 1000),
            "page_size": page_size,
            "initial_page_count": initial_page_count,
            "initial_logical_bytes": initial_logical_bytes,
            "final_page_count": final_page_count,
            "final_logical_bytes": final_logical_bytes,
            "steps": progress_state["steps"],
            "complete": True,
        },
        "tables": tables,
        "row_counts": row_counts,
        "mutation": {
            "source_connection_writes": False,
            "checkpoint": False,
            "vacuum": False,
            "migration": False,
            "destination_files": False,
            "persistent_project_state": False,
        },
        "claim": "RUST_BROKER_SQLITE_LIVE_BACKUP_PARITY_PROVEN_R10_FIXTURES",
    }
    payload["snapshot_hash"] = hashlib.sha256(_canonical_json_bytes(payload)).hexdigest()
    return payload


__all__ = [
    "BrokerLiveSnapshotError",
    "MAXIMUM_DATABASE_BYTES",
    "MAXIMUM_DURATION_SECONDS",
    "PAGES_PER_STEP",
    "RETRY_SLEEP_SECONDS",
    "SNAPSHOT_ID",
    "snapshot_live_broker_database",
]
