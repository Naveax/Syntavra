from __future__ import annotations

import json
import shutil
import subprocess
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any

import pytest

from syntavra_runtime.util import canonical_json, sha256_bytes, sha256_file

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


def _argv(engine: str, receipt: Path) -> list[str]:
    prefix = (
        [sys.executable, "-m", "syntavra_runtime.engine_entry"]
        if engine == "python"
        else [str(_selector_binary())]
    )
    return [*prefix, "--engine", engine, "claim", str(receipt)]


def _run(engine: str, receipt: Path) -> tuple[int, dict[str, Any]]:
    completed = subprocess.run(
        _argv(engine, receipt),
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


def _write_receipt(path: Path, value: dict[str, Any], *, valid_hash: bool = True) -> None:
    body = dict(value)
    body["receipt_hash"] = (
        sha256_bytes(canonical_json(body)) if valid_hash else "0" * 64
    )
    path.write_text(
        json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )


def test_native_valid_claim_matches_python(tmp_path: Path) -> None:
    artifact = tmp_path / "artifact.txt"
    artifact.write_text("native claim fixture\n", encoding="utf-8")
    receipt = tmp_path / "claim.json"
    _write_receipt(
        receipt,
        {
            "schema_version": 2,
            "claim": "5X_20X_QUALIFIED",
            "status": "PASS",
            "difficulty_score": 0.91,
            "artifact_hashes": {artifact.name: sha256_file(artifact)},
            "reasons": [],
        },
    )

    python_code, python_result = _run("python", receipt)
    rust_code, rust_result = _run("rust", receipt)

    assert rust_code == python_code == 0
    assert rust_result == python_result == {
        "ok": True,
        "reasons": [],
        "claim": "5X_20X_QUALIFIED",
        "status": "PASS",
    }


def test_native_claim_hash_mismatch_matches_python(tmp_path: Path) -> None:
    receipt = tmp_path / "claim.json"
    _write_receipt(
        receipt,
        {
            "schema_version": 2,
            "claim": "5X_NOT_PROVEN",
            "status": "NOT_PROVEN",
            "artifact_hashes": {},
        },
        valid_hash=False,
    )

    python_code, python_result = _run("python", receipt)
    rust_code, rust_result = _run("rust", receipt)

    assert rust_code == python_code == 3
    assert rust_result == python_result == {
        "ok": False,
        "reasons": ["receipt-hash-mismatch"],
        "claim": "5X_NOT_PROVEN",
        "status": "NOT_PROVEN",
    }


def test_native_missing_artifact_matches_python(tmp_path: Path) -> None:
    receipt = tmp_path / "claim.json"
    _write_receipt(
        receipt,
        {
            "schema_version": 2,
            "claim": "5X_30X_ENDURANCE_QUALIFIED",
            "status": "PASS",
            "artifact_hashes": {"missing.bin": "a" * 64},
        },
    )

    python_code, python_result = _run("python", receipt)
    rust_code, rust_result = _run("rust", receipt)

    assert rust_code == python_code == 3
    assert rust_result == python_result == {
        "ok": False,
        "reasons": ["artifact-invalid:missing.bin"],
        "claim": "5X_30X_ENDURANCE_QUALIFIED",
        "status": "PASS",
    }


def test_native_contradictory_claim_matches_python(tmp_path: Path) -> None:
    receipt = tmp_path / "claim.json"
    _write_receipt(
        receipt,
        {
            "schema_version": 2,
            "claim": "5X_NOT_PROVEN",
            "status": "PASS",
            "artifact_hashes": {},
        },
    )

    python_code, python_result = _run("python", receipt)
    rust_code, rust_result = _run("rust", receipt)

    assert rust_code == python_code == 3
    assert rust_result == python_result == {
        "ok": False,
        "reasons": ["contradictory-status"],
        "claim": "5X_NOT_PROVEN",
        "status": "PASS",
    }


def test_native_non_string_artifact_digest_is_invalid(tmp_path: Path) -> None:
    artifact = tmp_path / "artifact.txt"
    artifact.write_text("fixture", encoding="utf-8")
    receipt = tmp_path / "claim.json"
    _write_receipt(
        receipt,
        {
            "schema_version": 2,
            "claim": "5X_NOT_PROVEN",
            "status": "NOT_PROVEN",
            "artifact_hashes": {artifact.name: 123},
        },
    )

    python_code, python_result = _run("python", receipt)
    rust_code, rust_result = _run("rust", receipt)

    assert rust_code == python_code == 3
    assert rust_result == python_result
    assert rust_result["reasons"] == [f"artifact-invalid:{artifact.name}"]
