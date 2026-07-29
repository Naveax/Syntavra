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

ROUTING_PHASE = "R14"
ROUTING_SCHEMA_VERSION = 3
MAX_RESPONSE_BYTES = 1024 * 1024

RustRouteRunner = Callable[[Path, tuple[str, ...]], Mapping[str, Any]]

CONFIG_RESULT_KEYS = frozenset(
    {
        "schema_version",
        "values",
        "provenance",
        "config_hash",
        "warnings",
    }
)
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
    "config.resolve": ReadOnlyRoute(
        command="config.resolve",
        capability="config.resolve",
        mutation="read-only",
        result_keys=CONFIG_RESULT_KEYS,
    ),
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


def _explicit_config_input(
    command: str,
    config_wire_hex: str | None,
) -> tuple[bytes, dict[str, Any]]:
    if config_wire_hex is None:
        raise EngineSelectionError(
            "ENGINE_ROUTE_INPUT_REQUIRED_R14",
            "The selected route requires an explicit canonical configuration wire",
            phase=ROUTING_PHASE,
            schema_version=ROUTING_SCHEMA_VERSION,
            command=command,
            accepted_input_profiles=["explicit-config-wire-v1"],
            maximum_input_bytes=MAX_CONFIG_WIRE_BYTES,
            fallback_policy="none",
            fallback_attempted=False,
        )
    try:
        raw = decode_config_wire_hex(
            config_wire_hex,
            maximum_bytes=MAX_CONFIG_WIRE_BYTES,
        )
        snapshot = resolve_config_wire(raw)
    except ConfigError as exc:
        raise EngineSelectionError(
            "ENGINE_ROUTE_INPUT_INVALID_R14",
            "The explicit configuration wire is invalid",
            phase=ROUTING_PHASE,
            schema_version=ROUTING_SCHEMA_VERSION,
            command=command,
            input_profile="explicit-config-wire-v1",
            provided_hex_characters=len(str(config_wire_hex)),
            maximum_input_bytes=MAX_CONFIG_WIRE_BYTES,
            reason=str(exc),
            fallback_policy="none",
            fallback_attempted=False,
        ) from exc
    return raw, snapshot


def _prepare_route_input(
    command: str,
    config_wire_hex: str | None,
) -> PreparedRouteInput:
    if command == "version":
        if config_wire_hex is not None:
            raise EngineSelectionError(
                "ENGINE_ROUTE_INPUT_UNSUPPORTED_R14",
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

    if command == "status":
        if config_wire_hex is None:
            raw = encode_config_wire([{}])
            snapshot = resolve_config_wire(raw)
            profile = "default-config-only"
            rust_argv = ("status",)
        else:
            raw, snapshot = _explicit_config_input(command, config_wire_hex)
            profile = "explicit-config-wire-v1"
            rust_argv = ("status", raw.hex())
        return PreparedRouteInput(
            metadata=_input_metadata(profile, raw),
            rust_argv=rust_argv,
            python_result=status_projection(snapshot),
        )

    if command == "config.resolve":
        raw, snapshot = _explicit_config_input(command, config_wire_hex)
        return PreparedRouteInput(
            metadata=_input_metadata("explicit-config-wire-v1", raw),
            rust_argv=("config", "resolve", raw.hex()),
            python_result=snapshot,
        )

    raise RuntimeError(f"unhandled read-only route: {command}")


def _canonical_result_bytes(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        default=str,
    ).encode("utf-8")


def _result_digest(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_result_bytes(value)).hexdigest()


def _validate_response_size(command: str, value: Mapping[str, Any]) -> dict[str, Any]:
    response_bytes = len(_canonical_result_bytes(value))
    if response_bytes > MAX_RESPONSE_BYTES:
        raise EngineSelectionError(
            "ENGINE_ROUTE_RESPONSE_TOO_LARGE_R14",
            "The read-only route result exceeded the response limit",
            phase=ROUTING_PHASE,
            schema_version=ROUTING_SCHEMA_VERSION,
            command=command,
            response_bytes=response_bytes,
            maximum_response_bytes=MAX_RESPONSE_BYTES,
            fallback_policy="none",
            fallback_attempted=False,
        )
    return dict(value)


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
    mismatches = sorted(
        key for key, expected_value in expected.items() if result.get(key) != expected_value
    )
    if mismatches:
        raise EngineSelectionError(
            "RUST_ROUTE_RESULT_IDENTITY_INVALID",
            "Rust version route failed the locked product identity contract",
            phase=ROUTING_PHASE,
            schema_version=ROUTING_SCHEMA_VERSION,
            command="version",
            mismatched_keys=mismatches,
            expected_sha256=_result_digest(expected),
            actual_sha256=_result_digest(result),
            fallback_policy="none",
            fallback_attempted=False,
        )
    return result


def _validate_exact_result(
    command: str,
    value: Mapping[str, Any],
    *,
    prepared: PreparedRouteInput,
    expected_keys: frozenset[str],
    error_code: str,
    error_message: str,
) -> dict[str, Any]:
    result = _validate_result_shape(command, value, expected_keys)
    expected = prepared.python_result
    if result != expected:
        mismatched_keys = sorted(
            key
            for key in expected_keys
            if result.get(key) != expected.get(key)
        )
        raise EngineSelectionError(
            error_code,
            error_message,
            phase=ROUTING_PHASE,
            schema_version=ROUTING_SCHEMA_VERSION,
            command=command,
            input_profile=prepared.metadata["profile"],
            mismatched_keys=mismatched_keys,
            expected_sha256=_result_digest(expected),
            actual_sha256=_result_digest(result),
            fallback_policy="none",
            fallback_attempted=False,
        )
    return result


def _validate_rust_result(
    definition: ReadOnlyRoute,
    value: Mapping[str, Any],
    prepared: PreparedRouteInput,
) -> dict[str, Any]:
    if definition.command == "version":
        return _validate_version_result(value)
    if definition.command == "status":
        return _validate_exact_result(
            "status",
            value,
            prepared=prepared,
            expected_keys=definition.result_keys,
            error_code="RUST_STATUS_ROUTE_PARITY_INVALID",
            error_message="Rust status route differs from the Python status projection",
        )
    if definition.command == "config.resolve":
        return _validate_exact_result(
            "config.resolve",
            value,
            prepared=prepared,
            expected_keys=definition.result_keys,
            error_code="RUST_CONFIG_RESOLVE_ROUTE_PARITY_INVALID",
            error_message="Rust config.resolve route differs from the Python snapshot",
        )
    raise RuntimeError(f"unhandled read-only route: {definition.command}")


class ReadOnlyCommandRouter:
    """R14 capability-whitelisted routing with explicit config snapshot parity."""

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
                "ENGINE_ROUTE_UNSUPPORTED_R14",
                "The selected R14 route is not in the read-only capability whitelist",
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
                    "RUST_ENGINE_UNAVAILABLE_R14",
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
                    "RUST_ENGINE_BINARY_NOT_FOUND_R14",
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
                    "RUST_ROUTE_EXECUTION_FAILED_R14",
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
            result = _validate_rust_result(definition, raw, prepared)

        result = _validate_response_size(definition.command, result)
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
