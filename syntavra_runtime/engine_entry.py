from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from .telemetry_metrics_router_r24 import TelemetryMetricsRouterR24
from .engine_cli import main as engine_main
from .engine_selector import ENGINE_MODES, EngineSelectionError, EngineSelector
from .model_gateway import GatewayError
from .runtime_paths import discover_project_root, resolve_state_root
from .sandbox import SandboxError

SELECTOR_COMMANDS = frozenset({"engine"})
CODEX_BRIDGE_COMMAND = "codex-mcp-bridge"
READ_ONLY_COMMANDS = {
    ("config", "show"): "config.show",
    ("config", "validate"): "config.validate",
    ("pipeline", "describe"): "pipeline.describe",
    ("plugins", "list"): "plugins.list",
}


def _configure_utf8_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8")


def _emit(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, default=str))


def _extract_engine_override(argv: list[str]) -> tuple[str | None, list[str]]:
    override: str | None = None
    forwarded: list[str] = []
    index = 0
    while index < len(argv):
        value = argv[index]
        if value == "--engine":
            if override is not None:
                raise EngineSelectionError(
                    "ENGINE_OVERRIDE_DUPLICATE",
                    "--engine may be provided only once",
                )
            if index + 1 >= len(argv):
                raise EngineSelectionError(
                    "ENGINE_OVERRIDE_MISSING_VALUE",
                    "--engine requires auto, python or rust",
                    allowed=list(ENGINE_MODES),
                )
            override = argv[index + 1]
            index += 2
            continue
        if value.startswith("--engine="):
            if override is not None:
                raise EngineSelectionError(
                    "ENGINE_OVERRIDE_DUPLICATE",
                    "--engine may be provided only once",
                )
            override = value.split("=", 1)[1]
            index += 1
            continue
        forwarded.append(value)
        index += 1
    return override, forwarded


def _without_context_flags(argv: list[str]) -> list[str]:
    """Remove global project/state flags before inserting one canonical pair."""

    forwarded: list[str] = []
    index = 0
    while index < len(argv):
        value = argv[index]
        if value in {"--project", "--state-root"}:
            if index + 1 >= len(argv):
                raise EngineSelectionError(
                    "CONTEXT_OPTION_MISSING_VALUE",
                    f"{value} requires a value",
                )
            index += 2
            continue
        if value.startswith("--project=") or value.startswith("--state-root="):
            index += 1
            continue
        forwarded.append(value)
        index += 1
    return forwarded


def _context(argv: list[str]) -> tuple[Path, Path, list[str], list[str]]:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--project", default="auto")
    parser.add_argument("--state-root")
    parser.add_argument("--skill-root")
    parser.add_argument("--codex-home")
    parser.add_argument("--host", default="codex")
    parser.add_argument("--json", action="store_true")
    values, _ = parser.parse_known_args(argv)
    project = discover_project_root(values.project, strict=False)
    state = resolve_state_root(project, values.state_root, namespace="pre-release")
    canonical = [
        "--project",
        str(project),
        "--state-root",
        str(state),
        *_without_context_flags(argv),
    ]
    _, rest = parser.parse_known_args(canonical)
    return project, state, rest, canonical


def _find_command(rest: list[str]) -> str:
    for value in rest:
        if not value.startswith("-"):
            return value
    return ""


