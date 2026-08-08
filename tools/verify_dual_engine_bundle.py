#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
from pathlib import Path

MAX_FILE_BYTES = 512 * 1024 * 1024


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _regular(path: Path) -> bool:
    metadata = path.lstat()
    return stat.S_ISREG(metadata.st_mode) and not stat.S_ISLNK(metadata.st_mode)


def verify(
    *, bundle_dir: Path, platform: str, architecture: str, extension: str
) -> dict[str, object]:
    root = bundle_dir.resolve(strict=True)
    expected = {
        "selector": root / "bin" / f"syntavra{extension}",
        "rust_engine": root / "bin" / f"syntavra-rs{extension}",
        "rust_contract_runtime": root / "bin" / f"syntavra-full-parity{extension}",
        "dual_engine_contract": root / "contracts" / "dual-engine-public-surface-v2.json",
    }
    wheels = sorted((root / "python").glob("syntavra_runtime-0.0.1-*.whl"))
    if len(wheels) != 1:
        raise RuntimeError(f"bundle requires exactly one Python engine wheel, got {wheels!r}")
    expected["python_engine_wheel"] = wheels[0]

    files: dict[str, dict[str, object]] = {}
    for role, path in expected.items():
        if not path.is_file() or not _regular(path):
            raise RuntimeError(f"bundle role is missing or unsafe: {role}: {path}")
        size = path.stat().st_size
        if size <= 0 or size > MAX_FILE_BYTES:
            raise RuntimeError(f"bundle file size is invalid: {role}: {size}")
        relative = path.relative_to(root).as_posix()
        files[role] = {
            "path": relative,
            "bytes": size,
            "sha256": _sha256(path),
        }

    contract = json.loads(expected["dual_engine_contract"].read_text(encoding="utf-8"))
    if contract.get("policy", {}).get("one_install_contains_python_and_rust") is not True:
        raise RuntimeError("dual-engine bundle policy is not enabled")
    if contract.get("policy", {}).get("hidden_fallback_forbidden") is not True:
        raise RuntimeError("dual-engine bundle must forbid hidden fallback")

    return {
        "ok": True,
        "schema_version": 1,
        "product": "Syntavra",
        "product_version": "0.0.1",
        "release_channel": "pre-release",
        "platform": platform,
        "architecture": architecture,
        "contains_python_engine": True,
        "contains_rust_engine": True,
        "contains_native_selector": True,
        "hidden_fallback": False,
        "full_dual_engine_parity": contract.get("claim")
        == "FULL_DUAL_ENGINE_PARITY_PROVEN",
        "files": files,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify one Syntavra dual-engine bundle.")
    parser.add_argument("--bundle-dir", type=Path, required=True)
    parser.add_argument("--platform", required=True)
    parser.add_argument("--architecture", required=True)
    parser.add_argument("--extension", default="")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = verify(
        bundle_dir=args.bundle_dir,
        platform=args.platform,
        architecture=args.architecture,
        extension=args.extension,
    )
    rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8", newline="\n")
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
