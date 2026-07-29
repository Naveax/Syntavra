from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from syntavra_runtime.broker_live_snapshot_contract import snapshot_live_broker_database
from syntavra_runtime.engine_cli import main as engine_main
from syntavra_runtime.engine_selector import (
    ENGINE_CONTRACT_SHA256,
    RUST_CAPABILITIES,
    EngineSelectionError,
    EngineSelector,
)
from syntavra_runtime.read_only_router_r21 import ReadOnlyCommandRouterR21
from syntavra_runtime.state import StateDB
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
    if len(arguments) == 5 and arguments[:2] == ("state", "broker-live-snapshot"):
        return snapshot_live_broker_database(
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


def _live_database(project: Path) -> tuple[Path, str, sqlite3.Connection]:
    database = project / ".syntavra" / "runtime-v3" / "broker.sqlite3"
    project_id = project_id_for_root(project)
    StateDB(database)
    holder = sqlite3.connect(database, timeout=0.0, isolation_level=None)
    holder.execute("PRAGMA foreign_keys=ON")
    assert holder.execute("PRAGMA journal_mode=WAL").fetchone()[0].casefold() == "wal"
    holder.execute("INSERT OR REPLACE INTO metadata(key,value) VALUES('channel','pre-release')")
    holder.execute("SELECT count(*) FROM metadata").fetchone()
    assert Path(f"{database}-wal").is_file()
    assert Path(f"{database}-shm").is_file()
    return database, project_id, holder


def _persistent_bytes(database: Path) -> dict[str, bytes]:
    value = {"database": database.read_bytes()}
    wal = Path(f"{database}-wal")
    if wal.exists():
        value["wal"] = wal.read_bytes()
    return value


def test_live_broker_route_has_stable_cross_engine_parity_without_writes(
    tmp_path: Path,
) -> None:
    database, project_id, holder = _live_database(tmp_path)
    relative = database.relative_to(tmp_path).as_posix()
    calls: list[tuple[str, ...]] = []

    def runner(binary: Path, arguments: tuple[str, ...]):
        calls.append(arguments)
        return _rust_runner(binary, arguments)

    router = ReadOnlyCommandRouterR21(
        _selector(tmp_path),
        runner=runner,
        project_input_root=tmp_path,
    )
    try:
        before = _persistent_bytes(database)
        python_result = router.route(
            "state.broker-live-snapshot",
            cli_override="python",
            database_path=relative,
        )
        rust_result = router.route(
            "state.broker-live-snapshot",
            cli_override="rust",
            database_path=relative,
        )
        after = _persistent_bytes(database)
    finally:
        holder.close()

    assert python_result["result"] == rust_result["result"]
    assert rust_result["result"]["database"]["wal_present"] is True
    assert rust_result["result"]["database"]["shm_present"] is True
    assert rust_result["result"]["backup"]["complete"] is True
    assert rust_result["phase"] == "R21"
    assert rust_result["schema_version"] == 10
    assert rust_result["command"] == "state.broker-live-snapshot"
    assert rust_result["capability"] == "state.broker-live-snapshot"
    assert rust_result["mutation"] == "read-only"
    material = f"{project_id}\n{relative}\n".encode("utf-8")
    assert rust_result["input"] == {
        "profile": "project-bound-bounded-live-broker-sqlite-v1",
        "format": "project-id-and-relative-live-broker-path-v1",
        "bytes": len(material),
        "sha256": hashlib.sha256(material).hexdigest(),
    }
    assert rust_result["fallback"] == {"policy": "none", "attempted": False}
    assert calls == [
        ("state", "broker-live-snapshot", project_id, str(tmp_path), relative)
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
def test_live_broker_route_rejects_missing_or_conflicting_input_before_engine(
    tmp_path: Path,
    kwargs: dict[str, object],
) -> None:
    calls: list[tuple[str, ...]] = []

    def runner(binary: Path, arguments: tuple[str, ...]):
        calls.append(arguments)
        return _rust_runner(binary, arguments)

    router = ReadOnlyCommandRouterR21(
        _selector(tmp_path), runner=runner, project_input_root=tmp_path
    )
    with pytest.raises(EngineSelectionError) as caught:
        router.route("state.broker-live-snapshot", cli_override="rust", **kwargs)
    assert caught.value.code in {
        "ENGINE_ROUTE_BROKER_LIVE_DATABASE_INPUT_REQUIRED_R21",
        "ENGINE_ROUTE_BROKER_LIVE_INPUT_CONFLICT_R21",
    }
    assert caught.value.details["fallback_attempted"] is False
    assert calls == []


def test_live_broker_rollback_journal_fails_before_engine_selection(
    tmp_path: Path,
) -> None:
    database, _project_id, holder = _live_database(tmp_path)
    relative = database.relative_to(tmp_path).as_posix()
    journal = Path(f"{database}-journal")
    journal.write_bytes(b"sensitive-journal")
    calls: list[tuple[str, ...]] = []

    def runner(binary: Path, arguments: tuple[str, ...]):
        calls.append(arguments)
        return _rust_runner(binary, arguments)

    router = ReadOnlyCommandRouterR21(
        _selector(tmp_path), runner=runner, project_input_root=tmp_path
    )
    try:
        with pytest.raises(EngineSelectionError) as caught:
            router.route(
                "state.broker-live-snapshot",
                cli_override="rust",
                database_path=relative,
            )
    finally:
        journal.unlink(missing_ok=True)
        holder.close()
    rendered = json.dumps(caught.value.to_dict(), sort_keys=True)
    assert caught.value.code == "ENGINE_ROUTE_BROKER_LIVE_PREFLIGHT_FAILED_R21"
    assert caught.value.details["broker_error"] == "BROKER_LIVE_ROLLBACK_JOURNAL_PRESENT"
    assert "sensitive-journal" not in rendered
    assert str(tmp_path) not in rendered
    assert calls == []


def test_live_broker_path_escape_is_redacted_and_fails_closed(tmp_path: Path) -> None:
    database, _project_id, holder = _live_database(tmp_path)
    outside = tmp_path.parent / "broker.sqlite3"
    outside.write_bytes(database.read_bytes())
    router = ReadOnlyCommandRouterR21(
        _selector(tmp_path), project_input_root=tmp_path
    )
    try:
        with pytest.raises(EngineSelectionError) as caught:
            router.route(
                "state.broker-live-snapshot",
                cli_override="rust",
                database_path=outside,
            )
    finally:
        outside.unlink(missing_ok=True)
        holder.close()
    rendered = json.dumps(caught.value.to_dict(), sort_keys=True)
    assert caught.value.code == "ENGINE_ROUTE_BROKER_LIVE_PREFLIGHT_FAILED_R21"
    assert caught.value.details["broker_error"] == "BROKER_DATABASE_PATH_ESCAPE"
    assert str(outside) not in rendered
    assert str(tmp_path) not in rendered


def test_live_broker_project_root_symlink_fails_before_engine_selection(
    tmp_path: Path,
) -> None:
    target = tmp_path / "target"
    target.mkdir()
    database, _project_id, holder = _live_database(target)
    relative = database.relative_to(target).as_posix()
    link = tmp_path / "project-link"
    try:
        link.symlink_to(target, target_is_directory=True)
    except (OSError, NotImplementedError):
        holder.close()
        pytest.skip("directory symlinks are unavailable")
    router = ReadOnlyCommandRouterR21(
        _selector(target), project_input_root=link
    )
    try:
        with pytest.raises(EngineSelectionError) as caught:
            router.route(
                "state.broker-live-snapshot",
                cli_override="rust",
                database_path=relative,
            )
    finally:
        holder.close()
    rendered = json.dumps(caught.value.to_dict(), sort_keys=True)
    assert caught.value.code == "ENGINE_ROUTE_BROKER_LIVE_PREFLIGHT_FAILED_R21"
    assert caught.value.details["broker_error"] == "STATE_PROJECT_ROOT_SYMLINK"
    assert str(link) not in rendered
    assert str(target) not in rendered


def test_live_broker_candidate_hash_is_verified(tmp_path: Path) -> None:
    database, _project_id, holder = _live_database(tmp_path)
    relative = database.relative_to(tmp_path).as_posix()

    def runner(binary: Path, arguments: tuple[str, ...]):
        value = _rust_runner(binary, arguments)
        if len(arguments) == 5 and arguments[:2] == ("state", "broker-live-snapshot"):
            value["snapshot_hash"] = "0" * 64
        return value

    router = ReadOnlyCommandRouterR21(
        _selector(tmp_path), runner=runner, project_input_root=tmp_path
    )
    try:
        with pytest.raises(EngineSelectionError) as caught:
            router.route(
                "state.broker-live-snapshot",
                cli_override="rust",
                database_path=relative,
            )
    finally:
        holder.close()
    assert caught.value.code == "RUST_BROKER_LIVE_RESULT_HASH_INVALID_R21"
    assert caught.value.details["fallback_attempted"] is False


def test_live_broker_source_drift_uses_digest_only_diagnostics(tmp_path: Path) -> None:
    database, _project_id, holder = _live_database(tmp_path)
    relative = database.relative_to(tmp_path).as_posix()

    def runner(binary: Path, arguments: tuple[str, ...]):
        if len(arguments) == 5 and arguments[:2] == ("state", "broker-live-snapshot"):
            holder.execute(
                "INSERT OR REPLACE INTO metadata(key,value) VALUES('drift','sensitive-value')"
            )
        return _rust_runner(binary, arguments)

    router = ReadOnlyCommandRouterR21(
        _selector(tmp_path), runner=runner, project_input_root=tmp_path
    )
    try:
        with pytest.raises(EngineSelectionError) as caught:
            router.route(
                "state.broker-live-snapshot",
                cli_override="rust",
                database_path=relative,
            )
    finally:
        holder.close()
    rendered = json.dumps(caught.value.to_dict(), sort_keys=True)
    assert caught.value.code == "RUST_BROKER_LIVE_ROUTE_PARITY_INVALID_R21"
    assert "tables" in caught.value.details["mismatched_keys"]
    assert len(caught.value.details["expected_sha256"]) == 64
    assert len(caught.value.details["actual_sha256"]) == 64
    assert "sensitive-value" not in rendered
    assert str(tmp_path) not in rendered
    assert caught.value.details["fallback_attempted"] is False


def test_live_broker_execution_failure_never_falls_back(tmp_path: Path) -> None:
    database, _project_id, holder = _live_database(tmp_path)
    relative = database.relative_to(tmp_path).as_posix()

    def runner(binary: Path, arguments: tuple[str, ...]):
        if len(arguments) == 5 and arguments[:2] == ("state", "broker-live-snapshot"):
            raise RuntimeError("sensitive live failure")
        return _rust_runner(binary, arguments)

    router = ReadOnlyCommandRouterR21(
        _selector(tmp_path), runner=runner, project_input_root=tmp_path
    )
    try:
        with pytest.raises(EngineSelectionError) as caught:
            router.route(
                "state.broker-live-snapshot",
                cli_override="rust",
                database_path=relative,
            )
    finally:
        holder.close()
    rendered = json.dumps(caught.value.to_dict(), sort_keys=True)
    assert caught.value.code == "RUST_ROUTE_EXECUTION_FAILED_R21"
    assert caught.value.details["exception_message"] == "redacted"
    assert "sensitive live failure" not in rendered
    assert caught.value.details["fallback_attempted"] is False


def test_r21_delegates_r20_routes_and_upgrades_envelope(tmp_path: Path) -> None:
    router = ReadOnlyCommandRouterR21(
        _selector(tmp_path), project_input_root=tmp_path
    )
    value = router.route("version", cli_override="python")
    assert value["phase"] == "R21"
    assert value["schema_version"] == 10


def test_engine_cli_routes_live_broker_snapshot(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    database, _project_id, holder = _live_database(tmp_path)
    relative = database.relative_to(tmp_path).as_posix()
    selector = _selector(tmp_path)
    router = ReadOnlyCommandRouterR21(selector, project_input_root=tmp_path)
    try:
        code = engine_main(
            [
                "--project",
                str(tmp_path),
                "engine",
                "route",
                "state.broker-live-snapshot",
                "--database-path",
                relative,
            ],
            selector=selector,
            cli_override="python",
            router=router,
        )
    finally:
        holder.close()
    value = json.loads(capsys.readouterr().out)
    assert code == 0
    assert value["phase"] == "R21"
    assert value["command"] == "state.broker-live-snapshot"
    assert value["result"]["backup"]["complete"] is True
