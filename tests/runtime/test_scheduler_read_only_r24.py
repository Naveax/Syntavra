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
from syntavra_runtime.scheduler_read_only_contract import (
    SchedulerReadOnlyError,
    scheduler_read_only_result,
)
from syntavra_runtime.scheduler_read_only_router_r24 import SchedulerReadOnlyRouterR24
from syntavra_runtime.unified_cli import main as unified_main


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
    rows = [
        (
            "job-a",
            "project-a",
            '["python","-V"]',
            1,
            "queued",
            0,
            3,
            120.0,
            "strict",
            "cpu",
            '{"kind":"first"}',
            1.0,
            1.0,
            1.0,
            "",
            0.0,
            "",
            "{}",
        ),
        (
            "job-b",
            "project-b",
            '["cargo","test"]',
            2,
            "succeeded",
            1,
            3,
            240.0,
            "strict",
            "cpu",
            '{"kind":"second"}',
            2.0,
            2.0,
            2.0,
            "",
            0.0,
            "",
            '{"ok":true}',
        ),
    ]
    connection.executemany(
        """
        INSERT INTO scheduled_jobs(
            job_id,project_id,argv_json,priority,state,attempt,max_attempts,
            timeout_seconds,sandbox_profile,resource_class,metadata_json,
            scheduled_at,created_at,updated_at,lease_owner,lease_until,last_error,result_json
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        rows,
    )
    connection.commit()
    connection.close()
    return database


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


def _selector(project: Path, state: Path) -> EngineSelector:
    binary = project / "syntavra-rs"
    binary.write_bytes(b"test")
    return EngineSelector(
        project_root=project,
        state_root=state,
        env={"HOME": str(project / "home")},
        rust_binary=binary,
        runner=_verification_runner,
    )


def _candidate(_binary: Path, arguments: tuple[str, ...]) -> Mapping[str, Any]:
    if arguments[:2] == ("scheduler", "stats"):
        return scheduler_read_only_result(Path(arguments[2]), "scheduler.stats")
    if arguments[:2] == ("scheduler", "list"):
        states = json.loads(bytes.fromhex(arguments[4]))
        return scheduler_read_only_result(
            Path(arguments[2]),
            "scheduler.list",
            states=states,
            limit=int(arguments[3]),
        )
    raise AssertionError(arguments)


def test_missing_scheduler_database_is_empty_and_creates_no_state(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    state = project / ".syntavra" / "pre-release"

    assert engine_main(
        [
            "--engine",
            "python",
            "--project",
            str(project),
            "--state-root",
            str(state),
            "scheduler",
            "stats",
        ]
    ) == 0
    assert json.loads(capsys.readouterr().out) == {
        "database_integrity": True,
        "projects": 0,
        "states": {},
    }
    assert not state.exists()

    assert unified_main(
        [
            "--project",
            str(project),
            "--state-root",
            str(state),
            "scheduler",
            "list",
        ]
    ) == 0
    assert json.loads(capsys.readouterr().out) == {"jobs": []}
    assert not state.exists()


def test_populated_scheduler_stats_and_list_are_deterministic_and_unchanged(
    tmp_path: Path,
) -> None:
    state = tmp_path / "state"
    database = _create_database(state)
    before = (database.read_bytes(), database.stat().st_mtime_ns)

    stats = scheduler_read_only_result(state, "scheduler.stats")
    listing = scheduler_read_only_result(
        state,
        "scheduler.list",
        states=("SUCCEEDED", "queued", "queued"),
        limit=25,
    )

    assert stats == {
        "database_integrity": True,
        "projects": 2,
        "states": {"queued": 1, "succeeded": 1},
    }
    assert [row["job_id"] for row in listing["jobs"]] == ["job-b", "job-a"]
    assert (database.read_bytes(), database.stat().st_mtime_ns) == before
    assert not Path(f"{database}-journal").exists()
    assert not Path(f"{database}-shm").exists()
    assert not Path(f"{database}-wal").exists()


def test_auto_selects_verified_rust_scheduler_route(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    state = project / "state"
    _create_database(state)
    calls: list[tuple[str, ...]] = []

    def runner(binary: Path, arguments: tuple[str, ...]) -> Mapping[str, Any]:
        calls.append(arguments)
        return _candidate(binary, arguments)

    result = SchedulerReadOnlyRouterR24(
        _selector(project, state),
        runner=runner,
        project_input_root=project,
        platform_probe=lambda: ("linux", "x86_64"),
    ).route(
        "scheduler.list",
        cli_override="auto",
        scheduler_states=("queued",),
        scheduler_limit=10,
    )

    assert result["selection"]["resolved"] == "rust"
    assert result["capability"] == "scheduler.list"
    assert [row["job_id"] for row in result["result"]["jobs"]] == ["job-a"]
    assert calls and calls[0][:2] == ("scheduler", "list")


def test_sidecar_and_schema_drift_fail_before_engine_selection(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    state = project / "state"
    database = _create_database(state)
    Path(f"{database}-wal").write_bytes(b"not-a-wal")

    with pytest.raises(EngineSelectionError) as sidecar:
        SchedulerReadOnlyRouterR24(
            _selector(project, state),
            project_input_root=project,
        ).route("scheduler.stats", cli_override="auto")
    assert sidecar.value.code == "ENGINE_ROUTE_SCHEDULER_PREFLIGHT_FAILED_R24"

    Path(f"{database}-wal").unlink()
    connection = sqlite3.connect(database)
    connection.execute("DROP INDEX scheduled_jobs_ready_idx")
    connection.commit()
    connection.close()
    with pytest.raises(SchedulerReadOnlyError, match="SCHEDULER_READ_ONLY_SCHEMA_INDEX_INVALID"):
        scheduler_read_only_result(state, "scheduler.stats")


def test_rust_drift_and_failure_never_fall_back(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    state = project / "state"
    _create_database(state)

    def drifting(binary: Path, arguments: tuple[str, ...]) -> Mapping[str, Any]:
        result = dict(_candidate(binary, arguments))
        result["projects"] = 99
        return result

    router = SchedulerReadOnlyRouterR24(
        _selector(project, state),
        runner=drifting,
        project_input_root=project,
    )
    with pytest.raises(EngineSelectionError) as drift:
        router.route("scheduler.stats", cli_override="rust")
    assert drift.value.code == "RUST_SCHEDULER_READ_ONLY_PARITY_INVALID_R24"
    assert drift.value.details["fallback_attempted"] is False

    calls: list[tuple[str, ...]] = []

    def failing(_binary: Path, arguments: tuple[str, ...]) -> Mapping[str, Any]:
        calls.append(arguments)
        raise RuntimeError("candidate failure")

    router = SchedulerReadOnlyRouterR24(
        _selector(project, state),
        runner=failing,
        project_input_root=project,
    )
    with pytest.raises(EngineSelectionError) as failure:
        router.route("scheduler.stats", cli_override="rust")
    assert failure.value.code == "RUST_ROUTE_EXECUTION_FAILED_R24"
    assert failure.value.details["fallback_attempted"] is False
    assert len(calls) == 1
