#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Mapping

from syntavra_runtime.engine_selector import EngineSelectionError, EngineSelector
from syntavra_runtime.read_only_router_r21 import ReadOnlyCommandRouterR21
from syntavra_runtime.state_snapshot_contract import project_id_for_root
from verify_broker_snapshot_parity import _create_schema

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "contracts" / "engine" / "read-only-routing-v10.json"


def _cargo_rust_json(_binary: Path, arguments: tuple[str, ...]) -> Mapping[str, Any]:
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
            f"Rust engine command failed ({completed.returncode}): {completed.stderr.strip()}"
        )
    value = json.loads(completed.stdout)
    if not isinstance(value, dict):
        raise RuntimeError("Rust engine output must be a JSON object")
    return value


def _open_wal(database: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(database, timeout=0.0, isolation_level=None)
    mode = connection.execute("PRAGMA journal_mode=WAL").fetchone()[0]
    if str(mode).casefold() != "wal":
        connection.close()
        raise RuntimeError("failed to establish R21 WAL fixture")
    connection.execute(
        "INSERT OR REPLACE INTO metadata(key,value) VALUES('r21-live-fixture','1')"
    )
    connection.execute("SELECT count(*) FROM metadata").fetchone()
    if not Path(f"{database}-wal").is_file() or not Path(f"{database}-shm").is_file():
        connection.close()
        raise RuntimeError("R21 WAL/SHM fixture sidecars are missing")
    return connection


def _persistent_bytes(database: Path) -> dict[str, bytes]:
    value = {"database": database.read_bytes()}
    wal = Path(f"{database}-wal")
    if wal.exists():
        value["wal"] = wal.read_bytes()
    return value


def verify() -> dict[str, object]:
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory(prefix="syntavra-r21-") as directory:
        root = Path(directory)
        project = root / "project"
        project.mkdir()
        project_id = project_id_for_root(project)
        database = project / ".syntavra" / "runtime-v3" / "broker.sqlite3"
        _create_schema(database, project_id)
        holder = _open_wal(database)
        relative = database.relative_to(project).as_posix()
        calls: list[tuple[str, ...]] = []

        def runner(binary: Path, arguments: tuple[str, ...]) -> Mapping[str, Any]:
            calls.append(arguments)
            return _cargo_rust_json(binary, arguments)

        selector = EngineSelector(
            project_root=project,
            env={"HOME": str(root / "home")},
            rust_binary=ROOT / "Cargo.toml",
            runner=_cargo_rust_json,
        )
        router = ReadOnlyCommandRouterR21(
            selector,
            runner=runner,
            project_input_root=project,
        )

        try:
            before = _persistent_bytes(database)
            python_snapshot = router.route(
                "state.broker-live-snapshot",
                cli_override="python",
                database_path=relative,
            )
            rust_snapshot = router.route(
                "state.broker-live-snapshot",
                cli_override="rust",
                database_path=relative,
            )
            after = _persistent_bytes(database)

            journal_error: EngineSelectionError | None = None
            journal = Path(f"{database}-journal")
            journal.write_bytes(b"diagnostic-journal")
            try:
                router.route(
                    "state.broker-live-snapshot",
                    cli_override="rust",
                    database_path=relative,
                )
            except EngineSelectionError as exc:
                journal_error = exc
            finally:
                journal.unlink(missing_ok=True)

            drift_error: EngineSelectionError | None = None

            def drift_runner(
                binary: Path, arguments: tuple[str, ...]
            ) -> Mapping[str, Any]:
                if arguments[:2] == ("state", "broker-live-snapshot"):
                    holder.execute(
                        "INSERT OR REPLACE INTO metadata(key,value) "
                        "VALUES('r21-drift','sensitive-drift')"
                    )
                return _cargo_rust_json(binary, arguments)

            drift_router = ReadOnlyCommandRouterR21(
                selector,
                runner=drift_runner,
                project_input_root=project,
            )
            try:
                drift_router.route(
                    "state.broker-live-snapshot",
                    cli_override="rust",
                    database_path=relative,
                )
            except EngineSelectionError as exc:
                drift_error = exc
        finally:
            holder.close()

        outside = root / "broker.sqlite3"
        shutil.copyfile(database, outside)
        escape_error: EngineSelectionError | None = None
        try:
            router.route(
                "state.broker-live-snapshot",
                cli_override="rust",
                database_path=outside,
            )
        except EngineSelectionError as exc:
            escape_error = exc

        link = root / "project-link"
        symlink_error: EngineSelectionError | None = None
        try:
            link.symlink_to(project, target_is_directory=True)
        except (OSError, NotImplementedError):
            link = project
        if link != project:
            link_router = ReadOnlyCommandRouterR21(
                selector,
                runner=_cargo_rust_json,
                project_input_root=link,
            )
            try:
                link_router.route(
                    "state.broker-live-snapshot",
                    cli_override="rust",
                    database_path=relative,
                )
            except EngineSelectionError as exc:
                symlink_error = exc

        route_rows = {
            str(row.get("command")): row
            for row in contract.get("routes", [])
            if isinstance(row, dict)
        }
        live_row = route_rows.get("state.broker-live-snapshot", {})
        live_policy = contract.get("broker_live_snapshot_route", {})
        material = f"{project_id}\n{relative}\n".encode("utf-8")
        rendered = json.dumps(
            [
                python_snapshot,
                rust_snapshot,
                journal_error.to_dict() if journal_error else {},
                drift_error.to_dict() if drift_error else {},
                escape_error.to_dict() if escape_error else {},
                symlink_error.to_dict() if symlink_error else {},
            ],
            sort_keys=True,
        )
        routes = [
            "config.resolve",
            "receipt.inspect",
            "state.broker-live-snapshot",
            "state.broker-snapshot",
            "state.inspect",
            "state.layout",
            "status",
            "version",
        ]
        checks = {
            "contract_schema": contract.get("schema_version") == 10,
            "contract_phase": contract.get("phase") == "R21",
            "route_inventory": sorted(route_rows) == routes,
            "live_capability": live_row.get("required_capability")
            == "state.broker-live-snapshot",
            "live_read_only": live_row.get("mutation") == "read-only",
            "live_input_profile": live_row.get("accepted_input_profiles")
            == ["project-bound-bounded-live-broker-sqlite-v1"],
            "live_rust_argv": live_row.get("rust_argv", {}).get(
                "project-bound-bounded-live-broker-sqlite-v1"
            )
            == [
                "state",
                "broker-live-snapshot",
                "<derived-project-id>",
                "<selected-project-root>",
                "<database-path>",
            ],
            "python_authority": live_policy.get("python_authority")
            == "syntavra_runtime.broker_live_snapshot_contract.snapshot_live_broker_database",
            "online_backup": live_policy.get("backup_api") == "sqlite-online-backup",
            "destination_memory": live_policy.get("backup_destination") == "memory",
            "bounded_bytes": live_policy.get("maximum_database_bytes") == 67108864,
            "bounded_duration": live_policy.get("maximum_duration_milliseconds") == 5000,
            "wal_pair_policy": live_policy.get("wal_shm_pair") == "require-both-or-neither",
            "no_mutation": live_policy.get("mutation") is False,
            "reference_output": python_snapshot["result"] == rust_snapshot["result"],
            "wal_snapshot": rust_snapshot["result"]["database"]["wal_present"] is True
            and rust_snapshot["result"]["database"]["shm_present"] is True,
            "backup_complete": rust_snapshot["result"]["backup"]["complete"] is True,
            "phase_upgrade": python_snapshot.get("phase") == "R21"
            and rust_snapshot.get("phase") == "R21"
            and python_snapshot.get("schema_version") == 10
            and rust_snapshot.get("schema_version") == 10,
            "input_metadata": rust_snapshot.get("input")
            == {
                "profile": "project-bound-bounded-live-broker-sqlite-v1",
                "format": "project-id-and-relative-live-broker-path-v1",
                "bytes": len(material),
                "sha256": hashlib.sha256(material).hexdigest(),
            },
            "selection_rust": rust_snapshot.get("selection", {}).get("resolved") == "rust",
            "candidate_executed": (
                "state",
                "broker-live-snapshot",
                project_id,
                str(project),
                relative,
            )
            in calls,
            "persistent_bytes_unchanged": before == after,
            "journal_rejected": journal_error is not None
            and journal_error.code == "ENGINE_ROUTE_BROKER_LIVE_PREFLIGHT_FAILED_R21"
            and journal_error.details.get("broker_error")
            == "BROKER_LIVE_ROLLBACK_JOURNAL_PRESENT",
            "cross_engine_drift_rejected": drift_error is not None
            and drift_error.code == "RUST_BROKER_LIVE_ROUTE_PARITY_INVALID_R21"
            and drift_error.details.get("fallback_attempted") is False,
            "escape_rejected": escape_error is not None
            and escape_error.code == "ENGINE_ROUTE_BROKER_LIVE_PREFLIGHT_FAILED_R21"
            and escape_error.details.get("broker_error") == "BROKER_DATABASE_PATH_ESCAPE",
            "root_symlink_rejected": link == project
            or (
                symlink_error is not None
                and symlink_error.code == "ENGINE_ROUTE_BROKER_LIVE_PREFLIGHT_FAILED_R21"
                and symlink_error.details.get("broker_error")
                == "STATE_PROJECT_ROOT_SYMLINK"
            ),
            "project_path_redacted": str(project) not in rendered
            and (link == project or str(link) not in rendered),
            "database_path_redacted": str(database) not in rendered
            and str(outside) not in rendered,
            "drift_value_redacted": "sensitive-drift" not in rendered,
        }
        if not all(checks.values()):
            raise RuntimeError(f"R21 live broker routing parity failed: {checks}")
        return {
            "ok": True,
            "phase": "R21",
            "checks": checks,
            "routes": routes,
            "input_profile": "project-bound-bounded-live-broker-sqlite-v1",
            "database_sha256": hashlib.sha256(database.read_bytes()).hexdigest(),
            "fallback_policy": "none",
            "reference_engine": "python",
            "candidate_engine": "rust",
            "claim": "RUST_LIVE_BROKER_SNAPSHOT_ROUTING_PARITY_PROVEN_R21",
        }


def main() -> int:
    print(json.dumps(verify(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
