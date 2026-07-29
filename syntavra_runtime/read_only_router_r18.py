from __future__ import annotations

from pathlib import Path
from typing import Any

from .engine_selector import EngineSelectionError, EngineSelector
from .read_only_router import MAX_RESPONSE_BYTES, RustRouteRunner, _result_digest
from .read_only_router_r17 import (
    ReadOnlyCommandRouterR17,
    _canonical_result_size,
)
from .state_snapshot_contract import (
    MAX_FILE_BYTES,
    StateInspectionError,
    inspect_state_root,
    project_id_for_root,
)

ROUTING_PHASE = "R18"
ROUTING_SCHEMA_VERSION = 7
STATE_INSPECT_COMMAND = "state.inspect"
STATE_INSPECT_CAPABILITY = "state.inspect"
STATE_INSPECT_PROFILE = "project-bound-state-root-v1"
STATE_INSPECT_RESULT_KEYS = frozenset(
    {
        "ok",
        "schema_version",
        "contract_version",
        "inspection_id",
        "project_id",
        "project_binding",
        "paths",
        "mutation",
        "claim",
    }
)


def _upgrade_error(exc: EngineSelectionError) -> EngineSelectionError:
    details = dict(exc.details)
    details["phase"] = ROUTING_PHASE
    details["schema_version"] = ROUTING_SCHEMA_VERSION
    return EngineSelectionError(exc.code, exc.message, **details)


def _inspection_input_metadata(project_id: str) -> dict[str, Any]:
    return {
        "profile": STATE_INSPECT_PROFILE,
        "format": "sha256-normalized-absolute-path-v1",
        "bytes": 32,
        "sha256": project_id,
    }


