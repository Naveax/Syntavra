#!/usr/bin/env python3
from __future__ import annotations

import argparse
import contextlib
import io
import json
from collections import defaultdict
from typing import Any

from tools import report_missing_native_public_routes as public_surface


SCHEMA_VERSION = 2
EXPECTED_PUBLIC_ROUTES = 245

# These are execution entrypoints, not an independent route authority. Route identity
# always comes from report_missing_native_public_routes.py.
SOURCE_ENTRYPOINTS = {
    "prerelease": "syntavra_runtime.prerelease_cli.main",
    "core": "syntavra_runtime.unified_cli._core_main",
    "legacy": "syntavra_runtime.cli.main",
    "engine": "syntavra_runtime.engine_cli.main",
    "external-benchmark": "syntavra_runtime.external_benchmark_cli.main",
    "engine-installed-read-only": "syntavra_runtime.engine_cli.main",
    "unified-direct": "syntavra_runtime.unified_cli.main",
}
PARSER_SOURCES = frozenset({"prerelease", "core", "legacy", "engine", "external-benchmark"})


def _exit_code(exc: SystemExit) -> int:
    value = exc.code
    if value is None:
        return 0
    if isinstance(value, int):
        return value
    return 1


def route_execution_manifest() -> list[dict[str, Any]]:
    manifest: list[dict[str, Any]] = []
    for route, sources in public_surface.python_public_route_sources().items():
        handlers = sorted(
            {
                SOURCE_ENTRYPOINTS[source]
                for source in sources
                if source in SOURCE_ENTRYPOINTS
            }
        )
        unknown_sources = sorted(source for source in sources if source not in SOURCE_ENTRYPOINTS)
        parser_owned = any(source in PARSER_SOURCES for source in sources)
        manifest.append(
            {
                "route": route,
                "sources": list(sources),
                "entrypoints": handlers,
                "entrypoint": handlers[0] if len(handlers) == 1 else None,
                "unknown_sources": unknown_sources,
                "parser_owned": parser_owned,
                # Global argparse contract. Family-specific domain errors and output
                # schemas are frozen later in Phase 1, but successful public CLI
                # execution remains zero throughout the Python reference surface.
                "success_exit": 0,
                "parser_error_exit": 2 if parser_owned else None,
            }
        )
    return manifest


