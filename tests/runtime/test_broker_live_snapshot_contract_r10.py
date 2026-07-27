from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Iterator

import pytest

import syntavra_runtime.broker_live_snapshot_contract as live_contract
from syntavra_runtime.broker_live_snapshot_contract import (
    BrokerLiveSnapshotError,
    snapshot_live_broker_database,
)
from syntavra_runtime.broker_snapshot_contract import BrokerSnapshotError
from syntavra_runtime.state import StateDB
from syntavra_runtime.state_snapshot_contract import project_id_for_root

ROOT = Path(__file__).resolve().parents[2]
CONTRACT = ROOT / "contracts" / "state" / "broker-live-snapshot-v1.json"


def _database(tmp_path: Path) -> Path:
    return tmp_path / ".syntavra" / "runtime-v3" / "broker.sqlite3"


def _insert_fixture_rows(db: sqlite3.Connection, project_id: str) -> None:
    db.execute("INSERT OR REPLACE INTO metadata(key,value) VALUES('channel','pre-release')")
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
            "job-live-1",
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
            "tree-live",
            "environment-live",
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
            "job-live-1",
            "COMPLETED",
            0,
            12.25,
            "sha256:evidence",
            '{"completed_at":12.25,"job_id":"job-live-1","state":"COMPLETED"}',
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
            "cache-live-1",
            '["python","-m","pytest"]',
            "tree-live",
            "environment-live",
            "dependency-live",
            "toolchain-live",
            1,
            0,
            "sha256:verify",
            '["tests/runtime/test_example.py"]',
            13.5,
        ),
    )


def _live_fixture(
    tmp_path: Path,
    *,
    populated: bool = True,
) -> tuple[Path, str, sqlite3.Connection]:
    database = _database(tmp_path)
    project_id = project_id_for_root(tmp_path)
    StateDB(database)
    holder = sqlite3.connect(database, timeout=0.0, isolation_level=None)
    holder.execute("PRAGMA foreign_keys=ON")
    assert holder.execute("PRAGMA journal_mode=WAL").fetchone()[0].casefold() == "wal"
    if populated:
        _insert_fixture_rows(holder, project_id)
    holder.execute("SELECT count(*) FROM metadata").fetchone()
    assert Path(f"{database}-wal").is_file()
    assert Path(f"{database}-shm").is_file()
    return database, project_id, holder


def _snapshot(tmp_path: Path, database: Path, project_id: str) -> dict[str, object]:
    return snapshot_live_broker_database(
        tmp_path,
        database.relative_to(tmp_path),
        expected_project_id=project_id,
    )


def _assert_error(
    expected: str,
    tmp_path: Path,
    database: str | Path,
    project_id: str,
) -> None:
    with pytest.raises(BrokerSnapshotError) as caught:
        snapshot_live_broker_database(
            tmp_path,
            database,
            expected_project_id=project_id,
        )
    assert caught.value.code == expected


def _persistent_bytes(database: Path) -> dict[str, bytes]:
    output = {"database": database.read_bytes()}
    wal = Path(f"{database}-wal")
    if wal.exists():
        output["wal"] = wal.read_bytes()
    return output


def test_r10_contract_matches_runtime_constants() -> None:
    value = json.loads(CONTRACT.read_text(encoding="utf-8"))
    policy = value["backup_policy"]
    assert value["snapshot_id"] == live_contract.SNAPSHOT_ID
    assert value["broker_schema_version"] == 2
    assert policy["pages_per_step"] == live_contract.PAGES_PER_STEP
    assert policy["maximum_database_bytes"] == live_contract.MAXIMUM_DATABASE_BYTES
    assert policy["maximum_duration_milliseconds"] == int(
        live_contract.MAXIMUM_DURATION_SECONDS * 1000
    )
    assert policy["retry_sleep_milliseconds"] == int(
        live_contract.RETRY_SLEEP_SECONDS * 1000
    )


def test_live_wal_snapshot_is_canonical_and_does_not_change_persistent_bytes(
    tmp_path: Path,
) -> None:
    database, project_id, holder = _live_fixture(tmp_path)
    try:
        before = _persistent_bytes(database)
        value = _snapshot(tmp_path, database, project_id)
        after = _persistent_bytes(database)
    finally:
        holder.close()

    assert before == after
    assert value["ok"] is True
    assert value["snapshot_id"] == "syntavra-broker-live-snapshot-v1"
    assert value["broker_schema_version"] == 2
    assert value["project_id"] == project_id
    assert value["database"]["source_open_mode"] == "read-only-live"
    assert value["database"]["source_query_only"] is True
    assert value["database"]["source_journal_mode"] == "wal"
    assert value["database"]["wal_present"] is True
    assert value["database"]["shm_present"] is True
    assert value["database"]["backup_destination"] == "memory"
    assert value["backup"]["complete"] is True
    assert value["backup"]["steps"] >= 1
    assert value["backup"]["final_page_count"] >= 1
    assert value["row_counts"] == {
        "metadata": 2,
        "jobs": 1,
        "completion_events": 1,
        "verifier_results": 1,
    }
    assert value["tables"]["jobs"][0]["argv_json"] == ["python", "-V"]
    assert value["mutation"] == {
        "source_connection_writes": False,
        "checkpoint": False,
        "vacuum": False,
        "migration": False,
        "destination_files": False,
        "persistent_project_state": False,
    }


