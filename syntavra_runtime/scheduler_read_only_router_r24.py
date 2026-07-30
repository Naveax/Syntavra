from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

from .config_show_router_r24 import ConfigShowRouterR24
from .engine_selector import EngineSelectionError
from .read_only_cli_contract import ROUTING_PHASE, ROUTING_SCHEMA_VERSION
from .read_only_router import MAX_RESPONSE_BYTES, _canonical_result_bytes, _result_digest
from .read_only_router_r22 import _RouteScopedSelector
from .scheduler_read_only_contract import (
    CAPABILITIES,
    INPUT_FORMAT,
    INPUT_PROFILE,
    ROUTES,
    SchedulerReadOnlyError,
    canonical_limit,
    canonical_request_bytes,
    canonical_states,
    rust_argv,
    scheduler_read_only_result,
)


class SchedulerReadOnlyRouterR24(ConfigShowRouterR24):
    """R24 routing for state-free scheduler stats/list inspection."""

    @staticmethod
    def supported_commands() -> tuple[str, ...]:
        return tuple(sorted((*ConfigShowRouterR24.supported_commands(), *ROUTES)))

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
        scheduler_states: Iterable[str] | None = None,
        scheduler_limit: int | None = None,
    ) -> dict[str, Any]:
        normalized = str(command).strip().casefold()
        if normalized not in ROUTES:
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
                "ENGINE_ROUTE_SCHEDULER_INPUT_UNSUPPORTED_R24",
                "scheduler read-only routes use only the selected state root and bounded list filters",
                phase=ROUTING_PHASE,
                schema_version=ROUTING_SCHEMA_VERSION,
                command=normalized,
                accepted_input_profiles=[INPUT_PROFILE],
                fallback_policy="none",
                fallback_attempted=False,
            )

        states = tuple(scheduler_states or ())
        limit = 100 if scheduler_limit is None else scheduler_limit
        if normalized == "scheduler.stats" and (states or scheduler_limit is not None):
            raise EngineSelectionError(
                "ENGINE_ROUTE_SCHEDULER_STATS_FILTER_UNSUPPORTED_R24",
                "scheduler.stats accepts no state or limit filters",
                phase=ROUTING_PHASE,
                schema_version=ROUTING_SCHEMA_VERSION,
                command=normalized,
                fallback_policy="none",
                fallback_attempted=False,
            )

        try:
            selected_states = canonical_states(states)
            selected_limit = canonical_limit(limit)
            expected = scheduler_read_only_result(
                self.selector.state_root,
                normalized,
                states=selected_states,
                limit=selected_limit,
            )
            request_bytes = canonical_request_bytes(
                normalized,
                states=selected_states,
                limit=selected_limit,
            )
        except SchedulerReadOnlyError as exc:
            raise EngineSelectionError(
                "ENGINE_ROUTE_SCHEDULER_PREFLIGHT_FAILED_R24",
                "Scheduler read-only preflight failed before engine selection",
                phase=ROUTING_PHASE,
                schema_version=ROUTING_SCHEMA_VERSION,
                command=normalized,
                reason=str(exc),
                fallback_policy="none",
                fallback_attempted=False,
            ) from exc

        capability = CAPABILITIES[normalized]
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
                    "Rust is selected but the scheduler read-only capability is not verified",
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
                candidate = dict(
                    self.runner(
                        binary,
                        rust_argv(
                            normalized,
                            self.selector.state_root,
                            states=selected_states,
                            limit=selected_limit,
                        ),
                    )
                )
            except EngineSelectionError:
                raise
            except Exception as exc:
                raise EngineSelectionError(
                    "RUST_ROUTE_EXECUTION_FAILED_R24",
                    "Rust scheduler inspection failed and was not re-executed in Python",
                    phase=ROUTING_PHASE,
                    schema_version=ROUTING_SCHEMA_VERSION,
                    command=normalized,
                    capability=capability,
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
                    "RUST_SCHEDULER_READ_ONLY_PARITY_INVALID_R24",
                    "Rust scheduler inspection differs from the Python canonical result",
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
                "The scheduler read-only result exceeded the response limit",
                phase=ROUTING_PHASE,
                schema_version=ROUTING_SCHEMA_VERSION,
                command=normalized,
                response_bytes=response_bytes,
                maximum_response_bytes=MAX_RESPONSE_BYTES,
                fallback_policy="none",
                fallback_attempted=False,
            )

        import hashlib

        return {
            "ok": True,
            "phase": ROUTING_PHASE,
            "schema_version": ROUTING_SCHEMA_VERSION,
            "command": normalized,
            "capability": capability,
            "mutation": "read-only",
            "selection": selection.to_dict(),
            "input": {
                "profile": INPUT_PROFILE,
                "format": INPUT_FORMAT,
                "bytes": len(request_bytes),
                "sha256": hashlib.sha256(request_bytes).hexdigest(),
            },
            "fallback": {"policy": "none", "attempted": False},
            "result": expected,
        }


__all__ = ["SchedulerReadOnlyRouterR24"]
