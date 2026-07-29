from __future__ import annotations

from pathlib import Path
from typing import Any

from .engine_selector import EngineSelectionError, EngineSelector
from .read_only_cli_contract import (
    ROUTING_PHASE,
    ROUTING_SCHEMA_VERSION,
    static_route_result,
    static_route_rust_argv,
    static_routes,
)
from .read_only_router import MAX_RESPONSE_BYTES, RustRouteRunner, _canonical_result_bytes, _result_digest
from .read_only_router_r22 import (
    PlatformProbe,
    ReadOnlyCommandRouterR22,
    _RouteScopedSelector,
    _default_platform_probe,
)

STATIC_ROUTE_CAPABILITIES = {
    "pipeline.describe": "pipeline.describe",
    "plugins.list": "plugins.list",
}


def _upgrade_error(exc: EngineSelectionError) -> EngineSelectionError:
    details = dict(exc.details)
    details["phase"] = ROUTING_PHASE
    details["schema_version"] = ROUTING_SCHEMA_VERSION
    return EngineSelectionError(exc.code, exc.message, **details)


class ReadOnlyCommandRouterR24(ReadOnlyCommandRouterR22):
    """R24 router for side-effect-free static product introspection commands."""

    def __init__(
        self,
        selector: EngineSelector,
        *,
        runner: RustRouteRunner | None = None,
        project_input_root: Path | None = None,
        platform_probe: PlatformProbe | None = None,
    ) -> None:
        super().__init__(
            selector,
            runner=runner,
            project_input_root=project_input_root,
            platform_probe=platform_probe,
        )
        self.platform_probe = platform_probe or _default_platform_probe

    @staticmethod
    def supported_commands() -> tuple[str, ...]:
        return tuple(sorted((*ReadOnlyCommandRouterR22.supported_commands(), *static_routes())))

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
        capability = STATIC_ROUTE_CAPABILITIES.get(normalized)
        if capability is None:
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

        if any(
            (
                config_wire_hex is not None,
                live_config,
                session_override_json_hex is not None,
                task_override_json_hex is not None,
                receipt_wire_hex is not None,
                database_path is not None,
            )
        ):
            raise EngineSelectionError(
                "ENGINE_ROUTE_STATIC_INPUT_UNSUPPORTED_R24",
                "R24 static read-only routes accept no command input",
                phase=ROUTING_PHASE,
                schema_version=ROUTING_SCHEMA_VERSION,
                command=normalized,
                accepted_input_profiles=["none"],
                fallback_policy="none",
                fallback_attempted=False,
            )

        expected = static_route_result(normalized)
        scoped_selector = _RouteScopedSelector(
            self.selector,
            command=normalized,
            capability=capability,
            platform_probe=self.platform_probe,
        )
        selection = scoped_selector.resolve(cli_override=cli_override)

        if selection.resolved == "rust":
            verification = scoped_selector.verify_rust()
            if not verification.compatible or capability not in verification.capabilities:
                raise EngineSelectionError(
                    "RUST_ENGINE_UNAVAILABLE_R24",
                    "Rust is selected but the static CLI capability is not verified",
                    phase=ROUTING_PHASE,
                    schema_version=ROUTING_SCHEMA_VERSION,
                    command=normalized,
                    capability=capability,
                    selection=selection.to_dict(),
                    verification=verification.to_dict(),
                    fallback_policy="none",
                    fallback_attempted=False,
                )
            binary = self.selector.discover_rust_binary()
            if binary is None:
                raise EngineSelectionError(
                    "RUST_ENGINE_BINARY_NOT_FOUND_R24",
                    "Rust is selected but no verified binary is available",
                    phase=ROUTING_PHASE,
                    schema_version=ROUTING_SCHEMA_VERSION,
                    command=normalized,
                    capability=capability,
                    selection=selection.to_dict(),
                    fallback_policy="none",
                    fallback_attempted=False,
                )
            try:
                candidate = dict(self.runner(binary, static_route_rust_argv(normalized)))
            except EngineSelectionError:
                raise
            except Exception as exc:
                raise EngineSelectionError(
                    "RUST_ROUTE_EXECUTION_FAILED_R24",
                    "The Rust static read-only route failed and was not re-executed in Python",
                    phase=ROUTING_PHASE,
                    schema_version=ROUTING_SCHEMA_VERSION,
                    command=normalized,
                    capability=capability,
                    exception=type(exc).__name__,
                    exception_message="redacted",
                    fallback_policy="none",
                    fallback_attempted=False,
                ) from exc

            expected_keys = frozenset(expected)
            actual_keys = frozenset(candidate)
            if actual_keys != expected_keys or candidate != expected:
                mismatched_keys = sorted(
                    key
                    for key in expected_keys | actual_keys
                    if candidate.get(key) != expected.get(key)
                )
                raise EngineSelectionError(
                    "RUST_STATIC_CLI_ROUTE_PARITY_INVALID_R24",
                    "Rust static CLI result differs from the Python canonical result",
                    phase=ROUTING_PHASE,
                    schema_version=ROUTING_SCHEMA_VERSION,
                    command=normalized,
                    capability=capability,
                    mismatched_keys=mismatched_keys,
                    expected_sha256=_result_digest(expected),
                    actual_sha256=_result_digest(candidate),
                    fallback_policy="none",
                    fallback_attempted=False,
                )

        response_bytes = len(_canonical_result_bytes(expected))
        if response_bytes > MAX_RESPONSE_BYTES:
            raise EngineSelectionError(
                "ENGINE_ROUTE_RESPONSE_TOO_LARGE_R24",
                "The static read-only result exceeded the response limit",
                phase=ROUTING_PHASE,
                schema_version=ROUTING_SCHEMA_VERSION,
                command=normalized,
                response_bytes=response_bytes,
                maximum_response_bytes=MAX_RESPONSE_BYTES,
                fallback_policy="none",
                fallback_attempted=False,
            )

        return {
            "ok": True,
            "phase": ROUTING_PHASE,
            "schema_version": ROUTING_SCHEMA_VERSION,
            "command": normalized,
            "capability": capability,
            "mutation": "read-only",
            "selection": selection.to_dict(),
            "input": {"profile": "none", "format": None, "bytes": 0, "sha256": None},
            "fallback": {"policy": "none", "attempted": False},
            "result": expected,
        }