def _read_only_request(rest: list[str]) -> tuple[str, dict[str, Any]] | None:
    route = READ_ONLY_COMMANDS.get(tuple(rest))
    if route is not None:
        return route, {}
    if len(rest) == 3 and rest[0] == "config" and rest[1] == "explain":
        return "config.explain", {"explain_path": rest[2]}
    if len(rest) == 3 and rest[:2] == ["migrate", "plan"]:
        return "migration.plan", {"migration_database": rest[2]}
    if rest == ["telemetry", "metrics"]:
        return "telemetry.metrics", {}
    if rest == ["telemetry", "metrics", "--prometheus"]:
        return "telemetry.metrics", {"telemetry_prometheus": True}
    if rest == ["scheduler", "stats"]:
        return "scheduler.stats", {}
    if len(rest) >= 2 and rest[:2] == ["scheduler", "list"]:
        states: list[str] = []
        limit = 100
        index = 2
        while index < len(rest):
            value = rest[index]
            if value == "--state":
                if index + 1 >= len(rest):
                    raise EngineSelectionError(
                        "SCHEDULER_READ_ONLY_STATE_MISSING_R24",
                        "--state requires a scheduler state",
                    )
                states.append(rest[index + 1])
                index += 2
                continue
            if value.startswith("--state="):
                states.append(value.split("=", 1)[1])
                index += 1
                continue
            if value == "--limit":
                if index + 1 >= len(rest):
                    raise EngineSelectionError(
                        "SCHEDULER_READ_ONLY_LIMIT_MISSING_R24",
                        "--limit requires an integer",
                    )
                raw_limit = rest[index + 1]
                index += 2
            elif value.startswith("--limit="):
                raw_limit = value.split("=", 1)[1]
                index += 1
            else:
                return None
            try:
                limit = int(raw_limit)
            except ValueError as exc:
                raise EngineSelectionError(
                    "SCHEDULER_READ_ONLY_LIMIT_INVALID_R24",
                    "--limit requires an integer",
                ) from exc
        return "scheduler.list", {
            "scheduler_states": tuple(states),
            "scheduler_limit": limit,
        }
    return None


def main(argv: list[str] | None = None) -> int:
    _configure_utf8_stdio()
    raw = list(sys.argv[1:] if argv is None else argv)

    # This route intentionally runs before project/state canonicalization. A global
    # Codex MCP process must start unbound and receive repository identity through
    # syntavra.project.bind; project-scope installs may still auto-bind via the
    # SYNTAVRA_PROJECT value placed in their local Codex configuration.
    if raw == [CODEX_BRIDGE_COMMAND]:
        from .codex_mcp_bridge import main as codex_bridge_main

        return int(codex_bridge_main())

    try:
        override, forwarded = _extract_engine_override(raw)
        project, state, rest, values = _context(forwarded)
        # Child Syntavra commands inherit the same project/state identity even when
        # a host launches them without repeating the global flags.
        os.environ["SYNTAVRA_PROJECT"] = str(project)
        os.environ["SYNTAVRA_STATE_ROOT"] = str(state)
        command = _find_command(rest)
        selector = EngineSelector(project_root=project, state_root=state)
        request = _read_only_request(rest)
        if request is not None:
            route, route_kwargs = request
            router = TelemetryMetricsRouterR24(selector, project_input_root=project)
            routed = router.route(
                route,
                cli_override=override,
                **route_kwargs,
            )
            result = routed["result"]
            if route == "telemetry.metrics":
                if result["format"] == "prometheus":
                    print(result["text"])
                else:
                    _emit(result["metrics"])
            else:
                _emit(result)
            return 0
        if command in SELECTOR_COMMANDS:
            return int(engine_main(values, selector=selector, cli_override=override))
        if command or not any(value in {"-h", "--help"} for value in values):
            selector.gate_general_command(command or "<missing>", cli_override=override)
    except EngineSelectionError as exc:
        _emit(exc.to_dict())
        return 4

    from .unified_cli import main as python_main

    try:
        return int(python_main(values))
    except (ValueError, KeyError, GatewayError, FileNotFoundError, PermissionError, SandboxError) as exc:
        _emit(
            {
                "ok": False,
                "error": {
                    "code": "PYTHON_PUBLIC_COMMAND_FAILED",
                    "message": "The selected Python engine failed while executing the public command.",
                    "details": {
                        "command": command or "<missing>",
                        "error": f"{type(exc).__name__}: {exc}",
                        "fallback": "forbidden",
                    },
                },
            }
        )
        return 4


if __name__ == "__main__":
    raise SystemExit(main())
