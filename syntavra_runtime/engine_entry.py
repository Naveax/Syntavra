from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .migration_plan_router_r24 import MigrationPlanRouterR24
from .engine_cli import main as engine_main
from .engine_selector import ENGINE_MODES, EngineSelectionError, EngineSelector

SELECTOR_COMMANDS = frozenset({"engine"})
READ_ONLY_COMMANDS = {
    ("config", "show"): "config.show",
    ("config", "validate"): "config.validate",
    ("pipeline", "describe"): "pipeline.describe",
    ("plugins", "list"): "plugins.list",
}


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


def _context(argv: list[str]) -> tuple[Path, Path, list[str]]:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--project", default=".")
    parser.add_argument("--state-root")
    parser.add_argument("--skill-root")
    parser.add_argument("--codex-home")
    parser.add_argument("--host", default="codex")
    parser.add_argument("--json", action="store_true")
    values, rest = parser.parse_known_args(argv)
    project = Path(values.project).resolve(strict=False)
    state = (
        Path(values.state_root).resolve(strict=False)
        if values.state_root
        else project / ".syntavra" / "pre-release"
    )
    return project, state, rest


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
    raw = list(sys.argv[1:] if argv is None else argv)
    try:
        override, values = _extract_engine_override(raw)
        project, state, rest = _context(values)
        command = _find_command(rest)
        selector = EngineSelector(project_root=project, state_root=state)
        request = _read_only_request(rest)
        if request is not None:
            route, route_kwargs = request
            router = MigrationPlanRouterR24(selector, project_input_root=project)
            routed = router.route(
                route,
                cli_override=override,
                **route_kwargs,
            )
            _emit(routed["result"])
            return 0
        if command in SELECTOR_COMMANDS:
            return int(engine_main(values, selector=selector, cli_override=override))
        if command or not any(value in {"-h", "--help"} for value in values):
            selector.gate_general_command(command or "<missing>", cli_override=override)
    except EngineSelectionError as exc:
        _emit(exc.to_dict())
        return 4

    from .unified_cli import main as python_main

    return int(python_main(values))


if __name__ == "__main__":
    raise SystemExit(main())
