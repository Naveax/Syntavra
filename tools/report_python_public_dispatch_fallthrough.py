#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ast
import importlib
import inspect
import json
import textwrap
from dataclasses import dataclass
from typing import Any, Callable

from tools import report_missing_native_public_routes as public_surface
from tools import report_python_public_execution_contract as execution_contract


SCHEMA_VERSION = 2
EXPECTED_PUBLIC_ROUTES = 245


@dataclass(frozen=True)
class DispatcherSpec:
    source: str
    module: str
    function: str
    selector: str
    route_token_index: int
    expected_implicit_fallbacks: frozenset[str] = frozenset()
    expect_generic_runtime_fallthrough: bool = False


DISPATCHER_SPECS = (
    DispatcherSpec(
        source="core",
        module="syntavra_runtime.unified_cli",
        function="_core_main",
        selector="command",
        route_token_index=0,
        expect_generic_runtime_fallthrough=True,
    ),
    DispatcherSpec(
        source="prerelease",
        module="syntavra_runtime.prerelease_cli",
        function="main",
        selector="command",
        route_token_index=0,
        expect_generic_runtime_fallthrough=True,
    ),
    DispatcherSpec(
        source="engine",
        module="syntavra_runtime.engine_cli",
        function="main",
        selector="action",
        route_token_index=1,
        expect_generic_runtime_fallthrough=True,
    ),
    DispatcherSpec(
        source="external-benchmark",
        module="syntavra_runtime.external_benchmark_cli",
        function="main",
        selector="action",
        route_token_index=1,
        expected_implicit_fallbacks=frozenset({"external-suite"}),
        expect_generic_runtime_fallthrough=False,
    ),
)


def _callable_name(value: Callable[..., Any]) -> str:
    return f"{value.__module__}.{value.__qualname__}"


def _resolve_route_defaults(
    parser: argparse.ArgumentParser,
    route: str,
) -> dict[str, Any]:
    """Resolve argparse defaults inherited by one canonical parser route."""

    current = parser
    defaults: dict[str, Any] = dict(getattr(current, "_defaults", {}))
    for token in route.split():
        child: argparse.ArgumentParser | None = None
        for action in public_surface._subparsers(current):
            candidate = action.choices.get(token)
            if candidate is not None:
                child = candidate
                break
        if child is None:
            raise KeyError(f"cannot resolve parser route {route!r} at token {token!r}")
        current = child
        defaults.update(getattr(current, "_defaults", {}))
    return defaults


def legacy_leaf_handlers() -> dict[str, str]:
    surfaces = {
        source: (parser, skip_top_level)
        for source, parser, skip_top_level in public_surface.python_public_parser_surfaces()
    }
    parser, skip_top_level = surfaces["legacy"]
    handlers: dict[str, str] = {}
    for route in sorted(public_surface._parser_paths(parser, skip_top_level=skip_top_level)):
        defaults = _resolve_route_defaults(parser, route)
        handler = defaults.get("func")
        if callable(handler):
            handlers[route] = _callable_name(handler)
    return handlers


def exact_leaf_handler_manifest() -> list[dict[str, Any]]:
    """Attach the most specific callable owner available to every public route.

    Legacy argparse routes expose their concrete ``func`` defaults. Manual-dispatch
    surfaces intentionally retain their real public dispatcher entrypoint; the
    dispatcher audits below prove that parser selector values cannot drift into a
    generic runtime fallthrough.
    """

    legacy_handlers = legacy_leaf_handlers()
    rows: list[dict[str, Any]] = []
    for item in execution_contract.route_execution_manifest():
        route = str(item["route"])
        sources = list(item["sources"])
        entrypoint = item.get("entrypoint")
        if sources == ["legacy"] and route in legacy_handlers:
            handler = legacy_handlers[route]
            kind = "argparse-default"
        else:
            handler = entrypoint
            kind = "dispatch-entrypoint" if item.get("parser_owned") else "direct-entrypoint"
        rows.append(
            {
                "route": route,
                "sources": sources,
                "entrypoint": entrypoint,
                "handler": handler,
                "handler_kind": kind,
            }
        )
    return rows


def _function_node(function: Callable[..., Any]) -> ast.FunctionDef | ast.AsyncFunctionDef:
    source = textwrap.dedent(inspect.getsource(function))
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return node
    raise RuntimeError(f"cannot locate function AST for {_callable_name(function)}")


def _args_selector(node: ast.AST, selector: str) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "args"
        and node.attr == selector
    )


def _literal_strings(node: ast.AST) -> set[str]:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return {node.value}
    if isinstance(node, (ast.Set, ast.Tuple, ast.List)):
        result: set[str] = set()
        for item in node.elts:
            if not isinstance(item, ast.Constant) or not isinstance(item.value, str):
                return set()
            result.add(item.value)
        return result
    return set()


