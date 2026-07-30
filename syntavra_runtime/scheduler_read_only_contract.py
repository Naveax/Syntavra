from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import stat
from pathlib import Path
from typing import Any, Final, Iterable

CONTRACT_VERSION: Final = 1
SCHEMA_VERSION: Final = 1
DATABASE_NAME: Final = "scheduler.sqlite3"
MAXIMUM_DATABASE_BYTES: Final = 64 * 1024 * 1024
MAXIMUM_STATES: Final = 16
MAXIMUM_LIMIT: Final = 1000
ROUTES: Final = ("scheduler.list", "scheduler.stats")
CAPABILITIES: Final = {route: route for route in ROUTES}
ALLOWED_STATES: Final = frozenset(
    {"queued", "running", "succeeded", "failed", "dead-letter", "cancelled"}
)
INPUT_PROFILE: Final = "selected-state-root-quiescent-scheduler-sqlite-v1"
INPUT_FORMAT: Final = "implicit-database-path+canonical-json"

_SCHEDULED_JOB_COLUMNS: Final = (
    ("job_id", "TEXT", 0, 1),
    ("project_id", "TEXT", 1, 0),
    ("argv_json", "TEXT", 1, 0),
    ("priority", "INTEGER", 1, 0),
    ("state", "TEXT", 1, 0),
    ("attempt", "INTEGER", 1, 0),
    ("max_attempts", "INTEGER", 1, 0),
    ("timeout_seconds", "REAL", 1, 0),
    ("sandbox_profile", "TEXT", 1, 0),
    ("resource_class", "TEXT", 1, 0),
    ("metadata_json", "TEXT", 1, 0),
    ("scheduled_at", "REAL", 1, 0),
    ("created_at", "REAL", 1, 0),
    ("updated_at", "REAL", 1, 0),
    ("lease_owner", "TEXT", 1, 0),
    ("lease_until", "REAL", 1, 0),
    ("last_error", "TEXT", 1, 0),
    ("result_json", "TEXT", 1, 0),
)
_TABLE_COLUMNS: Final = {
    "scheduled_jobs": _SCHEDULED_JOB_COLUMNS,
    "job_dependencies": (
        ("job_id", "TEXT", 1, 1),
        ("dependency_id", "TEXT", 1, 2),
    ),
    "scheduler_events": (
        ("sequence", "INTEGER", 0, 1),
        ("job_id", "TEXT", 1, 0),
        ("event", "TEXT", 1, 0),
        ("payload_json", "TEXT", 1, 0),
        ("created_at", "REAL", 1, 0),
    ),
}
_REQUIRED_INDEXES: Final = frozenset(
    {"scheduled_jobs_ready_idx", "scheduled_jobs_project_idx"}
)
_SIDECAR_SUFFIXES: Final = ("-journal", "-shm", "-wal")


class SchedulerReadOnlyError(RuntimeError):
    pass


def _error(code: str, cause: BaseException | None = None) -> SchedulerReadOnlyError:
    error = SchedulerReadOnlyError(code)
    if cause is not None:
        error.__cause__ = cause
    return error


def canonical_states(values: Iterable[str] = ()) -> tuple[str, ...]:
    rows: list[str] = []
    seen: set[str] = set()
    for value in values:
        state = str(value).strip().casefold()
        if not state or state not in ALLOWED_STATES:
            raise _error("SCHEDULER_READ_ONLY_STATE_INVALID")
        if state not in seen:
            seen.add(state)
            rows.append(state)
    if len(rows) > MAXIMUM_STATES:
        raise _error("SCHEDULER_READ_ONLY_TOO_MANY_STATES")
    return tuple(sorted(rows))


def canonical_limit(value: int = 100) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise _error("SCHEDULER_READ_ONLY_LIMIT_INVALID", exc)
    return max(1, min(parsed, MAXIMUM_LIMIT))


