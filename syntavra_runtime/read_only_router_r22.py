from __future__ import annotations

import platform
import sys
from pathlib import Path
from typing import Any, Callable

from .engine_selector import (
    EngineSelection,
    EngineSelectionError,
    EngineSelector,
    EngineVerification,
)
from .read_only_router import RustRouteRunner
from .read_only_router_r21 import ReadOnlyCommandRouterR21

ROUTING_PHASE = "R22"
ROUTING_SCHEMA_VERSION = 11
AUTO_POLICY = "route-scoped-capability-aware-r22"
AUTO_ROUTE_CAPABILITIES = {
    "config.resolve": "config.resolve",
    "receipt.inspect": "receipt.inspect",
    "state.broker-live-snapshot": "state.broker-live-snapshot",
    "state.broker-snapshot": "state.broker-snapshot",
    "state.inspect": "state.inspect",
    "state.layout": "state.layout",
    "status": "status",
    "version": "version",
}
SUPPORTED_PLATFORM_PAIRS = frozenset(
    {
        ("linux", "x86_64"),
        ("macos", "aarch64"),
        ("macos", "x86_64"),
        ("windows", "x86_64"),
    }
)

PlatformProbe = Callable[[], tuple[str, str]]


def _default_platform_probe() -> tuple[str, str]:
    return sys.platform, platform.machine()


def _normalized_platform(system_name: str, machine_name: str) -> tuple[str, str]:
    system = str(system_name).strip().casefold()
    machine = str(machine_name).strip().casefold()
    system_aliases = {
        "darwin": "macos",
        "linux": "linux",
        "win32": "windows",
        "windows": "windows",
    }
    machine_aliases = {
        "amd64": "x86_64",
        "arm64": "aarch64",
        "aarch64": "aarch64",
        "x86_64": "x86_64",
    }
    return system_aliases.get(system, system), machine_aliases.get(machine, machine)


def _auto_selection(
    source: EngineSelection,
    *,
    resolved: str,
    reason: str,
) -> EngineSelection:
    return EngineSelection(
        requested="auto",
        resolved=resolved,
        source=source.source,
        scope=source.scope,
        source_path=source.source_path,
        reason=reason,
        auto_policy=AUTO_POLICY,
        fallback_policy="no-fallback-after-selection",
    )


class _RouteScopedSelector:
    """Resolve ``auto`` lazily at the route's existing post-preflight boundary."""

    def __init__(
        self,
        base: EngineSelector,
        *,
        command: str,
        capability: str | None,
        platform_probe: PlatformProbe,
    ) -> None:
        self._base = base
        self._command = command
        self._capability = capability
        self._platform_probe = platform_probe
        self._selection: EngineSelection | None = None
        self._verification: EngineVerification | None = None

    def __getattr__(self, name: str) -> Any:
        return getattr(self._base, name)

    def resolve(self, *, cli_override: str | None = None) -> EngineSelection:
        if self._selection is not None:
            return self._selection

        original = self._base.resolve(cli_override=cli_override)
        if original.requested != "auto":
            self._selection = original
            return original

        if self._capability is None:
            self._selection = _auto_selection(
                original,
                resolved="python",
                reason="AUTO_ROUTE_NOT_ADMITTED_R22",
            )
            return self._selection

        try:
            platform_pair = _normalized_platform(*self._platform_probe())
        except Exception:
            self._selection = _auto_selection(
                original,
                resolved="python",
                reason="AUTO_ROUTE_PLATFORM_PROBE_FAILED_R22",
            )
            return self._selection
        if platform_pair not in SUPPORTED_PLATFORM_PAIRS:
            self._selection = _auto_selection(
                original,
                resolved="python",
                reason="AUTO_ROUTE_PLATFORM_UNSUPPORTED_R22",
            )
            return self._selection

        try:
            verification = self._base.verify_rust()
        except Exception:
            self._selection = _auto_selection(
                original,
                resolved="python",
                reason="AUTO_ROUTE_VERIFICATION_FAILED_R22",
            )
            return self._selection

        self._verification = verification
        if not verification.available:
            reason = "AUTO_ROUTE_RUST_UNAVAILABLE_R22"
        elif not verification.compatible:
            reason = "AUTO_ROUTE_CONTRACT_INCOMPATIBLE_R22"
        elif self._capability not in verification.capabilities:
            reason = "AUTO_ROUTE_CAPABILITY_MISSING_R22"
        elif self._base.discover_rust_binary() is None:
            reason = "AUTO_ROUTE_RUST_BINARY_MISSING_R22"
        else:
            self._selection = _auto_selection(
                original,
                resolved="rust",
                reason="AUTO_ROUTE_RUST_SELECTED_R22",
            )
            return self._selection

        self._selection = _auto_selection(
            original,
            resolved="python",
            reason=reason,
        )
        return self._selection

    def verify_rust(self) -> EngineVerification:
        if self._verification is not None:
            return self._verification
        return self._base.verify_rust()


class ReadOnlyCommandRouterR22(ReadOnlyCommandRouterR21):
    """R22 route-scoped capability-aware ``auto`` selection.

    Explicit Python and Rust selections preserve the R21 behavior. ``auto`` may
    select Rust only after the delegated route has completed its existing
    Python preflight and only when platform, binary, contract and capability
    checks all pass. Candidate execution failures remain fail-closed.
    """

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
        )
        self.platform_probe = platform_probe or _default_platform_probe

    @staticmethod
    def supported_commands() -> tuple[str, ...]:
        return tuple(sorted(AUTO_ROUTE_CAPABILITIES))

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
        scoped_selector = _RouteScopedSelector(
            self.selector,
            command=normalized,
            capability=AUTO_ROUTE_CAPABILITIES.get(normalized),
            platform_probe=self.platform_probe,
        )
        delegated = ReadOnlyCommandRouterR21(
            scoped_selector,  # type: ignore[arg-type]
            runner=self.runner,
            project_input_root=self.project_input_root,
        )
        try:
            result = delegated.route(
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
            details = dict(exc.details)
            details["phase"] = ROUTING_PHASE
            details["schema_version"] = ROUTING_SCHEMA_VERSION
            raise EngineSelectionError(exc.code, exc.message, **details) from exc

        result["phase"] = ROUTING_PHASE
        result["schema_version"] = ROUTING_SCHEMA_VERSION
        return result
