from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from .engine_selector import EngineSelection, EngineSelectionError, EngineSelector
from .read_only_router import RustRouteRunner
from .read_only_router_r21 import ReadOnlyCommandRouterR21

ROUTING_PHASE = "R22"
ROUTING_SCHEMA_VERSION = 11
AUTO_POLICY = "route-scoped-capability-aware-rust-v1"
SUPPORTED_AUTO_PLATFORMS = frozenset({"darwin", "linux", "win32"})
AUTO_RUST_COMMANDS = frozenset(ReadOnlyCommandRouterR21.supported_commands())


def _normalized_platform(value: str) -> str:
    lowered = str(value).strip().casefold()
    if lowered.startswith("linux"):
        return "linux"
    if lowered.startswith("win"):
        return "win32"
    if lowered.startswith("darwin"):
        return "darwin"
    return lowered


def _auto_selection(
    selection: EngineSelection,
    *,
    resolved: str,
    reason: str,
) -> dict[str, Any]:
    value = selection.to_dict()
    value.update(
        {
            "resolved": resolved,
            "reason": reason,
            "auto_policy": AUTO_POLICY,
            "fallback_policy": "fail-closed",
        }
    )
    return value


class ReadOnlyCommandRouterR22(ReadOnlyCommandRouterR21):
    """Route-scoped capability-aware automatic engine selection.

    Automatic selection may choose Rust only for the installed read-only route
    whitelist and only after binary, platform, contract and capability checks
    pass. Selecting Python before execution is policy resolution, not fallback.
    Once a Rust route starts, any failure remains fail-closed.
    """

    def __init__(
        self,
        selector: EngineSelector,
        *,
        runner: RustRouteRunner | None = None,
        project_input_root: Path | None = None,
        platform_name: str | None = None,
    ) -> None:
        super().__init__(
            selector,
            runner=runner,
            project_input_root=project_input_root,
        )
        self.platform_name = _normalized_platform(
            sys.platform if platform_name is None else platform_name
        )

    @staticmethod
    def supported_commands() -> tuple[str, ...]:
        return ReadOnlyCommandRouterR21.supported_commands()

    def _auto_decision(self, command: str) -> dict[str, Any]:
        route_supported = command in AUTO_RUST_COMMANDS
        platform_supported = self.platform_name in SUPPORTED_AUTO_PLATFORMS
        verification = None

        if not route_supported:
            selected = "python"
            reason = "AUTO_ROUTE_PYTHON_UNSUPPORTED_ROUTE_R22"
        elif not platform_supported:
            selected = "python"
            reason = "AUTO_ROUTE_PYTHON_UNSUPPORTED_PLATFORM_R22"
        else:
            verification = self.selector.verify_rust()
            capability_present = command in verification.capabilities
            if not verification.available:
                selected = "python"
                reason = "AUTO_ROUTE_PYTHON_RUST_UNAVAILABLE_R22"
            elif not verification.compatible:
                selected = "python"
                reason = "AUTO_ROUTE_PYTHON_CONTRACT_INCOMPATIBLE_R22"
            elif not capability_present:
                selected = "python"
                reason = "AUTO_ROUTE_PYTHON_CAPABILITY_MISSING_R22"
            else:
                selected = "rust"
                reason = "AUTO_ROUTE_RUST_ELIGIBLE_R22"

        capability_present = bool(
            verification is not None and command in verification.capabilities
        )
        return {
            "policy": AUTO_POLICY,
            "stage": "pre-execution",
            "command": command,
            "required_capability": command,
            "selected_engine": selected,
            "reason": reason,
            "route_supported": route_supported,
            "platform": self.platform_name,
            "platform_supported": platform_supported,
            "rust_available": bool(verification and verification.available),
            "rust_compatible": bool(verification and verification.compatible),
            "capability_present": capability_present,
            "rust_started": False,
            "fallback_attempted": False,
        }

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
        original = self.selector.resolve(cli_override=cli_override)
        decision: dict[str, Any] | None = None
        effective_override = cli_override

        if original.requested == "auto":
            decision = self._auto_decision(normalized)
            effective_override = str(decision["selected_engine"])

        try:
            result = super().route(
                normalized,
                cli_override=effective_override,
                config_wire_hex=config_wire_hex,
                live_config=live_config,
                session_override_json_hex=session_override_json_hex,
                task_override_json_hex=task_override_json_hex,
                receipt_wire_hex=receipt_wire_hex,
                database_path=database_path,
            )
        except EngineSelectionError as exc:
            details = dict(exc.details)
            details["phase"] = ROUTING_PHASE
            details["schema_version"] = ROUTING_SCHEMA_VERSION
            if decision is not None:
                details["auto_decision"] = decision
            raise EngineSelectionError(exc.code, exc.message, **details) from exc

        result["phase"] = ROUTING_PHASE
        result["schema_version"] = ROUTING_SCHEMA_VERSION
        if decision is not None:
            selected = str(decision["selected_engine"])
            result["selection"] = _auto_selection(
                original,
                resolved=selected,
                reason=str(decision["reason"]),
            )
            result["auto_decision"] = decision
        return result
