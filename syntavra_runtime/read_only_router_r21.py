from __future__ import annotations

import copy
import hashlib
from pathlib import Path
from typing import Any

from .broker_live_snapshot_contract import (
    BrokerLiveSnapshotError,
    snapshot_live_broker_database,
)
from .broker_snapshot_contract import _canonical_json_bytes
from .engine_selector import EngineSelectionError, EngineSelector
from .read_only_router import MAX_RESPONSE_BYTES, RustRouteRunner, _result_digest
from .read_only_router_r17 import _canonical_result_size
from .read_only_router_r20 import ReadOnlyCommandRouterR20
from .state_snapshot_contract import StateInspectionError, project_id_for_root

ROUTING_PHASE = "R21"
ROUTING_SCHEMA_VERSION = 10
BROKER_LIVE_SNAPSHOT_COMMAND = "state.broker-live-snapshot"
BROKER_LIVE_SNAPSHOT_CAPABILITY = "state.broker-live-snapshot"
BROKER_LIVE_INPUT_PROFILE = "project-bound-bounded-live-broker-sqlite-v1"
BROKER_LIVE_INPUT_FORMAT = "project-id-and-relative-live-broker-path-v1"
BROKER_LIVE_RESULT_KEYS = frozenset(
    {
        "ok",
        "schema_version",
        "contract_version",
        "snapshot_id",
        "broker_schema_version",
        "project_id",
        "project_binding",
        "database",
        "backup",
        "tables",
        "row_counts",
        "mutation",
        "claim",
        "snapshot_hash",
    }
)


def _upgrade_error(exc: EngineSelectionError) -> EngineSelectionError:
    details = dict(exc.details)
    details["phase"] = ROUTING_PHASE
    details["schema_version"] = ROUTING_SCHEMA_VERSION
    return EngineSelectionError(exc.code, exc.message, **details)


def _broker_live_input_metadata(project_id: str, relative_path: str) -> dict[str, Any]:
    material = f"{project_id}\n{relative_path}\n".encode("utf-8")
    return {
        "profile": BROKER_LIVE_INPUT_PROFILE,
        "format": BROKER_LIVE_INPUT_FORMAT,
        "bytes": len(material),
        "sha256": hashlib.sha256(material).hexdigest(),
    }


def _stable_live_projection(value: dict[str, Any]) -> dict[str, Any]:
    """Remove only R10-declared observational fields from parity comparison."""

    projected = copy.deepcopy(value)
    projected.pop("snapshot_hash", None)
    database = projected.get("database")
    if isinstance(database, dict):
        database.pop("source_changed_during_backup", None)
    return projected


def _snapshot_hash_valid(value: dict[str, Any]) -> bool:
    claimed = value.get("snapshot_hash")
    if not isinstance(claimed, str) or len(claimed) != 64:
        return False
    if any(character not in "0123456789abcdef" for character in claimed):
        return False
    material = copy.deepcopy(value)
    material.pop("snapshot_hash", None)
    actual = hashlib.sha256(_canonical_json_bytes(material)).hexdigest()
    return claimed == actual


