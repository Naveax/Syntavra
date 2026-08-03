#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ast
import json
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "syntavra_runtime"

_ADD_PARSER_ASSIGN = re.compile(
    r"(?P<target>[A-Za-z_]\w*)\s*=\s*(?P<owner>[A-Za-z_]\w*)\.add_parser\(\s*[\"'](?P<name>[^\"']+)[\"']"
)
_ADD_PARSER_DIRECT = re.compile(
    r"(?P<owner>[A-Za-z_]\w*)\.add_parser\(\s*[\"'](?P<name>[^\"']+)[\"']"
)
_ADD_SUBPARSERS = re.compile(
    r"(?P<target>[A-Za-z_]\w*)\s*=\s*(?P<owner>[A-Za-z_]\w*)\.add_subparsers\("
)
_ARGUMENT = re.compile(r"\.add_argument\(\s*[\"'](?P<name>[^\"']+)[\"']")
_ENV = re.compile(r"\bSYNTAVRA_[A-Z0-9_]+\b")
_MCP_NAME = re.compile(r"[\"']name[\"']\s*:\s*[\"']([A-Za-z0-9_.:-]+)[\"']")
_ROUTE_INVENTORY_NAME = "INSTALLED_READ_ONLY_ROUTE_COMMANDS"

# Public command certification follows the parsers and explicit dispatch branches
# reachable from the installed ``syntavra`` entry point. Source scanning remains a
# diagnostic only; it is not allowed to create contractual command paths.
_MANUAL_DISPATCH_CLI_COMMANDS: tuple[str, ...] = (
    "evidence gc",
    "evidence rotate-key",
    "evidence stats",
)


def _python_files() -> list[Path]:
    return sorted(path for path in RUNTIME.rglob("*.py") if path.is_file())


def _module_name(path: Path) -> str:
    relative = path.relative_to(ROOT).with_suffix("")
    return ".".join(relative.parts)


def _subparser_actions(parser: argparse.ArgumentParser) -> tuple[argparse._SubParsersAction, ...]:
    return tuple(
        action
        for action in parser._actions
        if isinstance(action, argparse._SubParsersAction)
    )


def _walk_parser(
    parser: argparse.ArgumentParser,
    prefix: tuple[str, ...] = (),
) -> set[str]:
    commands: set[str] = set()
    for action in _subparser_actions(parser):
        for name, child in action.choices.items():
            path = (*prefix, str(name))
            child_subparsers = _subparser_actions(child)
            if not child_subparsers or not any(
                bool(getattr(child_action, "required", False))
                for child_action in child_subparsers
            ):
                commands.add(" ".join(path))
            commands.update(_walk_parser(child, path))
    return commands


def _installed_cli_commands() -> set[str]:
    previous_bootstrap = os.environ.get("SYNTAVRA_PORTABLE_BOOTSTRAP")
    os.environ["SYNTAVRA_PORTABLE_BOOTSTRAP"] = "1"
    try:
        from syntavra_runtime import (
            cli,
            engine_cli,
            external_benchmark_cli,
            prerelease_cli,
            unified_cli,
        )

        parsers = (
            engine_cli.build_parser(),
            unified_cli._core_parser(),
            prerelease_cli._parser(),
            external_benchmark_cli.parser(),
            cli.build_parser(),
        )
        commands = set(_MANUAL_DISPATCH_CLI_COMMANDS)
        for parser in parsers:
            commands.update(_walk_parser(parser))
        return commands
    finally:
        if previous_bootstrap is None:
            os.environ.pop("SYNTAVRA_PORTABLE_BOOTSTRAP", None)
        else:
            os.environ["SYNTAVRA_PORTABLE_BOOTSTRAP"] = previous_bootstrap


def _literal_strings(tree: ast.AST) -> set[str]:
    values: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            values.add(node.value)
    return values


