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


def _run(engine: str, project: Path, state_root: Path, destination: Path) -> tuple[int, Any]:
    prefix = (
        [sys.executable, "-m", "syntavra_runtime.engine_entry"]
        if engine == "python"
        else [str(_selector_binary())]
    )
    completed = subprocess.run(
        [
            *prefix,
            "--engine",
            engine,
            "--project",
            str(project),
            "--state-root",
            str(state_root),
            "telemetry",
            "bundle",
            str(destination),
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=240,
    )
    assert completed.stdout.strip(), {
        "engine": engine,
        "returncode": completed.returncode,
        "stderr": completed.stderr,
    }
    return completed.returncode, json.loads(completed.stdout)


def _stable_bundle(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value.pop("generated_at"), (int, float))
    return value


def _fixture(state_root: Path) -> None:
    observability = state_root / "observability"
    observability.mkdir(parents=True)
    lines: list[str] = []
    for index in range(205):
        lines.append(
            json.dumps(
                {
                    "timestamp": float(index),
                    "level": "info",
                    "service": "syntavra",
                    "event": "fixture",
                    "trace": {},
                    "payload": {"index": index},
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        )
    lines.insert(10, "not-json")
    (observability / "events.jsonl").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )


def test_native_telemetry_bundle_matches_python(tmp_path: Path) -> None:
    project = tmp_path / "project"
    source = tmp_path / "source"
    python_state = tmp_path / "python-state"
    rust_state = tmp_path / "rust-state"
    python_bundle = tmp_path / "python-bundle.json"
    rust_bundle = tmp_path / "rust-bundle.json"
    project.mkdir()
    _fixture(source)
    shutil.copytree(source, python_state)
    shutil.copytree(source, rust_state)
    python_bundle.write_text("stale", encoding="utf-8")
    rust_bundle.write_text("stale", encoding="utf-8")

    python_code, python_result = _run(
        "python", project, python_state, python_bundle
    )
    rust_code, rust_result = _run("rust", project, rust_state, rust_bundle)

    assert rust_code == python_code == 0
    assert python_result == {"path": str(python_bundle)}
    assert rust_result == {"path": str(rust_bundle)}
    python_value = _stable_bundle(python_bundle)
    rust_value = _stable_bundle(rust_bundle)
    assert rust_value == python_value
    assert rust_value["schema_version"] == 1
    assert rust_value["service"] == "syntavra"
    assert rust_value["metrics"] == {
        "counters": [],
        "gauges": [],
        "histograms": [],
    }
    assert rust_value["extra"] == {}
    assert len(rust_value["log_tail"]) == 199
    assert rust_value["log_tail"][0]["payload"]["index"] == 6
    assert rust_value["log_tail"][-1]["payload"]["index"] == 204


def test_native_telemetry_bundle_empty_state_matches_python(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    python_bundle = tmp_path / "nested" / "python.json"
    rust_bundle = tmp_path / "nested" / "rust.json"

    python_result = _run(
        "python", project, tmp_path / "python-state", python_bundle
    )
    rust_result = _run("rust", project, tmp_path / "rust-state", rust_bundle)

    assert python_result[0] == rust_result[0] == 0
    assert _stable_bundle(rust_bundle) == _stable_bundle(python_bundle)
    assert _stable_bundle(rust_bundle)["log_tail"] == []
