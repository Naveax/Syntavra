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


def python_public_routes() -> set[str]:
    from syntavra_runtime import cli, engine_cli, external_benchmark_cli, prerelease_cli, unified_cli

    pre_release = _parser_paths(prerelease_cli._parser())
    core = _parser_paths(unified_cli._core_parser())

    # unified_cli dispatches these top-level commands away from legacy cli.py.
    shadowed = frozenset(prerelease_cli.PRE_RELEASE_COMMANDS | unified_cli.CORE_COMMANDS)
    legacy = _parser_paths(cli.build_parser(), skip_top_level=shadowed)

    engine = _parser_paths(engine_cli.build_parser())
    engine.update(
        f"engine route {route}"
        for route in engine_cli.INSTALLED_READ_ONLY_ROUTE_COMMANDS
    )

    external = _parser_paths(external_benchmark_cli.parser())

    # unified_cli owns these evidence actions directly before falling back to the
    # legacy parser; they are public routes even though legacy argparse only owns
    # get/describe.
    synthetic = {
        "evidence stats",
        "evidence gc",
        "evidence rotate-key",
    }

    return pre_release | core | legacy | engine | external | synthetic


def report() -> dict[str, object]:
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    expected = contract["python_surface"]
    rust = contract["rust_surface"]
    python_routes = python_public_routes()
    native = set(rust["native_public_commands"])
    missing = sorted(python_routes - native)
    extra_native = sorted(native - python_routes)
    digest = _digest(python_routes)

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
