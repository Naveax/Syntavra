#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "contracts" / "engine" / "dual-engine-public-surface-v2.json"
CRATES = ROOT / "crates"


def _subparsers(parser: argparse.ArgumentParser) -> list[argparse._SubParsersAction]:
    return [
        action
        for action in parser._actions
        if isinstance(action, argparse._SubParsersAction)
    ]


def _parser_paths(
    parser: argparse.ArgumentParser,
    *,
    prefix: tuple[str, ...] = (),
    skip_top_level: frozenset[str] = frozenset(),
) -> set[str]:
    """Return executable argparse command paths, not positional-value variants."""

    actions = _subparsers(parser)
    if not actions:
        return {" ".join(prefix)} if prefix else set()

    result: set[str] = set()
    if prefix and not any(action.required for action in actions):
        result.add(" ".join(prefix))

    for action in actions:
        seen: set[int] = set()
        for name, child in action.choices.items():
            # argparse aliases can point at one parser object. Keep the canonical
            # spelling once rather than inflating the public surface with aliases.
            identity = id(child)
            if identity in seen:
                continue
            seen.add(identity)
            if not prefix and name in skip_top_level:
                continue
            result.update(_parser_paths(child, prefix=(*prefix, name)))
    return result


def _digest(paths: Iterable[str]) -> str:
    payload = json.dumps(
        sorted(set(paths)),
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def python_public_parser_surfaces() -> list[
    tuple[str, argparse.ArgumentParser, frozenset[str]]
]:
    """Build every argparse tree that contributes to the canonical Python surface."""

    from syntavra_runtime import (
        cli,
        engine_cli,
        external_benchmark_cli,
        prerelease_cli,
        unified_cli,
    )

    # unified_cli dispatches these top-level commands away from legacy cli.py.
    shadowed = frozenset(prerelease_cli.PRE_RELEASE_COMMANDS | unified_cli.CORE_COMMANDS)
    return [
        ("prerelease", prerelease_cli._parser(), frozenset()),
        ("core", unified_cli._core_parser(), frozenset()),
        ("legacy", cli.build_parser(), shadowed),
        ("engine", engine_cli.build_parser(), frozenset()),
        ("external-benchmark", external_benchmark_cli.parser(), frozenset()),
    ]


def python_public_route_sources() -> dict[str, list[str]]:
    """Return the full public route manifest and the parser/direct source of each route."""

    from syntavra_runtime import engine_cli

    by_route: dict[str, set[str]] = {}
    for source, parser, skip_top_level in python_public_parser_surfaces():
        for route in _parser_paths(parser, skip_top_level=skip_top_level):
            by_route.setdefault(route, set()).add(source)

    for route in engine_cli.INSTALLED_READ_ONLY_ROUTE_COMMANDS:
        by_route.setdefault(f"engine route {route}", set()).add(
            "engine-installed-read-only"
        )

    # unified_cli owns these evidence actions directly before falling back to the
    # legacy parser; they are public routes even though legacy argparse only owns
    # get/describe.
    for route in ("evidence stats", "evidence gc", "evidence rotate-key"):
        by_route.setdefault(route, set()).add("unified-direct")

    return {
        route: sorted(sources)
        for route, sources in sorted(by_route.items())
    }


def python_public_routes() -> set[str]:
    return set(python_public_route_sources())


def _action_name(action: argparse.Action) -> str:
    if action.option_strings:
        return "/".join(action.option_strings)
    metavar = action.metavar
    if isinstance(metavar, tuple):
        rendered = "/".join(str(item) for item in metavar)
    elif metavar is not None:
        rendered = str(metavar)
    else:
        rendered = str(action.dest)
    return rendered


def _namespace_dest_collisions(
    parser: argparse.ArgumentParser,
    *,
    source: str,
    prefix: tuple[str, ...] = (),
    skip_top_level: frozenset[str] = frozenset(),
    inherited: tuple[tuple[str, str, str], ...] = (),
) -> list[dict[str, str]]:
    """Find argparse namespace destinations reused across a parser ancestry chain.

    argparse parses parent and child actions into one Namespace. Reusing an ancestor
    destination in a descendant silently overwrites the ancestor value. That is how
    the historical top-level ``command`` / headless positional ``command`` collision
    bypassed dispatch. User-facing metavar text is intentionally ignored; only the
    actual Action.dest participates in this audit.
    """

    collisions: list[dict[str, str]] = []
    inherited_by_dest: dict[str, tuple[str, str]] = {
        dest: (owner_path, owner_action)
        for dest, owner_path, owner_action in inherited
    }

    local: list[tuple[str, str, str]] = []
    for action in parser._actions:
        dest = str(action.dest)
        if dest == argparse.SUPPRESS or isinstance(action, argparse._HelpAction):
            continue
        action_name = _action_name(action)
        route_path = " ".join(prefix) if prefix else "<root>"
        if dest in inherited_by_dest:
            ancestor_path, ancestor_action = inherited_by_dest[dest]
            collisions.append(
                {
                    "source": source,
                    "route": route_path,
                    "dest": dest,
                    "ancestor_route": ancestor_path,
                    "ancestor_action": ancestor_action,
                    "descendant_action": action_name,
                }
            )
        local.append((dest, route_path, action_name))

    lineage = (*inherited, *local)
    for subparser_action in _subparsers(parser):
        seen: set[int] = set()
        for name, child in subparser_action.choices.items():
            identity = id(child)
            if identity in seen:
                continue
            seen.add(identity)
            if not prefix and name in skip_top_level:
                continue
            collisions.extend(
                _namespace_dest_collisions(
                    child,
                    source=source,
                    prefix=(*prefix, name),
                    skip_top_level=skip_top_level,
                    inherited=lineage,
                )
            )
    return collisions


def python_public_namespace_collisions() -> list[dict[str, str]]:
    collisions: list[dict[str, str]] = []
    for source, parser, skip_top_level in python_public_parser_surfaces():
        collisions.extend(
            _namespace_dest_collisions(
                parser,
                source=source,
                skip_top_level=skip_top_level,
            )
        )

    unique = {
        (
            item["source"],
            item["route"],
            item["dest"],
            item["ancestor_route"],
            item["ancestor_action"],
            item["descendant_action"],
        ): item
        for item in collisions
    }
    return [
        unique[key]
        for key in sorted(unique)
    ]


def report() -> dict[str, object]:
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    expected = contract["python_surface"]
    rust = contract["rust_surface"]
    route_sources = python_public_route_sources()
    python_routes = set(route_sources)
    native = set(rust["native_public_commands"])
    missing = sorted(python_routes - native)
    extra_native = sorted(native - python_routes)
    digest = _digest(python_routes)
    duplicate_routes = {
        route: sources
        for route, sources in route_sources.items()
        if len(sources) > 1
    }
    namespace_collisions = python_public_namespace_collisions()

    expected_count = int(expected["public_command_count"])
    expected_digest = str(expected["command_paths_sha256"])
    expected_native = int(rust["native_public_command_count"])
    expected_missing = int(rust["missing_native_public_command_count"])
    expected_modules = int(rust["module_count"])
    expected_coverage = int(rust["native_coverage_ppm"])
    derived_modules = sum(1 for path in CRATES.rglob("*.rs") if path.is_file())
    derived_coverage = (
        len(native) * 1_000_000 // len(python_routes)
        if python_routes
        else 0
    )
    inventory_matches = (
        len(python_routes) == expected_count
        and digest == expected_digest
        and len(native) == expected_native
        and len(missing) == expected_missing
        and derived_modules == expected_modules
        and derived_coverage == expected_coverage
        and not extra_native
        and not duplicate_routes
        and not namespace_collisions
    )

    by_top_level: dict[str, list[str]] = {}
    for route in missing:
        by_top_level.setdefault(route.split(" ", 1)[0], []).append(route)

    return {
        "ok": inventory_matches,
        "contract_claim": contract["claim"],
        "python": {
            "derived_count": len(python_routes),
            "expected_count": expected_count,
            "derived_sha256": digest,
            "expected_sha256": expected_digest,
            "manifest": [
                {"route": route, "sources": route_sources[route]}
                for route in sorted(route_sources)
            ],
            "duplicate_route_count": len(duplicate_routes),
            "duplicate_routes": duplicate_routes,
            "namespace_collision_count": len(namespace_collisions),
            "namespace_collisions": namespace_collisions,
        },
        "rust": {
            "native_count": len(native),
            "expected_native_count": expected_native,
            "missing_count": len(missing),
            "expected_missing_count": expected_missing,
            "module_count": derived_modules,
            "expected_module_count": expected_modules,
            "native_coverage_ppm": derived_coverage,
            "expected_native_coverage_ppm": expected_coverage,
            "extra_native_routes": extra_native,
        },
        "missing_routes": missing,
        "missing_by_top_level": dict(sorted(by_top_level.items())),
    }


def main() -> int:
    value = report()
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if value["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
