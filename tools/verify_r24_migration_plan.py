#!/usr/bin/env python3
from __future__ import annotations

import json
import sqlite3
import subprocess
import tempfile
from pathlib import Path

from syntavra_runtime.migration_plan_read_only_contract import migration_plan_read_only_result

ROOT = Path(__file__).resolve().parents[1]


def _plain_database(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE payload(value TEXT NOT NULL)")
    connection.execute("INSERT INTO payload VALUES('unchanged')")
    connection.commit()
    connection.close()
    return path


def _migration_database(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.execute(
        """
        CREATE TABLE syntavra_schema_migrations(
            version INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            identity TEXT NOT NULL,
            applied_at REAL NOT NULL
        )
        """
    )
    connection.executemany(
        "INSERT INTO syntavra_schema_migrations(version,name,identity,applied_at) "
        "VALUES(?,?,?,?)",
        [
            (1, "initial", "a" * 64, 100.0),
            (4, "fourth", "b" * 64, 400.0),
        ],
    )
    connection.commit()
    connection.close()
    return path


def _rust(project: Path, logical: str, *, success: bool = True) -> dict[str, object] | str:
    completed = subprocess.run(
        [
            "cargo",
            "run",
            "--quiet",
            "--locked",
            "-p",
            "syntavra-cli",
            "--",
            "migration",
            "plan",
            str(project),
            logical.encode("utf-8").hex(),
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=180,
    )
    if success:
        if completed.returncode != 0:
            raise RuntimeError(
                f"Rust migration.plan failed ({completed.returncode}): "
                f"{completed.stderr.strip()}"
            )
        value = json.loads(completed.stdout)
        if not isinstance(value, dict):
            raise RuntimeError("Rust migration.plan output must be an object")
        return value
    if completed.returncode == 0:
        raise RuntimeError("Rust migration.plan unexpectedly accepted a forbidden input")
    return completed.stderr


def verify() -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="syntavra-r24-migration-plan-") as directory:
        project = Path(directory) / "project"
        project.mkdir()

        missing_logical = "data/missing.sqlite3"
        expected_missing = migration_plan_read_only_result(project, missing_logical)
        candidate_missing = _rust(project, missing_logical)
        if candidate_missing != expected_missing:
            raise RuntimeError("Missing database migration.plan parity failed")
        if (project / "data").exists():
            raise RuntimeError("Missing database migration.plan created a parent directory")

        plain_logical = "data/plain.sqlite3"
        plain = _plain_database(project / plain_logical)
        plain_before = (plain.read_bytes(), plain.stat().st_mtime_ns)
        expected_plain = migration_plan_read_only_result(project, plain_logical)
        candidate_plain = _rust(project, plain_logical)
        if candidate_plain != expected_plain:
            raise RuntimeError("Migration-table-absent parity failed")
        if (plain.read_bytes(), plain.stat().st_mtime_ns) != plain_before:
            raise RuntimeError("Migration plan modified a database without a migration table")
        connection = sqlite3.connect(plain)
        try:
            table_count = connection.execute(
                "SELECT COUNT(*) FROM sqlite_master "
                "WHERE type='table' AND name='syntavra_schema_migrations'"
            ).fetchone()[0]
        finally:
            connection.close()
        if table_count != 0:
            raise RuntimeError("Migration plan created the migration table")

        migrated_logical = "state/product.sqlite3"
        migrated = _migration_database(project / migrated_logical)
        migrated_before = (migrated.read_bytes(), migrated.stat().st_mtime_ns)
        expected_migrated = migration_plan_read_only_result(project, migrated_logical)
        candidate_migrated = _rust(project, migrated_logical)
        if candidate_migrated != expected_migrated:
            raise RuntimeError("Populated migration plan parity failed")
        if expected_migrated["current_version"] != 4:
            raise RuntimeError("Migration plan did not report the highest version")
        if (migrated.read_bytes(), migrated.stat().st_mtime_ns) != migrated_before:
            raise RuntimeError("Migration plan changed database bytes or mtime")

        Path(f"{migrated}-wal").write_bytes(b"not-a-wal")
        error = str(_rust(project, migrated_logical, success=False))
        if "MIGRATION_PLAN_SIDECAR_PRESENT" not in error:
            raise RuntimeError("Rust migration plan did not fail closed on a WAL sidecar")

        return {
            "ok": True,
            "phase": "R24",
            "command": "migration.plan",
            "capability": "migration.plan",
            "missing_database": "version-zero-empty-plan",
            "migration_table_absent": "version-zero-no-table-creation",
            "populated_current_version": 4,
            "database_bytes_unchanged": True,
            "database_mtime_unchanged": True,
            "sidecars_created": False,
            "sidecars_rejected": True,
            "fallback_policy": "none",
            "claim": "RUST_MIGRATION_PLAN_READ_ONLY_CLI_PARITY_PROVEN_R24",
        }


def main() -> int:
    print(json.dumps(verify(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
