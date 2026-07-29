from __future__ import annotations

from typing import Any

from .engine_selector import EngineSelectionError
from .live_config_discovery import discover_live_config_wire
from .read_only_router import ReadOnlyCommandRouter
from .unified_config import ConfigError

ROUTING_PHASE = "R15"
ROUTING_SCHEMA_VERSION = 4
LIVE_CONFIG_PROFILE = "live-config-discovery-v1"
LIVE_CONFIG_COMMANDS = frozenset({"config.resolve", "status"})


def _upgrade_error(exc: EngineSelectionError) -> EngineSelectionError:
    details = dict(exc.details)
    details["phase"] = ROUTING_PHASE
    details["schema_version"] = ROUTING_SCHEMA_VERSION
    return EngineSelectionError(exc.code, exc.message, **details)


class ReadOnlyCommandRouterR15(ReadOnlyCommandRouter):
    """R15 read-only router with Python-owned live config discovery."""

    def route(
        self,
        command: str,
        *,
        cli_override: str | None = None,
        config_wire_hex: str | None = None,
        live_config: bool = False,
    ) -> dict[str, Any]:
        normalized = str(command).strip().casefold()
        if live_config and config_wire_hex is not None:
            raise EngineSelectionError(
                "ENGINE_ROUTE_INPUT_CONFLICT_R15",
                "Live configuration discovery and explicit configuration input are mutually exclusive",
                phase=ROUTING_PHASE,
                schema_version=ROUTING_SCHEMA_VERSION,
                command=normalized or "<missing>",
                accepted_input_profiles=[LIVE_CONFIG_PROFILE, "explicit-config-wire-v1"],
                fallback_policy="none",
                fallback_attempted=False,
            )
        if live_config and normalized not in LIVE_CONFIG_COMMANDS:
            raise EngineSelectionError(
                "ENGINE_ROUTE_LIVE_CONFIG_UNSUPPORTED_R15",
                "The selected route does not accept live configuration discovery",
                phase=ROUTING_PHASE,
                schema_version=ROUTING_SCHEMA_VERSION,
                command=normalized or "<missing>",
                supported=sorted(LIVE_CONFIG_COMMANDS),
                fallback_policy="none",
                fallback_attempted=False,
            )
        effective_wire = config_wire_hex
        if live_config:
            try:
                wire = discover_live_config_wire(
                    project_root=self.selector.project_root,
                    env=self.selector.env,
                )
            except ConfigError as exc:
                raise EngineSelectionError(
                    "ENGINE_ROUTE_LIVE_CONFIG_INVALID_R15",
                    "Live configuration discovery failed before engine selection",
                    phase=ROUTING_PHASE,
                    schema_version=ROUTING_SCHEMA_VERSION,
                    command=normalized,
                    input_profile=LIVE_CONFIG_PROFILE,
                    reason=str(exc),
                    fallback_policy="none",
                    fallback_attempted=False,
                ) from exc
            effective_wire = wire.hex()
        try:
            result = super().route(
                normalized,
                cli_override=cli_override,
                config_wire_hex=effective_wire,
            )
        except EngineSelectionError as exc:
            raise _upgrade_error(exc) from exc
        result["phase"] = ROUTING_PHASE
        result["schema_version"] = ROUTING_SCHEMA_VERSION
        if live_config:
            result["input"] = dict(result["input"])
            result["input"]["profile"] = LIVE_CONFIG_PROFILE
        return result
