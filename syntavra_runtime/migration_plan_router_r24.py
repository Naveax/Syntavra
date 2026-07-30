from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Iterable

from .engine_selector import EngineSelectionError
from .migration_plan_read_only_contract import (
    CAPABILITY,
    INPUT_FORMAT,
    INPUT_PROFILE,
    ROUTE,
    MigrationPlanReadOnlyError,
    canonical_request_bytes,
    migration_plan_read_only_result,
    rust_argv,
)
from .read_only_cli_contract import ROUTING_PHASE, ROUTING_SCHEMA_VERSION
from .read_only_router import MAX_RESPONSE_BYTES, _canonical_result_bytes, _result_digest
from .read_only_router_r22 import _RouteScopedSelector
from .scheduler_read_only_router_r24 import SchedulerReadOnlyRouterR24


class MigrationPlanRouterR24(SchedulerReadOnlyRouterR24):
    """R24 routing for project-bound quiescent migration.plan inspection."""

    @staticmethod
    def supported_commands() -> tuple[str, ...]:
        return tuple(sorted((*SchedulerReadOnlyRouterR24.supported_commands(), ROUTE)))

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
        migration_database: str | Path | None = None,
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
                scheduler_states=scheduler_states,
                scheduler_limit=scheduler_limit,
            )

        if migration_database is None:
            raise EngineSelectionError(
                "ENGINE_ROUTE_MIGRATION_DATABASE_REQUIRED_R24",
                "migration.plan requires one project-bound database path",
                phase=ROUTING_PHASE,
                schema_version=ROUTING_SCHEMA_VERSION,
                command=ROUTE,
                accepted_input_profiles=[INPUT_PROFILE],
                fallback_policy="none",
                fallback_attempted=False,
            )
        if explain_path is not None or scheduler_states is not None or scheduler_limit is not None or any(
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
                "ENGINE_ROUTE_MIGRATION_INPUT_UNSUPPORTED_R24",
                "migration.plan accepts only one project-bound database path",
                phase=ROUTING_PHASE,
                schema_version=ROUTING_SCHEMA_VERSION,
                command=ROUTE,
                accepted_input_profiles=[INPUT_PROFILE],
                fallback_policy="none",
                fallback_attempted=False,
            )

        try:
            expected = migration_plan_read_only_result(
                self.selector.project_root,
                migration_database,
            )
            request_bytes = canonical_request_bytes(
                self.selector.project_root,
                migration_database,
            )
            arguments = rust_argv(self.selector.project_root, migration_database)
        except MigrationPlanReadOnlyError as exc:
            raise EngineSelectionError(
                "ENGINE_ROUTE_MIGRATION_PLAN_PREFLIGHT_FAILED_R24",
                "Migration plan preflight failed before engine selection",
                phase=ROUTING_PHASE,
                schema_version=ROUTING_SCHEMA_VERSION,
                command=ROUTE,
                reason=str(exc),
                fallback_policy="none",
                fallback_attempted=False,
            ) from exc

        scoped_selector = _RouteScopedSelector(
            self.selector,
            command=ROUTE,
            capability=CAPABILITY,
            platform_probe=self.platform_probe,
        )
        selection = scoped_selector.resolve(cli_override=cli_override)

        if selection.resolved == "rust":
            verification = scoped_selector.verify_rust()
            if not verification.compatible or CAPABILITY not in verification.capabilities:
                raise EngineSelectionError(
                    "RUST_ENGINE_UNAVAILABLE_R24",
                    "Rust is selected but migration.plan is not verified",
                    phase=ROUTING_PHASE,
                    schema_version=ROUTING_SCHEMA_VERSION,
                    command=ROUTE,
                    capability=CAPABILITY,
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
                    capability=CAPABILITY,
                    selection=selection.to_dict(),
                    fallback_policy="none",
                    fallback_attempted=False,
                )
            try:
                candidate = dict(self.runner(binary, arguments))
            except EngineSelectionError:
                raise
            except Exception as exc:
                raise EngineSelectionError(
                    "RUST_ROUTE_EXECUTION_FAILED_R24",
                    "Rust migration.plan failed and was not re-executed in Python",
                    phase=ROUTING_PHASE,
                    schema_version=ROUTING_SCHEMA_VERSION,
                    command=ROUTE,
                    capability=CAPABILITY,
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
                    "RUST_MIGRATION_PLAN_PARITY_INVALID_R24",
                    "Rust migration.plan differs from the Python canonical result",
                    phase=ROUTING_PHASE,
                    schema_version=ROUTING_SCHEMA_VERSION,
                    command=ROUTE,
                    capability=CAPABILITY,
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
                "The migration plan result exceeded the response limit",
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
            "capability": CAPABILITY,
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


__all__ = ["MigrationPlanRouterR24"]
