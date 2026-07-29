from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from syntavra_runtime.broker_snapshot_contract import snapshot_broker_database
from syntavra_runtime.engine_cli import main as engine_main
from syntavra_runtime.engine_selector import (
    ENGINE_CONTRACT_SHA256,
    RUST_CAPABILITIES,
    EngineSelectionError,
    EngineSelector,
)
from syntavra_runtime.read_only_router_r20 import ReadOnlyCommandRouterR20
from syntavra_runtime.state_snapshot_contract import project_id_for_root


def _capability_rows() -> list[dict[str, str]]:
    return [
        {"name": name, "maturity": "preview", "mutation": "read-only"}
        for name in RUST_CAPABILITIES
    ]


def _rust_runner(_binary: Path, arguments: tuple[str, ...]):
    if arguments == ("version",):
        return {
            "product": "Syntavra",
            "product_version": "0.0.1",
            "release_channel": "pre-release",
            "engine": "rust",
            "engine_stability": "experimental",
            "contract_version": 1,
        }
    if len(arguments) == 5 and arguments[:2] == ("state", "broker-snapshot"):
        return snapshot_broker_database(
            arguments[3],
            arguments[4],
            expected_project_id=arguments[2],
        )
    if arguments == ("engine", "capabilities"):
        return {"contract_version": 1, "capabilities": _capability_rows()}
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
        env={"HOME": str(project / "home")},
        rust_binary=binary,
        runner=_rust_runner,
    )


def _create_database(project: Path) -> tuple[Path, str]:
    project_id = project_id_for_root(project)
    database = project / ".syntavra" / "runtime-v3" / "broker.sqlite3"
    database.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(database)
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
        db.execute("INSERT INTO metadata(key,value) VALUES('schema_version','2')")
        db.execute("INSERT INTO metadata(key,value) VALUES('channel','pre-release')")
        db.commit()
    finally:
        db.close()
    return database, project_id


def _tree_snapshot(root: Path) -> list[tuple[str, int, int, bytes | None]]:
    output: list[tuple[str, int, int, bytes | None]] = []
    for path in sorted(root.rglob("*")):
        metadata = path.lstat()
        payload = path.read_bytes() if path.is_file() and not path.is_symlink() else None
        output.append(
            (
                path.relative_to(root).as_posix(),
                metadata.st_size,
                metadata.st_mtime_ns,
                payload,
            )
        )
    return output


def test_broker_snapshot_has_exact_cross_engine_parity_without_mutation(
    tmp_path: Path,
) -> None:
    database, project_id = _create_database(tmp_path)
    relative = database.relative_to(tmp_path).as_posix()
    calls: list[tuple[str, ...]] = []

    def runner(binary: Path, arguments: tuple[str, ...]):
        calls.append(arguments)
        return _rust_runner(binary, arguments)

    router = ReadOnlyCommandRouterR20(
        _selector(tmp_path),
        runner=runner,
        project_input_root=tmp_path,
    )
    before = _tree_snapshot(tmp_path)
    python_result = router.route(
        "state.broker-snapshot",
        cli_override="python",
        database_path=relative,
    )
    rust_result = router.route(
        "state.broker-snapshot",
        cli_override="rust",
        database_path=relative,
    )
    after = _tree_snapshot(tmp_path)

    assert python_result["result"] == rust_result["result"]
    assert rust_result["result"] == snapshot_broker_database(
        tmp_path,
        relative,
        expected_project_id=project_id,
    )
    assert rust_result["phase"] == "R20"
    assert rust_result["schema_version"] == 9
    assert rust_result["command"] == "state.broker-snapshot"
    assert rust_result["capability"] == "state.broker-snapshot"
    assert rust_result["mutation"] == "read-only"
    material = f"{project_id}\n{relative}\n".encode("utf-8")
    assert rust_result["input"] == {
        "profile": "project-bound-quiescent-broker-sqlite-v1",
        "format": "project-id-and-relative-broker-path-v1",
        "bytes": len(material),
        "sha256": hashlib.sha256(material).hexdigest(),
    }
    assert rust_result["fallback"] == {"policy": "none", "attempted": False}
    assert calls == [
        ("state", "broker-snapshot", project_id, str(tmp_path), relative)
    ]
    assert before == after
    rendered = json.dumps(rust_result, sort_keys=True)
    assert str(tmp_path) not in rendered
    assert str(database) not in rendered


