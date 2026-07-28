from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from .engine_selector import ENGINE_CONTRACT_VERSION, EngineSelectionError, EngineSelector
from .release_identity import CHANNEL, VERSION

ROUTING_PHASE = "R11"
ROUTING_SCHEMA_VERSION = 1
MAX_RESPONSE_BYTES = 1024 * 1024

RustRouteRunner = Callable[[Path, tuple[str, ...]], Mapping[str, Any]]


@dataclass(frozen=True)
class ReadOnlyRoute:
    command: str
    capability: str
    mutation: str
    rust_argv: tuple[str, ...]


READ_ONLY_ROUTES: Mapping[str, ReadOnlyRoute] = {
    "version": ReadOnlyRoute(
        command="version",
        capability="version",
        mutation="read-only",
        rust_argv=("version",),
    ),
}

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


def _validate_version_result(value: Mapping[str, Any]) -> dict[str, Any]:
    actual_keys = frozenset(str(key) for key in value)
    if actual_keys != VERSION_RESULT_KEYS:
        raise EngineSelectionError(
            "RUST_ROUTE_RESULT_SCHEMA_INVALID",
            "Rust version route returned an unexpected result shape",
            phase=ROUTING_PHASE,
            schema_version=ROUTING_SCHEMA_VERSION,
            expected=sorted(VERSION_RESULT_KEYS),
            actual=sorted(actual_keys),
            fallback_attempted=False,
        )
    result = dict(value)
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
            mismatches=mismatches,
            fallback_attempted=False,
        )
    return result


class ReadOnlyCommandRouter:
    """R11 capability-whitelisted routing with no cross-engine fallback."""

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
    ) -> dict[str, Any]:
        normalized = str(command).strip().casefold()
        definition = READ_ONLY_ROUTES.get(normalized)
        if definition is None:
            raise EngineSelectionError(
                "ENGINE_ROUTE_UNSUPPORTED_R11",
                "The selected R11 route is not in the read-only capability whitelist",
                phase=ROUTING_PHASE,
                schema_version=ROUTING_SCHEMA_VERSION,
                command=normalized or "<missing>",
                supported=list(self.supported_commands()),
                fallback_policy="none",
                fallback_attempted=False,
            )

        selection = self.selector.resolve(cli_override=cli_override)
        if selection.resolved == "python":
            result = _python_version_result()
        else:
            verification = self.selector.verify_rust()
            if not verification.compatible:
                raise EngineSelectionError(
                    "RUST_ENGINE_UNAVAILABLE_R11",
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
                    "RUST_ENGINE_BINARY_NOT_FOUND_R11",
                    "Rust is selected but no verified binary is available for routing",
                    phase=ROUTING_PHASE,
                    schema_version=ROUTING_SCHEMA_VERSION,
                    command=definition.command,
                    selection=selection.to_dict(),
                    fallback_policy="none",
                    fallback_attempted=False,
                )
            try:
                raw = self.runner(binary, definition.rust_argv)
            except EngineSelectionError:
                raise
            except Exception as exc:
                raise EngineSelectionError(
                    "RUST_ROUTE_EXECUTION_FAILED_R11",
                    "The Rust read-only route failed and was not re-executed in Python",
                    phase=ROUTING_PHASE,
                    schema_version=ROUTING_SCHEMA_VERSION,
                    command=definition.command,
                    exception=type(exc).__name__,
                    exception_message=str(exc),
                    fallback_policy="none",
                    fallback_attempted=False,
                ) from exc
            result = _validate_version_result(raw)

        return {
            "ok": True,
            "phase": ROUTING_PHASE,
            "schema_version": ROUTING_SCHEMA_VERSION,
            "command": definition.command,
            "capability": definition.capability,
            "mutation": definition.mutation,
            "selection": selection.to_dict(),
            "fallback": {"policy": "none", "attempted": False},
            "result": result,
        }