def test_empty_live_wal_database_is_valid(tmp_path: Path) -> None:
    database, project_id, holder = _live_fixture(tmp_path, populated=False)
    try:
        value = _snapshot(tmp_path, database, project_id)
    finally:
        holder.close()
    assert value["row_counts"] == {
        "metadata": 1,
        "jobs": 0,
        "completion_events": 0,
        "verifier_results": 0,
    }


def test_rollback_journal_and_invalid_wal_shm_pairs_fail_closed(tmp_path: Path) -> None:
    database, project_id, holder = _live_fixture(tmp_path)
    try:
        journal = Path(f"{database}-journal")
        journal.write_bytes(b"journal")
        _assert_error(
            "BROKER_LIVE_ROLLBACK_JOURNAL_PRESENT",
            tmp_path,
            database.relative_to(tmp_path),
            project_id,
        )
        journal.unlink()
    finally:
        holder.close()

    database.unlink(missing_ok=True)
    StateDB(database)
    Path(f"{database}-wal").write_bytes(b"wal-only")
    _assert_error(
        "BROKER_LIVE_WAL_SHM_PAIR_INVALID",
        tmp_path,
        database.relative_to(tmp_path),
        project_id,
    )


def test_sidecar_symlink_fails_closed(tmp_path: Path) -> None:
    database = _database(tmp_path)
    project_id = project_id_for_root(tmp_path)
    StateDB(database)
    target = tmp_path / "sidecar-target"
    target.write_bytes(b"target")
    wal = Path(f"{database}-wal")
    try:
        os.symlink(target, wal)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation is unavailable")
    _assert_error(
        "BROKER_LIVE_SIDECAR_SYMLINK",
        tmp_path,
        database.relative_to(tmp_path),
        project_id,
    )


def test_size_and_deadline_limits_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database, project_id, holder = _live_fixture(tmp_path)
    try:
        monkeypatch.setattr(live_contract, "MAXIMUM_DATABASE_BYTES", 1)
        _assert_error(
            "BROKER_LIVE_DATABASE_TOO_LARGE",
            tmp_path,
            database.relative_to(tmp_path),
            project_id,
        )
        monkeypatch.setattr(live_contract, "MAXIMUM_DATABASE_BYTES", 64 * 1024 * 1024)
        monkeypatch.setattr(live_contract, "MAXIMUM_DURATION_SECONDS", -1.0)
        _assert_error(
            "BROKER_LIVE_BACKUP_TIMEOUT",
            tmp_path,
            database.relative_to(tmp_path),
            project_id,
        )
    finally:
        holder.close()


def test_r9_logical_validation_runs_on_completed_backup(tmp_path: Path) -> None:
    database, project_id, holder = _live_fixture(tmp_path)
    try:
        holder.execute(
            "UPDATE jobs SET argv_json='not-json' WHERE job_id='job-live-1'"
        )
        _assert_error(
            "BROKER_JSON_INVALID",
            tmp_path,
            database.relative_to(tmp_path),
            project_id,
        )
    finally:
        holder.close()


def test_concurrent_writer_does_not_break_transactional_backup(tmp_path: Path) -> None:
    database, project_id, holder = _live_fixture(tmp_path)
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
                        "INSERT OR REPLACE INTO metadata(key,value) VALUES('writer-sequence',?)",
                        (str(sequence),),
                    )
                    sequence += 1
                except sqlite3.OperationalError as exc:
                    if "locked" not in str(exc).casefold() and "busy" not in str(exc).casefold():
                        failures.append(exc)
                        return
                time.sleep(0.001)
        finally:
            connection.close()

    thread = threading.Thread(target=writer, daemon=True)
    thread.start()
    try:
        value = _snapshot(tmp_path, database, project_id)
    finally:
        stop.set()
        thread.join(timeout=5.0)
        holder.close()

    assert not thread.is_alive()
    assert not failures
    assert value["ok"] is True
    assert value["backup"]["complete"] is True
    assert value["broker_schema_version"] == 2
