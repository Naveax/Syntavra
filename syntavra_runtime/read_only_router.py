from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from .config_contract import (
    MAX_CONFIG_WIRE_BYTES,
    decode_config_wire_hex,
    encode_config_wire,
    resolve_config_wire,
    status_projection,
)
from .engine_selector import ENGINE_CONTRACT_VERSION, EngineSelectionError, EngineSelector
from .release_identity import CHANNEL, VERSION
from .unified_config import ConfigError

ROUTING_PHASE = "R13"
ROUTING_SCHEMA_VERSION = 2
MAX_RESPONSE_BYTES = 1024 * 1024

RustRouteRunner = Callable[[Path, tuple[str, ...]], Mapping[str, Any]]

VERSION_RESULT_KEYS = frozenset(
    {
        "product",
        "product_version",
        "release_channel",
        "engine",
        "engine_stability",
        "contract_version",
    }
)
STATUS_RESULT_KEYS = frozenset(
    {
        "product",
        "product_version",
        "release_channel",
        "stability",
        "version_locked",
        "reference_engine",
        "candidate_engine",
        "candidate_stability",
        "config_schema_version",
        "config_hash",
        "warnings",
        "general_command_routing",
        "mutation",
    }
)


@dataclass(frozen=True)
class ReadOnlyRoute:
    command: str
    capability: str
    mutation: str
    result_keys: frozenset[str]


@dataclass(frozen=True)
class PreparedRouteInput:
    metadata: Mapping[str, Any]
    rust_argv: tuple[str, ...]
    python_result: Mapping[str, Any]


READ_ONLY_ROUTES: Mapping[str, ReadOnlyRoute] = {
    "status": ReadOnlyRoute(
        command="status",
        capability="status",
        mutation="read-only",
        result_keys=STATUS_RESULT_KEYS,
    ),
    "version": ReadOnlyRoute(
        command="version",
        capability="version",
        mutation="read-only",
        result_keys=VERSION_RESULT_KEYS,
    ),
}


def _run_rust_json(binary: Path, arguments: tuple[str, ...]) -> Mapping[str, Any]:
    completed = subprocess.run(
        [str(binary), *arguments],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"Rust route failed ({completed.returncode}): {completed.stderr.strip()}"
        )
    if len(completed.stdout.encode("utf-8")) > MAX_RESPONSE_BYTES:
        raise RuntimeError("Rust route response exceeded 1 MiB")
    value = json.loads(completed.stdout)
    if not isinstance(value, dict):
        raise RuntimeError("Rust route response must be a JSON object")
    return value


def _python_version_result() -> dict[str, Any]:
    return {
        "product": "Syntavra",
        "product_version": VERSION,
        "release_channel": CHANNEL,
        "engine": "python",
        "engine_stability": "reference",
        "contract_version": ENGINE_CONTRACT_VERSION,
    }


def _input_metadata(profile: str, raw: bytes | None) -> dict[str, Any]:
    return {
        "profile": profile,
        "format": "R6CFG1" if raw is not None else None,
        "bytes": len(raw) if raw is not None else 0,
        "sha256": hashlib.sha256(raw).hexdigest() if raw is not None else None,
    }


def _prepare_route_input(
    command: str,
    config_wire_hex: str | None,
) -> PreparedRouteInput:
    if command == "version":
        if config_wire_hex is not None:
            raise EngineSelectionError(
                "ENGINE_ROUTE_INPUT_UNSUPPORTED_R13",
                "The version route does not accept configuration input",
                phase=ROUTING_PHASE,
                schema_version=ROUTING_SCHEMA_VERSION,
                command="version",
                accepted_input_profiles=["none"],
                fallback_policy="none",
                fallback_attempted=False,
            )
        return PreparedRouteInput(
            metadata=_input_metadata("none", None),
            rust_argv=("version",),
            python_result=_python_version_result(),
        )

    if command != "status":
        raise RuntimeError(f"unhandled read-only route: {command}")

    if config_wire_hex is None:
        raw = encode_config_wire([{}])
        profile = "default-config-only"
        rust_argv = ("status",)
        snapshot = resolve_config_wire(raw)
    else:
        try:
            raw = decode_config_wire_hex(
                config_wire_hex,
                maximum_bytes=MAX_CONFIG_WIRE_BYTES,
            )
            snapshot = resolve_config_wire(raw)
        except ConfigError as exc:
            raise EngineSelectionError(
                "ENGINE_ROUTE_INPUT_INVALID_R13",
                "The explicit status configuration wire is invalid",
                phase=ROUTING_PHASE,
                schema_version=ROUTING_SCHEMA_VERSION,
                command="status",
                input_profile="explicit-config-wire-v1",
                provided_hex_characters=len(str(config_wire_hex)),
                maximum_input_bytes=MAX_CONFIG_WIRE_BYTES,
                reason=str(exc),
                fallback_policy="none",
                fallback_attempted=False,
            ) from exc
        profile = "explicit-config-wire-v1"
        rust_argv = ("status", raw.hex())

    return PreparedRouteInput(
        metadata=_input_metadata(profile, raw),
        rust_argv=rust_argv,
        python_result=status_projection(snapshot),
    )


def _validate_result_shape(
    command: str,
    value: Mapping[str, Any],
    expected_keys: frozenset[str],
) -> dict[str, Any]:
    actual_keys = frozenset(str(key) for key in value)
    if actual_keys != expected_keys:
        raise EngineSelectionError(
            "RUST_ROUTE_RESULT_SCHEMA_INVALID",
            "Rust read-only route returned an unexpected result shape",
            phase=ROUTING_PHASE,
            schema_version=ROUTING_SCHEMA_VERSION,
            command=command,
            expected=sorted(expected_keys),
            actual=sorted(actual_keys),
            fallback_policy="none",
            fallback_attempted=False,
        )
    return dict(value)


