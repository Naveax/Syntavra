from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _run(argv: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=180,
    )


def _python(*arguments: str) -> subprocess.CompletedProcess[str]:
    return _run(
        [
            sys.executable,
            "-m",
            "syntavra_runtime.engine_entry",
            "--engine",
            "python",
            *arguments,
        ]
    )


def _rust(*arguments: str) -> subprocess.CompletedProcess[str]:
    if shutil.which("cargo") is None:
        pytest.skip("Cargo is required for the native mode differential")
    return _run(
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
            *arguments,
        ]
    )


def _json(result: subprocess.CompletedProcess[str]) -> Any:
    assert result.returncode == 0, {
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }
    return json.loads(result.stdout)


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _verify_receipt(value: dict[str, Any], state_root: Path) -> None:
    assert value["mode"] in {"full", "lite", "ultra", "commit", "review", "compress"}
    assert isinstance(value["updated_at"], (int, float))
    assert abs(time.time() - float(value["updated_at"])) < 30
    body = dict(value)
    receipt_hash = body.pop("receipt_hash")
    assert receipt_hash == hashlib.sha256(_canonical(body)).hexdigest()
    stored = json.loads((state_root / "optimization-mode.json").read_text(encoding="utf-8"))
    assert stored == value


def test_native_mode_manifest_matches_python_exactly(tmp_path: Path) -> None:
    python_state = tmp_path / "python"
    rust_state = tmp_path / "rust"
    python_value = _json(_python("--state-root", str(python_state), "run", "mode"))
    rust_value = _json(_rust("--state-root", str(rust_state), "run", "mode"))
    assert rust_value == python_value


def test_native_mode_set_matches_python_semantics(tmp_path: Path) -> None:
    python_state = tmp_path / "python"
    rust_state = tmp_path / "rust"
    arguments = ("run", "mode", "review", "--source", "parity-test")
    python_value = _json(_python("--state-root", str(python_state), *arguments))
    rust_value = _json(_rust("--state-root", str(rust_state), *arguments))

    for value in (python_value, rust_value):
        assert value["mode"] == "review"
        assert value["source"] == "parity-test"
        assert value["profile"]["name"] == "review"
    assert rust_value["profile"] == python_value["profile"]
    _verify_receipt(python_value, python_state)
    _verify_receipt(rust_value, rust_state)


def test_native_mode_alias_matches_python_semantics(tmp_path: Path) -> None:
    python_state = tmp_path / "python"
    rust_state = tmp_path / "rust"
    arguments = ("run", "mode", "codex-ultra", "--source", "alias-test")
    python_value = _json(_python("--state-root", str(python_state), *arguments))
    rust_value = _json(_rust("--state-root", str(rust_state), *arguments))

    assert python_value["mode"] == "ultra"
    assert rust_value["mode"] == "ultra"
    assert rust_value["profile"] == python_value["profile"]
    _verify_receipt(python_value, python_state)
    _verify_receipt(rust_value, rust_state)


def test_native_mode_read_after_write_matches_python(tmp_path: Path) -> None:
    python_state = tmp_path / "python"
    rust_state = tmp_path / "rust"
    _json(_python("--state-root", str(python_state), "run", "mode", "commit"))
    _json(_rust("--state-root", str(rust_state), "run", "mode", "commit"))

    python_manifest = _json(_python("--state-root", str(python_state), "run", "mode"))
    rust_manifest = _json(_rust("--state-root", str(rust_state), "run", "mode"))
    assert rust_manifest == python_manifest
    assert rust_manifest["active"]["name"] == "commit"
