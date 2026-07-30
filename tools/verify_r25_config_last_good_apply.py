#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from syntavra_runtime.config_contract import encode_config_wire
from syntavra_runtime.config_last_good_apply import (
    CLAIM,
    ConfigLastGoodApplyError,
    apply_config_last_good,
    canonical_apply_json,
)
from syntavra_runtime.state_snapshot_contract import project_id_for_root

ROOT = Path(__file__).resolve().parents[1]


def _target(project: Path) -> Path:
    return project / ".syntavra" / "pre-release" / "config-last-good.json"


def _state_root(project: Path) -> Path:
    return project / ".syntavra"


def _rust(project: Path, project_id: str, wire: bytes) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "cargo",
            "run",
            "--quiet",
            "--locked",
            "-p",
            "syntavra-cli",
            "--bin",
            "syntavra-config-last-good-apply",
            "--",
            project_id,
            str(project),
            wire.hex(),
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=180,
    )


def _python_error(project: Path, project_id: str, wire: bytes) -> str:
    try:
        apply_config_last_good(
            project_root=project,
            expected_project_id=project_id,
            config_wire=wire,
        )
    except ConfigLastGoodApplyError as exc:
        return exc.code
    raise RuntimeError("Python atomic apply unexpectedly succeeded")


def _rust_error(project: Path, project_id: str, wire: bytes) -> str:
    completed = _rust(project, project_id, wire)
    if completed.returncode == 0:
        raise RuntimeError("Rust atomic apply unexpectedly succeeded")
    return completed.stderr.splitlines()[0].strip()


def _reset(project: Path) -> None:
    shutil.rmtree(_state_root(project), ignore_errors=True)


def _assert_write_parity(project: Path, project_id: str, wire: bytes) -> dict[str, object]:
    expected = apply_config_last_good(
        project_root=project,
        expected_project_id=project_id,
        config_wire=wire,
    )
    expected_json = canonical_apply_json(expected)
    expected_bytes = _target(project).read_bytes()
    _reset(project)

    completed = _rust(project, project_id, wire)
    if completed.returncode != 0:
        raise RuntimeError(
            f"Rust atomic apply failed ({completed.returncode}): {completed.stderr.strip()}"
        )
    candidate_json = completed.stdout.strip()
    candidate_bytes = _target(project).read_bytes()
    if candidate_json != expected_json:
        raise RuntimeError("Python/Rust atomic apply result bytes differ")
    if candidate_bytes != expected_bytes:
        raise RuntimeError("Python/Rust last-good file bytes differ")
    if str(project) in candidate_json or wire.hex() in candidate_json:
        raise RuntimeError("atomic apply result leaked excluded input material")
    if (project / ".syntavra" / "pre-release" / "config-last-good.lock").exists():
        raise RuntimeError("Rust atomic apply left a lock file")
    if tuple(_target(project).parent.glob(".config-last-good.*.tmp")):
        raise RuntimeError("Rust atomic apply left a temporary file")
    return json.loads(candidate_json)


def verify() -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="syntavra-r25-config-apply-") as directory:
        project = Path(directory) / "project"
        project.mkdir()
        project_id = project_id_for_root(project)
        valid_wire = encode_config_wire(
            [
                {
                    "project": {
                        "runtime": {"profile": "compact"},
                        "routing": {"budget_bytes": 4096},
                    }
                }
            ]
        )
        written = _assert_write_parity(project, project_id, valid_wire)
        if written["action"] != "written" or written["claim"] != CLAIM:
            raise RuntimeError("atomic write result contract mismatch")

        python_unchanged = apply_config_last_good(
            project_root=project,
            expected_project_id=project_id,
            config_wire=valid_wire,
        )
        rust_unchanged = _rust(project, project_id, valid_wire)
        if rust_unchanged.returncode != 0:
            raise RuntimeError(rust_unchanged.stderr.strip())
        if rust_unchanged.stdout.strip() != canonical_apply_json(python_unchanged):
            raise RuntimeError("unchanged result parity failed")

        _reset(project)
        fallback_wire = encode_config_wire(
            [
                {"project": {"runtime": {"profile": "audit"}}},
                {"project": {"runtime": {"profile": "invalid-profile"}}},
            ]
        )
        retained = apply_config_last_good(
            project_root=project,
            expected_project_id=project_id,
            config_wire=fallback_wire,
        )
        rust_retained = _rust(project, project_id, fallback_wire)
        if rust_retained.returncode != 0:
            raise RuntimeError(rust_retained.stderr.strip())
        if rust_retained.stdout.strip() != canonical_apply_json(retained):
            raise RuntimeError("retain-existing result parity failed")
        if _state_root(project).exists():
            raise RuntimeError("retain-existing created state")

        parent = project / ".syntavra" / "pre-release"
        parent.mkdir(parents=True)
        lock = parent / "config-last-good.lock"
        lock.write_text("owned\n", encoding="utf-8")
        python_code = _python_error(project, project_id, valid_wire)
        lock.unlink()
        lock.write_text("owned\n", encoding="utf-8")
        rust_code = _rust_error(project, project_id, valid_wire)
        if python_code != rust_code or python_code != "CONFIG_LAST_GOOD_APPLY_LOCK_BUSY":
            raise RuntimeError("lock failure parity failed")
        lock.unlink()

        symlink_checked = False
        if os.name != "nt":
            outside = Path(directory) / "outside.json"
            outside.write_text("outside", encoding="utf-8")
            _target(project).symlink_to(outside)
            python_code = _python_error(project, project_id, valid_wire)
            _target(project).unlink()
            _target(project).symlink_to(outside)
            rust_code = _rust_error(project, project_id, valid_wire)
            if python_code != rust_code or python_code != "CONFIG_LAST_GOOD_APPLY_TARGET_SYMLINK":
                raise RuntimeError("target symlink failure parity failed")
            if outside.read_text(encoding="utf-8") != "outside":
                raise RuntimeError("target symlink fixture mutated outside file")
            symlink_checked = True

        return {
            "ok": True,
            "phase": "R25",
            "command": "config.last-good.lifecycle.apply",
            "stage": "shadow",
            "write_action": written["action"],
            "unchanged_action": python_unchanged["action"],
            "retain_action": retained["action"],
            "project_binding": True,
            "exclusive_lock": True,
            "atomic_replace": True,
            "file_sync": True,
            "symlink_fixture": symlink_checked,
            "public_cli_exposed": False,
            "fallback_policy": "none-after-rust-start",
            "claim": CLAIM,
            "full_product_parity": "FULL_PARITY_NOT_PROVEN",
        }


def main() -> int:
    print(json.dumps(verify(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
