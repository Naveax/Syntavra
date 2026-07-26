#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from syntavra_runtime.state_snapshot_contract import (
    StateInspectionError,
    inspect_state_root,
    project_id_for_root,
)

ROOT = Path(__file__).resolve().parents[1]


def _rust_json(*arguments: str) -> dict[str, Any]:
    completed = subprocess.run(
        [
            "cargo",
            "run",
            "--quiet",
            "--locked",
            "-p",
            "syntavra-cli",
            "--",
            *arguments,
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"Rust engine command failed ({completed.returncode}): {completed.stderr.strip()}"
        )
    value = json.loads(completed.stdout)
    if not isinstance(value, dict):
        raise RuntimeError("Rust engine output must be a JSON object")
    return value


def _rust_error(*arguments: str) -> str:
    completed = subprocess.run(
        [
            "cargo",
            "run",
            "--quiet",
            "--locked",
            "-p",
            "syntavra-cli",
            "--",
            *arguments,
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if completed.returncode == 0:
        raise RuntimeError("Rust engine unexpectedly accepted invalid R8 fixture")
    return completed.stderr.splitlines()[0].strip()


def _tree_snapshot(root: Path) -> list[tuple[str, int, int, bytes | None]]:
    rows: list[tuple[str, int, int, bytes | None]] = []
    for path in sorted(root.rglob("*")):
        metadata = path.lstat()
        payload = path.read_bytes() if path.is_file() and not path.is_symlink() else None
        rows.append(
            (
                path.relative_to(root).as_posix(),
                metadata.st_mode,
                metadata.st_mtime_ns,
                payload,
            )
        )
    return rows


def _populate(root: Path) -> None:
    state = root / ".syntavra"
    state.mkdir()
    (state / "config.toml").write_text("mode = \"safe\"\n", encoding="utf-8")
    (state / "engine.json").write_text('{"engine":"python"}\n', encoding="utf-8")
    (state / "pre-release").mkdir()
    (state / "runtime-v3").mkdir()


def _valid_case(root: Path, name: str) -> dict[str, Any]:
    project_id = project_id_for_root(root)
    before = _tree_snapshot(root)
    python_value = inspect_state_root(root, expected_project_id=project_id)
    rust_value = _rust_json("state", "inspect", project_id, str(root))
    after = _tree_snapshot(root)
    if python_value != rust_value:
        raise RuntimeError(f"R8 valid parity failed: {name}")
    if before != after:
        raise RuntimeError(f"R8 inspection mutated fixture: {name}")
    return python_value


def _invalid_case(root: Path, expected_project_id: str, error: str, name: str) -> None:
    try:
        inspect_state_root(root, expected_project_id=expected_project_id)
    except StateInspectionError as exc:
        python_error = exc.code
    else:
        raise RuntimeError(f"Python accepted invalid R8 fixture: {name}")
    rust_error = _rust_error("state", "inspect", expected_project_id, str(root))
    if python_error != error or rust_error != error:
        raise RuntimeError(
            f"R8 invalid parity failed: {name}: python={python_error!r} rust={rust_error!r}"
        )


def verify() -> dict[str, Any]:
    valid: list[str] = []
    invalid: list[str] = []

    with tempfile.TemporaryDirectory(prefix="syntavra-r8-empty-") as directory:
        root = Path(directory)
        value = _valid_case(root, "empty-state-root")
        if [row["observed_kind"] for row in value["paths"]] != ["missing"] * 5:
            raise RuntimeError("R8 empty-state representation drifted")
        valid.append("empty-state-root")

    with tempfile.TemporaryDirectory(prefix="syntavra-r8-populated-") as directory:
        root = Path(directory)
        _populate(root)
        value = _valid_case(root, "populated-known-paths")
        if value["mutation"] != {"filesystem": False, "database_opened": False}:
            raise RuntimeError("R8 mutation boundary drifted")
        valid.append("populated-known-paths")

    with tempfile.TemporaryDirectory(prefix="syntavra-r8-mismatch-") as directory:
        root = Path(directory)
        wrong = "0" * 64
        if project_id_for_root(root) == wrong:
            wrong = "1" * 64
        _invalid_case(root, wrong, "STATE_PROJECT_MISMATCH", "project-mismatch")
        invalid.append("project-mismatch")

    with tempfile.TemporaryDirectory(prefix="syntavra-r8-kind-") as directory:
        root = Path(directory)
        state = root / ".syntavra"
        state.mkdir()
        (state / "config.toml").mkdir()
        _invalid_case(
            root,
            project_id_for_root(root),
            "STATE_PATH_KIND_MISMATCH",
            "known-path-kind-mismatch",
        )
        invalid.append("known-path-kind-mismatch")

    with tempfile.TemporaryDirectory(prefix="syntavra-r8-size-") as directory:
        root = Path(directory)
        state = root / ".syntavra"
        state.mkdir()
        (state / "config.toml").write_bytes(b"x" * (1024 * 1024 + 1))
        _invalid_case(
            root,
            project_id_for_root(root),
            "STATE_FILE_SIZE_LIMIT",
            "bounded-file-read",
        )
        invalid.append("bounded-file-read")

    with tempfile.TemporaryDirectory(prefix="syntavra-r8-link-") as directory:
        root = Path(directory)
        target = root / "target"
        target.mkdir()
        try:
            os.symlink(target, root / ".syntavra", target_is_directory=True)
        except (OSError, NotImplementedError):
            symlink_checked = False
        else:
            _invalid_case(
                root,
                project_id_for_root(root),
                "STATE_PATH_SYMLINK",
                "state-root-symlink",
            )
            invalid.append("state-root-symlink")
            symlink_checked = True

    return {
        "ok": True,
        "phase": "R8",
        "valid": valid,
        "invalid": invalid,
        "symlink_checked": symlink_checked,
        "claim": "RUST_STATE_ROOT_READ_PARITY_PROVEN_R8_FIXTURES",
        "boundaries": {
            "filesystem_state_reads": True,
            "filesystem_mutation": False,
            "database_access": False,
            "recursive_directory_read": False,
        },
    }


def main() -> int:
    print(json.dumps(verify(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