def _alias_rows(
    parser: argparse.ArgumentParser,
    *,
    source: str,
    prefix: tuple[str, ...] = (),
    skip_top_level: frozenset[str] = frozenset(),
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for action in public_surface._subparsers(parser):
        groups: dict[int, list[tuple[str, argparse.ArgumentParser]]] = defaultdict(list)
        order: list[int] = []
        for name, child in action.choices.items():
            identity = id(child)
            if identity not in groups:
                order.append(identity)
            groups[identity].append((name, child))

        for identity in order:
            names_and_children = groups[identity]
            canonical, child = names_and_children[0]
            if not prefix and canonical in skip_top_level:
                continue
            canonical_path = " ".join((*prefix, canonical))
            aliases = [" ".join((*prefix, name)) for name, _ in names_and_children[1:]]
            if aliases:
                rows.append(
                    {
                        "source": source,
                        "canonical": canonical_path,
                        "aliases": aliases,
                    }
                )
            rows.extend(
                _alias_rows(
                    child,
                    source=source,
                    prefix=(*prefix, canonical),
                    skip_top_level=skip_top_level,
                )
            )
    return rows


def parser_alias_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for source, parser, skip_top_level in public_surface.python_public_parser_surfaces():
        rows.extend(
            _alias_rows(
                parser,
                source=source,
                skip_top_level=skip_top_level,
            )
        )
    return sorted(rows, key=lambda item: (item["source"], item["canonical"]))


def alias_conflicts() -> list[dict[str, str]]:
    canonical_routes = set(public_surface.python_public_route_sources())
    owners: dict[str, set[str]] = defaultdict(set)
    for row in parser_alias_rows():
        for alias in row["aliases"]:
            owners[alias].add(row["canonical"])

    conflicts: list[dict[str, str]] = []
    for alias in sorted(owners):
        canonical = sorted(owners[alias])
        if len(canonical) > 1:
            conflicts.append(
                {
                    "alias": alias,
                    "reason": "alias maps to multiple canonical paths",
                    "canonical": ", ".join(canonical),
                }
            )
        if alias in canonical_routes and alias not in owners[alias]:
            conflicts.append(
                {
                    "alias": alias,
                    "reason": "alias collides with a canonical public path",
                    "canonical": ", ".join(canonical),
                }
            )
    return conflicts


def _expected_shadow_source(route: str) -> str:
    """Return the unified-cli owner selected before the legacy fallback.

    Ownership is leaf-sensitive. In particular, ``prove`` is normally a
    prerelease command, but three proof actions are intercepted first and routed
    to external_benchmark_cli. The audit must model that real precedence rather
    than assigning one owner to an entire top-level command.
    """

    from syntavra_runtime import prerelease_cli, unified_cli

    parts = route.split(" ")
    command = parts[0] if parts else ""
    if command == "prove" and len(parts) > 1 and parts[1] in unified_cli.EXTERNAL_PROOF_ACTIONS:
        return "external-benchmark"
    if command in prerelease_cli.PRE_RELEASE_COMMANDS:
        return "prerelease"
    if command in unified_cli.CORE_COMMANDS:
        return "core"
    return ""


def shadow_audit() -> dict[str, Any]:
    canonical = public_surface.python_public_route_sources()
    surfaces = {
        source: (parser, skip_top_level)
        for source, parser, skip_top_level in public_surface.python_public_parser_surfaces()
    }
    legacy_parser, legacy_shadowed = surfaces["legacy"]
    legacy_raw = public_surface._parser_paths(legacy_parser, skip_top_level=frozenset())

    rows: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    for command in sorted(legacy_shadowed):
        legacy_routes = sorted(
            route for route in legacy_raw if route == command or route.startswith(command + " ")
        )
        canonical_routes = sorted(
            route for route in canonical if route == command or route.startswith(command + " ")
        )
        route_owners = [
            {
                "route": route,
                "expected_source": _expected_shadow_source(route),
                "sources": canonical[route],
            }
            for route in canonical_routes
        ]
        rows.append(
            {
                "command": command,
                # A unified override is valid even when the legacy parser has no
                # parser branch with the same name. The important invariant is
                # that every canonical leaf has one real pre-legacy owner and no
                # legacy leakage.
                "legacy_route_count": len(legacy_routes),
                "canonical_route_count": len(canonical_routes),
                "route_owners": route_owners,
            }
        )

        if not canonical_routes:
            failures.append({"command": command, "reason": "shadowed command has no canonical owner"})
            continue

        for route in canonical_routes:
            expected_source = _expected_shadow_source(route)
            sources = canonical[route]
            if not expected_source:
                failures.append(
                    {
                        "command": command,
                        "reason": f"{route!r} has no declared pre-legacy dispatch owner",
                    }
                )
                continue
            if expected_source not in sources:
                failures.append(
                    {
                        "command": command,
                        "reason": f"{route!r} is not owned by expected source {expected_source!r}",
                    }
                )
            if "legacy" in sources:
                failures.append(
                    {
                        "command": command,
                        "reason": f"{route!r} leaked through the shadowed legacy surface",
                    }
                )

    return {"rows": rows, "failures": failures}


def parser_help_reachability() -> dict[str, Any]:
    checked: list[dict[str, str]] = []
    failures: list[dict[str, str]] = []
    for source, parser, skip_top_level in public_surface.python_public_parser_surfaces():
        for route in sorted(public_surface._parser_paths(parser, skip_top_level=skip_top_level)):
            argv = [*route.split(" "), "--help"]
            stdout = io.StringIO()
            stderr = io.StringIO()
            try:
                with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                    parser.parse_args(argv)
            except SystemExit as exc:
                code = _exit_code(exc)
                if code != 0:
                    failures.append(
                        {
                            "source": source,
                            "route": route,
                            "reason": f"leaf --help exited {code}",
                        }
                    )
                else:
                    checked.append({"source": source, "route": route})
            except Exception as exc:  # pragma: no cover - surfaced as CI evidence
                failures.append(
                    {
                        "source": source,
                        "route": route,
                        "reason": f"{type(exc).__name__}: {exc}",
                    }
                )
            else:
                failures.append(
                    {
                        "source": source,
                        "route": route,
                        "reason": "leaf --help returned without argparse help exit",
                    }
                )
    return {"checked": checked, "failures": failures}


def parser_error_contract() -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    sentinel = "__syntavra_unknown_public_command__"
    for source, parser, _ in public_surface.python_public_parser_surfaces():
        stdout = io.StringIO()
        stderr = io.StringIO()
        try:
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                parser.parse_args([sentinel])
        except SystemExit as exc:
            code = _exit_code(exc)
        except Exception as exc:  # pragma: no cover - surfaced as CI evidence
            failures.append(
                {
                    "source": source,
                    "reason": f"{type(exc).__name__}: {exc}",
                }
            )
            continue
        else:
            code = 0

        rows.append({"source": source, "invalid_command_exit": code})
        if code != 2:
            failures.append(
                {
                    "source": source,
                    "reason": f"unknown public command exited {code}, expected 2",
                }
            )
    return {"rows": rows, "failures": failures}


def report() -> dict[str, Any]:
    contract = json.loads(public_surface.CONTRACT.read_text(encoding="utf-8"))
    expected = contract["python_surface"]
    route_sources = public_surface.python_public_route_sources()
    routes = set(route_sources)
    manifest = route_execution_manifest()
    namespace_collisions = public_surface.python_public_namespace_collisions()
    duplicates = {
        route: sources
        for route, sources in route_sources.items()
        if len(sources) > 1
    }
    owner_failures = [
        {
            "route": row["route"],
            "entrypoints": row["entrypoints"],
            "unknown_sources": row["unknown_sources"],
        }
        for row in manifest
        if len(row["entrypoints"]) != 1 or row["unknown_sources"]
    ]
    aliases = parser_alias_rows()
    alias_failures = alias_conflicts()
    shadow = shadow_audit()
    reachability = parser_help_reachability()
    parser_errors = parser_error_contract()

    expected_count = int(expected["public_command_count"])
    expected_digest = str(expected["command_paths_sha256"])
    derived_digest = public_surface._digest(routes)

    ok = (
        len(routes) == EXPECTED_PUBLIC_ROUTES
        and len(routes) == expected_count
        and derived_digest == expected_digest
        and not duplicates
        and not namespace_collisions
        and not owner_failures
        and not alias_failures
        and not shadow["failures"]
        and not reachability["failures"]
        and not parser_errors["failures"]
    )

    return {
        "ok": ok,
        "schema_version": SCHEMA_VERSION,
        "python": {
            "route_count": len(routes),
            "expected_route_count": expected_count,
            "route_sha256": derived_digest,
            "expected_route_sha256": expected_digest,
            "unique_execution_owner_count": len(manifest) - len(owner_failures),
            "owner_failure_count": len(owner_failures),
            "owner_failures": owner_failures,
            "duplicate_route_count": len(duplicates),
            "duplicate_routes": duplicates,
            "namespace_collision_count": len(namespace_collisions),
            "namespace_collisions": namespace_collisions,
            "alias_group_count": len(aliases),
            "alias_groups": aliases,
            "alias_conflict_count": len(alias_failures),
            "alias_conflicts": alias_failures,
            "shadow_rule_count": len(shadow["rows"]),
            "shadow_rules": shadow["rows"],
            "shadow_failure_count": len(shadow["failures"]),
            "shadow_failures": shadow["failures"],
            "parser_leaf_reachability_count": len(reachability["checked"]),
            "parser_leaf_reachability_failures": reachability["failures"],
            "parser_error_contract": parser_errors["rows"],
            "parser_error_failures": parser_errors["failures"],
            "manifest": manifest,
        },
    }


def main() -> int:
    value = report()
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if value["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
