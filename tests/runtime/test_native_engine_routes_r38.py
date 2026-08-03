from __future__ import annotations

import json
import shutil
import subprocess
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_WIRE_HEX = b"R6CFG1\nphase\t0\n".hex()


def _json_command(argv: list[str]) -> Any:
    completed = subprocess.run(
        argv,
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert completed.returncode == 0, {
        "argv": argv,
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }
    return json.loads(completed.stdout)


def _python_engine(*arguments: str) -> Any:
    return _json_command(
        [
            sys.executable,
            "-m",
            "syntavra_runtime.engine_entry",
            "--engine",
            "python",
            *arguments,
        ]
    )


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
    runtime = ROOT / "target" / "debug" / f"syntavra-rs{suffix}"
    assert selector.is_file(), selector
    assert runtime.is_file(), runtime
    return selector


def _rust_engine(*arguments: str) -> Any:
    return _json_command(
        [
            str(_selector_binary()),
            "--engine",
            "rust",
            *arguments,
        ]
    )


def _rust_expected(value: Any) -> Any:
    expected = json.loads(json.dumps(value))
    selection = expected.get("selection")
    if isinstance(selection, dict):
        selection["requested"] = "rust"
        selection["resolved"] = "rust"
    result = expected.get("result")
    if isinstance(result, dict) and result.get("engine") == "python":
        result["engine"] = "rust"
        result["engine_stability"] = "experimental"
    return expected


def _project_with_config(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    syntavra = project / ".syntavra"
    syntavra.mkdir(parents=True)
    (syntavra / "config.toml").write_text(
        "[runtime]\nprofile = 'compact'\n[routing]\nbudget_bytes = 4096\n",
        encoding="utf-8",
    )
    return project


@pytest.mark.parametrize(
    "arguments",
    [
        ("engine", "route", "version"),
        ("engine", "route", "status"),
        ("engine", "route", "pipeline.describe"),
        ("engine", "route", "plugins.list"),
        ("engine", "route", "state.layout"),
        ("engine", "route", "telemetry.metrics"),
        (
            "engine",
            "route",
            "telemetry.metrics",
            "--telemetry-prometheus",
        ),
    ],
)
def test_native_static_engine_routes_match_canonical_python_contract(
    arguments: tuple[str, ...],
) -> None:
    assert _rust_engine(*arguments) == _rust_expected(_python_engine(*arguments))


def test_native_config_resolve_route_matches_canonical_python_contract() -> None:
    arguments = (
        "engine",
        "route",
        "config.resolve",
        "--config-wire-hex",
        DEFAULT_CONFIG_WIRE_HEX,
    )
    assert _rust_engine(*arguments) == _rust_expected(_python_engine(*arguments))


@pytest.mark.parametrize(
    "route,extra",
    [
        ("config.show", ()),
        ("config.validate", ()),
        ("config.explain", ("--explain-path", "runtime.profile")),
    ],
)
def test_native_live_config_routes_match_python_exactly(
    tmp_path: Path,
    route: str,
    extra: tuple[str, ...],
) -> None:
    project = _project_with_config(tmp_path)
    arguments = (
        "--project",
        str(project),
        "engine",
        "route",
        route,
        *extra,
    )
    assert _rust_engine(*arguments) == _rust_expected(_python_engine(*arguments))


def test_native_status_live_overrides_match_python_exactly(tmp_path: Path) -> None:
    project = _project_with_config(tmp_path)
    session = json.dumps(
        {"routing": {"budget_bytes": 8192}},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8").hex()
    task = json.dumps(
        {"runtime": {"profile": "balanced"}},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8").hex()
    arguments = (
        "--project",
        str(project),
        "engine",
        "route",
        "status",
        "--live-config",
        "--session-override-json-hex",
        session,
        "--task-override-json-hex",
        task,
    )
    assert _rust_engine(*arguments) == _rust_expected(_python_engine(*arguments))


def test_native_migration_plan_route_matches_python_exactly(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    arguments = (
        "--project",
        str(project),
        "engine",
        "route",
        "migration.plan",
        "--migration-database",
        "state/missing.sqlite3",
    )
    assert _rust_engine(*arguments) == _rust_expected(_python_engine(*arguments))


def test_native_scheduler_stats_route_matches_python_exactly(tmp_path: Path) -> None:
    arguments = (
        "--state-root",
        str(tmp_path / "state"),
        "engine",
        "route",
        "scheduler.stats",
    )
    assert _rust_engine(*arguments) == _rust_expected(_python_engine(*arguments))


def test_native_scheduler_list_route_matches_python_exactly(tmp_path: Path) -> None:
    arguments = (
        "--state-root",
        str(tmp_path / "state"),
        "engine",
        "route",
        "scheduler.list",
        "--scheduler-state",
        "queued",
        "--scheduler-state",
        "failed",
        "--scheduler-limit",
        "7",
    )
    assert _rust_engine(*arguments) == _rust_expected(_python_engine(*arguments))


def test_native_state_inspect_route_matches_python_exactly(tmp_path: Path) -> None:
    project = _project_with_config(tmp_path)
    arguments = (
        "--project",
        str(project),
        "engine",
        "route",
        "state.inspect",
    )
    assert _rust_engine(*arguments) == _rust_expected(_python_engine(*arguments))


def test_auto_engine_route_preserves_python_auto_policy() -> None:
    actual = _json_command(
        [
            str(_selector_binary()),
            "engine",
            "route",
            "version",
        ]
    )
    assert actual == _python_engine("engine", "route", "version")