def canonical_request_bytes(action: str, *, states: Iterable[str] = (), limit: int = 100) -> bytes:
    route = str(action).strip().casefold()
    if route not in ROUTES:
        raise _error("SCHEDULER_READ_ONLY_ROUTE_UNSUPPORTED")
    payload: dict[str, Any] = {"route": route}
    if route == "scheduler.list":
        payload["states"] = list(canonical_states(states))
        payload["limit"] = canonical_limit(limit)
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def rust_argv(
    action: str,
    state_root: Path,
    *,
    states: Iterable[str] = (),
    limit: int = 100,
) -> tuple[str, ...]:
    route = str(action).strip().casefold()
    if route == "scheduler.stats":
        return ("scheduler", "stats", str(state_root))
    if route == "scheduler.list":
        encoded_states = json.dumps(
            list(canonical_states(states)),
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8").hex()
        return (
            "scheduler",
            "list",
            str(state_root),
            str(canonical_limit(limit)),
            encoded_states,
        )
    raise _error("SCHEDULER_READ_ONLY_ROUTE_UNSUPPORTED")


def _regular_metadata(path: Path, code_prefix: str) -> os.stat_result | None:
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


def _database_path(state_root: Path) -> Path:
    root = Path(state_root)
    try:
        root_metadata = root.lstat()
    except FileNotFoundError:
        return root / DATABASE_NAME
    except OSError as exc:
        raise _error("SCHEDULER_READ_ONLY_STATE_ROOT_METADATA_FAILED", exc)
    if stat.S_ISLNK(root_metadata.st_mode):
        raise _error("SCHEDULER_READ_ONLY_STATE_ROOT_SYMLINK")
    if not stat.S_ISDIR(root_metadata.st_mode):
        raise _error("SCHEDULER_READ_ONLY_STATE_ROOT_NOT_DIRECTORY")
    return root / DATABASE_NAME


def _source_identity(database: Path) -> tuple[int, int, int, int]:
    metadata = _regular_metadata(database, "SCHEDULER_READ_ONLY_DATABASE")
    if metadata is None:
        raise _error("SCHEDULER_READ_ONLY_DATABASE_DISAPPEARED")
    return (
        int(metadata.st_dev),
        int(metadata.st_ino),
        int(metadata.st_size),
        int(metadata.st_mtime_ns),
    )


def _reject_sidecars(database: Path) -> None:
    for suffix in _SIDECAR_SUFFIXES:
        path = Path(f"{database}{suffix}")
        if _regular_metadata(path, "SCHEDULER_READ_ONLY_SIDECAR") is not None:
            raise _error("SCHEDULER_READ_ONLY_SIDECAR_PRESENT")


def _open_database(database: Path) -> sqlite3.Connection:
    metadata = _regular_metadata(database, "SCHEDULER_READ_ONLY_DATABASE")
    if metadata is None:
        raise _error("SCHEDULER_READ_ONLY_DATABASE_MISSING")
    if int(metadata.st_size) > MAXIMUM_DATABASE_BYTES:
        raise _error("SCHEDULER_READ_ONLY_DATABASE_TOO_LARGE")
    uri = f"{database.as_uri()}?mode=ro&immutable=1"
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(uri, uri=True, timeout=0.0, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        connection.execute("PRAGMA trusted_schema=OFF")
        query_only = connection.execute("PRAGMA query_only").fetchone()
    except sqlite3.Error as exc:
        if connection is not None:
            connection.close()
        raise _error("SCHEDULER_READ_ONLY_DATABASE_OPEN_FAILED", exc)
    if query_only is None or int(query_only[0]) != 1:
        connection.close()
        raise _error("SCHEDULER_READ_ONLY_QUERY_ONLY_FAILED")
    return connection


def _validate_schema(connection: sqlite3.Connection) -> None:
    try:
        objects = {
            (str(row[0]), str(row[1]))
            for row in connection.execute(
                "SELECT type,name FROM sqlite_master WHERE type IN ('table','index')"
            )
        }
        for table, expected in _TABLE_COLUMNS.items():
            if ("table", table) not in objects:
                raise _error("SCHEDULER_READ_ONLY_SCHEMA_MISSING")
            actual = tuple(
                (str(row[1]), str(row[2]).upper(), int(row[3]), int(row[5]))
                for row in connection.execute(f"PRAGMA table_info({table})")
            )
            if actual != expected:
                raise _error("SCHEDULER_READ_ONLY_SCHEMA_COLUMNS_INVALID")
        indexes = {name for kind, name in objects if kind == "index"}
        if not _REQUIRED_INDEXES.issubset(indexes):
            raise _error("SCHEDULER_READ_ONLY_SCHEMA_INDEX_INVALID")
        foreign_keys = tuple(connection.execute("PRAGMA foreign_key_list(job_dependencies)"))
        if len(foreign_keys) != 1:
            raise _error("SCHEDULER_READ_ONLY_SCHEMA_FOREIGN_KEY_INVALID")
        row = foreign_keys[0]
        if str(row[2]) != "scheduled_jobs" or str(row[3]) != "job_id" or str(row[4]) != "job_id":
            raise _error("SCHEDULER_READ_ONLY_SCHEMA_FOREIGN_KEY_INVALID")
    except SchedulerReadOnlyError:
        raise
    except sqlite3.Error as exc:
        raise _error("SCHEDULER_READ_ONLY_SCHEMA_QUERY_FAILED", exc)


def _validate_json_columns(row: dict[str, Any]) -> None:
    for name, expected_type in (
        ("argv_json", list),
        ("metadata_json", dict),
        ("result_json", dict),
    ):
        try:
            value = json.loads(str(row[name]))
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise _error("SCHEDULER_READ_ONLY_ROW_JSON_INVALID", exc)
        if not isinstance(value, expected_type):
            raise _error("SCHEDULER_READ_ONLY_ROW_JSON_INVALID")


def _empty_result(route: str) -> dict[str, Any]:
    if route == "scheduler.stats":
        return {"states": {}, "projects": 0, "database_integrity": True}
    return {"jobs": []}


def scheduler_read_only_result(
    state_root: Path,
    action: str,
    *,
    states: Iterable[str] = (),
    limit: int = 100,
) -> dict[str, Any]:
    route = str(action).strip().casefold()
    if route not in ROUTES:
        raise _error("SCHEDULER_READ_ONLY_ROUTE_UNSUPPORTED")
    selected_states = canonical_states(states)
    selected_limit = canonical_limit(limit)
    database = _database_path(Path(state_root))
    metadata = _regular_metadata(database, "SCHEDULER_READ_ONLY_DATABASE")
    if metadata is None:
        _reject_sidecars(database)
        return _empty_result(route)

    _reject_sidecars(database)
    identity_before = _source_identity(database)
    connection = _open_database(database)
    try:
        _validate_schema(connection)
        if route == "scheduler.stats":
            state_rows = connection.execute(
                "SELECT state,COUNT(*) FROM scheduled_jobs GROUP BY state ORDER BY state"
            )
            state_counts = {str(row[0]): int(row[1]) for row in state_rows}
            projects_row = connection.execute(
                "SELECT COUNT(DISTINCT project_id) FROM scheduled_jobs"
            ).fetchone()
            integrity_row = connection.execute("PRAGMA integrity_check").fetchone()
            result = {
                "states": state_counts,
                "projects": int(projects_row[0]) if projects_row is not None else 0,
                "database_integrity": bool(integrity_row and str(integrity_row[0]) == "ok"),
            }
        else:
            query = "SELECT * FROM scheduled_jobs"
            params: list[Any] = []
            if selected_states:
                query += " WHERE state IN (" + ",".join("?" for _ in selected_states) + ")"
                params.extend(selected_states)
            query += " ORDER BY created_at DESC,job_id DESC LIMIT ?"
            params.append(selected_limit)
            jobs = [dict(row) for row in connection.execute(query, tuple(params))]
            for row in jobs:
                _validate_json_columns(row)
            result = {"jobs": jobs}
    except SchedulerReadOnlyError:
        raise
    except sqlite3.Error as exc:
        raise _error("SCHEDULER_READ_ONLY_QUERY_FAILED", exc)
    finally:
        connection.close()

    _reject_sidecars(database)
    if _source_identity(database) != identity_before:
        raise _error("SCHEDULER_READ_ONLY_SOURCE_CHANGED")
    return result


def request_digest(action: str, *, states: Iterable[str] = (), limit: int = 100) -> str:
    return hashlib.sha256(
        canonical_request_bytes(action, states=states, limit=limit)
    ).hexdigest()


__all__ = [
    "ALLOWED_STATES",
    "CAPABILITIES",
    "CONTRACT_VERSION",
    "INPUT_FORMAT",
    "INPUT_PROFILE",
    "MAXIMUM_DATABASE_BYTES",
    "MAXIMUM_LIMIT",
    "ROUTES",
    "SCHEMA_VERSION",
    "SchedulerReadOnlyError",
    "canonical_limit",
    "canonical_request_bytes",
    "canonical_states",
    "request_digest",
    "rust_argv",
    "scheduler_read_only_result",
]
