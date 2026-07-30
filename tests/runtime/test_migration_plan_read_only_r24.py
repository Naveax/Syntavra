from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Mapping

import pytest

from syntavra_runtime.engine_entry import main as engine_main
from syntavra_runtime.engine_selector import (
    ENGINE_CONTRACT_SHA256,
    RUST_CAPABILITIES,
    EngineSelectionError,
    EngineSelector,
)
from syntavra_runtime.migration_plan_read_only_contract import (
    MigrationPlanReadOnlyError,
    migration_plan_read_only_result,
)
from syntavra_runtime.migration_plan_router_r24 import MigrationPlanRouterR24
from syntavra_runtime.unified_cli import main as unified_main


def _create_plain_database(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE payload(value TEXT NOT NULL)")
    connection.execute("INSERT INTO payload VALUES('unchanged')")
    connection.commit()
    connection.close()
    return path


def _create_migration_database(path: Path) -> Path:
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
            (3, "third", "b" * 64, 300.0),
        ],
    )
    connection.commit()
    connection.close()
    return path


def _verification_runner(_binary: Path, arguments: tuple[str, ...]) -> Mapping[str, Any]:
    if arguments == ("version",):
        return {
            "product": "Syntavra",
            "product_version": "0.0.1",
            "release_channel": "pre-release",
            "engine": "rust",
            "engine_stability": "experimental",
            "contract_version": 1,
        }
    if arguments == ("engine", "capabilities"):
        return {
            "contract_version": 1,
            "capabilities": [
                {"name": name, "maturity": "preview", "mutation": "read-only"}
                for name in RUST_CAPABILITIES
            ],
        }
    if arguments == ("engine", "contract-hash"):
        return {
            "engine": "rust",
            "contract_version": 1,
            "algorithm": "sha256",
            "contract_hash": ENGINE_CONTRACT_SHA256,
        }
    raise AssertionError(arguments)


def _selector(project: Path) -> EngineSelector:
    binary = project / "syntavra-rs"
    binary.write_bytes(b"test")
    return EngineSelector(
        project_root=project,
        state_root=project / ".syntavra" / "pre-release",
        env={"HOME": str(project / "home")},
        rust_binary=binary,
        runner=_verification_runner,
    )


def _candidate(_binary: Path, arguments: tuple[str, ...]) -> Mapping[str, Any]:
    assert arguments[:2] == ("migration", "plan")
    project = Path(arguments[2])
    database = bytes.fromhex(arguments[3]).decode("utf-8")
    return migration_plan_read_only_result(project, database)


