#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
READINESS_PATH = ROOT / "release" / "publish-readiness.json"


class CandidatePlanError(RuntimeError):
    pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _artifact_row(root: Path, path: Path) -> dict[str, Any]:
    return {
        "path": path.relative_to(root).as_posix(),
        "bytes": path.stat().st_size,
        "sha256": _sha256(path),
    }


def _collect(root: Path, relative: str, suffixes: tuple[str, ...]) -> list[dict[str, Any]]:
    directory = root / relative
    if not directory.is_dir():
        raise CandidatePlanError(f"missing artifact directory: {relative}")
    paths = sorted(
        path
        for path in directory.iterdir()
        if path.is_file() and any(path.name.endswith(suffix) for suffix in suffixes)
    )
    return [_artifact_row(root, path) for path in paths]


def _require_single(label: str, rows: list[dict[str, Any]]) -> None:
    if len(rows) != 1:
        raise CandidatePlanError(f"{label} artifact count must be exactly 1, observed {len(rows)}")


def _git_head() -> str:
    proc = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    return proc.stdout.strip() if proc.returncode == 0 else ""


def build_plan(artifact_root: Path, *, exact_head: str) -> dict[str, Any]:
    readiness = json.loads(READINESS_PATH.read_text(encoding="utf-8"))
    if readiness.get("version") != "0.0.1" or readiness.get("channel") != "pre-release":
        raise CandidatePlanError("release identity is not locked to 0.0.1 pre-release")

    target_names = (
        "python",
        "npm",
        "npm_sdk",
        "vscode",
        "native",
        "legacy_native_companion",
    )
    for name in target_names:
        target = readiness.get(name)
        if not isinstance(target, dict):
            raise CandidatePlanError(f"missing readiness target: {name}")
        if target.get("published") is not False:
            raise CandidatePlanError(f"target unexpectedly claims published state: {name}")

    python_rows = _collect(artifact_root, "python", (".whl", ".tar.gz"))
    if not any(row["path"].endswith(".whl") for row in python_rows):
        raise CandidatePlanError("Python wheel artifact missing")
    if not any(row["path"].endswith(".tar.gz") for row in python_rows):
        raise CandidatePlanError("Python source archive artifact missing")

    npm_rows = _collect(artifact_root, "npm-installer", (".tgz",))
    sdk_rows = _collect(artifact_root, "npm-sdk", (".tgz",))
    vscode_rows = _collect(artifact_root, "vscode", (".vsix",))
    legacy_rows = _collect(artifact_root, "rust-legacy", (".crate",))
    _require_single("npm installer", npm_rows)
    _require_single("npm sdk", sdk_rows)
    _require_single("VS Code", vscode_rows)
    _require_single("legacy native companion", legacy_rows)

    rust_rows = _collect(artifact_root, "rust-production", (".crate",))
    rust_names = {Path(row["path"]).name for row in rust_rows}
    for required in ("syntavra-contracts-0.0.1.crate", "syntavra-core-0.0.1.crate"):
        if required not in rust_names:
            raise CandidatePlanError(f"required Rust crate artifact missing: {required}")

    rust_state_path = artifact_root / "rust-production-state.json"
    if not rust_state_path.is_file():
        raise CandidatePlanError("missing rust-production-state.json")
    rust_state = json.loads(rust_state_path.read_text(encoding="utf-8"))
    if rust_state.get("ok") is not True:
        raise CandidatePlanError("Rust production publication graph is not ready for planning")
    if rust_state.get("publish_order") != ["syntavra-contracts", "syntavra-core", "syntavra-cli"]:
        raise CandidatePlanError("Rust publication order drift")
    if rust_state.get("registry_publication_performed") is not False:
        raise CandidatePlanError("Rust state unexpectedly claims registry publication")
    if rust_state.get("package_state") not in {
        "package-ready",
        "registry-dependency-publication-required",
    }:
        raise CandidatePlanError(f"unexpected Rust package state: {rust_state.get('package_state')}")

    if not exact_head or len(exact_head) != 40 or any(ch not in "0123456789abcdef" for ch in exact_head.lower()):
        raise CandidatePlanError(f"invalid exact head: {exact_head!r}")

    receipt_slots = {
        "pypi": None,
        "npm_installer": None,
        "npm_sdk": None,
        "vscode_marketplace": None,
        "crates_io": {
            "syntavra-contracts": None,
            "syntavra-core": None,
            "syntavra-cli": None,
        },
        "legacy_native_companion": None,
    }

    plan = {
        "schema_version": 1,
        "product": "Syntavra",
        "version": "0.0.1",
        "channel": "pre-release",
        "exact_head": exact_head,
        "publication_performed": False,
        "claim_boundary": "REGISTRY_PUBLICATION_NOT_PERFORMED",
        "readiness_claim_boundary": readiness["claim_boundary"],
        "targets": {
            "python": {
                "registry": "pypi",
                "readiness": readiness["python"],
                "artifacts": python_rows,
            },
            "npm": {
                "registry": "npm",
                "readiness": readiness["npm"],
                "artifacts": npm_rows,
            },
            "npm_sdk": {
                "registry": "npm",
                "readiness": readiness["npm_sdk"],
                "artifacts": sdk_rows,
            },
            "vscode": {
                "registry": "vscode-marketplace",
                "readiness": readiness["vscode"],
                "artifacts": vscode_rows,
            },
            "native": {
                "registry": "crates.io",
                "readiness": readiness["native"],
                "artifacts": rust_rows,
                "package_state": rust_state,
            },
            "legacy_native_companion": {
                "registry": "crates.io",
                "readiness": readiness["legacy_native_companion"],
                "artifacts": legacy_rows,
            },
        },
        "registry_receipts": receipt_slots,
    }
    return plan


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact-root", required=True)
    parser.add_argument("--exact-head")
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)

    plan = build_plan(
        Path(args.artifact_root).resolve(),
        exact_head=args.exact_head or _git_head(),
    )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(plan, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(plan, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
