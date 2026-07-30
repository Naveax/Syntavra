from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from .config_contract import resolve_config_wire
from .config_explain_router_r24 import ConfigExplainRouterR24
from .config_show_contract import (
    CANDIDATE_CAPABILITY,
    INPUT_FORMAT,
    INPUT_PROFILE,
    RESULT_KEYS,
    ROUTE,
    rust_argv,
    show_result,
)
from .engine_selector import EngineSelectionError
from .live_config_discovery import discover_live_config_wire
from .read_only_cli_contract import ROUTING_PHASE, ROUTING_SCHEMA_VERSION
from .read_only_router import MAX_RESPONSE_BYTES, _canonical_result_bytes, _result_digest
from .read_only_router_r22 import _RouteScopedSelector
from .unified_config import ConfigError


class ConfigShowRouterR24(ConfigExplainRouterR24):
    """Canonical state-free config.show routing over one immutable live wire."""

    @staticmethod
    def supported_commands() -> tuple[str, ...]:
        return tuple(sorted((*ConfigExplainRouterR24.supported_commands(), ROUTE)))

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
        explain_path: str | None = None,
    ) -> dict[str, Any]:
        normalized = str(command).strip().casefold()
        if normalized != ROUTE:
            return super().route(
                normalized,
                cli_override=cli_override,
                config_wire_hex=config_wire_hex,
                live_config=live_config,
                session_override_json_hex=session_override_json_hex,
                task_override_json_hex=task_override_json_hex,
                receipt_wire_hex=receipt_wire_hex,
                database_path=database_path,
                explain_path=explain_path,
            )

        if explain_path is not None or any(
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
                "ENGINE_ROUTE_CONFIG_SHOW_INPUT_UNSUPPORTED_R24",
                "config.show accepts no command input and uses implicit live config discovery",
                phase=ROUTING_PHASE,
                schema_version=ROUTING_SCHEMA_VERSION,
                command=ROUTE,
                accepted_input_profiles=[INPUT_PROFILE],
                fallback_policy="none",
                fallback_attempted=False,
            )

        try:
            wire = discover_live_config_wire(
                project_root=self.selector.project_root,
                env=self.selector.env,
            )
            snapshot = resolve_config_wire(wire)
            expected = show_result(snapshot)
        except ConfigError as exc:
            raise EngineSelectionError(
                "ENGINE_ROUTE_LIVE_CONFIG_INVALID_R24",
                "Live configuration resolution failed before config.show engine selection",
                phase=ROUTING_PHASE,
                schema_version=ROUTING_SCHEMA_VERSION,
                command=ROUTE,
                input_profile=INPUT_PROFILE,
                reason=str(exc),
                fallback_policy="none",
                fallback_attempted=False,
            ) from exc

        scoped_selector = _RouteScopedSelector(
            self.selector,
            command=ROUTE,
            capability=CANDIDATE_CAPABILITY,
            platform_probe=self.platform_probe,
        )
        selection = scoped_selector.resolve(cli_override=cli_override)

        if selection.resolved == "rust":
            verification = scoped_selector.verify_rust()
            if (
                not verification.compatible
                or CANDIDATE_CAPABILITY not in verification.capabilities
            ):
                raise EngineSelectionError(
                    "RUST_ENGINE_UNAVAILABLE_R24",
                    "Rust is selected but config.show is not verified",
                    phase=ROUTING_PHASE,
                    schema_version=ROUTING_SCHEMA_VERSION,
                    command=ROUTE,
                    capability=CANDIDATE_CAPABILITY,
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
                    command=ROUTE,
                    capability=CANDIDATE_CAPABILITY,
                    selection=selection.to_dict(),
                    fallback_policy="none",
                    fallback_attempted=False,
                )
            try:
                candidate = dict(self.runner(binary, rust_argv(wire)))
            except EngineSelectionError:
                raise
            except Exception as exc:
                raise EngineSelectionError(
                    "RUST_ROUTE_EXECUTION_FAILED_R24",
                    "Rust config.show failed and was not re-executed in Python",
                    phase=ROUTING_PHASE,
                    schema_version=ROUTING_SCHEMA_VERSION,
                    command=ROUTE,
                    capability=CANDIDATE_CAPABILITY,
                    exception=type(exc).__name__,
                    exception_message="redacted",
                    fallback_policy="none",
                    fallback_attempted=False,
                ) from exc
            if candidate != expected:
                keys = frozenset(candidate) | frozenset(expected)
                mismatched_keys = sorted(
                    key for key in keys if candidate.get(key) != expected.get(key)
                )
                raise EngineSelectionError(
                    "RUST_CONFIG_SHOW_PARITY_INVALID_R24",
                    "Rust config.show differs from the Python canonical snapshot",
                    phase=ROUTING_PHASE,
                    schema_version=ROUTING_SCHEMA_VERSION,
                    command=ROUTE,
                    capability=CANDIDATE_CAPABILITY,
                    mismatched_keys=mismatched_keys,
                    expected_sha256=_result_digest(expected),
                    actual_sha256=_result_digest(candidate),
                    fallback_policy="none",
                    fallback_attempted=False,
                )

        if frozenset(expected) != RESULT_KEYS or "loaded_at" in expected:
            raise EngineSelectionError(
                "CONFIG_SHOW_RESULT_SCHEMA_INVALID_R24",
                "The config.show result differs from the canonical deterministic shape",
                phase=ROUTING_PHASE,
                schema_version=ROUTING_SCHEMA_VERSION,
                command=ROUTE,
                expected=sorted(RESULT_KEYS),
                actual=sorted(expected),
                forbidden=["loaded_at"],
                fallback_policy="none",
                fallback_attempted=False,
            )
        response_bytes = len(_canonical_result_bytes(expected))
        if response_bytes > MAX_RESPONSE_BYTES:
            raise EngineSelectionError(
                "ENGINE_ROUTE_RESPONSE_TOO_LARGE_R24",
                "The config.show result exceeded the response limit",
                phase=ROUTING_PHASE,
                schema_version=ROUTING_SCHEMA_VERSION,
                command=ROUTE,
                response_bytes=response_bytes,
                maximum_response_bytes=MAX_RESPONSE_BYTES,
                fallback_policy="none",
                fallback_attempted=False,
            )

        return {
            "ok": True,
            "phase": ROUTING_PHASE,
            "schema_version": ROUTING_SCHEMA_VERSION,
            "command": ROUTE,
            "capability": CANDIDATE_CAPABILITY,
            "mutation": "read-only",
            "selection": selection.to_dict(),
            "input": {
                "profile": INPUT_PROFILE,
                "format": INPUT_FORMAT,
                "bytes": len(wire),
                "sha256": hashlib.sha256(wire).hexdigest(),
            },
            "fallback": {"policy": "none", "attempted": False},
            "result": expected,
        }
