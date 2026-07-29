from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

from .engine_selector import EngineSelectionError, EngineSelector
from .read_only_router import MAX_RESPONSE_BYTES, RustRouteRunner, _result_digest
from .read_only_router_r17 import _canonical_result_size
from .read_only_router_r18 import ReadOnlyCommandRouterR18
from .state_receipt_contract import ReceiptContractError, inspect_receipt_wire
from .state_snapshot_contract import StateInspectionError, project_id_for_root

ROUTING_PHASE = "R19"
ROUTING_SCHEMA_VERSION = 8
RECEIPT_INSPECT_COMMAND = "receipt.inspect"
RECEIPT_INSPECT_CAPABILITY = "receipt.inspect"
RECEIPT_INPUT_PROFILE = "project-bound-receipt-wire-v1"
RECEIPT_INPUT_FORMAT = "R7RCPT1-lowercase-hex-v1"
MAX_RECEIPT_WIRE_BYTES = 64 * 1024
_LOWER_HEX = re.compile(r"^[0-9a-f]+$")
RECEIPT_RESULT_KEYS = frozenset(
    {
        "ok",
        "schema_version",
        "product_version",
        "contract_version",
        "engine",
        "operation",
        "created_at_ms",
        "project_id",
        "receipt_id",
        "payload_hash",
        "previous_hash",
        "fallback",
        "receipt_hash",
        "project_binding",
        "hash_valid",
        "claim",
    }
)


def _upgrade_error(exc: EngineSelectionError) -> EngineSelectionError:
    details = dict(exc.details)
    details["phase"] = ROUTING_PHASE
    details["schema_version"] = ROUTING_SCHEMA_VERSION
    return EngineSelectionError(exc.code, exc.message, **details)


def _decode_receipt_wire_hex(value: str) -> bytes:
    encoded = str(value)
    if len(encoded) > MAX_RECEIPT_WIRE_BYTES * 2:
        raise ReceiptContractError("RECEIPT_ROUTE_SIZE_LIMIT")
    if len(encoded) % 2 or _LOWER_HEX.fullmatch(encoded) is None:
        raise ReceiptContractError("RECEIPT_ROUTE_HEX_NONCANONICAL")
    wire = bytes.fromhex(encoded)
    if len(wire) > MAX_RECEIPT_WIRE_BYTES:
        raise ReceiptContractError("RECEIPT_ROUTE_SIZE_LIMIT")
    return wire


def _receipt_input_metadata(wire: bytes) -> dict[str, Any]:
    return {
        "profile": RECEIPT_INPUT_PROFILE,
        "format": RECEIPT_INPUT_FORMAT,
        "bytes": len(wire),
        "sha256": hashlib.sha256(wire).hexdigest(),
    }


