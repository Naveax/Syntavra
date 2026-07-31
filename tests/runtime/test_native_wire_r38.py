from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _engine(engine: str, *arguments: str) -> Any:
    if engine == "rust" and shutil.which("cargo") is None:
        pytest.skip("Cargo is required for the real Rust binary differential")
    argv = (
        [
            "cargo",
            "run",
            "--quiet",
            "--locked",
            "--bin",
            "syntavra",
            "--",
            "--engine",
            "rust",
        ]
        if engine == "rust"
        else [
            sys.executable,
            "-m",
            "syntavra_runtime.engine_entry",
            "--engine",
            "python",
        ]
    )
    completed = subprocess.run(
        [*argv, *arguments],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert completed.returncode == 0, {
        "engine": engine,
        "arguments": arguments,
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }
    return json.loads(completed.stdout)


def test_native_wire_json_fallback_matches_python_exactly() -> None:
    source = json.dumps({"short": True}, separators=(",", ":"))
    arguments = ("run", "wire", "encode", source)
    assert _engine("rust", *arguments) == _engine("python", *arguments)


def test_native_wire_compaction_matches_python_exactly(tmp_path: Path) -> None:
    repeated_path = "/workspace/project/src/very/long/module/file.rs"
    payload = [
        {
            "filename": repeated_path,
            "metadata": {
                "filename": repeated_path,
                "description": "deterministic repeated metadata value",
            },
        },
        {
            "filename": repeated_path,
            "metadata": {
                "filename": repeated_path,
                "description": "deterministic repeated metadata value",
            },
        },
        {
            "filename": repeated_path,
            "metadata": {
                "filename": repeated_path,
                "description": "deterministic repeated metadata value",
            },
        },
    ]
    source = tmp_path / "wire-source.json"
    source.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    arguments = ("run", "wire", "encode", str(source), "--minimum-savings", "0")
    assert _engine("rust", *arguments) == _engine("python", *arguments)


def test_native_wire_decodes_python_envelope_exactly(tmp_path: Path) -> None:
    payload = {
        "entries": [
            {"path": "/workspace/project/src/module.rs", "kind": "source"},
            {"path": "/workspace/project/src/module.rs", "kind": "source"},
        ]
    }
    source = tmp_path / "wire-source.json"
    source.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    encoded = _engine(
        "python",
        "run",
        "wire",
        "encode",
        str(source),
        "--minimum-savings",
        "0",
    )
    envelope = tmp_path / "wire-envelope.json"
    envelope.write_text(
        json.dumps(encoded, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    arguments = ("run", "wire", "decode", str(envelope))
    assert _engine("rust", *arguments) == _engine("python", *arguments) == payload
