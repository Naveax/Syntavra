from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .engine_selector import ENGINE_MODES, EngineSelectionError, EngineSelector


def _emit(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, default=str))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="syntavra", description="Syntavra R4 engine selector")
    parser.add_argument("--project", default=".")
    parser.add_argument("--state-root")
    parser.add_argument("--skill-root")
    parser.add_argument("--codex-home")
    parser.add_argument("--host", default="codex")
    parser.add_argument("--json", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)
    engine = sub.add_parser("engine")
    actions = engine.add_subparsers(dest="action", required=True)
    actions.add_parser("list")
    actions.add_parser("status")
    use = actions.add_parser("use")
    use.add_argument("engine", choices=ENGINE_MODES)
    use.add_argument("--scope", choices=("project", "user"), default="project")
    verify = actions.add_parser("verify")
    verify.add_argument("--all", action="store_true", dest="all_engines")
    return parser


def main(
    argv: list[str] | None = None,
    *,
    selector: EngineSelector | None = None,
    cli_override: str | None = None,
) -> int:
    values = list(sys.argv[1:] if argv is None else argv)
    args = build_parser().parse_args(values)
    active = selector or EngineSelector(
        project_root=Path(args.project),
        state_root=Path(args.state_root) if args.state_root else None,
    )
    try:
        if args.action == "list":
            result = active.list_engines(cli_override=cli_override)
        elif args.action == "status":
            result = active.status(cli_override=cli_override)
        elif args.action == "use":
            result = active.use(args.engine, scope=args.scope)
            if cli_override is not None:
                result["command_override"] = active.resolve(cli_override=cli_override).to_dict()
        elif args.action == "verify":
            result = active.verify(cli_override=cli_override, all_engines=args.all_engines)
        else:  # pragma: no cover - argparse guarantees the action set
            raise RuntimeError(args.action)
    except EngineSelectionError as exc:
        _emit(exc.to_dict())
        return 4
    _emit(result)
    return 0 if result.get("ok", False) else 3