@pytest.mark.parametrize(
    "kwargs",
    [
        {},
        {"database_path": "broker.sqlite3", "config_wire_hex": "00"},
        {"database_path": "broker.sqlite3", "live_config": True},
        {"database_path": "broker.sqlite3", "receipt_wire_hex": "00"},
        {"database_path": "broker.sqlite3", "session_override_json_hex": "7b7d"},
        {"database_path": "broker.sqlite3", "task_override_json_hex": "7b7d"},
    ],
)
def test_broker_route_rejects_missing_or_conflicting_inputs_before_engine(
    tmp_path: Path,
    kwargs: dict[str, object],
) -> None:
    calls: list[tuple[str, ...]] = []

    def runner(binary: Path, arguments: tuple[str, ...]):
        calls.append(arguments)
        return _rust_runner(binary, arguments)

    router = ReadOnlyCommandRouterR20(
        _selector(tmp_path),
        runner=runner,
        project_input_root=tmp_path,
    )
    with pytest.raises(EngineSelectionError) as error:
        router.route("state.broker-snapshot", cli_override="rust", **kwargs)
    assert error.value.code in {
        "ENGINE_ROUTE_BROKER_DATABASE_INPUT_REQUIRED_R20",
        "ENGINE_ROUTE_BROKER_INPUT_CONFLICT_R20",
    }
    assert error.value.details["fallback_attempted"] is False
    assert calls == []


def test_broker_sidecars_fail_closed_before_engine_selection(tmp_path: Path) -> None:
    database, _project_id = _create_database(tmp_path)
    relative = database.relative_to(tmp_path).as_posix()
    sidecar = Path(f"{database}-wal")
    sidecar.write_bytes(b"sensitive-sidecar")
    calls: list[tuple[str, ...]] = []

    def runner(binary: Path, arguments: tuple[str, ...]):
        calls.append(arguments)
        return _rust_runner(binary, arguments)

    router = ReadOnlyCommandRouterR20(
        _selector(tmp_path),
        runner=runner,
        project_input_root=tmp_path,
    )
    with pytest.raises(EngineSelectionError) as error:
        router.route(
            "state.broker-snapshot",
            cli_override="rust",
            database_path=relative,
        )
    rendered = json.dumps(error.value.to_dict(), sort_keys=True)
    assert error.value.code == "ENGINE_ROUTE_BROKER_PREFLIGHT_FAILED_R20"
    assert error.value.details["broker_error"] == "BROKER_DATABASE_SIDECAR_PRESENT"
    assert str(tmp_path) not in rendered
    assert str(database) not in rendered
    assert "sensitive-sidecar" not in rendered
    assert error.value.details["fallback_attempted"] is False
    assert calls == []


def test_broker_path_escape_fails_closed_and_redacts_path(tmp_path: Path) -> None:
    database, _project_id = _create_database(tmp_path)
    outside = tmp_path.parent / "broker.sqlite3"
    outside.write_bytes(database.read_bytes())
    calls: list[tuple[str, ...]] = []

    def runner(binary: Path, arguments: tuple[str, ...]):
        calls.append(arguments)
        return _rust_runner(binary, arguments)

    router = ReadOnlyCommandRouterR20(
        _selector(tmp_path),
        runner=runner,
        project_input_root=tmp_path,
    )
    try:
        with pytest.raises(EngineSelectionError) as error:
            router.route(
                "state.broker-snapshot",
                cli_override="rust",
                database_path=outside,
            )
    finally:
        outside.unlink(missing_ok=True)
    rendered = json.dumps(error.value.to_dict(), sort_keys=True)
    assert error.value.code == "ENGINE_ROUTE_BROKER_PREFLIGHT_FAILED_R20"
    assert error.value.details["broker_error"] == "BROKER_DATABASE_PATH_ESCAPE"
    assert str(outside) not in rendered
    assert str(tmp_path) not in rendered
    assert calls == []


