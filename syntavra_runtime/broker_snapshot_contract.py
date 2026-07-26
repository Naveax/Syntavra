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

from .state_snapshot_contract import StateInspectionError, project_id_for_root

SNAPSHOT_SCHEMA_VERSION: Final = 1
CONTRACT_VERSION: Final = 1
SNAPSHOT_ID: Final = "syntavra-broker-snapshot-v1"
BROKER_SCHEMA_VERSION: Final = 2
DATABASE_NAME: Final = "broker.sqlite3"
FORBIDDEN_SIDECARS: Final = ("-journal", "-shm", "-wal")
_LOWER_HEX_64 = re.compile(r"^[0-9a-f]{64}$")

ColumnSpec = tuple[str, str, bool, int, str | None]
TableSpec = tuple[
    tuple[ColumnSpec, ...],
    tuple[str, ...],
    frozenset[str],
    frozenset[str],
]

TABLE_SPECS: Final[dict[str, TableSpec]] = {
    "metadata": (
        (
            ("key", "TEXT", False, 1, None),
            ("value", "TEXT", True, 0, None),
        ),
        ("key",),
        frozenset(),
        frozenset(),
    ),
    "jobs": (
        (
            ("job_id", "TEXT", False, 1, None),
            ("state", "TEXT", True, 0, None),
            ("argv_json", "TEXT", True, 0, None),
            ("cwd", "TEXT", True, 0, None),
            ("created_at", "REAL", True, 0, None),
            ("started_at", "REAL", False, 0, None),
            ("completed_at", "REAL", False, 0, None),
            ("pid", "INTEGER", False, 0, None),
            ("exit_code", "INTEGER", False, 0, None),
            ("timed_out", "INTEGER", True, 0, "0"),
            ("cancelled", "INTEGER", True, 0, "0"),
            ("summary", "TEXT", True, 0, "''"),
            ("evidence_handle", "TEXT", True, 0, "''"),
            ("error", "TEXT", True, 0, "''"),
            ("timeout_seconds", "REAL", True, 0, "0"),
            ("stdout_path", "TEXT", True, 0, "''"),
            ("stderr_path", "TEXT", True, 0, "''"),
            ("repository_tree", "TEXT", True, 0, "'unknown'"),
            ("environment_hash", "TEXT", True, 0, "'unknown'"),
            ("project_id", "TEXT", True, 0, "''"),
        ),
        ("job_id",),
        frozenset({"argv_json"}),
        frozenset({"cancelled", "timed_out"}),
    ),
    "completion_events": (
        (
            ("sequence", "INTEGER", False, 1, None),
            ("job_id", "TEXT", True, 0, None),
            ("state", "TEXT", True, 0, None),
            ("exit_code", "INTEGER", False, 0, None),
            ("completed_at", "REAL", True, 0, None),
            ("evidence_handle", "TEXT", True, 0, None),
            ("payload_json", "TEXT", True, 0, None),
        ),
        ("sequence",),
        frozenset({"payload_json"}),
        frozenset(),
    ),
    "verifier_results": (
        (
            ("cache_key", "TEXT", False, 1, None),
            ("command_json", "TEXT", True, 0, None),
            ("tree_hash", "TEXT", True, 0, None),
            ("environment_hash", "TEXT", True, 0, None),
            ("dependency_hash", "TEXT", True, 0, None),
            ("toolchain_hash", "TEXT", True, 0, None),
            ("success", "INTEGER", True, 0, None),
            ("exit_code", "INTEGER", True, 0, None),
            ("evidence_handle", "TEXT", True, 0, None),
            ("affected_paths_json", "TEXT", True, 0, None),
            ("created_at", "REAL", True, 0, None),
        ),
        ("cache_key",),
        frozenset({"affected_paths_json", "command_json"}),
        frozenset({"success"}),
    ),
}

EXPECTED_INDEXES: Final = {
    "jobs_state_idx": {
        "table": "jobs",
        "unique": False,
        "columns": (("state", False), ("created_at", True)),
    }
}

EXPECTED_FOREIGN_KEYS: Final = {
    "completion_events": (
        ("jobs", "job_id", "job_id", "NO ACTION", "NO ACTION"),
    )
}