class ReadOnlyCommandRouterR18(ReadOnlyCommandRouterR17):
    """R18 router admitting bounded project-bound state inspection."""

    def __init__(
        self,
        selector: EngineSelector,
        *,
        runner: RustRouteRunner | None = None,
        project_input_root: Path | None = None,
    ) -> None:
        super().__init__(selector, runner=runner)
        self.project_input_root = Path(
            selector.project_root if project_input_root is None else project_input_root
        ).expanduser()

    @staticmethod
    def supported_commands() -> tuple[str, ...]:
        return tuple(
            sorted((*ReadOnlyCommandRouterR17.supported_commands(), STATE_INSPECT_COMMAND))
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
    ) -> dict[str, Any]:
        normalized = str(command).strip().casefold()
        if normalized != STATE_INSPECT_COMMAND:
            try:
                result = super().route(
                    normalized,
                    cli_override=cli_override,
                    config_wire_hex=config_wire_hex,
                    live_config=live_config,
                    session_override_json_hex=session_override_json_hex,
                    task_override_json_hex=task_override_json_hex,
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
        ):
            raise EngineSelectionError(
                "ENGINE_ROUTE_STATE_INSPECT_INPUT_UNSUPPORTED_R18",
                "The state.inspect route accepts only the selected project root",
                phase=ROUTING_PHASE,
                schema_version=ROUTING_SCHEMA_VERSION,
                command=STATE_INSPECT_COMMAND,
                accepted_input_profiles=[STATE_INSPECT_PROFILE],
                fallback_policy="none",
                fallback_attempted=False,
            )

        try:
            project_id = project_id_for_root(self.project_input_root)
            expected = inspect_state_root(
                self.project_input_root,
                expected_project_id=project_id,
            )
        except StateInspectionError as exc:
            raise EngineSelectionError(
                "ENGINE_ROUTE_STATE_INSPECT_PREFLIGHT_FAILED_R18",
                "Project-bound state inspection failed before engine selection",
                phase=ROUTING_PHASE,
                schema_version=ROUTING_SCHEMA_VERSION,
                command=STATE_INSPECT_COMMAND,
                input_profile=STATE_INSPECT_PROFILE,
                state_error=exc.code,
                fallback_policy="none",
                fallback_attempted=False,
            ) from exc

        selection = self.selector.resolve(cli_override=cli_override)
        if selection.resolved == "python":
            result = expected
        else:
            verification = self.selector.verify_rust()
            if not verification.compatible:
                raise EngineSelectionError(
                    "RUST_ENGINE_UNAVAILABLE_R18",
                    "Rust is selected but its binary or contract verification failed",
                    phase=ROUTING_PHASE,
                    schema_version=ROUTING_SCHEMA_VERSION,
                    command=STATE_INSPECT_COMMAND,
                    input_profile=STATE_INSPECT_PROFILE,
                    selection=selection.to_dict(),
                    verification=verification.to_dict(),
                    fallback_policy="none",
                    fallback_attempted=False,
                )
            binary = self.selector.discover_rust_binary()
            if binary is None:
                raise EngineSelectionError(
                    "RUST_ENGINE_BINARY_NOT_FOUND_R18",
                    "Rust is selected but no verified binary is available for routing",
                    phase=ROUTING_PHASE,
                    schema_version=ROUTING_SCHEMA_VERSION,
                    command=STATE_INSPECT_COMMAND,
                    input_profile=STATE_INSPECT_PROFILE,
                    selection=selection.to_dict(),
                    fallback_policy="none",
                    fallback_attempted=False,
                )
            try:
                raw = self.runner(
                    binary,
                    (
                        "state",
                        "inspect",
                        project_id,
                        str(self.project_input_root),
                    ),
                )
            except EngineSelectionError:
                raise
            except Exception as exc:
                raise EngineSelectionError(
                    "RUST_ROUTE_EXECUTION_FAILED_R18",
                    "The Rust read-only route failed and was not re-executed in Python",
                    phase=ROUTING_PHASE,
                    schema_version=ROUTING_SCHEMA_VERSION,
                    command=STATE_INSPECT_COMMAND,
                    input_profile=STATE_INSPECT_PROFILE,
                    exception=type(exc).__name__,
                    exception_message="redacted",
                    fallback_policy="none",
                    fallback_attempted=False,
                ) from exc
            result = dict(raw)
            actual_keys = frozenset(str(key) for key in result)
            if actual_keys != STATE_INSPECT_RESULT_KEYS:
                raise EngineSelectionError(
                    "RUST_STATE_INSPECT_RESULT_SCHEMA_INVALID_R18",
                    "Rust state.inspect returned an unexpected result shape",
                    phase=ROUTING_PHASE,
                    schema_version=ROUTING_SCHEMA_VERSION,
                    command=STATE_INSPECT_COMMAND,
                    input_profile=STATE_INSPECT_PROFILE,
                    expected=sorted(STATE_INSPECT_RESULT_KEYS),
                    actual=sorted(actual_keys),
                    fallback_policy="none",
                    fallback_attempted=False,
                )
            if result != expected:
                mismatched_keys = sorted(
                    key
                    for key in STATE_INSPECT_RESULT_KEYS
                    if result.get(key) != expected.get(key)
                )
                raise EngineSelectionError(
                    "RUST_STATE_INSPECT_ROUTE_PARITY_INVALID_R18",
                    "Rust state.inspect differs from the Python canonical inspection",
                    phase=ROUTING_PHASE,
                    schema_version=ROUTING_SCHEMA_VERSION,
                    command=STATE_INSPECT_COMMAND,
                    input_profile=STATE_INSPECT_PROFILE,
                    mismatched_keys=mismatched_keys,
                    expected_sha256=_result_digest(expected),
                    actual_sha256=_result_digest(result),
                    fallback_policy="none",
                    fallback_attempted=False,
                )

        response_bytes = _canonical_result_size(result)
        if response_bytes > MAX_RESPONSE_BYTES:
            raise EngineSelectionError(
                "ENGINE_ROUTE_RESPONSE_TOO_LARGE_R18",
                "The state.inspect result exceeded the response limit",
                phase=ROUTING_PHASE,
                schema_version=ROUTING_SCHEMA_VERSION,
                command=STATE_INSPECT_COMMAND,
                input_profile=STATE_INSPECT_PROFILE,
                response_bytes=response_bytes,
                maximum_response_bytes=MAX_RESPONSE_BYTES,
                fallback_policy="none",
                fallback_attempted=False,
            )

        return {
            "ok": True,
            "phase": ROUTING_PHASE,
            "schema_version": ROUTING_SCHEMA_VERSION,
            "command": STATE_INSPECT_COMMAND,
            "capability": STATE_INSPECT_CAPABILITY,
            "mutation": "read-only",
            "selection": selection.to_dict(),
            "input": _inspection_input_metadata(project_id),
            "fallback": {"policy": "none", "attempted": False},
            "result": result,
            "limits": {"maximum_file_bytes": MAX_FILE_BYTES},
        }
