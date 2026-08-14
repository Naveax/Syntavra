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


def test_native_redact_nested_json_matches_python_exactly(tmp_path: Path) -> None:
    payload = {
        "openai": "sk" + "-proj-" + "ABCDEFGHIJKLMNOPQRSTUVWXYZ1234567890",
        "github": "gh" + "p_" + "ABCDEFGHIJKLMNOPQRSTUVWXYZ1234567890",
        "aws": "AKIAABCDEFGHIJKLMNOP",
        "nested": ["safe", {"enabled": True}],
    }
    source = tmp_path / "secrets.json"
    source.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    arguments = ("run", "redact", str(source))
    assert _engine("rust", *arguments) == _engine("python", *arguments)


def test_native_redact_text_file_matches_python_exactly(tmp_path: Path) -> None:
    source = tmp_path / "secrets.txt"
    source.write_text(
        "Authorization: supersecretvalue\n"
        "postgres://user:password@localhost:5432/database\n"
        "-----BEGIN PRIVATE KEY-----\n"
        "sensitive-private-material\n"
        "-----END PRIVATE KEY-----\n",
        encoding="utf-8",
    )
    arguments = ("run", "redact", str(source))
    assert _engine("rust", *arguments) == _engine("python", *arguments)