def test_missing_database_returns_zero_plan_without_creating_state(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    database = "data/missing.sqlite3"

    assert engine_main(
        [
            "--engine",
            "python",
            "--project",
            str(project),
            "migrate",
            "plan",
            database,
        ]
    ) == 0
    expected = {
        "current_version": 0,
        "database": database,
        "pending": [],
        "target_version": 0,
    }
    assert json.loads(capsys.readouterr().out) == expected
    assert not (project / "data").exists()
    assert not (project / ".syntavra").exists()

    assert unified_main(
        ["--project", str(project), "migrate", "plan", database]
    ) == 0
    assert json.loads(capsys.readouterr().out) == expected
    assert not (project / "data").exists()
    assert not (project / ".syntavra").exists()


def test_existing_database_without_migration_table_is_not_modified(tmp_path: Path) -> None:
    project = tmp_path / "project"
    database = _create_plain_database(project / "data" / "plain.sqlite3")
    before = (database.read_bytes(), database.stat().st_mtime_ns)

    result = migration_plan_read_only_result(project, "data/plain.sqlite3")

    assert result == {
        "current_version": 0,
        "database": "data/plain.sqlite3",
        "pending": [],
        "target_version": 0,
    }
    assert (database.read_bytes(), database.stat().st_mtime_ns) == before
    connection = sqlite3.connect(database)
    try:
        assert connection.execute(
            "SELECT COUNT(*) FROM sqlite_master "
            "WHERE type='table' AND name='syntavra_schema_migrations'"
        ).fetchone()[0] == 0
    finally:
        connection.close()
    assert not Path(f"{database}-journal").exists()
    assert not Path(f"{database}-shm").exists()
    assert not Path(f"{database}-wal").exists()


def test_valid_migration_table_reports_highest_version_without_writes(tmp_path: Path) -> None:
    project = tmp_path / "project"
    database = _create_migration_database(project / "state" / "product.sqlite3")
    before = (database.read_bytes(), database.stat().st_mtime_ns)

    result = migration_plan_read_only_result(project, "state/product.sqlite3")

    assert result == {
        "current_version": 3,
        "database": "state/product.sqlite3",
        "pending": [],
        "target_version": 3,
    }
    assert (database.read_bytes(), database.stat().st_mtime_ns) == before


def test_auto_selects_verified_rust_migration_plan(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    _create_migration_database(project / "db.sqlite3")
    calls: list[tuple[str, ...]] = []

    def runner(binary: Path, arguments: tuple[str, ...]) -> Mapping[str, Any]:
        calls.append(arguments)
        return _candidate(binary, arguments)

    result = MigrationPlanRouterR24(
        _selector(project),
        runner=runner,
        project_input_root=project,
        platform_probe=lambda: ("linux", "x86_64"),
    ).route(
        "migration.plan",
        cli_override="auto",
        migration_database="db.sqlite3",
    )

    assert result["selection"]["resolved"] == "rust"
    assert result["capability"] == "migration.plan"
    assert result["result"]["current_version"] == 3
    assert calls and calls[0][:2] == ("migration", "plan")


def test_path_escape_sidecar_symlink_and_schema_drift_fail_closed(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    outside = _create_plain_database(tmp_path / "outside.sqlite3")
    with pytest.raises(MigrationPlanReadOnlyError, match="MIGRATION_PLAN_DATABASE_PATH_ESCAPE"):
        migration_plan_read_only_result(project, outside)

    database = _create_plain_database(project / "db.sqlite3")
    Path(f"{database}-wal").write_bytes(b"not-a-wal")
    with pytest.raises(MigrationPlanReadOnlyError, match="MIGRATION_PLAN_SIDECAR_PRESENT"):
        migration_plan_read_only_result(project, "db.sqlite3")
    Path(f"{database}-wal").unlink()

    link = project / "linked.sqlite3"
    try:
        link.symlink_to(database)
    except OSError:
        pass
    else:
        with pytest.raises(MigrationPlanReadOnlyError, match="MIGRATION_PLAN_DATABASE_SYMLINK"):
            migration_plan_read_only_result(project, "linked.sqlite3")

    drift = project / "drift.sqlite3"
    connection = sqlite3.connect(drift)
    connection.execute("CREATE TABLE syntavra_schema_migrations(version TEXT PRIMARY KEY)")
    connection.commit()
    connection.close()
    with pytest.raises(MigrationPlanReadOnlyError, match="MIGRATION_PLAN_SCHEMA_COLUMNS_INVALID"):
        migration_plan_read_only_result(project, "drift.sqlite3")


def test_rust_drift_and_failure_never_fall_back(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    _create_migration_database(project / "db.sqlite3")

    def drifting(binary: Path, arguments: tuple[str, ...]) -> Mapping[str, Any]:
        result = dict(_candidate(binary, arguments))
        result["current_version"] = 99
        return result

    router = MigrationPlanRouterR24(
        _selector(project),
        runner=drifting,
        project_input_root=project,
    )
    with pytest.raises(EngineSelectionError) as drift:
        router.route(
            "migration.plan",
            cli_override="rust",
            migration_database="db.sqlite3",
        )
    assert drift.value.code == "RUST_MIGRATION_PLAN_PARITY_INVALID_R24"
    assert drift.value.details["fallback_attempted"] is False

    calls: list[tuple[str, ...]] = []

    def failing(_binary: Path, arguments: tuple[str, ...]) -> Mapping[str, Any]:
        calls.append(arguments)
        raise RuntimeError("candidate failure")

    router = MigrationPlanRouterR24(
        _selector(project),
        runner=failing,
        project_input_root=project,
    )
    with pytest.raises(EngineSelectionError) as failure:
        router.route(
            "migration.plan",
            cli_override="rust",
            migration_database="db.sqlite3",
        )
    assert failure.value.code == "RUST_ROUTE_EXECUTION_FAILED_R24"
    assert failure.value.details["fallback_attempted"] is False
    assert len(calls) == 1