def test_broker_project_root_symlink_fails_before_engine_selection(
    tmp_path: Path,
) -> None:
    target = tmp_path / "target"
    target.mkdir()
    database, _project_id = _create_database(target)
    relative = database.relative_to(target).as_posix()
    link = tmp_path / "project-link"
    try:
        link.symlink_to(target, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("directory symlinks are unavailable")
    calls: list[tuple[str, ...]] = []

    def runner(binary: Path, arguments: tuple[str, ...]):
        calls.append(arguments)
        return _rust_runner(binary, arguments)

    router = ReadOnlyCommandRouterR20(
        _selector(target),
        runner=runner,
        project_input_root=link,
    )
    with pytest.raises(EngineSelectionError) as error:
        router.route(
            "state.broker-snapshot",
            cli_override="rust",
            database_path=relative,
        )
    rendered = json.dumps(error.value.to_dict(), sort_keys=True)
    assert error.value.code == "ENGINE_ROUTE_BROKER_PREFLIGHT_FAILED_R20"
    assert error.value.details["broker_error"] == "STATE_PROJECT_ROOT_SYMLINK"
    assert str(link) not in rendered
    assert str(target) not in rendered
    assert calls == []


def test_broker_drift_uses_digest_only_diagnostics(tmp_path: Path) -> None:
    database, _project_id = _create_database(tmp_path)
    relative = database.relative_to(tmp_path).as_posix()
    calls: list[tuple[str, ...]] = []

    def runner(binary: Path, arguments: tuple[str, ...]):
        calls.append(arguments)
        if len(arguments) == 5 and arguments[:2] == ("state", "broker-snapshot"):
            value = _rust_runner(binary, arguments)
            value["tables"]["metadata"].append(
                {"key": "sensitive-drift", "value": "secret-value"}
            )
            return value
        return _rust_runner(binary, arguments)

    router = ReadOnlyCommandRouterR20(
        _selector(tmp_path),
        runner=runner,
        project_input_root=tmp_path,
    )
    with pytest.raises(EngineSelectionError) as error:
        router.route(
            "state.broker-snapshot",
            cli_override="rust",
            database_path=relative,
        )
    rendered = json.dumps(error.value.to_dict(), sort_keys=True)
    assert error.value.code == "RUST_BROKER_ROUTE_PARITY_INVALID_R20"
    assert error.value.details["mismatched_keys"] == ["tables"]
    assert len(error.value.details["expected_sha256"]) == 64
    assert len(error.value.details["actual_sha256"]) == 64
    assert "sensitive-drift" not in rendered
    assert "secret-value" not in rendered
    assert str(tmp_path) not in rendered
    assert error.value.details["fallback_attempted"] is False
    assert len(calls) == 1


def test_broker_execution_failure_never_falls_back_to_python(tmp_path: Path) -> None:
    database, _project_id = _create_database(tmp_path)
    relative = database.relative_to(tmp_path).as_posix()
    calls: list[tuple[str, ...]] = []

    def runner(binary: Path, arguments: tuple[str, ...]):
        calls.append(arguments)
        if len(arguments) == 5 and arguments[:2] == ("state", "broker-snapshot"):
            raise RuntimeError("sensitive broker failure")
        return _rust_runner(binary, arguments)

    router = ReadOnlyCommandRouterR20(
        _selector(tmp_path),
        runner=runner,
        project_input_root=tmp_path,
    )
    with pytest.raises(EngineSelectionError) as error:
        router.route(
            "state.broker-snapshot",
            cli_override="rust",
            database_path=relative,
        )
    rendered = json.dumps(error.value.to_dict(), sort_keys=True)
    assert error.value.code == "RUST_ROUTE_EXECUTION_FAILED_R20"
    assert error.value.details["exception_message"] == "redacted"
    assert "sensitive broker failure" not in rendered
    assert str(tmp_path) not in rendered
    assert error.value.details["fallback_attempted"] is False
    assert len(calls) == 1


def test_engine_cli_routes_broker_snapshot(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    database, project_id = _create_database(tmp_path)
    relative = database.relative_to(tmp_path).as_posix()
    selector = _selector(tmp_path)
    router = ReadOnlyCommandRouterR20(
        selector,
        runner=_rust_runner,
        project_input_root=tmp_path,
    )
    code = engine_main(
        [
            "--project",
            str(tmp_path),
            "engine",
            "route",
            "state.broker-snapshot",
            "--database-path",
            relative,
        ],
        selector=selector,
        cli_override="rust",
        router=router,
    )
    output = json.loads(capsys.readouterr().out)
    assert code == 0
    assert output["phase"] == "R20"
    assert output["schema_version"] == 9
    assert output["selection"]["resolved"] == "rust"
    assert output["result"]["project_id"] == project_id
    assert output["result"]["database"]["relative_path"] == relative
    assert str(tmp_path) not in json.dumps(output, sort_keys=True)
