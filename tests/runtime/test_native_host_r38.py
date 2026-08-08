from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]


@lru_cache(maxsize=1)
def _selector_binary() -> Path:
    if shutil.which("cargo") is None:
        pytest.skip("Cargo is required for the real Rust binary differential")
    completed = subprocess.run(
        ["cargo", "build", "--quiet", "--locked", "--bins"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert completed.returncode == 0, {
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }
    suffix = ".exe" if sys.platform == "win32" else ""
    selector = ROOT / "target" / "debug" / f"syntavra{suffix}"
    assert selector.is_file(), selector
    return selector


def _run(
    engine: str,
    *arguments: str,
    environment: dict[str, str] | None = None,
) -> tuple[int, Any]:
    prefix = (
        [sys.executable, "-m", "syntavra_runtime.engine_entry"]
        if engine == "python"
        else [str(_selector_binary())]
    )
    completed = subprocess.run(
        [*prefix, "--engine", engine, *arguments],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=300,
        env=environment,
    )
    assert completed.stdout.strip(), {
        "engine": engine,
        "arguments": arguments,
        "returncode": completed.returncode,
        "stderr": completed.stderr,
    }
    return completed.returncode, json.loads(completed.stdout)


def test_native_host_capabilities_matches_complete_python_registry() -> None:
    python_code, python_result = _run("python", "host", "capabilities")
    rust_code, rust_result = _run("rust", "host", "capabilities")

    assert rust_code == python_code == 0
    assert rust_result == python_result
    assert rust_result["coverage"] == {
        "claim_boundary": "registry coverage is implementation coverage, not live host certification",
        "controlled_hosts": 40,
        "coverage": 40 / 44,
        "hosts": 44,
        "stream_capture_hosts": 4,
        "tiers": {
            "HOOK_ENFORCED": 2,
            "INSTRUCTION_ONLY": 3,
            "MCP_CONTROLLED": 30,
            "MCP_PLUS_PROXY": 8,
            "UNSUPPORTED": 1,
        },
        "verified_hosts": 11,
    }


@pytest.mark.parametrize(
    "arguments",
    [
        ("host",),
        ("host", "negotiate", "codex"),
        ("host", "negotiate", "pi", "--runtime-unavailable"),
        ("host", "negotiate", "UNREGISTERED-HOST"),
    ],
)
def test_native_host_negotiation_matches_python(arguments: tuple[str, ...]) -> None:
    python_result = _run("python", *arguments)
    rust_result = _run("rust", *arguments)
    assert rust_result == python_result


def test_native_host_detect_matches_python_in_controlled_environment(tmp_path: Path) -> None:
    project = tmp_path / "project"
    home = tmp_path / "home"
    binary_root = tmp_path / "bin"
    (project / ".codex").mkdir(parents=True)
    (project / "opencode.json").write_text("{}", encoding="utf-8")
    (home / ".config" / "zed").mkdir(parents=True)
    binary_root.mkdir()

    executable = binary_root / ("goose.exe" if sys.platform == "win32" else "goose")
    executable.write_bytes(b"")
    if sys.platform != "win32":
        executable.chmod(0o755)

    environment = os.environ.copy()
    environment["PATH"] = str(binary_root)
    if sys.platform == "win32":
        environment["PATHEXT"] = ".exe"

    arguments = (
        "--project",
        str(project),
        "host",
        "detect",
        "--home",
        str(home),
    )
    python_code, python_result = _run("python", *arguments, environment=environment)
    rust_code, rust_result = _run("rust", *arguments, environment=environment)

    assert rust_code == python_code == 0
    assert rust_result == python_result
    assert [row["host"] for row in rust_result["hosts"]] == [
        "codex",
        "opencode",
        "zed",
        "goose",
    ]
    assert rust_result["hosts"][-1]["executable"] is not None
