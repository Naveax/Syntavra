#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "MANIFEST.sha256"
GENERATED_FILES = {
    "fusion-release-smoke.json",
    "release-smoke.json",
    "platform-registry.json",
    "native-dry-run.json",
}
TRANSIENT_PARTS = {
    "__pycache__",
    ".pytest_cache",
    "node_modules",
    ".venv",
    "venv",
    ".tox",
    ".nox",
    ".mypy_cache",
    ".ruff_cache",
}
NON_TEXT_PREFIX = ("benchmarks", "results", "real-tasks")


def is_generated_path(relative: Path) -> bool:
    parts = relative.parts
    return (
        bool(parts) and parts[0] in {".git", ".syntavra", "build", "dist"}
    ) or any(
        part in TRANSIENT_PARTS or part.endswith(".egg-info")
        for part in parts
    )


def canonical_manifest_bytes(relative: Path, data: bytes) -> bytes:
    """Return repository-canonical bytes for deterministic hashing.

    Git stores normal text files with LF according to .gitattributes.
    Normalize UTF-8 text before hashing so a Windows CRLF working tree
    produces the same manifest as Linux and macOS clean checkouts.
    """

    if tuple(relative.parts[:3]) == NON_TEXT_PREFIX:
        return data

    if b"\0" in data:
        return data

    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return data

    return data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def canonical_digest(path: Path) -> str:
    relative = path.relative_to(ROOT)
    canonical = canonical_manifest_bytes(relative, path.read_bytes())
    return hashlib.sha256(canonical).hexdigest()


def candidates() -> list[Path]:
    rows: list[Path] = []

    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue

        relative = path.relative_to(ROOT)

        if is_generated_path(relative):
            continue

        if (
            path.name == "MANIFEST.sha256"
            and path.parent == ROOT
        ) or path.name in GENERATED_FILES or path.suffix == ".pyc":
            continue

        rows.append(path)

    return sorted(
        rows,
        key=lambda value: value.relative_to(ROOT).as_posix(),
    )


def render() -> str:
    return "".join(
        f"{canonical_digest(path)}  "
        f"{path.relative_to(ROOT).as_posix()}\n"
        for path in candidates()
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Regenerate the exact Syntavra repository SHA-256 manifest."
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="fail when MANIFEST.sha256 is stale",
    )
    args = parser.parse_args()

    expected = render()
    current = (
        MANIFEST.read_text(encoding="utf-8")
        if MANIFEST.is_file()
        else ""
    )

    if args.check:
        if current != expected:
            current_lines = set(current.splitlines())
            expected_lines = set(expected.splitlines())

            print("MANIFEST.sha256 is stale")

            for line in sorted(current_lines - expected_lines)[:20]:
                print(f"stale-or-missing: {line}")

            for line in sorted(expected_lines - current_lines)[:20]:
                print(f"new-or-changed: {line}")

            return 1

        print(f"MANIFEST.sha256 verified: {len(candidates())} files")
        return 0

    if current == expected:
        print(f"MANIFEST.sha256 already current: {len(candidates())} files")
        return 0

    MANIFEST.write_text(
        expected,
        encoding="utf-8",
        newline="\n",
    )
    print(f"MANIFEST.sha256 refreshed: {len(candidates())} files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
