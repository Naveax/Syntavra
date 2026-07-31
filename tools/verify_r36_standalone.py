#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from pathlib import Path

FORBIDDEN_PARTS = ("site-packages", "__pycache__")
FORBIDDEN_SUFFIXES = (".py", ".pyc", ".pyd")


def canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def validate_files(files: list[Path]) -> list[str]:
    names = sorted(path.name for path in files)
    for name in names:
        lowered = name.lower()
        if lowered.endswith(FORBIDDEN_SUFFIXES) or any(part in lowered for part in FORBIDDEN_PARTS):
            raise RuntimeError(f"forbidden Python payload in standalone distribution: {name}")
        if lowered in {"python", "python.exe", "python3", "python3.exe"}:
            raise RuntimeError(f"Python executable is forbidden: {name}")
    return names


def smoke(binary: Path) -> dict[str, object]:
    environment = os.environ.copy()
    environment["PYTHONHOME"] = str(binary.parent / "missing-python-home")
    environment["PYTHONPATH"] = str(binary.parent / "missing-python-path")
    expected = "standalone-rust"
    value_hex = expected.encode("utf-8").hex()
    completed = subprocess.run(
        [str(binary), "--child", "echo", value_hex],
        capture_output=True,
        text=True,
        env=environment,
        timeout=15,
        check=False,
    )
    if completed.returncode != 0 or completed.stdout != expected:
        raise RuntimeError(
            f"standalone smoke failed: code={completed.returncode} stdout={completed.stdout!r} stderr={completed.stderr!r}"
        )
    return {
        "exit_code": completed.returncode,
        "stdout_sha256": hashlib.sha256(completed.stdout.encode()).hexdigest(),
        "python_environment_invalidated": True,
    }


def verify(binary: Path, package_dir: Path, platform: str, architecture: str) -> dict[str, object]:
    binary = binary.resolve(strict=True)
    package_dir = package_dir.resolve(strict=True)
    files = [path for path in package_dir.rglob("*") if path.is_file()]
    names = validate_files(files)
    binary_hash = sha256(binary)
    manifest_body = {
        "schema_version": 1,
        "contract": "syntavra-rust-standalone-v1",
        "product": "Syntavra",
        "product_version": "0.0.1",
        "release_channel": "pre-release",
        "platform": platform,
        "architecture": architecture,
        "binary": binary.name,
        "binary_sha256": binary_hash,
        "files": names,
        "python_required": False,
        "forbidden_python_files": [],
        "smoke": smoke(binary),
    }
    return {
        **manifest_body,
        "distribution_sha256": hashlib.sha256(canonical(manifest_body)).hexdigest(),
        "claim": "RUST_STANDALONE_DISTRIBUTION_PROVEN_R36",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", required=True, type=Path)
    parser.add_argument("--package-dir", required=True, type=Path)
    parser.add_argument("--platform", required=True)
    parser.add_argument("--architecture", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    result = verify(args.binary, args.package_dir, args.platform, args.architecture)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