class ReadOnlyCommandRouterR19(ReadOnlyCommandRouterR18):
    """R19 router admitting bounded project-bound receipt inspection."""

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
            sorted((*ReadOnlyCommandRouterR18.supported_commands(), RECEIPT_INSPECT_COMMAND))
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
    ) -> dict[str, Any]:
        normalized = str(command).strip().casefold()
        if normalized != RECEIPT_INSPECT_COMMAND:
            if receipt_wire_hex is not None:
                raise EngineSelectionError(
                    "ENGINE_ROUTE_RECEIPT_INPUT_UNSUPPORTED_R19",
                    "Receipt input is accepted only by receipt.inspect",
                    phase=ROUTING_PHASE,
                    schema_version=ROUTING_SCHEMA_VERSION,
                    command=normalized or "<missing>",
                    accepted_command=RECEIPT_INSPECT_COMMAND,
                    fallback_policy="none",
                    fallback_attempted=False,
                )
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
                "ENGINE_ROUTE_RECEIPT_INPUT_CONFLICT_R19",
                "receipt.inspect does not accept configuration input",
                phase=ROUTING_PHASE,
                schema_version=ROUTING_SCHEMA_VERSION,
                command=RECEIPT_INSPECT_COMMAND,
                accepted_input_profiles=[RECEIPT_INPUT_PROFILE],
                fallback_policy="none",
                fallback_attempted=False,
            )
        if receipt_wire_hex is None:
            raise EngineSelectionError(
                "ENGINE_ROUTE_RECEIPT_INPUT_REQUIRED_R19",
                "receipt.inspect requires one bounded receipt wire",
                phase=ROUTING_PHASE,
                schema_version=ROUTING_SCHEMA_VERSION,
                command=RECEIPT_INSPECT_COMMAND,
                accepted_input_profiles=[RECEIPT_INPUT_PROFILE],
                fallback_policy="none",
                fallback_attempted=False,
            )

        try:
            project_id = project_id_for_root(self.project_input_root)
            wire = _decode_receipt_wire_hex(receipt_wire_hex)
            expected = inspect_receipt_wire(wire, expected_project_id=project_id)
        except (ReceiptContractError, StateInspectionError) as exc:
            code = getattr(exc, "code", type(exc).__name__)
            raise EngineSelectionError(
                "ENGINE_ROUTE_RECEIPT_PREFLIGHT_FAILED_R19",
                "Project-bound receipt inspection failed before engine selection",
                phase=ROUTING_PHASE,
                schema_version=ROUTING_SCHEMA_VERSION,
                command=RECEIPT_INSPECT_COMMAND,
                input_profile=RECEIPT_INPUT_PROFILE,
                receipt_error=str(code),
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
                    "RUST_ENGINE_UNAVAILABLE_R19",
                    "Rust is selected but its binary or contract verification failed",
                    phase=ROUTING_PHASE,
                    schema_version=ROUTING_SCHEMA_VERSION,
                    command=RECEIPT_INSPECT_COMMAND,
                    input_profile=RECEIPT_INPUT_PROFILE,
                    selection=selection.to_dict(),
                    verification=verification.to_dict(),
                    fallback_policy="none",
                    fallback_attempted=False,
                )
            binary = self.selector.discover_rust_binary()
            if binary is None:
                raise EngineSelectionError(
                    "RUST_ENGINE_BINARY_NOT_FOUND_R19",
                    "Rust is selected but no verified binary is available for routing",
                    phase=ROUTING_PHASE,
                    schema_version=ROUTING_SCHEMA_VERSION,
                    command=RECEIPT_INSPECT_COMMAND,
                    input_profile=RECEIPT_INPUT_PROFILE,
                    selection=selection.to_dict(),
                    fallback_policy="none",
                    fallback_attempted=False,
                )
            try:
                raw = self.runner(
                    binary,
                    (
                        "receipt",
                        "inspect",
                        project_id,
                        wire.hex(),
                    ),
                )
            except EngineSelectionError:
                raise
            except Exception as exc:
                raise EngineSelectionError(
                    "RUST_ROUTE_EXECUTION_FAILED_R19",
                    "The Rust read-only route failed and was not re-executed in Python",
                    phase=ROUTING_PHASE,
                    schema_version=ROUTING_SCHEMA_VERSION,
                    command=RECEIPT_INSPECT_COMMAND,
                    input_profile=RECEIPT_INPUT_PROFILE,
                    exception=type(exc).__name__,
                    exception_message="redacted",
                    fallback_policy="none",
                    fallback_attempted=False,
                ) from exc
            result = dict(raw)
            actual_keys = frozenset(str(key) for key in result)
            if actual_keys != RECEIPT_RESULT_KEYS:
                raise EngineSelectionError(
                    "RUST_RECEIPT_RESULT_SCHEMA_INVALID_R19",
                    "Rust receipt.inspect returned an unexpected result shape",
                    phase=ROUTING_PHASE,
                    schema_version=ROUTING_SCHEMA_VERSION,
                    command=RECEIPT_INSPECT_COMMAND,
                    input_profile=RECEIPT_INPUT_PROFILE,
                    expected=sorted(RECEIPT_RESULT_KEYS),
                    actual=sorted(actual_keys),
                    fallback_policy="none",
                    fallback_attempted=False,
                )
            if result != expected:
                mismatched_keys = sorted(
                    key
                    for key in RECEIPT_RESULT_KEYS
                    if result.get(key) != expected.get(key)
                )
                raise EngineSelectionError(
                    "RUST_RECEIPT_ROUTE_PARITY_INVALID_R19",
                    "Rust receipt.inspect differs from the Python canonical result",
                    phase=ROUTING_PHASE,
                    schema_version=ROUTING_SCHEMA_VERSION,
                    command=RECEIPT_INSPECT_COMMAND,
                    input_profile=RECEIPT_INPUT_PROFILE,
                    mismatched_keys=mismatched_keys,
                    expected_sha256=_result_digest(expected),
                    actual_sha256=_result_digest(result),
                    fallback_policy="none",
                    fallback_attempted=False,
                )

        response_bytes = _canonical_result_size(result)
        if response_bytes > MAX_RESPONSE_BYTES:
            raise EngineSelectionError(
                "ENGINE_ROUTE_RESPONSE_TOO_LARGE_R19",
                "The receipt.inspect result exceeded the response limit",
                phase=ROUTING_PHASE,
                schema_version=ROUTING_SCHEMA_VERSION,
                command=RECEIPT_INSPECT_COMMAND,
                input_profile=RECEIPT_INPUT_PROFILE,
                response_bytes=response_bytes,
                maximum_response_bytes=MAX_RESPONSE_BYTES,
                fallback_policy="none",
                fallback_attempted=False,
            )

        return {
            "ok": True,
            "phase": ROUTING_PHASE,
            "schema_version": ROUTING_SCHEMA_VERSION,
            "command": RECEIPT_INSPECT_COMMAND,
            "capability": RECEIPT_INSPECT_CAPABILITY,
            "mutation": "read-only",
            "selection": selection.to_dict(),
            "input": _receipt_input_metadata(wire),
            "fallback": {"policy": "none", "attempted": False},
            "result": result,
        }
