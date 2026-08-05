from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from tests.runtime.test_native_engine_routes_r38 import _rust_expected, _selector_binary

ROOT = Path(__file__).resolve().parents[2]


def _run(engine: str, *arguments: str) -> subprocess.CompletedProcess[str]:
    prefix = (
        [sys.executable, "-m", "syntavra_runtime.engine_entry"]
        if engine == "python"
        else [str(_selector_binary())]
    )
    return subprocess.run(
        [*prefix, "--engine", engine, *arguments],
        cwd=ROOT,
        check=False,
        capture_output=True,
        encoding="utf-8",
        errors="strict",
        timeout=300,
    )


def _json(completed: subprocess.CompletedProcess[str]) -> Any:
    return json.loads(completed.stdout)


def test_native_generic_engine_route_normalizes_known_route() -> None:
    arguments = ("engine", "route", "  VeRsIoN  ")
    python = _run("python", *arguments)
    rust = _run("rust", *arguments)
    assert rust.returncode == python.returncode == 0
    assert rust.stderr == python.stderr == ""
    assert _json(rust) == _rust_expected(_json(python))


def test_native_generic_engine_route_unknown_error_matches_python() -> None:
    arguments = ("engine", "route", "  Not.Real  ")
    python = _run("python", *arguments)
    rust = _run("rust", *arguments)
    assert rust.returncode == python.returncode == 4
    assert rust.stderr == python.stderr == ""
    assert _json(rust) == _json(python)
    assert _json(rust)["error"]["details"]["command"] == "not.real"


def test_native_generic_engine_route_unknown_ignores_route_specific_options() -> None:
    arguments = (
        "engine",
        "route",
        "unknown.route",
        "--scheduler-state",
        "queued",
        "--scheduler-state",
        "failed",
        "--scheduler-limit",
        "7",
        "--telemetry-prometheus",
    )
    python = _run("python", *arguments)
    rust = _run("rust", *arguments)
    assert rust.returncode == python.returncode == 4
    assert rust.stderr == python.stderr == ""
    assert _json(rust) == _json(python)
    assert _json(rust)["error"]["code"] == "ENGINE_ROUTE_UNSUPPORTED_R14"