def _selector_aliases(node: ast.AST, selector: str) -> set[str]:
    aliases: set[str] = set()
    for candidate in ast.walk(node):
        if not isinstance(candidate, (ast.Assign, ast.AnnAssign)):
            continue
        value = candidate.value
        if value is None or not _args_selector(value, selector):
            continue
        targets = candidate.targets if isinstance(candidate, ast.Assign) else [candidate.target]
        for target in targets:
            if isinstance(target, ast.Name):
                aliases.add(target.id)
    return aliases


def _selector_reference(node: ast.AST, selector: str, aliases: set[str]) -> bool:
    return _args_selector(node, selector) or (isinstance(node, ast.Name) and node.id in aliases)


def _compared_selector_values(node: ast.AST, selector: str) -> set[str]:
    aliases = _selector_aliases(node, selector)
    values: set[str] = set()
    for candidate in ast.walk(node):
        if not isinstance(candidate, ast.Compare) or len(candidate.ops) != 1:
            continue
        operator = candidate.ops[0]
        comparator = candidate.comparators[0]
        if _selector_reference(candidate.left, selector, aliases):
            if isinstance(operator, (ast.Eq, ast.In)):
                values.update(_literal_strings(comparator))
            continue
        if isinstance(operator, ast.Eq) and _selector_reference(comparator, selector, aliases):
            values.update(_literal_strings(candidate.left))
    return values


def _generic_runtime_fallthrough_count(node: ast.AST, selector: str) -> int:
    aliases = _selector_aliases(node, selector)
    count = 0
    for candidate in ast.walk(node):
        if not isinstance(candidate, ast.Raise) or not isinstance(candidate.exc, ast.Call):
            continue
        call = candidate.exc
        if not isinstance(call.func, ast.Name) or call.func.id != "RuntimeError":
            continue
        if len(call.args) == 1 and _selector_reference(call.args[0], selector, aliases):
            count += 1
    return count


def _surface_parser(source: str) -> tuple[argparse.ArgumentParser, frozenset[str]]:
    for candidate, parser, skip_top_level in public_surface.python_public_parser_surfaces():
        if candidate == source:
            return parser, skip_top_level
    raise KeyError(source)


def _selector_branch(
    function_node: ast.FunctionDef | ast.AsyncFunctionDef,
    selector: str,
    value: str,
) -> ast.If:
    for candidate in ast.walk(function_node):
        if not isinstance(candidate, ast.If):
            continue
        if value in _compared_selector_values(candidate.test, selector):
            return candidate
    raise RuntimeError(f"cannot locate {selector}={value!r} branch")


def dispatcher_audit(spec: DispatcherSpec) -> dict[str, Any]:
    parser, skip_top_level = _surface_parser(spec.source)
    paths = sorted(public_surface._parser_paths(parser, skip_top_level=skip_top_level))
    expected_values: set[str] = set()
    malformed_paths: list[str] = []
    for route in paths:
        parts = route.split()
        if len(parts) <= spec.route_token_index:
            malformed_paths.append(route)
            continue
        expected_values.add(parts[spec.route_token_index])

    module = importlib.import_module(spec.module)
    function = getattr(module, spec.function)
    function_node = _function_node(function)
    explicit_values = _compared_selector_values(function_node, spec.selector)
    generic_count = _generic_runtime_fallthrough_count(function_node, spec.selector)

    missing_values = sorted(expected_values - explicit_values)
    stale_values = sorted(explicit_values - expected_values)
    implicit_values = sorted(expected_values - explicit_values)
    expected_implicit = sorted(spec.expected_implicit_fallbacks)

    failures: list[str] = []
    if malformed_paths:
        failures.append(f"malformed parser paths for selector index: {malformed_paths!r}")
    if bool(generic_count) != spec.expect_generic_runtime_fallthrough:
        failures.append(
            "generic RuntimeError selector fallthrough presence changed: "
            f"expected={spec.expect_generic_runtime_fallthrough} actual={bool(generic_count)}"
        )
    if spec.expect_generic_runtime_fallthrough and missing_values:
        failures.append(
            "parser selector values can reach generic RuntimeError: "
            + ", ".join(missing_values)
        )
    if spec.expected_implicit_fallbacks:
        if set(implicit_values) != set(spec.expected_implicit_fallbacks):
            failures.append(
                f"implicit fallback drift: expected={expected_implicit!r} actual={implicit_values!r}"
            )
    elif not spec.expect_generic_runtime_fallthrough and missing_values:
        failures.append(
            "manual dispatcher has undeclared implicit fallbacks: "
            + ", ".join(missing_values)
        )
    if stale_values:
        failures.append("stale dispatcher selector values: " + ", ".join(stale_values))

    return {
        "source": spec.source,
        "dispatcher": f"{spec.module}.{spec.function}",
        "selector": spec.selector,
        "route_token_index": spec.route_token_index,
        "parser_route_count": len(paths),
        "expected_selector_values": sorted(expected_values),
        "explicit_selector_values": sorted(explicit_values),
        "implicit_selector_values": implicit_values,
        "expected_implicit_fallbacks": expected_implicit,
        "generic_runtime_fallthrough_count": generic_count,
        "failures": failures,
    }


