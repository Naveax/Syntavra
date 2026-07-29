from __future__ import annotations

from typing import Any

from .engine_selector import EngineSelectionError
from .live_config_discovery import decode_override_json_hex, discover_live_config_wire
from .read_only_router import ReadOnlyCommandRouter
from .read_only_router_r15 import (
    LIVE_CONFIG_COMMANDS,
    LIVE_CONFIG_PROFILE,
    ReadOnlyCommandRouterR15,
)
from .unified_config import ConfigError

ROUTING_PHASE = "R16"
ROUTING_SCHEMA_VERSION = 5
LIVE_OVERRIDE_PROFILE = "live-config-session-task-v1"


def _upgrade_error(exc: EngineSelectionError) -> EngineSelectionError:
    details = dict(exc.details)
    details["phase"] = ROUTING_PHASE
    details["schema_version"] = ROUTING_SCHEMA_VERSION
    return EngineSelectionError(exc.code, exc.message, **details)


class ReadOnlyCommandRouterR16(ReadOnlyCommandRouterR15):
    """R16 router with bounded transient session and task overrides."""

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
        override_requested = (
            session_override_json_hex is not None or task_override_json_hex is not None
        )

        if override_requested and not live_config:
            raise EngineSelectionError(
                "ENGINE_ROUTE_OVERRIDE_REQUIRES_LIVE_CONFIG_R16",
                "Session and task overrides require live configuration discovery",
                phase=ROUTING_PHASE,
                schema_version=ROUTING_SCHEMA_VERSION,
                command=normalized or "<missing>",
                input_profile=LIVE_OVERRIDE_PROFILE,
                fallback_policy="none",
                fallback_attempted=False,
            )
        if override_requested and config_wire_hex is not None:
            raise EngineSelectionError(
                "ENGINE_ROUTE_INPUT_CONFLICT_R16",
                "Transient overrides cannot be combined with explicit configuration input",
                phase=ROUTING_PHASE,
                schema_version=ROUTING_SCHEMA_VERSION,
                command=normalized or "<missing>",
                accepted_input_profiles=[
                    "explicit-config-wire-v1",
                    LIVE_CONFIG_PROFILE,
                    LIVE_OVERRIDE_PROFILE,
                ],
                fallback_policy="none",
                fallback_attempted=False,
            )
        if override_requested and normalized not in LIVE_CONFIG_COMMANDS:
            raise EngineSelectionError(
                "ENGINE_ROUTE_OVERRIDE_UNSUPPORTED_R16",
                "The selected route does not accept transient configuration overrides",
                phase=ROUTING_PHASE,
                schema_version=ROUTING_SCHEMA_VERSION,
                command=normalized or "<missing>",
                supported=sorted(LIVE_CONFIG_COMMANDS),
                fallback_policy="none",
                fallback_attempted=False,
            )

        if not override_requested:
            try:
                result = super().route(
                    normalized,
                    cli_override=cli_override,
                    config_wire_hex=config_wire_hex,
                    live_config=live_config,
                )
            except EngineSelectionError as exc:
                raise _upgrade_error(exc) from exc
            result["phase"] = ROUTING_PHASE
            result["schema_version"] = ROUTING_SCHEMA_VERSION
            return result

        try:
            session = (
                decode_override_json_hex(
                    session_override_json_hex,
                    scope="session",
                )
                if session_override_json_hex is not None
                else {}
            )
            task = (
                decode_override_json_hex(
                    task_override_json_hex,
                    scope="task",
                )
                if task_override_json_hex is not None
                else {}
            )
            wire = discover_live_config_wire(
                project_root=self.selector.project_root,
                env=self.selector.env,
                session=session,
                task=task,
            )
        except ConfigError as exc:
            raise EngineSelectionError(
                "ENGINE_ROUTE_OVERRIDE_INVALID_R16",
                "Transient configuration override validation failed before engine selection",
                phase=ROUTING_PHASE,
                schema_version=ROUTING_SCHEMA_VERSION,
                command=normalized,
                input_profile=LIVE_OVERRIDE_PROFILE,
                reason=str(exc),
                fallback_policy="none",
                fallback_attempted=False,
            ) from exc

        try:
            result = ReadOnlyCommandRouter.route(
                self,
                normalized,
                cli_override=cli_override,
                config_wire_hex=wire.hex(),
            )
        except EngineSelectionError as exc:
            raise _upgrade_error(exc) from exc
        result["phase"] = ROUTING_PHASE
        result["schema_version"] = ROUTING_SCHEMA_VERSION
        result["input"] = dict(result["input"])
        result["input"]["profile"] = LIVE_OVERRIDE_PROFILE
        return result
