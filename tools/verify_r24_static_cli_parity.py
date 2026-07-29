#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path

from syntavra_runtime.engine_selector import EngineSelector
from syntavra_runtime.read_only_cli_contract import static_route_result
from syntavra_runtime.read_only_router_r24 import ReadOnlyCommandRouterR24

ROOT = Path(__file__).resolve().parents[1]
ROUTES = ("pipeline.describe", "plugins.list")


def _build_binary() -> Path:
    completed = subprocess.run(
        ["cargo", "build", "--quiet", "--locked", "-p", "syntavra-cli"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=180,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"R24 Rust build failed: {completed.stderr.strip()}")
    name = "syntavra-rs.exe" if os.name == "nt" else "syntavra-rs"
    binary = ROOT / "target" / "debug" / name
    if not binary.is_file():
        raise RuntimeError("R24 Rust binary was not produced")
    return binary


def verify() -> dict[str, object]:
    binary = _build_binary()
    results: dict[str, object] = {}
    with tempfile.TemporaryDirectory(prefix="syntavra-r24-") as directory:
        project = Path(directory)
        selector = EngineSelector(
            project_root=project,
            env={"HOME": str(project / "home")},
            rust_binary=binary,
        )
        router = ReadOnlyCommandRouterR24(
            selector,
            project_input_root=project,
        )
        for route in ROUTES:
            routed = router.route(route, cli_override="rust")
            if routed.get("result") != static_route_result(route):
                raise RuntimeError(f"R24 route parity failed: {route}")
            if routed.get("mutation") != "read-only":
                raise RuntimeError(f"R24 mutation boundary failed: {route}")
            if routed.get("fallback") != {"policy": "none", "attempted": False}:
                raise RuntimeError(f"R24 fallback boundary failed: {route}")
            results[route] = routed
        unexpected = sorted(path.relative_to(project).as_posix() for path in project.rglob("*") if path.name != "home")
        if unexpected:
            raise RuntimeError(f"R24 routes mutated project state: {unexpected!r}")
    return {
        "ok": True,
        "phase": "R24",
        "routes": list(ROUTES),
        "reference_engine": "python",
        "candidate_engine": "rust",
        "filesystem_mutation": False,
        "database_access": False,
        "network_access": False,
        "results": results,
        "claim": "RUST_STATIC_READ_ONLY_CLI_PARITY_PROVEN_R24",
    }


def main() -> int:
    print(json.dumps(verify(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
