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

from syntavra_runtime.broker_snapshot_contract import snapshot_broker_database
from syntavra_runtime.engine_selector import EngineSelectionError, EngineSelector
from syntavra_runtime.read_only_router_r20 import ReadOnlyCommandRouterR20
from syntavra_runtime.state_snapshot_contract import project_id_for_root

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "contracts" / "engine" / "read-only-routing-v9.json"


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
        timeout=120,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"Rust engine command failed ({completed.returncode}): {completed.stderr.strip()}"
        )
    value = json.loads(completed.stdout)
    if not isinstance(value, dict):
        raise RuntimeError("Rust engine output must be a JSON object")
    return value


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


def verify() -> dict[str, object]:
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory(prefix="syntavra-r20-") as directory:
        root = Path(directory)
        project = root / "project"
        project.mkdir()
        database, project_id = _create_database(project)
        relative = database.relative_to(project).as_posix()
        selector = EngineSelector(
            project_root=project,
            env={"HOME": str(root / "home")},
            rust_binary=ROOT / "Cargo.toml",
            runner=_cargo_rust_json,
        )
        router = ReadOnlyCommandRouterR20(
            selector,
            runner=_cargo_rust_json,
            project_input_root=project,
        )

        before = _tree_snapshot(project)
        python_snapshot = router.route(
            "state.broker-snapshot",
            cli_override="python",
            database_path=relative,
        )
        rust_snapshot = router.route(
            "state.broker-snapshot",
            cli_override="rust",
            database_path=relative,
        )
        after = _tree_snapshot(project)

        sidecar_error: EngineSelectionError | None = None
        escape_error: EngineSelectionError | None = None
        symlink_error: EngineSelectionError | None = None

        sidecar = Path(f"{database}-wal")
        sidecar.write_bytes(b"diagnostic-sidecar")
        try:
            router.route(
                "state.broker-snapshot",
                cli_override="rust",
                database_path=relative,
            )
        except EngineSelectionError as exc:
            sidecar_error = exc
        finally:
            sidecar.unlink(missing_ok=True)

        outside = root / "broker.sqlite3"
        shutil.copyfile(database, outside)
        try:
            router.route(
                "state.broker-snapshot",
                cli_override="rust",
                database_path=outside,
            )
        except EngineSelectionError as exc:
            escape_error = exc

        link = root / "project-link"
        link.symlink_to(project, target_is_directory=True)
        link_router = ReadOnlyCommandRouterR20(
            selector,
            runner=_cargo_rust_json,
            project_input_root=link,
        )
        try:
            link_router.route(
                "state.broker-snapshot",
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
        broker_row = route_rows.get("state.broker-snapshot", {})
        broker_policy = contract.get("broker_snapshot_route", {})
        material = f"{project_id}\n{relative}\n".encode("utf-8")
        rendered = json.dumps(
            [
                python_snapshot,
                rust_snapshot,
                sidecar_error.to_dict() if sidecar_error else {},
                escape_error.to_dict() if escape_error else {},
                symlink_error.to_dict() if symlink_error else {},
            ],
            sort_keys=True,
        )
        routes = [
            "config.resolve",
            "receipt.inspect",
            "state.broker-snapshot",
            "state.inspect",
            "state.layout",
            "status",
            "version",
        ]
        checks = {
            "contract_schema": contract.get("schema_version") == 9,
            "contract_phase": contract.get("phase") == "R20",
            "route_inventory": sorted(route_rows) == routes,
            "broker_capability": broker_row.get("required_capability")
            == "state.broker-snapshot",
            "broker_read_only": broker_row.get("mutation") == "read-only",
            "broker_input_profile": broker_row.get("accepted_input_profiles")
            == ["project-bound-quiescent-broker-sqlite-v1"],
            "broker_rust_argv": broker_row.get("rust_argv", {}).get(
                "project-bound-quiescent-broker-sqlite-v1"
            )
            == [
                "state",
                "broker-snapshot",
                "<derived-project-id>",
                "<selected-project-root>",
                "<database-path>",
            ],
            "python_authority": broker_policy.get("python_authority")
            == "syntavra_runtime.broker_snapshot_contract.snapshot_broker_database",
            "quiescent_only": broker_policy.get("quiescent") is True,
            "sidecars_rejected": broker_policy.get("sidecar_policy")
            == "reject-before-selection",
            "query_only": broker_policy.get("query_only") is True,
            "no_mutation": broker_policy.get("mutation") is False,
            "python_reference": python_snapshot["result"]
            == snapshot_broker_database(
                project,
                relative,
                expected_project_id=project_id,
            ),
            "cross_engine_parity": python_snapshot["result"]
            == rust_snapshot["result"],
            "phase_upgrade": python_snapshot.get("phase") == "R20"
            and rust_snapshot.get("phase") == "R20"
            and python_snapshot.get("schema_version") == 9
            and rust_snapshot.get("schema_version") == 9,
            "input_metadata": rust_snapshot.get("input")
            == {
                "profile": "project-bound-quiescent-broker-sqlite-v1",
                "format": "project-id-and-relative-broker-path-v1",
                "bytes": len(material),
                "sha256": hashlib.sha256(material).hexdigest(),
            },
            "selection_rust": rust_snapshot.get("selection", {}).get("resolved")
            == "rust",
            "project_state_unchanged": before == after,
            "sidecar_rejected": sidecar_error is not None
            and sidecar_error.code == "ENGINE_ROUTE_BROKER_PREFLIGHT_FAILED_R20"
            and sidecar_error.details.get("broker_error")
            == "BROKER_DATABASE_SIDECAR_PRESENT"
            and sidecar_error.details.get("fallback_attempted") is False,
            "escape_rejected": escape_error is not None
            and escape_error.code == "ENGINE_ROUTE_BROKER_PREFLIGHT_FAILED_R20"
            and escape_error.details.get("broker_error")
            == "BROKER_DATABASE_PATH_ESCAPE"
            and escape_error.details.get("fallback_attempted") is False,
            "root_symlink_rejected": symlink_error is not None
            and symlink_error.code == "ENGINE_ROUTE_BROKER_PREFLIGHT_FAILED_R20"
            and symlink_error.details.get("broker_error")
            == "STATE_PROJECT_ROOT_SYMLINK"
            and symlink_error.details.get("fallback_attempted") is False,
            "project_path_redacted": str(project) not in rendered
            and str(link) not in rendered,
            "database_path_redacted": str(database) not in rendered
            and str(outside) not in rendered,
        }
        if not all(checks.values()):
            raise RuntimeError(f"R20 broker snapshot routing parity failed: {checks}")
        return {
            "ok": True,
            "phase": "R20",
            "checks": checks,
            "routes": routes,
            "input_profile": "project-bound-quiescent-broker-sqlite-v1",
            "database_sha256": hashlib.sha256(database.read_bytes()).hexdigest(),
            "fallback_policy": "none",
            "reference_engine": "python",
            "candidate_engine": "rust",
            "claim": "RUST_QUIESCENT_BROKER_SNAPSHOT_ROUTING_PARITY_PROVEN_R20",
        }


def main() -> int:
    print(json.dumps(verify(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
