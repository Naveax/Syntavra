from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .config_show_router_r24 import ConfigShowRouterR24
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


def _read_only_request(rest: list[str]) -> tuple[str, str | None] | None:
    route = READ_ONLY_COMMANDS.get(tuple(rest))
    if route is not None:
        return route, None
    if len(rest) == 3 and rest[0] == "config" and rest[1] == "explain":
        return "config.explain", rest[2]
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
            route, explain_path = request
            router = ConfigShowRouterR24(selector, project_input_root=project)
            routed = router.route(
                route,
                cli_override=override,
                explain_path=explain_path,
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