class ReadOnlyCommandRouterR21(ReadOnlyCommandRouterR20):
    """R21 router admitting bounded live/WAL broker SQLite snapshots."""

    def __init__(
        self,
        selector: EngineSelector,
        *,
        runner: RustRouteRunner | None = None,
        project_input_root: Path | None = None,
    ) -> None:
        super().__init__(
            selector,
            runner=runner,
            project_input_root=project_input_root,
        )

    @staticmethod
    def supported_commands() -> tuple[str, ...]:
        return tuple(
            sorted(
                (
                    *ReadOnlyCommandRouterR20.supported_commands(),
                    BROKER_LIVE_SNAPSHOT_COMMAND,
                )
            )
        )

    def route(
        self,
        command: str,
        *,
        cli_override: str | None = None,
        config_wire_hex: str | None = None,
        live_config: bool = False,
        session_override_json_hex: str | None = None,
        task_override_json_hex: str | None = None,
        receipt_wire_hex: str | None = None,
        database_path: str | Path | None = None,
    ) -> dict[str, Any]:
        normalized = str(command).strip().casefold()
        if normalized != BROKER_LIVE_SNAPSHOT_COMMAND:
            try:
                result = super().route(
                    normalized,
                    cli_override=cli_override,
                    config_wire_hex=config_wire_hex,
                    live_config=live_config,
                    session_override_json_hex=session_override_json_hex,
                    task_override_json_hex=task_override_json_hex,
                    receipt_wire_hex=receipt_wire_hex,
                    database_path=database_path,
                )
            except EngineSelectionError as exc:
                raise _upgrade_error(exc) from exc
            result["phase"] = ROUTING_PHASE
            result["schema_version"] = ROUTING_SCHEMA_VERSION
            return result

        if (
            config_wire_hex is not None
            or live_config
            or session_override_json_hex is not None
            or task_override_json_hex is not None
            or receipt_wire_hex is not None
        ):
            raise EngineSelectionError(
                "ENGINE_ROUTE_BROKER_LIVE_INPUT_CONFLICT_R21",
                "state.broker-live-snapshot accepts only one project-bound database path",
                phase=ROUTING_PHASE,
                schema_version=ROUTING_SCHEMA_VERSION,
                command=BROKER_LIVE_SNAPSHOT_COMMAND,
                accepted_input_profiles=[BROKER_LIVE_INPUT_PROFILE],
                fallback_policy="none",
                fallback_attempted=False,
            )
        if database_path is None or not str(database_path).strip():
            raise EngineSelectionError(
                "ENGINE_ROUTE_BROKER_LIVE_DATABASE_INPUT_REQUIRED_R21",
                "state.broker-live-snapshot requires one broker.sqlite3 path",
                phase=ROUTING_PHASE,
                schema_version=ROUTING_SCHEMA_VERSION,
                command=BROKER_LIVE_SNAPSHOT_COMMAND,
                accepted_input_profiles=[BROKER_LIVE_INPUT_PROFILE],
                fallback_policy="none",
                fallback_attempted=False,
            )

        try:
            project_id = project_id_for_root(self.project_input_root)
            expected = snapshot_live_broker_database(
                self.project_input_root,
                database_path,
                expected_project_id=project_id,
            )
        except (BrokerLiveSnapshotError, StateInspectionError) as exc:
            code = getattr(exc, "code", type(exc).__name__)
            raise EngineSelectionError(
                "ENGINE_ROUTE_BROKER_LIVE_PREFLIGHT_FAILED_R21",
                "Project-bound live broker snapshot failed before engine selection",
                phase=ROUTING_PHASE,
                schema_version=ROUTING_SCHEMA_VERSION,
                command=BROKER_LIVE_SNAPSHOT_COMMAND,
                input_profile=BROKER_LIVE_INPUT_PROFILE,
                broker_error=str(code),
                fallback_policy="none",
                fallback_attempted=False,
            ) from exc

        relative_path = str(expected["database"]["relative_path"])
        selection = self.selector.resolve(cli_override=cli_override)
        if selection.resolved == "rust":
            verification = self.selector.verify_rust()
            if not verification.compatible:
                raise EngineSelectionError(
                    "RUST_ENGINE_UNAVAILABLE_R21",
                    "Rust is selected but its binary or contract verification failed",
                    phase=ROUTING_PHASE,
                    schema_version=ROUTING_SCHEMA_VERSION,
                    command=BROKER_LIVE_SNAPSHOT_COMMAND,
                    input_profile=BROKER_LIVE_INPUT_PROFILE,
                    selection=selection.to_dict(),
                    verification=verification.to_dict(),
                    fallback_policy="none",
                    fallback_attempted=False,
                )
            binary = self.selector.discover_rust_binary()
            if binary is None:
                raise EngineSelectionError(
                    "RUST_ENGINE_BINARY_NOT_FOUND_R21",
                    "Rust is selected but no verified binary is available for routing",
                    phase=ROUTING_PHASE,
                    schema_version=ROUTING_SCHEMA_VERSION,
                    command=BROKER_LIVE_SNAPSHOT_COMMAND,
                    input_profile=BROKER_LIVE_INPUT_PROFILE,
                    selection=selection.to_dict(),
                    fallback_policy="none",
                    fallback_attempted=False,
                )
            try:
                raw = self.runner(
                    binary,
                    (
                        "state",
                        "broker-live-snapshot",
                        project_id,
                        str(self.project_input_root),
                        str(database_path),
                    ),
                )
            except EngineSelectionError:
                raise
            except Exception as exc:
                raise EngineSelectionError(
                    "RUST_ROUTE_EXECUTION_FAILED_R21",
                    "The Rust live broker route failed and was not re-executed in Python",
                    phase=ROUTING_PHASE,
                    schema_version=ROUTING_SCHEMA_VERSION,
                    command=BROKER_LIVE_SNAPSHOT_COMMAND,
                    input_profile=BROKER_LIVE_INPUT_PROFILE,
                    exception=type(exc).__name__,
                    exception_message="redacted",
                    fallback_policy="none",
                    fallback_attempted=False,
                ) from exc

            candidate = dict(raw)
            actual_keys = frozenset(str(key) for key in candidate)
            if actual_keys != BROKER_LIVE_RESULT_KEYS:
                raise EngineSelectionError(
                    "RUST_BROKER_LIVE_RESULT_SCHEMA_INVALID_R21",
                    "Rust state.broker-live-snapshot returned an unexpected result shape",
                    phase=ROUTING_PHASE,
                    schema_version=ROUTING_SCHEMA_VERSION,
                    command=BROKER_LIVE_SNAPSHOT_COMMAND,
                    input_profile=BROKER_LIVE_INPUT_PROFILE,
                    expected=sorted(BROKER_LIVE_RESULT_KEYS),
                    actual=sorted(actual_keys),
                    fallback_policy="none",
                    fallback_attempted=False,
                )
            if not _snapshot_hash_valid(candidate):
                raise EngineSelectionError(
                    "RUST_BROKER_LIVE_RESULT_HASH_INVALID_R21",
                    "Rust state.broker-live-snapshot returned an invalid canonical hash",
                    phase=ROUTING_PHASE,
                    schema_version=ROUTING_SCHEMA_VERSION,
                    command=BROKER_LIVE_SNAPSHOT_COMMAND,
                    input_profile=BROKER_LIVE_INPUT_PROFILE,
                    fallback_policy="none",
                    fallback_attempted=False,
                )

            expected_projection = _stable_live_projection(expected)
            candidate_projection = _stable_live_projection(candidate)
            if candidate_projection != expected_projection:
                keys = frozenset((*expected_projection.keys(), *candidate_projection.keys()))
                mismatched_keys = sorted(
                    key
                    for key in keys
                    if candidate_projection.get(key) != expected_projection.get(key)
                )
                raise EngineSelectionError(
                    "RUST_BROKER_LIVE_ROUTE_PARITY_INVALID_R21",
                    "Rust live broker snapshot differs from the Python canonical snapshot",
                    phase=ROUTING_PHASE,
                    schema_version=ROUTING_SCHEMA_VERSION,
                    command=BROKER_LIVE_SNAPSHOT_COMMAND,
                    input_profile=BROKER_LIVE_INPUT_PROFILE,
                    mismatched_keys=mismatched_keys,
                    expected_sha256=_result_digest(expected_projection),
                    actual_sha256=_result_digest(candidate_projection),
                    fallback_policy="none",
                    fallback_attempted=False,
                )

        # Python remains the output authority in R21. A selected Rust engine must
        # prove the stable live-snapshot projection first; failure never falls back.
        result = expected
        response_bytes = _canonical_result_size(result)
        if response_bytes > MAX_RESPONSE_BYTES:
            raise EngineSelectionError(
                "ENGINE_ROUTE_RESPONSE_TOO_LARGE_R21",
                "The state.broker-live-snapshot result exceeded the response limit",
                phase=ROUTING_PHASE,
                schema_version=ROUTING_SCHEMA_VERSION,
                command=BROKER_LIVE_SNAPSHOT_COMMAND,
                input_profile=BROKER_LIVE_INPUT_PROFILE,
                response_bytes=response_bytes,
                maximum_response_bytes=MAX_RESPONSE_BYTES,
                fallback_policy="none",
                fallback_attempted=False,
            )

        return {
            "ok": True,
            "phase": ROUTING_PHASE,
            "schema_version": ROUTING_SCHEMA_VERSION,
            "command": BROKER_LIVE_SNAPSHOT_COMMAND,
            "capability": BROKER_LIVE_SNAPSHOT_CAPABILITY,
            "mutation": "read-only",
            "selection": selection.to_dict(),
            "input": _broker_live_input_metadata(project_id, relative_path),
            "fallback": {"policy": "none", "attempted": False},
            "result": result,
        }
