from __future__ import annotations

from typing import Any, Mapping

from .engine_selector import EngineSelectionError
from .read_only_router import MAX_RESPONSE_BYTES, _input_metadata, _result_digest
from .read_only_router_r16 import ReadOnlyCommandRouterR16
from .state_receipt_contract import state_layout

ROUTING_PHASE = "R17"
ROUTING_SCHEMA_VERSION = 6
STATE_LAYOUT_COMMAND = "state.layout"
STATE_LAYOUT_CAPABILITY = "state.layout"
STATE_LAYOUT_RESULT_KEYS = frozenset(
    {
        "schema_version",
        "contract_version",
        "layout_id",
        "root",
        "project_binding",
        "engine_policy",
        "shared_paths",
        "receipt_envelope",
        "r7_access",
        "r8_access",
        "compatibility_rules",
    }
)


def _upgrade_error(exc: EngineSelectionError) -> EngineSelectionError:
    details = dict(exc.details)
    details["phase"] = ROUTING_PHASE
    details["schema_version"] = ROUTING_SCHEMA_VERSION
    return EngineSelectionError(exc.code, exc.message, **details)


def _canonical_result_size(value: Mapping[str, Any]) -> int:
    import json

    return len(
        json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            default=str,
        ).encode("utf-8")
    )


class ReadOnlyCommandRouterR17(ReadOnlyCommandRouterR16):
    """R17 router admitting the static state-layout capability."""

    @staticmethod
    def supported_commands() -> tuple[str, ...]:
        return tuple(sorted((*ReadOnlyCommandRouterR16.supported_commands(), STATE_LAYOUT_COMMAND)))

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
        if normalized != STATE_LAYOUT_COMMAND:
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
                "ENGINE_ROUTE_STATE_LAYOUT_INPUT_UNSUPPORTED_R17",
                "The state.layout route does not accept configuration input",
                phase=ROUTING_PHASE,
                schema_version=ROUTING_SCHEMA_VERSION,
                command=STATE_LAYOUT_COMMAND,
                accepted_input_profiles=["none"],
                fallback_policy="none",
                fallback_attempted=False,
            )

        expected = state_layout()
        selection = self.selector.resolve(cli_override=cli_override)
        if selection.resolved == "python":
            result = expected
        else:
            verification = self.selector.verify_rust()
            if not verification.compatible:
                raise EngineSelectionError(
                    "RUST_ENGINE_UNAVAILABLE_R17",
                    "Rust is selected but its binary or contract verification failed",
                    phase=ROUTING_PHASE,
                    schema_version=ROUTING_SCHEMA_VERSION,
                    command=STATE_LAYOUT_COMMAND,
                    selection=selection.to_dict(),
                    verification=verification.to_dict(),
                    fallback_policy="none",
                    fallback_attempted=False,
                )
            binary = self.selector.discover_rust_binary()
            if binary is None:
                raise EngineSelectionError(
                    "RUST_ENGINE_BINARY_NOT_FOUND_R17",
                    "Rust is selected but no verified binary is available for routing",
                    phase=ROUTING_PHASE,
                    schema_version=ROUTING_SCHEMA_VERSION,
                    command=STATE_LAYOUT_COMMAND,
                    selection=selection.to_dict(),
                    fallback_policy="none",
                    fallback_attempted=False,
                )
            try:
                raw = self.runner(binary, ("state", "layout"))
            except EngineSelectionError:
                raise
            except Exception as exc:
                raise EngineSelectionError(
                    "RUST_ROUTE_EXECUTION_FAILED_R17",
                    "The Rust read-only route failed and was not re-executed in Python",
                    phase=ROUTING_PHASE,
                    schema_version=ROUTING_SCHEMA_VERSION,
                    command=STATE_LAYOUT_COMMAND,
                    input_profile="none",
                    exception=type(exc).__name__,
                    exception_message="redacted",
                    fallback_policy="none",
                    fallback_attempted=False,
                ) from exc
            result = dict(raw)
            actual_keys = frozenset(str(key) for key in result)
            if actual_keys != STATE_LAYOUT_RESULT_KEYS:
                raise EngineSelectionError(
                    "RUST_STATE_LAYOUT_RESULT_SCHEMA_INVALID_R17",
                    "Rust state.layout returned an unexpected result shape",
                    phase=ROUTING_PHASE,
                    schema_version=ROUTING_SCHEMA_VERSION,
                    command=STATE_LAYOUT_COMMAND,
                    expected=sorted(STATE_LAYOUT_RESULT_KEYS),
                    actual=sorted(actual_keys),
                    fallback_policy="none",
                    fallback_attempted=False,
                )
            if result != expected:
                mismatched_keys = sorted(
                    key
                    for key in STATE_LAYOUT_RESULT_KEYS
                    if result.get(key) != expected.get(key)
                )
                raise EngineSelectionError(
                    "RUST_STATE_LAYOUT_ROUTE_PARITY_INVALID_R17",
                    "Rust state.layout differs from the Python canonical layout",
                    phase=ROUTING_PHASE,
                    schema_version=ROUTING_SCHEMA_VERSION,
                    command=STATE_LAYOUT_COMMAND,
                    input_profile="none",
                    mismatched_keys=mismatched_keys,
                    expected_sha256=_result_digest(expected),
                    actual_sha256=_result_digest(result),
                    fallback_policy="none",
                    fallback_attempted=False,
                )

        response_bytes = _canonical_result_size(result)
        if response_bytes > MAX_RESPONSE_BYTES:
            raise EngineSelectionError(
                "ENGINE_ROUTE_RESPONSE_TOO_LARGE_R17",
                "The state.layout result exceeded the response limit",
                phase=ROUTING_PHASE,
                schema_version=ROUTING_SCHEMA_VERSION,
                command=STATE_LAYOUT_COMMAND,
                response_bytes=response_bytes,
                maximum_response_bytes=MAX_RESPONSE_BYTES,
                fallback_policy="none",
                fallback_attempted=False,
            )

        return {
            "ok": True,
            "phase": ROUTING_PHASE,
            "schema_version": ROUTING_SCHEMA_VERSION,
            "command": STATE_LAYOUT_COMMAND,
            "capability": STATE_LAYOUT_CAPABILITY,
            "mutation": "read-only",
            "selection": selection.to_dict(),
            "input": _input_metadata("none", None),
            "fallback": {"policy": "none", "attempted": False},
            "result": result,
        }