def _validate_version_result(value: Mapping[str, Any]) -> dict[str, Any]:
    result = _validate_result_shape("version", value, VERSION_RESULT_KEYS)
    expected = {
        "product": "Syntavra",
        "product_version": VERSION,
        "release_channel": CHANNEL,
        "engine": "rust",
        "engine_stability": "experimental",
        "contract_version": ENGINE_CONTRACT_VERSION,
    }
    mismatches = {
        key: {"expected": expected_value, "actual": result.get(key)}
        for key, expected_value in expected.items()
        if result.get(key) != expected_value
    }
    if mismatches:
        raise EngineSelectionError(
            "RUST_ROUTE_RESULT_IDENTITY_INVALID",
            "Rust version route failed the locked product identity contract",
            phase=ROUTING_PHASE,
            schema_version=ROUTING_SCHEMA_VERSION,
            command="version",
            mismatches=mismatches,
            fallback_policy="none",
            fallback_attempted=False,
        )
    return result


def _validate_status_result(
    value: Mapping[str, Any],
    *,
    expected: Mapping[str, Any],
    input_profile: str,
) -> dict[str, Any]:
    result = _validate_result_shape("status", value, STATUS_RESULT_KEYS)
    if result != expected:
        mismatches = {
            key: {"expected": expected.get(key), "actual": result.get(key)}
            for key in sorted(STATUS_RESULT_KEYS)
            if result.get(key) != expected.get(key)
        }
        raise EngineSelectionError(
            "RUST_STATUS_ROUTE_PARITY_INVALID",
            "Rust status route differs from the Python status projection",
            phase=ROUTING_PHASE,
            schema_version=ROUTING_SCHEMA_VERSION,
            command="status",
            input_profile=input_profile,
            mismatches=mismatches,
            fallback_policy="none",
            fallback_attempted=False,
        )
    return result


def _validate_rust_result(
    command: str,
    value: Mapping[str, Any],
    prepared: PreparedRouteInput,
) -> dict[str, Any]:
    if command == "status":
        return _validate_status_result(
            value,
            expected=prepared.python_result,
            input_profile=str(prepared.metadata["profile"]),
        )
    if command == "version":
        return _validate_version_result(value)
    raise RuntimeError(f"unhandled read-only route: {command}")


class ReadOnlyCommandRouter:
    """R13 capability-whitelisted routing with bounded explicit config input."""

    def __init__(
        self,
        selector: EngineSelector,
        *,
        runner: RustRouteRunner | None = None,
    ) -> None:
        self.selector = selector
        self.runner = runner or _run_rust_json

    @staticmethod
    def supported_commands() -> tuple[str, ...]:
        return tuple(sorted(READ_ONLY_ROUTES))

    def route(
        self,
        command: str,
        *,
        cli_override: str | None = None,
        config_wire_hex: str | None = None,
    ) -> dict[str, Any]:
        normalized = str(command).strip().casefold()
        definition = READ_ONLY_ROUTES.get(normalized)
        if definition is None:
            raise EngineSelectionError(
                "ENGINE_ROUTE_UNSUPPORTED_R13",
                "The selected R13 route is not in the read-only capability whitelist",
                phase=ROUTING_PHASE,
                schema_version=ROUTING_SCHEMA_VERSION,
                command=normalized or "<missing>",
                supported=list(self.supported_commands()),
                fallback_policy="none",
                fallback_attempted=False,
            )

        prepared = _prepare_route_input(definition.command, config_wire_hex)
        selection = self.selector.resolve(cli_override=cli_override)
        if selection.resolved == "python":
            result = dict(prepared.python_result)
        else:
            verification = self.selector.verify_rust()
            if not verification.compatible:
                raise EngineSelectionError(
                    "RUST_ENGINE_UNAVAILABLE_R13",
                    "Rust is selected but its binary or contract verification failed",
                    phase=ROUTING_PHASE,
                    schema_version=ROUTING_SCHEMA_VERSION,
                    command=definition.command,
                    selection=selection.to_dict(),
                    verification=verification.to_dict(),
                    fallback_policy="none",
                    fallback_attempted=False,
                )
            binary = self.selector.discover_rust_binary()
            if binary is None:
                raise EngineSelectionError(
                    "RUST_ENGINE_BINARY_NOT_FOUND_R13",
                    "Rust is selected but no verified binary is available for routing",
                    phase=ROUTING_PHASE,
                    schema_version=ROUTING_SCHEMA_VERSION,
                    command=definition.command,
                    selection=selection.to_dict(),
                    fallback_policy="none",
                    fallback_attempted=False,
                )
            try:
                raw = self.runner(binary, prepared.rust_argv)
            except EngineSelectionError:
                raise
            except Exception as exc:
                raise EngineSelectionError(
                    "RUST_ROUTE_EXECUTION_FAILED_R13",
                    "The Rust read-only route failed and was not re-executed in Python",
                    phase=ROUTING_PHASE,
                    schema_version=ROUTING_SCHEMA_VERSION,
                    command=definition.command,
                    input_profile=prepared.metadata["profile"],
                    exception=type(exc).__name__,
                    exception_message="redacted",
                    fallback_policy="none",
                    fallback_attempted=False,
                ) from exc
            result = _validate_rust_result(definition.command, raw, prepared)

        return {
            "ok": True,
            "phase": ROUTING_PHASE,
            "schema_version": ROUTING_SCHEMA_VERSION,
            "command": definition.command,
            "capability": definition.capability,
            "mutation": definition.mutation,
            "selection": selection.to_dict(),
            "input": dict(prepared.metadata),
            "fallback": {"policy": "none", "attempted": False},
            "result": result,
        }
