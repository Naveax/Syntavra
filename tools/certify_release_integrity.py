#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Any

from tools.refresh_manifest import GENERATED_FILES, canonical_manifest_bytes, is_generated_path

ROOT = Path(__file__).resolve().parents[1]


def _git(root: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=root,
        text=True,
        encoding="utf-8",
        errors="strict",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout


def _tracked_paths(root: Path) -> list[Path]:
    payload = _git(root, "ls-files", "-z")
    paths: list[Path] = []
    for raw in payload.split("\0"):
        if not raw:
            continue
        relative = Path(raw)
        if relative.as_posix() == "MANIFEST.sha256":
            continue
        if is_generated_path(relative) or relative.name in GENERATED_FILES or relative.suffix == ".pyc":
            continue
        paths.append(relative)
    return sorted(paths, key=lambda value: value.as_posix())


def _dirty_relevant_paths(root: Path) -> list[str]:
    output = _git(root, "status", "--porcelain=v1", "--untracked-files=all")
    dirty: list[str] = []
    for raw in output.splitlines():
        if len(raw) < 4:
            dirty.append(raw)
            continue
        path_text = raw[3:]
        if " -> " in path_text:
            path_text = path_text.split(" -> ", 1)[1]
        relative = Path(path_text)
        if is_generated_path(relative) or relative.name in GENERATED_FILES or relative.suffix == ".pyc":
            continue
        dirty.append(raw)
    return dirty


def render_tracked_manifest(root: Path) -> str:
    rows: list[str] = []
    for relative in _tracked_paths(root):
        path = root / relative
        if path.is_symlink():
            raw = os.readlink(path).encode("utf-8")
        elif path.is_file():
            raw = path.read_bytes()
        else:
            raise RuntimeError(f"tracked path is not readable: {relative.as_posix()}")
        digest = hashlib.sha256(canonical_manifest_bytes(relative, raw)).hexdigest()
        rows.append(f"{digest}  {relative.as_posix()}\n")
    return "".join(rows)


def certify(root: Path = ROOT, *, expected_head: str | None = None) -> dict[str, Any]:
    root = root.resolve()
    failures: list[str] = []
    try:
        head = _git(root, "rev-parse", "HEAD").strip()
        tree = _git(root, "rev-parse", "HEAD^{tree}").strip()
        dirty = _dirty_relevant_paths(root)
        manifest_text = render_tracked_manifest(root)
    except Exception as exc:
        return {
            "schema_version": 1,
            "claim": "EXACT_HEAD_RELEASE_INTEGRITY_NOT_PROVEN",
            "ok": False,
            "score": 0.0,
            "failures": [f"integrity-exception:{type(exc).__name__}:{exc}"],
        }

    if expected_head and head != expected_head:
        failures.append(f"exact-head-mismatch:{head}!={expected_head}")
    if dirty:
        failures.extend(f"dirty:{row}" for row in dirty[:30])

    tracked = _tracked_paths(root)
    tracked_set = {path.as_posix() for path in tracked}
    generated_entries = {
        line.split("  ", 1)[1]
        for line in manifest_text.splitlines()
        if "  " in line
    }
    if tracked_set != generated_entries:
        failures.append("tracked-manifest-inventory-mismatch")

    manifest_sha256 = hashlib.sha256(manifest_text.encode("utf-8")).hexdigest()
    legacy_path = root / "MANIFEST.sha256"
    legacy = legacy_path.read_text(encoding="utf-8") if legacy_path.is_file() else ""
    legacy_matches = legacy == manifest_text
    ok = not failures
    return {
        "schema_version": 1,
        "claim": "EXACT_HEAD_RELEASE_INTEGRITY_PROVEN" if ok else "EXACT_HEAD_RELEASE_INTEGRITY_NOT_PROVEN",
        "claim_boundary": "Release integrity is bound to the exact clean Git HEAD/tree and a deterministically generated SHA-256 manifest of tracked source. The checked-in MANIFEST.sha256 is a legacy snapshot, not the canonical exact-head authority.",
        "ok": ok,
        "score": 9.4 if ok else 4.0,
        "exact_head": head,
        "git_tree": tree,
        "expected_head": expected_head,
        "tracked_source_count": len(tracked),
        "generated_manifest_sha256": manifest_sha256,
        "generated_manifest_bytes": len(manifest_text.encode("utf-8")),
        "committed_legacy_manifest_present": legacy_path.is_file(),
        "committed_legacy_manifest_matches_generated": legacy_matches,
        "dirty_relevant_count": len(dirty),
        "failures": failures,
        "manifest_text": manifest_text,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Certify exact-head release integrity without trusting a stale checked-in snapshot.")
    parser.add_argument("--repo", type=Path, default=ROOT)
    parser.add_argument("--expected-head")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--manifest-output", type=Path)
    parser.add_argument("--require-legacy-manifest-match", action="store_true")
    args = parser.parse_args()

    report = certify(args.repo, expected_head=args.expected_head)
    manifest_text = str(report.pop("manifest_text", ""))
    if args.manifest_output and manifest_text:
        args.manifest_output.parent.mkdir(parents=True, exist_ok=True)
        args.manifest_output.write_text(manifest_text, encoding="utf-8", newline="\n")
    if args.require_legacy_manifest_match and not report.get("committed_legacy_manifest_matches_generated"):
        report["ok"] = False
        report["claim"] = "EXACT_HEAD_RELEASE_INTEGRITY_NOT_PROVEN"
        report.setdefault("failures", []).append("legacy-manifest-stale")
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
