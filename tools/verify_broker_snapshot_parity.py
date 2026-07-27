#!/usr/bin/env python3
from __future__ import annotations

import json
import sqlite3
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from syntavra_runtime.broker_snapshot_contract import (
    BROKER_SCHEMA_VERSION,
    BrokerSnapshotError,
    snapshot_broker_database,
)
from syntavra_runtime.state_snapshot_contract import project_id_for_root

ROOT = Path(__file__).resolve().parents[1]


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


def _rust_json(project_id: str, project_root: Path, database: Path) -> dict[str, Any]:
    completed = subprocess.run(
        [
            "cargo",
            "run",
            "--quiet",
            "--locked",
            "-p",
            "syntavra-cli",
            "--",
            "state",
            "broker-snapshot",
            project_id,
            str(project_root),
            str(database.relative_to(project_root)),
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=180,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"Rust broker snapshot failed ({completed.returncode}): {completed.stderr.strip()}"
        )
    value = json.loads(completed.stdout)
    if not isinstance(value, dict):
        raise RuntimeError("Rust broker snapshot must be a JSON object")
    return value


def _rust_error(project_id: str, project_root: Path, database_path: str | Path) -> str:
    completed = subprocess.run(
        [
            "cargo",
            "run",
            "--quiet",
            "--locked",
            "-p",
            "syntavra-cli",
            "--",
            "state",
            "broker-snapshot",
            project_id,
            str(project_root),
            str(database_path),
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=180,
    )
    if completed.returncode == 0:
        raise RuntimeError("Rust accepted an invalid R9 fixture")
    return completed.stderr.splitlines()[0].strip()


def _python_error(project_id: str, project_root: Path, database_path: str | Path) -> str:
    try:
        snapshot_broker_database(
            project_root,
            database_path,
            expected_project_id=project_id,
        )
    except BrokerSnapshotError as exc:
        return exc.code
    raise RuntimeError("Python accepted an invalid R9 fixture")


def _invalid_case(
    name: str,
    expected_error: str,
    project_id: str,
    project_root: Path,
    database_path: str | Path,
) -> None:
    python_error = _python_error(project_id, project_root, database_path)
    rust_error = _rust_error(project_id, project_root, database_path)
    if python_error != expected_error or rust_error != expected_error:
        raise RuntimeError(
            f"R9 invalid parity failed: {name}: "
            f"python={python_error!r} rust={rust_error!r}"
        )


def verify() -> dict[str, Any]:
    valid: list[str] = []
    invalid: list[str] = []

    with tempfile.TemporaryDirectory(prefix="syntavra-r9-populated-") as directory:
        root = Path(directory)
        project_id = project_id_for_root(root)
        database = root / ".syntavra" / "runtime-v3" / "broker.sqlite3"
        _create_schema(database, project_id)
        python_value = snapshot_broker_database(
            root,
            database.relative_to(root),
            expected_project_id=project_id,
        )
        rust_value = _rust_json(project_id, root, database)
        if python_value != rust_value:
            raise RuntimeError("R9 populated logical snapshot parity failed")
        valid.append("populated-broker-schema-v2")

    with tempfile.TemporaryDirectory(prefix="syntavra-r9-empty-") as directory:
        root = Path(directory)
        project_id = project_id_for_root(root)
        database = root / ".syntavra" / "runtime-v3" / "broker.sqlite3"
        _create_schema(database, project_id, populated=False)
        python_value = snapshot_broker_database(
            root,
            database.relative_to(root),
            expected_project_id=project_id,
        )
        rust_value = _rust_json(project_id, root, database)
        if python_value != rust_value:
            raise RuntimeError("R9 empty logical snapshot parity failed")
        valid.append("empty-broker-schema-v2")

    with tempfile.TemporaryDirectory(prefix="syntavra-r9-invalid-") as directory:
        root = Path(directory)
        project_id = project_id_for_root(root)
        relative = Path(".syntavra/runtime-v3/broker.sqlite3")
        database = root / relative
        _create_schema(database, project_id)
        wrong = "0" * 64 if project_id != "0" * 64 else "1" * 64
        _invalid_case("project-mismatch", "BROKER_PROJECT_MISMATCH", wrong, root, relative)
        invalid.append("project-mismatch")

        Path(f"{database}-wal").write_bytes(b"wal")
        _invalid_case(
            "wal-sidecar",
            "BROKER_DATABASE_SIDECAR_PRESENT",
            project_id,
            root,
            relative,
        )
        Path(f"{database}-wal").unlink()
        invalid.append("wal-sidecar")

        with sqlite3.connect(database) as db:
            db.execute("UPDATE metadata SET value='3' WHERE key='schema_version'")
        _invalid_case(
            "schema-version",
            "BROKER_SCHEMA_VERSION_UNSUPPORTED",
            project_id,
            root,
            relative,
        )
        invalid.append("schema-version")

        database.unlink()
        _create_schema(database, project_id)
        with sqlite3.connect(database) as db:
            db.execute("CREATE TABLE unexpected(value TEXT)")
        _invalid_case(
            "unknown-table",
            "BROKER_SCHEMA_OBJECT_MISMATCH",
            project_id,
            root,
            relative,
        )
        invalid.append("unknown-table")

        database.unlink()
        _create_schema(database, project_id)
        with sqlite3.connect(database) as db:
            db.execute("UPDATE jobs SET argv_json='not-json' WHERE job_id='job-1'")
        _invalid_case(
            "invalid-json",
            "BROKER_JSON_INVALID",
            project_id,
            root,
            relative,
        )
        invalid.append("invalid-json")

        database.unlink()
        _create_schema(database, project_id)
        with sqlite3.connect(database) as db:
            db.execute("UPDATE jobs SET project_id=?", ("0" * 64,))
        _invalid_case(
            "job-project-mismatch",
            "BROKER_JOB_PROJECT_MISMATCH",
            project_id,
            root,
            relative,
        )
        invalid.append("job-project-mismatch")

    return {
        "ok": True,
        "phase": "R9",
        "valid": valid,
        "invalid": invalid,
        "claim": "RUST_BROKER_SQLITE_LOGICAL_READ_PARITY_PROVEN_R9_FIXTURES",
        "boundaries": {
            "database_read": True,
            "database_write": False,
            "quiescent_only": True,
            "wal_snapshot": False,
            "migration": False,
        },
    }


def main() -> int:
    print(json.dumps(verify(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
