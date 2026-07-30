#!/usr/bin/env python3
from __future__ import annotations

import json
import sqlite3
import subprocess
import tempfile
from pathlib import Path

from syntavra_runtime.scheduler_read_only_contract import scheduler_read_only_result

ROOT = Path(__file__).resolve().parents[1]


def _create_database(state_root: Path) -> Path:
    state_root.mkdir(parents=True)
    database = state_root / "scheduler.sqlite3"
    connection = sqlite3.connect(database)
    connection.executescript(
        """
        PRAGMA foreign_keys=ON;
        CREATE TABLE scheduled_jobs(
            job_id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            argv_json TEXT NOT NULL,
            priority INTEGER NOT NULL,
            state TEXT NOT NULL,
            attempt INTEGER NOT NULL,
            max_attempts INTEGER NOT NULL,
            timeout_seconds REAL NOT NULL,
            sandbox_profile TEXT NOT NULL,
            resource_class TEXT NOT NULL,
            metadata_json TEXT NOT NULL,
            scheduled_at REAL NOT NULL,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            lease_owner TEXT NOT NULL DEFAULT '',
            lease_until REAL NOT NULL DEFAULT 0,
            last_error TEXT NOT NULL DEFAULT '',
            result_json TEXT NOT NULL DEFAULT '{}'
        );
        CREATE INDEX scheduled_jobs_ready_idx
            ON scheduled_jobs(state,scheduled_at,priority,created_at);
        CREATE INDEX scheduled_jobs_project_idx
            ON scheduled_jobs(project_id,state);
        CREATE TABLE job_dependencies(
            job_id TEXT NOT NULL,
            dependency_id TEXT NOT NULL,
            PRIMARY KEY(job_id,dependency_id),
            FOREIGN KEY(job_id) REFERENCES scheduled_jobs(job_id) ON DELETE CASCADE
        );
        CREATE TABLE scheduler_events(
            sequence INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT NOT NULL,
            event TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            created_at REAL NOT NULL
        );
        """
    )
    connection.executemany(
        """
        INSERT INTO scheduled_jobs(
            job_id,project_id,argv_json,priority,state,attempt,max_attempts,
            timeout_seconds,sandbox_profile,resource_class,metadata_json,
            scheduled_at,created_at,updated_at,lease_owner,lease_until,last_error,result_json
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        [
            (
                "job-a", "project-a", '["python","-V"]', 1, "queued", 0, 3,
                120.0, "strict", "cpu", '{"kind":"first"}', 1.0, 1.0, 1.0,
                "", 0.0, "", "{}",
            ),
            (
                "job-b", "project-b", '["cargo","test"]', 2, "succeeded", 1, 3,
                240.0, "strict", "cpu", '{"kind":"second"}', 2.0, 2.0, 2.0,
                "", 0.0, "", '{"ok":true}',
            ),
        ],
    )
    connection.commit()
    connection.close()
    return database


def _rust(arguments: list[str]) -> dict[str, object]:
    completed = subprocess.run(
        [
            "cargo",
            "run",
            "--quiet",
            "--locked",
            "-p",
            "syntavra-cli",
            "--",
            *arguments,
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=180,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"Rust scheduler read-only route failed ({completed.returncode}): "
            f"{completed.stderr.strip()}"
        )
    value = json.loads(completed.stdout)
    if not isinstance(value, dict):
        raise RuntimeError("Rust scheduler output must be a JSON object")
    return value


def verify() -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="syntavra-r24-scheduler-") as directory:
        root = Path(directory)
        missing = root / "missing-state"
        expected_empty = scheduler_read_only_result(missing, "scheduler.stats")
        candidate_empty = _rust(["scheduler", "stats", str(missing)])
        if candidate_empty != expected_empty:
            raise RuntimeError("Missing-database scheduler.stats parity failed")
        if missing.exists():
            raise RuntimeError("Missing-database inspection created state")

        state = root / "state"
        database = _create_database(state)
        before = (database.read_bytes(), database.stat().st_mtime_ns)

        expected_stats = scheduler_read_only_result(state, "scheduler.stats")
        candidate_stats = _rust(["scheduler", "stats", str(state)])
        if candidate_stats != expected_stats:
            raise RuntimeError("Python/Rust scheduler.stats results differ")

        states = ["queued", "succeeded"]
        states_hex = json.dumps(states, separators=(",", ":")).encode("utf-8").hex()
        expected_list = scheduler_read_only_result(
            state,
            "scheduler.list",
            states=states,
            limit=25,
        )
        candidate_list = _rust(
            ["scheduler", "list", str(state), "25", states_hex]
        )
        if candidate_list != expected_list:
            raise RuntimeError("Python/Rust scheduler.list results differ")

        after = (database.read_bytes(), database.stat().st_mtime_ns)
        if before != after:
            raise RuntimeError("Scheduler read-only parity changed database bytes or mtime")
        for suffix in ("-journal", "-shm", "-wal"):
            if Path(f"{database}{suffix}").exists():
                raise RuntimeError(f"Scheduler read-only parity created {suffix} sidecar")

        return {
            "ok": True,
            "phase": "R24",
            "commands": ["scheduler.list", "scheduler.stats"],
            "capabilities": ["scheduler.list", "scheduler.stats"],
            "missing_database": "deterministic-empty",
            "populated_database": True,
            "state_filter": states,
            "limit": 25,
            "database_bytes_unchanged": True,
            "database_mtime_unchanged": True,
            "sidecars_created": False,
            "fallback_policy": "none",
            "claim": "RUST_SCHEDULER_READ_ONLY_CLI_PARITY_PROVEN_R24",
        }


def main() -> int:
    print(json.dumps(verify(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
