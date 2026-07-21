#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "MANIFEST.sha256"
GENERATED_FILES = {"fusion-release-smoke.json", "release-smoke.json", "platform-registry.json", "native-dry-run.json"}


def is_generated_path(relative: Path) -> bool:
    parts = relative.parts
    return (
        bool(parts) and parts[0] in {".git", ".signalcore", "build", "dist"}
    ) or any(part in {"__pycache__", ".pytest_cache"} or part.endswith(".egg-info") for part in parts)


def candidates() -> list[Path]:
    rows: list[Path] = []
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(ROOT)
        if is_generated_path(relative):
            continue
        if (path.name == "MANIFEST.sha256" and path.parent == ROOT) or path.name in GENERATED_FILES or path.suffix == ".pyc":
            continue
        rows.append(path)
    return sorted(rows, key=lambda value: value.relative_to(ROOT).as_posix())


def render() -> str:
    return "".join(
        f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.relative_to(ROOT).as_posix()}\n"
        for path in candidates()
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Regenerate the exact SignalCore repository SHA-256 manifest.")
    parser.add_argument("--check", action="store_true", help="fail when MANIFEST.sha256 is stale")
    args = parser.parse_args()
    expected = render()
    current = MANIFEST.read_text(encoding="utf-8") if MANIFEST.is_file() else ""
    if args.check:
        if current != expected:
            print("MANIFEST.sha256 is stale")
            return 1
        print(f"MANIFEST.sha256 verified: {len(candidates())} files")
        return 0
    if current == expected:
        print(f"MANIFEST.sha256 already current: {len(candidates())} files")
        return 0
    MANIFEST.write_text(expected, encoding="utf-8", newline="\n")
    print(f"MANIFEST.sha256 refreshed: {len(candidates())} files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