class BrokerSnapshotError(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _canonical_json_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise BrokerSnapshotError("BROKER_ROW_TYPE_INVALID") from exc


def _json_value(value: str) -> Any:
    def reject_constant(_: str) -> None:
        raise ValueError("non-finite JSON constant")

    try:
        return json.loads(value, parse_constant=reject_constant)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise BrokerSnapshotError("BROKER_JSON_INVALID") from exc


def _project_root(
    project_root: str | os.PathLike[str],
    expected_project_id: str,
) -> tuple[Path, str]:
    if not _LOWER_HEX_64.fullmatch(expected_project_id):
        raise BrokerSnapshotError("BROKER_EXPECTED_PROJECT_INVALID")
    try:
        actual = project_id_for_root(project_root)
        canonical = Path(project_root).resolve(strict=True)
    except (OSError, StateInspectionError) as exc:
        raise BrokerSnapshotError("BROKER_PROJECT_ROOT_INVALID") from exc
    if actual != expected_project_id:
        raise BrokerSnapshotError("BROKER_PROJECT_MISMATCH")
    return canonical, actual


def _database_path(root: Path, database_path: str | os.PathLike[str]) -> tuple[Path, str]:
    supplied = Path(database_path)
    candidate = supplied if supplied.is_absolute() else root / supplied
    candidate = Path(os.path.abspath(os.fspath(candidate)))
    try:
        common = Path(os.path.commonpath((os.fspath(root), os.fspath(candidate))))
    except ValueError as exc:
        raise BrokerSnapshotError("BROKER_DATABASE_PATH_ESCAPE") from exc
    if os.path.normcase(os.fspath(common)) != os.path.normcase(os.fspath(root)):
        raise BrokerSnapshotError("BROKER_DATABASE_PATH_ESCAPE")
    relative = Path(os.path.relpath(candidate, root))
    if relative == Path(".") or any(part in {"", ".", ".."} for part in relative.parts):
        raise BrokerSnapshotError("BROKER_DATABASE_PATH_ESCAPE")
    if candidate.name != DATABASE_NAME:
        raise BrokerSnapshotError("BROKER_DATABASE_NAME_INVALID")

    current = root
    for position, part in enumerate(relative.parts):
        current /= part
        try:
            metadata = current.lstat()
        except FileNotFoundError as exc:
            code = (
                "BROKER_DATABASE_MISSING"
                if position == len(relative.parts) - 1
                else "BROKER_DATABASE_PARENT_MISSING"
            )
            raise BrokerSnapshotError(code) from exc
        except OSError as exc:
            raise BrokerSnapshotError("BROKER_DATABASE_METADATA_FAILED") from exc
        if stat.S_ISLNK(metadata.st_mode):
            raise BrokerSnapshotError("BROKER_DATABASE_SYMLINK")
        if position < len(relative.parts) - 1 and not stat.S_ISDIR(metadata.st_mode):
            raise BrokerSnapshotError("BROKER_DATABASE_PARENT_INVALID")
    if not stat.S_ISREG(metadata.st_mode):
        raise BrokerSnapshotError("BROKER_DATABASE_NOT_FILE")
    return candidate, relative.as_posix()


def _sidecar_paths(database: Path) -> tuple[Path, ...]:
    return tuple(Path(f"{database}{suffix}") for suffix in FORBIDDEN_SIDECARS)


def _assert_no_sidecars(database: Path) -> None:
    for sidecar in _sidecar_paths(database):
        try:
            sidecar.lstat()
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise BrokerSnapshotError("BROKER_DATABASE_METADATA_FAILED") from exc
        raise BrokerSnapshotError("BROKER_DATABASE_SIDECAR_PRESENT")


def _file_identity(path: Path) -> tuple[int, int, int, int, int]:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise BrokerSnapshotError("BROKER_DATABASE_METADATA_FAILED") from exc
    return (
        metadata.st_mode,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_dev,
        metadata.st_ino,
    )


def _open_database(path: Path) -> sqlite3.Connection:
    uri = f"{path.as_uri()}?mode=ro&immutable=1"
    try:
        connection = sqlite3.connect(
            uri,
            uri=True,
            timeout=0.0,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        query_only = connection.execute("PRAGMA query_only").fetchone()
    except sqlite3.Error as exc:
        try:
            connection.close()
        except (NameError, sqlite3.Error):
            pass
        raise BrokerSnapshotError("BROKER_DATABASE_OPEN_FAILED") from exc
    if query_only is None or int(query_only[0]) != 1:
        connection.close()
        raise BrokerSnapshotError("BROKER_QUERY_ONLY_FAILED")
    return connection


def _schema_objects(db: sqlite3.Connection) -> None:
    try:
        rows = db.execute(
            "SELECT type,name,tbl_name FROM sqlite_master "
            "WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"
        ).fetchall()
    except sqlite3.Error as exc:
        raise BrokerSnapshotError("BROKER_SCHEMA_READ_FAILED") from exc

    tables = {str(row["name"]) for row in rows if row["type"] == "table"}
    indexes = {str(row["name"]) for row in rows if row["type"] == "index"}
    views = {str(row["name"]) for row in rows if row["type"] == "view"}
    triggers = {str(row["name"]) for row in rows if row["type"] == "trigger"}
    other_types = {str(row["type"]) for row in rows} - {"index", "table", "trigger", "view"}

    if tables != set(TABLE_SPECS):
        raise BrokerSnapshotError("BROKER_SCHEMA_OBJECT_MISMATCH")
    if indexes != set(EXPECTED_INDEXES):
        raise BrokerSnapshotError("BROKER_SCHEMA_INDEX_MISMATCH")
    if views or triggers or other_types:
        raise BrokerSnapshotError("BROKER_SCHEMA_OBJECT_MISMATCH")


def _table_columns(db: sqlite3.Connection, table: str, expected: tuple[ColumnSpec, ...]) -> None:
    try:
        rows = db.execute(f'PRAGMA table_info("{table}")').fetchall()
    except sqlite3.Error as exc:
        raise BrokerSnapshotError("BROKER_SCHEMA_READ_FAILED") from exc
    actual = tuple(
        (
            str(row["name"]),
            str(row["type"]).upper(),
            bool(row["notnull"]),
            int(row["pk"]),
            None if row["dflt_value"] is None else str(row["dflt_value"]),
        )
        for row in rows
    )
    if actual != expected:
        raise BrokerSnapshotError("BROKER_SCHEMA_COLUMN_MISMATCH")


def _indexes(db: sqlite3.Connection) -> None:
    for index_name, spec in EXPECTED_INDEXES.items():
        table = str(spec["table"])
        try:
            listed = db.execute(f'PRAGMA index_list("{table}")').fetchall()
            row = next((item for item in listed if item["name"] == index_name), None)
            detail = db.execute(f'PRAGMA index_xinfo("{index_name}")').fetchall()
        except sqlite3.Error as exc:
            raise BrokerSnapshotError("BROKER_SCHEMA_READ_FAILED") from exc
        if row is None or bool(row["unique"]) != bool(spec["unique"]):
            raise BrokerSnapshotError("BROKER_SCHEMA_INDEX_MISMATCH")
        columns = tuple(
            (str(item["name"]), bool(item["desc"]))
            for item in detail
            if int(item["key"]) == 1
        )
        if columns != spec["columns"]:
            raise BrokerSnapshotError("BROKER_SCHEMA_INDEX_MISMATCH")


def _foreign_keys(db: sqlite3.Connection) -> None:
    for table in TABLE_SPECS:
        try:
            rows = db.execute(f'PRAGMA foreign_key_list("{table}")').fetchall()
        except sqlite3.Error as exc:
            raise BrokerSnapshotError("BROKER_SCHEMA_READ_FAILED") from exc
        actual = tuple(
            (
                str(row["table"]),
                str(row["from"]),
                str(row["to"]),
                str(row["on_update"]),
                str(row["on_delete"]),
            )
            for row in rows
        )
        if actual != EXPECTED_FOREIGN_KEYS.get(table, ()):
            raise BrokerSnapshotError("BROKER_SCHEMA_FOREIGN_KEY_MISMATCH")
    try:
        violations = db.execute("PRAGMA foreign_key_check").fetchone()
    except sqlite3.Error as exc:
        raise BrokerSnapshotError("BROKER_SCHEMA_READ_FAILED") from exc
    if violations is not None:
        raise BrokerSnapshotError("BROKER_FOREIGN_KEY_INVALID")


def _broker_schema_version(db: sqlite3.Connection) -> int:
    try:
        rows = db.execute(
            "SELECT value FROM metadata WHERE key='schema_version'"
        ).fetchall()
    except sqlite3.Error as exc:
        raise BrokerSnapshotError("BROKER_SCHEMA_VERSION_MISSING") from exc
    if len(rows) != 1:
        raise BrokerSnapshotError("BROKER_SCHEMA_VERSION_MISSING")
    try:
        version = int(rows[0]["value"])
    except (TypeError, ValueError) as exc:
        raise BrokerSnapshotError("BROKER_SCHEMA_VERSION_UNSUPPORTED") from exc
    if version != BROKER_SCHEMA_VERSION:
        raise BrokerSnapshotError("BROKER_SCHEMA_VERSION_UNSUPPORTED")
    return version


def _normalize_scalar(value: Any, declared_type: str, nullable: bool) -> Any:
    if value is None:
        if nullable:
            return None
        raise BrokerSnapshotError("BROKER_ROW_TYPE_INVALID")
    if isinstance(value, (bytes, bytearray, memoryview)):
        raise BrokerSnapshotError("BROKER_ROW_TYPE_INVALID")
    if declared_type == "TEXT":
        if not isinstance(value, str):
            raise BrokerSnapshotError("BROKER_ROW_TYPE_INVALID")
        return value
    if declared_type == "INTEGER":
        if isinstance(value, bool) or not isinstance(value, int):
            raise BrokerSnapshotError("BROKER_ROW_TYPE_INVALID")
        return value
    if declared_type == "REAL":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise BrokerSnapshotError("BROKER_ROW_TYPE_INVALID")
        normalized = float(value)
        if not math.isfinite(normalized):
            raise BrokerSnapshotError("BROKER_ROW_TYPE_INVALID")
        return normalized
    raise BrokerSnapshotError("BROKER_SCHEMA_COLUMN_MISMATCH")


def _table_rows(
    db: sqlite3.Connection,
    table: str,
    spec: TableSpec,
    expected_project_id: str,
) -> list[dict[str, Any]]:
    columns, order_by, json_columns, boolean_columns = spec
    column_names = tuple(column[0] for column in columns)
    select_columns = ",".join(f'"{name}"' for name in column_names)
    order = ",".join(f'"{name}"' for name in order_by)
    try:
        rows = db.execute(
            f'SELECT {select_columns} FROM "{table}" ORDER BY {order}'
        ).fetchall()
    except sqlite3.Error as exc:
        raise BrokerSnapshotError("BROKER_ROW_READ_FAILED") from exc

    output: list[dict[str, Any]] = []
    for row in rows:
        normalized: dict[str, Any] = {}
        for name, declared_type, not_null, primary_key, _default in columns:
            nullable = not not_null and primary_key == 0
            value = _normalize_scalar(row[name], declared_type, nullable)
            if name in boolean_columns:
                if value not in {0, 1}:
                    raise BrokerSnapshotError("BROKER_ROW_TYPE_INVALID")
                value = bool(value)
            if name in json_columns:
                if not isinstance(value, str):
                    raise BrokerSnapshotError("BROKER_ROW_TYPE_INVALID")
                value = _json_value(value)
            normalized[name] = value
        if table == "jobs" and normalized["project_id"] != expected_project_id:
            raise BrokerSnapshotError("BROKER_JOB_PROJECT_MISMATCH")
        output.append(normalized)
    return output


def snapshot_broker_database(
    project_root: str | os.PathLike[str],
    database_path: str | os.PathLike[str],
    *,
    expected_project_id: str,
) -> dict[str, Any]:
    root, actual_project_id = _project_root(project_root, expected_project_id)
    database, relative_path = _database_path(root, database_path)
    _assert_no_sidecars(database)
    before = _file_identity(database)

    db = _open_database(database)
    try:
        _schema_objects(db)
        for table, spec in TABLE_SPECS.items():
            _table_columns(db, table, spec[0])
        _indexes(db)
        _foreign_keys(db)
        broker_schema_version = _broker_schema_version(db)
        tables = {
            table: _table_rows(db, table, spec, expected_project_id)
            for table, spec in TABLE_SPECS.items()
        }
    finally:
        db.close()

    after = _file_identity(database)
    _assert_no_sidecars(database)
    if after != before:
        raise BrokerSnapshotError("BROKER_DATABASE_CHANGED_DURING_READ")

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
            "open_mode": "read-only-immutable",
            "query_only": True,
            "quiescent": True,
            "sidecars_present": False,
        },
        "tables": tables,
        "row_counts": row_counts,
        "mutation": {
            "filesystem": False,
            "database": False,
            "sidecars": False,
        },
        "claim": "RUST_BROKER_SQLITE_LOGICAL_READ_PARITY_PROVEN_R9_FIXTURES",
    }
    payload["snapshot_hash"] = hashlib.sha256(_canonical_json_bytes(payload)).hexdigest()
    return payload
