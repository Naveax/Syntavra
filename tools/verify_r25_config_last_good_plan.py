#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path

from syntavra_runtime.config_contract import encode_config_wire
from syntavra_runtime.config_last_good_plan import (
    CLAIM,
    ConfigLastGoodPlanError,
    canonical_plan_json,
    config_last_good_plan,
)
from syntavra_runtime.state_snapshot_contract import project_id_for_root

ROOT = Path(__file__).resolve().parents[1]


def _tree(root: Path) -> tuple[tuple[str, int, int], ...]:
    rows: list[tuple[str, int, int]] = []
    for path in sorted(root.rglob("*")):
        metadata = path.lstat()
        rows.append(
            (
                path.relative_to(root).as_posix(),
                metadata.st_size,
                metadata.st_mtime_ns,
            )
        )
    return tuple(rows)


def _rust_plan(project: Path, project_id: str, wire: bytes) -> str:
    completed = subprocess.run(
        [
            "cargo",
            "run",
            "--quiet",
            "--locked",
            "-p",
            "syntavra-cli",
            "--bin",
            "syntavra-config-lifecycle-plan",
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
    if completed.returncode != 0:
        raise RuntimeError(
            f"Rust config lifecycle plan failed ({completed.returncode}): "
            f"{completed.stderr.strip()}"
        )
    return completed.stdout.strip()


def _rust_error(project: Path, project_id: str, wire: bytes) -> str:
    completed = subprocess.run(
        [
            "cargo",
            "run",
            "--quiet",
            "--locked",
            "-p",
            "syntavra-cli",
            "--bin",
            "syntavra-config-lifecycle-plan",
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
    if completed.returncode == 0:
        raise RuntimeError("Rust config lifecycle plan unexpectedly succeeded")
    return completed.stderr.splitlines()[0].strip()


def _python_error(project: Path, project_id: str, wire: bytes) -> str:
    try:
        config_last_good_plan(
            project_root=project,
            expected_project_id=project_id,
            config_wire=wire,
        )
    except ConfigLastGoodPlanError as exc:
        return exc.code
    raise RuntimeError("Python config lifecycle plan unexpectedly succeeded")


def _assert_success_parity(project: Path, project_id: str, wire: bytes) -> dict[str, object]:
    before = _tree(project)
    expected_object = config_last_good_plan(
        project_root=project,
        expected_project_id=project_id,
        config_wire=wire,
    )
    expected_json = canonical_plan_json(expected_object)
    candidate_json = _rust_plan(project, project_id, wire)
    after = _tree(project)

    if candidate_json != expected_json:
        raise RuntimeError("Python/Rust config lifecycle plan bytes differ")
    candidate_object = json.loads(candidate_json)
    if candidate_object != expected_object:
        raise RuntimeError("Python/Rust config lifecycle plan objects differ")
    if before != after:
        raise RuntimeError("config lifecycle planning mutated the project tree")
    if (project / ".syntavra").exists():
        raise RuntimeError("config lifecycle planning created product state")
    rendered = candidate_json
    if str(project) in rendered or wire.hex() in rendered or "loaded_at" in rendered:
        raise RuntimeError("config lifecycle plan leaked excluded input material")
    return candidate_object


def verify() -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="syntavra-r25-config-plan-") as directory:
        root = Path(directory)
        project = root / "project"
        project.mkdir()
        project_id = project_id_for_root(project)

        valid_wire = encode_config_wire(
            [
                {
                    "user": {"provider": {"timeout_seconds": 90.0}},
                    "project": {
                        "runtime": {"profile": "compact"},
                        "routing": {"budget_bytes": 4096},
                    },
                    "environment": {
                        "SYNTAVRA_CFG__PROVIDER__CREDENTIAL_REF": "secret://provider/key"
                    },
                }
            ]
        )
        valid = _assert_success_parity(project, project_id, valid_wire)
        if valid["decision"] != "write" or valid["fallback_used"] is not False:
            raise RuntimeError("valid config did not produce a write plan")

        fallback_wire = encode_config_wire(
            [
                {"project": {"runtime": {"profile": "audit"}}},
                {"project": {"runtime": {"profile": "invalid-profile"}}},
            ]
        )
        fallback = _assert_success_parity(project, project_id, fallback_wire)
        if fallback["decision"] != "retain-existing" or fallback["fallback_used"] is not True:
            raise RuntimeError("invalid current config did not retain prior last-good")

        for scope in ("session", "task"):
            wire = encode_config_wire([{scope: {"runtime": {"profile": "terse"}}}])
            python_code = _python_error(project, project_id, wire)
            rust_code = _rust_error(project, project_id, wire)
            if python_code != rust_code or python_code != "CONFIG_LIFECYCLE_EPHEMERAL_SCOPE_FORBIDDEN":
                raise RuntimeError(f"ephemeral-scope error parity failed for {scope}")

        mismatch_wire = encode_config_wire([{}])
        python_code = _python_error(project, "0" * 64, mismatch_wire)
        rust_code = _rust_error(project, "0" * 64, mismatch_wire)
        if python_code != rust_code or python_code != "CONFIG_LIFECYCLE_PROJECT_MISMATCH":
            raise RuntimeError("project-mismatch error parity failed")

        symlink_checked = False
        if os.name != "nt":
            alias = root / "project-link"
            alias.symlink_to(project, target_is_directory=True)
            python_code = _python_error(alias, project_id, mismatch_wire)
            rust_code = _rust_error(alias, project_id, mismatch_wire)
            if python_code != rust_code or python_code != "CONFIG_LIFECYCLE_PROJECT_ROOT_SYMLINK":
                raise RuntimeError("project-root symlink error parity failed")
            symlink_checked = True

        return {
            "ok": True,
            "phase": "R25",
            "command": "config.last-good.lifecycle.plan",
            "stage": "shadow",
            "input_format": "R6CFG1",
            "valid_decision": valid["decision"],
            "fallback_decision": fallback["decision"],
            "ephemeral_scopes_forbidden": ["session", "task"],
            "project_binding": True,
            "symlink_fixture": symlink_checked,
            "filesystem_write": False,
            "database_opened": False,
            "apply_authority": "blocked",
            "fallback_policy": "none-after-rust-start",
            "claim": CLAIM,
            "full_product_parity": "FULL_PARITY_NOT_PROVEN",
        }


def main() -> int:
    print(json.dumps(verify(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
