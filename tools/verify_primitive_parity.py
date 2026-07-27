#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from syntavra_runtime.canonical_primitives import (  # noqa: E402
    CanonicalPathError,
    canonical_manifest_bytes,
    manifest_digest_hex,
    normalize_repository_path,
    sha256_hex,
)

FIXTURE = ROOT / "parity" / "fixtures" / "primitives-v1.json"


def _build_rust() -> Path:
    completed = subprocess.run(
        ["cargo", "build", "--quiet", "--locked", "-p", "syntavra-cli"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=180,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"Rust primitive build failed ({completed.returncode}): {completed.stderr.strip()}"
        )
    name = "syntavra-rs.exe" if os.name == "nt" else "syntavra-rs"
    binary = ROOT / "target" / "debug" / name
    if not binary.is_file():
        raise RuntimeError(f"Rust primitive binary was not produced: {binary}")
    return binary


def _invoke(binary: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(binary), *arguments],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )


def _rust_json(binary: Path, *arguments: str) -> dict[str, Any]:
    completed = _invoke(binary, *arguments)
    if completed.returncode != 0:
        raise RuntimeError(
            f"Rust primitive command failed ({completed.returncode}): "
            f"{' '.join(arguments)}: {completed.stderr.strip()}"
        )
    value = json.loads(completed.stdout)
    if not isinstance(value, dict):
        raise RuntimeError("Rust primitive output must be a JSON object")
    return value


def verify() -> dict[str, Any]:
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    if fixture.get("schema_version") != 1:
        raise RuntimeError("unsupported primitive fixture schema")
    binary = _build_rust()
    checks: list[dict[str, Any]] = []

    for row in fixture.get("sha256", []):
        payload = bytes.fromhex(str(row["input_hex"]))
        expected = str(row["digest"])
        python_digest = sha256_hex(payload)
        rust = _rust_json(binary, "primitive", "sha256", payload.hex())
        ok = python_digest == expected and rust.get("digest") == expected
        checks.append({"group": "sha256", "id": row["id"], "ok": ok})

    for row in fixture.get("canonical_manifest", []):
        path = str(row["path"])
        payload = bytes.fromhex(str(row["input_hex"]))
        expected_bytes = bytes.fromhex(str(row["canonical_hex"]))
        expected_digest = str(row["digest"])
        python_bytes = canonical_manifest_bytes(path, payload)
        python_digest = manifest_digest_hex(path, payload)
        rust_canonical = _rust_json(
            binary,
            "primitive",
            "canonicalize",
            path,
            payload.hex(),
        )
        rust_digest = _rust_json(
            binary,
            "primitive",
            "manifest-digest",
            path,
            payload.hex(),
        )
        ok = (
            python_bytes == expected_bytes
            and python_digest == expected_digest
            and rust_canonical.get("canonical_hex") == expected_bytes.hex()
            and rust_canonical.get("digest") == expected_digest
            and rust_digest.get("digest") == expected_digest
        )
        checks.append({"group": "canonical_manifest", "id": row["id"], "ok": ok})

    for row in fixture.get("paths", []):
        raw = str(row["input"])
        expected_error = row.get("error")
        if expected_error:
            python_error = ""
            try:
                normalize_repository_path(raw)
            except CanonicalPathError as exc:
                python_error = exc.code
            completed = _invoke(binary, "primitive", "normalize-path", raw)
            ok = (
                python_error == expected_error
                and completed.returncode == 2
                and str(expected_error) in completed.stderr
            )
        else:
            expected = str(row["normalized"])
            rust = _rust_json(binary, "primitive", "normalize-path", raw)
            ok = normalize_repository_path(raw) == expected and rust.get("path") == expected
        checks.append({"group": "path", "id": row["id"], "ok": ok})

    failed = [row for row in checks if not row["ok"]]
    if failed:
        raise RuntimeError(f"Python/Rust primitive parity failed: {failed}")
    return {
        "ok": True,
        "phase": "R5",
        "reference_engine": "python",
        "candidate_engine": "rust",
        "fixture": FIXTURE.relative_to(ROOT).as_posix(),
        "checks": len(checks),
        "groups": {
            "sha256": len(fixture.get("sha256", [])),
            "canonical_manifest": len(fixture.get("canonical_manifest", [])),
            "paths": len(fixture.get("paths", [])),
        },
        "claim": "RUST_CANONICAL_PRIMITIVE_PARITY_PROVEN_R5_FIXTURES",
    }


def main() -> int:
    print(json.dumps(verify(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
