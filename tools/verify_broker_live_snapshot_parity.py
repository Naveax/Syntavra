#!/usr/bin/env python3
from __future__ import annotations

import json
import sqlite3
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from syntavra_runtime.broker_live_snapshot_contract import snapshot_live_broker_database
from syntavra_runtime.broker_snapshot_contract import BrokerSnapshotError
from syntavra_runtime.state_snapshot_contract import project_id_for_root
from verify_broker_snapshot_parity import _create_schema

ROOT = Path(__file__).resolve().parents[1]


def _open_wal(database: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(database, timeout=0.0, isolation_level=None)
    mode = connection.execute("PRAGMA journal_mode=WAL").fetchone()[0]
    if str(mode).casefold() != "wal":
        connection.close()
        raise RuntimeError("failed to establish WAL fixture")
    connection.execute(
        "INSERT OR REPLACE INTO metadata(key,value) VALUES('live-fixture','1')"
    )
    connection.execute("SELECT count(*) FROM metadata").fetchone()
    if not Path(f"{database}-wal").is_file() or not Path(f"{database}-shm").is_file():
        connection.close()
        raise RuntimeError("WAL/SHM fixture sidecars are missing")
    return connection


def _python_json(project_id: str, root: Path, database: Path) -> dict[str, Any]:
    return snapshot_live_broker_database(
        root,
        database.relative_to(root),
        expected_project_id=project_id,
    )


def _rust_run(
    project_id: str,
    root: Path,
    database_path: str | Path,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "cargo",
            "run",
            "--quiet",
            "--locked",
            "-p",
            "syntavra-cli",
            "--",
            "state",
            "broker-live-snapshot",
            project_id,
            str(root),
            str(database_path),
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=180,
    )


def _rust_json(project_id: str, root: Path, database: Path) -> dict[str, Any]:
    completed = _rust_run(project_id, root, database.relative_to(root))
    if completed.returncode != 0:
        raise RuntimeError(
            f"Rust live broker snapshot failed ({completed.returncode}): "
            f"{completed.stderr.strip()}"
        )
    value = json.loads(completed.stdout)
    if not isinstance(value, dict):
        raise RuntimeError("Rust live broker snapshot must be a JSON object")
    return value


def _normalized(value: dict[str, Any]) -> dict[str, Any]:
    copied = json.loads(json.dumps(value))
    copied.pop("snapshot_hash", None)
    copied["database"].pop("source_changed_during_backup", None)
    return copied


def _python_error(project_id: str, root: Path, database_path: str | Path) -> str:
    try:
        snapshot_live_broker_database(
            root,
            database_path,
            expected_project_id=project_id,
        )
    except BrokerSnapshotError as exc:
        return exc.code
    raise RuntimeError("Python accepted an invalid R10 fixture")


def _rust_error(project_id: str, root: Path, database_path: str | Path) -> str:
    completed = _rust_run(project_id, root, database_path)
    if completed.returncode == 0:
        raise RuntimeError("Rust accepted an invalid R10 fixture")
    return completed.stderr.splitlines()[0].strip()


def _invalid_case(
    name: str,
    expected: str,
    project_id: str,
    root: Path,
    database_path: str | Path,
) -> None:
    python_error = _python_error(project_id, root, database_path)
    rust_error = _rust_error(project_id, root, database_path)
    if python_error != expected or rust_error != expected:
        raise RuntimeError(
            f"R10 invalid parity failed: {name}: "
            f"python={python_error!r} rust={rust_error!r}"
        )


def _concurrent_engine(engine: str) -> None:
    with tempfile.TemporaryDirectory(prefix=f"syntavra-r10-{engine}-writer-") as directory:
        root = Path(directory)
        project_id = project_id_for_root(root)
        database = root / ".syntavra" / "runtime-v3" / "broker.sqlite3"
        _create_schema(database, project_id)
        holder = _open_wal(database)
        for index in range(256):
            holder.execute(
                "INSERT INTO metadata(key,value) VALUES(?,?)",
                (f"padding-{index:04d}", "x" * 1024),
            )
        stop = threading.Event()
        failures: list[BaseException] = []

        def writer() -> None:
            connection = sqlite3.connect(database, timeout=0.1, isolation_level=None)
            try:
                sequence = 0
                while not stop.is_set():
                    try:
                        connection.execute(
                            "INSERT OR REPLACE INTO metadata(key,value) "
                            "VALUES('writer-sequence',?)",
                            (str(sequence),),
                        )
                        sequence += 1
                    except sqlite3.OperationalError as exc:
                        text = str(exc).casefold()
                        if "locked" not in text and "busy" not in text:
                            failures.append(exc)
                            return
                    time.sleep(0.001)
            finally:
                connection.close()

        thread = threading.Thread(target=writer, daemon=True)
        thread.start()
        try:
            value = (
                _python_json(project_id, root, database)
                if engine == "python"
                else _rust_json(project_id, root, database)
            )
        finally:
            stop.set()
            thread.join(timeout=5.0)
            holder.close()
        if thread.is_alive() or failures:
            raise RuntimeError(f"R10 {engine} concurrent writer fixture failed")
        if not value.get("ok") or value.get("broker_schema_version") != 2:
            raise RuntimeError(f"R10 {engine} concurrent snapshot is invalid")
        if not value.get("backup", {}).get("complete"):
            raise RuntimeError(f"R10 {engine} concurrent backup is incomplete")


def verify() -> dict[str, Any]:
    valid: list[str] = []
    invalid: list[str] = []

    with tempfile.TemporaryDirectory(prefix="syntavra-r10-quiescent-") as directory:
        root = Path(directory)
        project_id = project_id_for_root(root)
        database = root / ".syntavra" / "runtime-v3" / "broker.sqlite3"
        _create_schema(database, project_id)
        python_value = _python_json(project_id, root, database)
        rust_value = _rust_json(project_id, root, database)
        if _normalized(python_value) != _normalized(rust_value):
            raise RuntimeError("R10 quiescent online-backup parity failed")
        valid.append("quiescent-broker-schema-v2")

    with tempfile.TemporaryDirectory(prefix="syntavra-r10-wal-") as directory:
        root = Path(directory)
        project_id = project_id_for_root(root)
        database = root / ".syntavra" / "runtime-v3" / "broker.sqlite3"
        _create_schema(database, project_id)
        holder = _open_wal(database)
        try:
            python_value = _python_json(project_id, root, database)
            rust_value = _rust_json(project_id, root, database)
        finally:
            holder.close()
        if _normalized(python_value) != _normalized(rust_value):
            raise RuntimeError("R10 WAL online-backup parity failed")
        valid.append("active-wal-broker-schema-v2")

    _concurrent_engine("python")
    _concurrent_engine("rust")
    valid.extend(("python-concurrent-writer", "rust-concurrent-writer"))

    with tempfile.TemporaryDirectory(prefix="syntavra-r10-invalid-") as directory:
        root = Path(directory)
        project_id = project_id_for_root(root)
        relative = Path(".syntavra/runtime-v3/broker.sqlite3")
        database = root / relative
        _create_schema(database, project_id)
        wrong = "0" * 64 if project_id != "0" * 64 else "1" * 64
        _invalid_case("project-mismatch", "BROKER_PROJECT_MISMATCH", wrong, root, relative)
        invalid.append("project-mismatch")

        Path(f"{database}-journal").write_bytes(b"journal")
        _invalid_case(
            "rollback-journal",
            "BROKER_LIVE_ROLLBACK_JOURNAL_PRESENT",
            project_id,
            root,
            relative,
        )
        Path(f"{database}-journal").unlink()
        invalid.append("rollback-journal")

        Path(f"{database}-wal").write_bytes(b"wal-only")
        _invalid_case(
            "wal-shm-pair",
            "BROKER_LIVE_WAL_SHM_PAIR_INVALID",
            project_id,
            root,
            relative,
        )
        Path(f"{database}-wal").unlink()
        invalid.append("wal-shm-pair")

        connection = sqlite3.connect(database)
        try:
            connection.execute(
                "UPDATE metadata SET value='3' WHERE key='schema_version'"
            )
            connection.commit()
        finally:
            connection.close()
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
        connection = sqlite3.connect(database)
        try:
            connection.execute("UPDATE jobs SET argv_json='not-json' WHERE job_id='job-1'")
            connection.commit()
        finally:
            connection.close()
        _invalid_case(
            "invalid-json",
            "BROKER_JSON_INVALID",
            project_id,
            root,
            relative,
        )
        invalid.append("invalid-json")

    return {
        "ok": True,
        "phase": "R10",
        "valid": valid,
        "invalid": invalid,
        "claim": "RUST_BROKER_SQLITE_LIVE_BACKUP_PARITY_PROVEN_R10_FIXTURES",
        "boundaries": {
            "source_database_write": False,
            "online_backup": True,
            "destination_memory_only": True,
            "wal_snapshot": True,
            "concurrent_writer": True,
            "bounded_bytes": 64 * 1024 * 1024,
            "bounded_duration_ms": 5000,
            "migration": False,
            "restore": False,
        },
    }


def main() -> int:
    print(json.dumps(verify(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