def prerelease_run_action_audit() -> dict[str, Any]:
    """Prove every parser-valid ``run <action>`` has exactly one dispatch owner.

    ``prerelease_cli.main`` deliberately chains three owners before its final
    ``RuntimeError(args.action)`` guard: platform actions, competitive actions, and
    prerelease-local actions. The parser is the route authority; owner sets are
    taken from the real platform registry or derived from the live Python function
    AST rather than repeated as another hardcoded route list.
    """

    parser, skip_top_level = _surface_parser("prerelease")
    paths = sorted(public_surface._parser_paths(parser, skip_top_level=skip_top_level))
    run_paths = [route for route in paths if route == "run" or route.startswith("run ")]
    expected_actions = {route.split()[1] for route in run_paths if len(route.split()) >= 2}

    prerelease = importlib.import_module("syntavra_runtime.prerelease_cli")
    platform_cli = importlib.import_module("syntavra_runtime.platform_cli")
    main_node = _function_node(prerelease.main)
    run_branch = _selector_branch(main_node, "command", "run")
    local_actions = _compared_selector_values(run_branch, "action")
    competitive_node = _function_node(prerelease._handle_competitive_run)
    competitive_actions = _compared_selector_values(competitive_node, "action")
    platform_actions = set(platform_cli.ACTIONS)

    owner_sets = {
        "platform": platform_actions,
        "competitive": competitive_actions,
        "prerelease-local": local_actions,
    }
    ownership: list[dict[str, Any]] = []
    missing_actions: list[str] = []
    multiple_owner_actions: list[dict[str, Any]] = []
    for action in sorted(expected_actions):
        owners = sorted(name for name, values in owner_sets.items() if action in values)
        row = {"action": action, "owners": owners, "owner_count": len(owners)}
        ownership.append(row)
        if not owners:
            missing_actions.append(action)
        elif len(owners) != 1:
            multiple_owner_actions.append(row)

    stale_by_owner = {
        name: sorted(values - expected_actions)
        for name, values in owner_sets.items()
        if values - expected_actions
    }
    generic_count = _generic_runtime_fallthrough_count(run_branch, "action")
    failures: list[str] = []
    if not run_paths:
        failures.append("prerelease parser exposes no run routes")
    if missing_actions:
        failures.append("run actions without a dispatch owner: " + ", ".join(missing_actions))
    if multiple_owner_actions:
        failures.append(
            "run actions with multiple dispatch owners: "
            + ", ".join(row["action"] for row in multiple_owner_actions)
        )
    if stale_by_owner:
        failures.append(f"dispatch owners contain parser-stale run actions: {stale_by_owner!r}")
    if generic_count != 1:
        failures.append(f"expected one guarded RuntimeError(args.action), found {generic_count}")

    return {
        "source": "prerelease-run",
        "selector": "action",
        "parser_route_count": len(run_paths),
        "parser_action_count": len(expected_actions),
        "expected_actions": sorted(expected_actions),
        "owner_sets": {name: sorted(values) for name, values in owner_sets.items()},
        "ownership": ownership,
        "missing_action_count": len(missing_actions),
        "missing_actions": missing_actions,
        "multiple_owner_action_count": len(multiple_owner_actions),
        "multiple_owner_actions": multiple_owner_actions,
        "stale_by_owner": stale_by_owner,
        "generic_runtime_fallthrough_count": generic_count,
        "generic_runtime_fallthrough_reachable_from_parser": bool(missing_actions),
        "failures": failures,
    }


def report() -> dict[str, Any]:
    base = execution_contract.report()
    manifest = exact_leaf_handler_manifest()
    handler_failures = [
        {"route": row["route"], "sources": row["sources"]}
        for row in manifest
        if not row["handler"]
    ]
    legacy_exact_count = sum(1 for row in manifest if row["handler_kind"] == "argparse-default")
    dispatcher_rows = [dispatcher_audit(spec) for spec in DISPATCHER_SPECS]
    dispatcher_failures = [
        {"source": row["source"], "failures": row["failures"]}
        for row in dispatcher_rows
        if row["failures"]
    ]
    nested_run = prerelease_run_action_audit()

    ok = (
        bool(base["ok"])
        and len(manifest) == EXPECTED_PUBLIC_ROUTES
        and not handler_failures
        and not dispatcher_failures
        and not nested_run["failures"]
    )
    return {
        "ok": ok,
        "schema_version": SCHEMA_VERSION,
        "base_execution_contract_ok": bool(base["ok"]),
        "python": {
            "route_count": len(manifest),
            "handler_count": len(manifest) - len(handler_failures),
            "handler_failure_count": len(handler_failures),
            "handler_failures": handler_failures,
            "legacy_exact_argparse_handler_count": legacy_exact_count,
            "dispatcher_audit_count": len(dispatcher_rows),
            "dispatcher_failure_count": len(dispatcher_failures),
            "dispatcher_failures": dispatcher_failures,
            "dispatchers": dispatcher_rows,
            "nested_run_dispatch": nested_run,
            "manifest": manifest,
        },
    }


def main() -> int:
    value = report()
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if value["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