def _named_string_sequence(tree: ast.AST, name: str) -> tuple[str, ...]:
    matches: list[tuple[str, ...]] = []
    for node in getattr(tree, "body", []):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if not any(isinstance(target, ast.Name) and target.id == name for target in targets):
            continue
        value = node.value
        if not isinstance(value, (ast.Tuple, ast.List)):
            raise RuntimeError(f"{name} must be a literal tuple or list")
        rows: list[str] = []
        for element in value.elts:
            if not isinstance(element, ast.Constant) or not isinstance(element.value, str):
                raise RuntimeError(f"{name} must contain only literal strings")
            rows.append(element.value)
        matches.append(tuple(rows))
    if len(matches) > 1:
        raise RuntimeError(f"duplicate {name} definitions")
    return matches[0] if matches else ()


def _extract_cli_paths(source: str) -> set[str]:
    parser_paths: dict[str, tuple[str, ...]] = {"parser": ()}
    subparser_paths: dict[str, tuple[str, ...]] = {}
    commands: set[str] = set()

    for raw_line in source.splitlines():
        for statement in raw_line.split(";"):
            line = statement.strip()
            sub_match = _ADD_SUBPARSERS.search(line)
            if sub_match:
                owner = sub_match.group("owner")
                subparser_paths[sub_match.group("target")] = parser_paths.get(owner, ())

            assign_match = _ADD_PARSER_ASSIGN.search(line)
            if assign_match:
                owner = assign_match.group("owner")
                name = assign_match.group("name")
                path = (*subparser_paths.get(owner, ()), name)
                parser_paths[assign_match.group("target")] = path
                commands.add(" ".join(path))
                continue

            direct_match = _ADD_PARSER_DIRECT.search(line)
            if direct_match:
                owner = direct_match.group("owner")
                name = direct_match.group("name")
                path = (*subparser_paths.get(owner, ()), name)
                commands.add(" ".join(path))

    return commands


def export_surface() -> dict[str, object]:
    modules: list[str] = []
    cli_commands: set[str] = _installed_cli_commands()
    source_cli_commands: set[str] = set()
    cli_arguments: set[str] = set()
    environment_variables: set[str] = set()
    mcp_name_literals: set[str] = set()
    parse_failures: list[str] = []
    route_inventory_definitions: list[str] = []

    for path in _python_files():
        modules.append(_module_name(path))
        source = path.read_text(encoding="utf-8")
        source_cli_commands.update(_extract_cli_paths(source))
        cli_arguments.update(match.group("name") for match in _ARGUMENT.finditer(source))
        environment_variables.update(_ENV.findall(source))
        if "mcp" in path.as_posix().lower():
            mcp_name_literals.update(_MCP_NAME.findall(source))
        try:
            tree = ast.parse(source, filename=str(path))
        except SyntaxError:
            parse_failures.append(path.relative_to(ROOT).as_posix())
            continue
        route_commands = _named_string_sequence(tree, _ROUTE_INVENTORY_NAME)
        if route_commands:
            route_inventory_definitions.append(path.relative_to(ROOT).as_posix())
            cli_commands.update(f"engine route {command}" for command in route_commands)
        for value in _literal_strings(tree):
            if value.startswith("SYNTAVRA_") and re.fullmatch(r"SYNTAVRA_[A-Z0-9_]+", value):
                environment_variables.add(value)

    if len(route_inventory_definitions) != 1:
        raise RuntimeError(
            "expected exactly one installed route inventory definition, "
            f"got {route_inventory_definitions!r}"
        )

    return {
        "schema_version": 1,
        "engine": "python",
        "source_root": "syntavra_runtime",
        "module_count": len(modules),
        "modules": modules,
        "cli_commands": sorted(cli_commands),
        "source_cli_commands": sorted(source_cli_commands),
        "cli_arguments": sorted(cli_arguments),
        "environment_variables": sorted(environment_variables),
        "mcp_name_literals": sorted(mcp_name_literals),
        "parse_failures": sorted(parse_failures),
        "route_inventory_definitions": route_inventory_definitions,
        "authoritative": {
            "modules": True,
            "cli_commands": True,
            "source_cli_commands": False,
            "cli_arguments": False,
            "environment_variables": False,
            "mcp_name_literals": False,
            "installed_engine_routes": True,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Export the deterministic Python public-surface inventory.")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    rendered = json.dumps(export_surface(), indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8", newline="\n")
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
