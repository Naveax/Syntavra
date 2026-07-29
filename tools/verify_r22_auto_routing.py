#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path

from syntavra_runtime.engine_selector import EngineSelectionError, EngineSelector
from syntavra_runtime.read_only_router_r22 import (
    AUTO_POLICY,
    AUTO_RUST_COMMANDS,
    ReadOnlyCommandRouterR22,
)

ROOT = Path(__file__).resolve().parents[1]


def _build_rust_binary() -> Path:
    subprocess.run(
        ["cargo", "build", "--quiet", "--locked", "-p", "syntavra-cli"],
        cwd=ROOT,
        check=True,
        timeout=180,
    )
    name = "syntavra-rs.exe" if os.name == "nt" else "syntavra-rs"
    binary = ROOT / "target" / "debug" / name
    if not binary.is_file():
        raise RuntimeError(f"Rust binary was not built: {binary}")
    return binary


def verify() -> dict[str, object]:
    binary = _build_rust_binary()
    with tempfile.TemporaryDirectory(prefix="syntavra-r22-") as temporary:
        project = Path(temporary) / "project"
        project.mkdir()
        env = {
            "HOME": temporary,
            "SYNTAVRA_ENGINE": "auto",
        }
        selector = EngineSelector(
            project_root=project,
            env=env,
            rust_binary=binary,
        )
        router = ReadOnlyCommandRouterR22(
            selector,
            project_input_root=project,
            platform_name="linux",
        )
        rust_result = router.route("version")

        missing_selector = EngineSelector(
            project_root=project,
            env=env,
            rust_binary=project / "missing-syntavra-rs",
        )
        missing_router = ReadOnlyCommandRouterR22(
            missing_selector,
            project_input_root=project,
            platform_name="linux",
        )
        python_result = missing_router.route("version")

        calls = 0

        def failing_runner(_binary: Path, _arguments: tuple[str, ...]) -> dict[str, object]:
            nonlocal calls
            calls += 1
            raise RuntimeError("injected candidate failure")

        failing_router = ReadOnlyCommandRouterR22(
            selector,
            runner=failing_runner,
            project_input_root=project,
            platform_name="linux",
        )
        no_fallback = False
        try:
            failing_router.route("version")
        except EngineSelectionError as exc:
            no_fallback = (
                calls == 1
                and exc.details.get("fallback_attempted") is False
                and exc.details.get("auto_decision", {}).get("selected_engine") == "rust"
            )

    checks = {
        "route_whitelist": set(AUTO_RUST_COMMANDS)
        == {
            "config.resolve",
            "receipt.inspect",
            "state.broker-live-snapshot",
            "state.broker-snapshot",
            "state.inspect",
            "state.layout",
            "status",
            "version",
        },
        "auto_rust": rust_result.get("selection", {}).get("requested") == "auto"
        and rust_result.get("selection", {}).get("resolved") == "rust"
        and rust_result.get("selection", {}).get("auto_policy") == AUTO_POLICY
        and rust_result.get("auto_decision", {}).get("reason")
        == "AUTO_ROUTE_RUST_ELIGIBLE_R22",
        "auto_python_prestart": python_result.get("selection", {}).get("requested")
        == "auto"
        and python_result.get("selection", {}).get("resolved") == "python"
        and python_result.get("auto_decision", {}).get("reason")
        == "AUTO_ROUTE_PYTHON_RUST_UNAVAILABLE_R22"
        and python_result.get("auto_decision", {}).get("fallback_attempted") is False,
        "no_fallback_after_rust_start": no_fallback,
    }
    if not all(checks.values()):
        raise RuntimeError(f"R22 auto-routing parity failed: {checks}")

    return {
        "ok": True,
        "phase": "R22",
        "policy": AUTO_POLICY,
        "routes": sorted(AUTO_RUST_COMMANDS),
        "checks": checks,
        "claim": "RUST_ROUTE_SCOPED_AUTO_SELECTION_PROVEN_R22",
    }


def main() -> int:
    print(json.dumps(verify(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
