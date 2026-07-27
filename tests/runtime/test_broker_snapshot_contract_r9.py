from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import struct
from pathlib import Path
from typing import Any

import pytest

from syntavra_runtime.broker_snapshot_contract import (
    BROKER_SCHEMA_VERSION,
    EXPECTED_FOREIGN_KEYS,
    EXPECTED_INDEXES,
    TABLE_SPECS,
    BrokerSnapshotError,
    snapshot_broker_database,
)
from syntavra_runtime.state_snapshot_contract import project_id_for_root

ROOT = Path(__file__).resolve().parents[2]
CONTRACT = ROOT / "contracts" / "state" / "broker-snapshot-v1.json"


def _f64(value: float) -> dict[str, str]:
    bits = struct.unpack(">Q", struct.pack(">d", value))[0]
    return {"$f64": f"{bits:016x}"}


def _create_schema(path: Path, project_id: str, *, populated: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    try:
        db.execute("PRAGMA foreign_keys=ON")
        db.executescript(
            """
            CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE jobs(
                job_id TEXT PRIMARY KEY,
                state TEXT NOT NULL,
                argv_json TEXT NOT NULL,
                cwd TEXT NOT NULL,
                created_at REAL NOT NULL,
                started_at REAL,
                completed_at REAL,
                pid INTEGER,
                exit_code INTEGER,
                timed_out INTEGER NOT NULL DEFAULT 0,
                cancelled INTEGER NOT NULL DEFAULT 0,
                summary TEXT NOT NULL DEFAULT '',
                evidence_handle TEXT NOT NULL DEFAULT '',
                error TEXT NOT NULL DEFAULT '',
                timeout_seconds REAL NOT NULL DEFAULT 0,
                stdout_path TEXT NOT NULL DEFAULT '',
                stderr_path TEXT NOT NULL DEFAULT '',
                repository_tree TEXT NOT NULL DEFAULT 'unknown',
                environment_hash TEXT NOT NULL DEFAULT 'unknown',
                project_id TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX jobs_state_idx ON jobs(state, created_at DESC);
            CREATE TABLE completion_events(
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL UNIQUE,
                state TEXT NOT NULL,
                exit_code INTEGER,
                completed_at REAL NOT NULL,
                evidence_handle TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                FOREIGN KEY(job_id) REFERENCES jobs(job_id)
            );
            CREATE TABLE verifier_results(
                cache_key TEXT PRIMARY KEY,
                command_json TEXT NOT NULL,
                tree_hash TEXT NOT NULL,
                environment_hash TEXT NOT NULL,
                dependency_hash TEXT NOT NULL,
                toolchain_hash TEXT NOT NULL,
                success INTEGER NOT NULL,
                exit_code INTEGER NOT NULL,
                evidence_handle TEXT NOT NULL,
                affected_paths_json TEXT NOT NULL,
                created_at REAL NOT NULL
            );
            """
        )
        db.execute(
            "INSERT INTO metadata(key,value) VALUES('schema_version',?)",
            (str(BROKER_SCHEMA_VERSION),),
        )
        db.execute("INSERT INTO metadata(key,value) VALUES('channel','pre-release')")
        if populated:
            db.execute(
                """
                INSERT INTO jobs(
                    job_id,state,argv_json,cwd,created_at,started_at,completed_at,pid,
                    exit_code,timed_out,cancelled,summary,evidence_handle,error,
                    timeout_seconds,stdout_path,stderr_path,repository_tree,
                    environment_hash,project_id
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    "job-1",
                    "COMPLETED",
                    '["python","-V"]',
                    "/workspace",
                    10.5,
                    11.0,
                    12.25,
                    42,
                    0,
                    0,
                    0,
                    "ok",
                    "sha256:evidence",
                    "",
                    30.0,
                    "stdout.log",
                    "stderr.log",
                    "tree-1",
                    "environment-1",
                    project_id,
                ),
            )
            db.execute(
                """
                INSERT INTO completion_events(
                    job_id,state,exit_code,completed_at,evidence_handle,payload_json
                ) VALUES(?,?,?,?,?,?)
                """,
                (
                    "job-1",
                    "COMPLETED",
                    0,
                    12.25,
                    "sha256:evidence",
                    '{"completed_at":12.25,"job_id":"job-1","state":"COMPLETED"}',
                ),
            )
            db.execute(
                """
                INSERT INTO verifier_results(
                    cache_key,command_json,tree_hash,environment_hash,dependency_hash,
                    toolchain_hash,success,exit_code,evidence_handle,
                    affected_paths_json,created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    "cache-1",
                    '["python","-m","pytest"]',
                    "tree-1",
                    "environment-1",
                    "dependency-1",
                    "toolchain-1",
                    1,
                    0,
                    "sha256:verify",
                    '["tests/runtime/test_example.py"]',
                    13.5,
                ),
            )
        db.commit()
    finally:
        db.close()


def _fixture(tmp_path: Path, *, populated: bool = True) -> tuple[Path, str]:
    project_id = project_id_for_root(tmp_path)
    database = tmp_path / ".syntavra" / "runtime-v3" / "broker.sqlite3"
    _create_schema(database, project_id, populated=populated)
    return database, project_id


def _execute_mutation(
    database: Path,
    sql: str,
    parameters: tuple[object, ...] = (),
) -> None:
    db = sqlite3.connect(database)
    try:
        db.execute(sql, parameters)
        db.commit()
    finally:
        db.close()


def _tree_snapshot(root: Path) -> list[tuple[str, int, int, int, bytes | None]]:
    rows: list[tuple[str, int, int, int, bytes | None]] = []
    for path in sorted(root.rglob("*")):
        metadata = path.lstat()
        payload = path.read_bytes() if path.is_file() and not path.is_symlink() else None
        rows.append(
            (
                path.relative_to(root).as_posix(),
                metadata.st_mode,
                metadata.st_size,
                metadata.st_mtime_ns,
                payload,
            )
        )
    return rows


def _snapshot(tmp_path: Path, database: Path, project_id: str) -> dict[str, Any]:
    return snapshot_broker_database(
        tmp_path,
        database.relative_to(tmp_path),
        expected_project_id=project_id,
    )


def _assert_error(
    code: str,
    tmp_path: Path,
    database_path: str | Path,
    project_id: str,
) -> None:
    with pytest.raises(BrokerSnapshotError) as caught:
        snapshot_broker_database(
            tmp_path,
            database_path,
            expected_project_id=project_id,
        )
    assert caught.value.code == code


def test_embedded_schema_specs_match_canonical_contract() -> None:
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    tables = {row["name"]: row for row in contract["tables"]}
    assert set(tables) == set(TABLE_SPECS)
    for table, (columns, order_by, json_columns, boolean_columns) in TABLE_SPECS.items():
        expected = tables[table]
        assert expected["order_by"] == list(order_by)
        assert set(expected["json_columns"]) == set(json_columns)
        assert set(expected["boolean_columns"]) == set(boolean_columns)
        assert [
            (
                row["name"],
                row["type"],
                row["not_null"],
                row["primary_key_position"],
                row["default"],
            )
            for row in expected["columns"]
        ] == list(columns)
    assert {row["name"]: row for row in contract["indexes"]} == {
        name: {
            "name": name,
            "table": spec["table"],
            "unique": spec["unique"],
            "columns": [
                {"name": column, "descending": descending}
                for column, descending in spec["columns"]
            ],
        }
        for name, spec in EXPECTED_INDEXES.items()
    }
    assert EXPECTED_FOREIGN_KEYS["completion_events"] == (
        ("jobs", "job_id", "job_id", "NO ACTION", "NO ACTION"),
    )


def test_populated_snapshot_is_canonical_and_non_mutating(tmp_path: Path) -> None:
    database, project_id = _fixture(tmp_path)
    before = _tree_snapshot(tmp_path)

    value = _snapshot(tmp_path, database, project_id)

    assert value["ok"] is True
    assert value["broker_schema_version"] == 2
    assert value["project_id"] == project_id
    assert value["database"] == {
        "relative_path": ".syntavra/runtime-v3/broker.sqlite3",
        "open_mode": "read-only-immutable",
        "query_only": True,
        "quiescent": True,
        "sidecars_present": False,
    }
    assert value["row_counts"] == {
        "metadata": 2,
        "jobs": 1,
        "completion_events": 1,
        "verifier_results": 1,
    }
    job = value["tables"]["jobs"][0]
    assert job["argv_json"] == ["python", "-V"]
    assert job["timed_out"] is False
    assert job["cancelled"] is False
    assert value["tables"]["completion_events"][0]["payload_json"] == {
        "completed_at": _f64(12.25),
        "job_id": "job-1",
        "state": "COMPLETED",
    }
    verifier = value["tables"]["verifier_results"][0]
    assert verifier["success"] is True
    assert verifier["command_json"] == ["python", "-m", "pytest"]
    assert verifier["affected_paths_json"] == ["tests/runtime/test_example.py"]
    digest = value.pop("snapshot_hash")
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    assert digest == hashlib.sha256(encoded).hexdigest()
    assert _tree_snapshot(tmp_path) == before


def test_empty_schema_snapshot_is_valid(tmp_path: Path) -> None:
    database, project_id = _fixture(tmp_path, populated=False)
    value = _snapshot(tmp_path, database, project_id)
    assert value["row_counts"] == {
        "metadata": 2,
        "jobs": 0,
        "completion_events": 0,
        "verifier_results": 0,
    }


def test_project_and_path_binding_fail_closed(tmp_path: Path) -> None:
    database, project_id = _fixture(tmp_path)
    _assert_error(
        "BROKER_EXPECTED_PROJECT_INVALID",
        tmp_path,
        database,
        "invalid",
    )
    wrong = "0" * 64 if project_id != "0" * 64 else "1" * 64
    _assert_error("BROKER_PROJECT_MISMATCH", tmp_path, database, wrong)

    outside = tmp_path.parent / "broker.sqlite3"
    outside.write_bytes(database.read_bytes())
    try:
        _assert_error("BROKER_DATABASE_PATH_ESCAPE", tmp_path, outside, project_id)
    finally:
        outside.unlink(missing_ok=True)


def test_quiescent_sidecar_policy_fails_closed(tmp_path: Path) -> None:
    database, project_id = _fixture(tmp_path)
    for suffix in ("-journal", "-shm", "-wal"):
        sidecar = Path(f"{database}{suffix}")
        sidecar.write_bytes(b"sidecar")
        _assert_error(
            "BROKER_DATABASE_SIDECAR_PRESENT",
            tmp_path,
            database,
            project_id,
        )
        sidecar.unlink()


def test_schema_version_and_object_drift_fail_closed(tmp_path: Path) -> None:
    database, project_id = _fixture(tmp_path)
    _execute_mutation(
        database,
        "UPDATE metadata SET value='3' WHERE key='schema_version'",
    )
    _assert_error(
        "BROKER_SCHEMA_VERSION_UNSUPPORTED",
        tmp_path,
        database,
        project_id,
    )

    database.unlink()
    _create_schema(database, project_id)
    _execute_mutation(database, "CREATE TABLE unexpected(value TEXT)")
    _assert_error("BROKER_SCHEMA_OBJECT_MISMATCH", tmp_path, database, project_id)

    database.unlink()
    _create_schema(database, project_id)
    _execute_mutation(database, "ALTER TABLE jobs ADD COLUMN unexpected TEXT")
    _assert_error("BROKER_SCHEMA_COLUMN_MISMATCH", tmp_path, database, project_id)


def test_logical_row_validation_fails_closed(tmp_path: Path) -> None:
    database, project_id = _fixture(tmp_path)
    _execute_mutation(
        database,
        "UPDATE jobs SET argv_json='not-json' WHERE job_id='job-1'",
    )
    _assert_error("BROKER_JSON_INVALID", tmp_path, database, project_id)

    database.unlink()
    _create_schema(database, project_id)
    _execute_mutation(
        database,
        "UPDATE jobs SET project_id=? WHERE job_id='job-1'",
        ("0" * 64,),
    )
    _assert_error("BROKER_JOB_PROJECT_MISMATCH", tmp_path, database, project_id)

    database.unlink()
    _create_schema(database, project_id)
    _execute_mutation(
        database,
        "UPDATE jobs SET timed_out=2 WHERE job_id='job-1'",
    )
    _assert_error("BROKER_ROW_TYPE_INVALID", tmp_path, database, project_id)


def test_symlinked_database_component_fails_closed(tmp_path: Path) -> None:
    real = tmp_path / "real-state"
    database = real / "runtime-v3" / "broker.sqlite3"
    project_id = project_id_for_root(tmp_path)
    _create_schema(database, project_id)
    link = tmp_path / ".syntavra"
    try:
        os.symlink(real, link, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation is unavailable")
    _assert_error("BROKER_DATABASE_SYMLINK", tmp_path, link / "runtime-v3" / "broker.sqlite3", project_id)
