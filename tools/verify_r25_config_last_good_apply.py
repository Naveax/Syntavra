#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Callable

from syntavra_runtime.config_contract import (
    decode_config_wire,
    encode_config_wire,
    resolve_config_phases,
)
from syntavra_runtime.config_last_good_apply import (
    LOCK_RELATIVE_PATH,
    ConfigLastGoodApplyError,
    apply_config_last_good,
    canonical_apply_json,
)
from syntavra_runtime.state_snapshot_contract import project_id_for_root

ROOT = Path(__file__).resolve().parents[1]
BINARY_NAME = "syntavra-config-lifecycle-apply.exe" if os.name == "nt" else "syntavra-config-lifecycle-apply"
BINARY = ROOT / "target" / "debug" / BINARY_NAME


def _build_binary() -> None:
    subprocess.run(
        [
            "cargo",
            "build",
            "--quiet",
            "--locked",
            "--bin",
            "syntavra-config-lifecycle-apply",
        ],
        cwd=ROOT,
        check=True,
    )


def _python_apply(
    project: Path,
    project_id: str,
    wire: bytes,
    *,
    fault: str | None = None,
) -> tuple[bool, str]:
    try:
        result = apply_config_last_good(
            project_root=project,
            expected_project_id=project_id,
            config_wire=wire,
            fault=fault,
        )
    except ConfigLastGoodApplyError as exc:
        return False, exc.code
    return True, canonical_apply_json(result)


def _rust_apply(
    project: Path,
    project_id: str,
    wire: bytes,
    *,
    fault: str | None = None,
) -> tuple[bool, str]:
    command = [str(BINARY), project_id, str(project), wire.hex()]
    if fault is not None:
        command.extend(["--fault", fault])
    completed = subprocess.run(
        command,
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode == 0:
        return True, completed.stdout.strip()
    lines = [line.strip() for line in completed.stderr.splitlines() if line.strip()]
    if not lines:
        raise RuntimeError("Rust apply binary failed without an error code")
    return False, lines[0]


def _state_snapshot(project: Path) -> dict[str, bytes]:
    root = project / ".syntavra"
    if not root.exists():
        return {}
    output: dict[str, bytes] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            output[path.relative_to(project).as_posix()] = path.read_bytes()
    return output


def _restore_state(project: Path, snapshot: dict[str, bytes]) -> None:
    shutil.rmtree(project / ".syntavra", ignore_errors=True)
    for relative, content in snapshot.items():
        path = project / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)


def _compare_single(
    *,
    project: Path,
    project_id: str,
    wire: bytes,
    baseline: dict[str, bytes],
    fault: str | None = None,
) -> dict[str, object]:
    _restore_state(project, baseline)
    python_ok, python_output = _python_apply(project, project_id, wire, fault=fault)
    python_state = _state_snapshot(project)

    _restore_state(project, baseline)
    rust_ok, rust_output = _rust_apply(project, project_id, wire, fault=fault)
    rust_state = _state_snapshot(project)

    if (python_ok, python_output) != (rust_ok, rust_output):
        raise RuntimeError(
            "Python/Rust config last-good apply result mismatch: "
            f"python={(python_ok, python_output)!r}, rust={(rust_ok, rust_output)!r}"
        )
    if python_state != rust_state:
        raise RuntimeError("Python/Rust config last-good apply filesystem state mismatch")
    return {
        "ok": python_ok,
        "output_sha256": hashlib.sha256(python_output.encode("utf-8")).hexdigest(),
        "state_files": sorted(python_state),
        "state_sha256": hashlib.sha256(
            b"".join(
                key.encode("utf-8") + b"\0" + value
                for key, value in sorted(python_state.items())
            )
        ).hexdigest(),
    }


def _compare_crash_recovery(
    *,
    project: Path,
    project_id: str,
    wire: bytes,
    fault: str,
) -> dict[str, object]:
    def run(
        apply: Callable[..., tuple[bool, str]],
    ) -> tuple[tuple[bool, str], tuple[bool, str], dict[str, bytes]]:
        _restore_state(project, {})
        first = apply(project, project_id, wire, fault=fault)
        lock = project / LOCK_RELATIVE_PATH
        if not lock.is_file():
            raise RuntimeError("fault injection did not preserve the transaction lock")
        os.utime(lock, (0, 0))
        second = apply(project, project_id, wire)
        return first, second, _state_snapshot(project)

    python = run(_python_apply)
    rust = run(_rust_apply)
    if python != rust:
        raise RuntimeError("Python/Rust crash-recovery sequence mismatch")
    if python[0][0] is not False or python[1][0] is not True:
        raise RuntimeError("crash-recovery sequence did not fail then recover")
    recovery = json.loads(python[1][1])
    if recovery["lock"]["stale_recovered"] is not True:
        raise RuntimeError("stale transaction lock was not recovered")
    return {
        "fault": fault,
        "fault_code": python[0][1],
        "recovery_action": recovery["result"]["action"],
        "state_files": sorted(python[2]),
    }


def verify() -> dict[str, object]:
    _build_binary()
    with tempfile.TemporaryDirectory(prefix="syntavra-r25-apply-") as directory:
        project = Path(directory) / "project"
        project.mkdir()
        project_id = project_id_for_root(project)
        compact = encode_config_wire(
            [{"project": {"runtime": {"profile": "compact"}, "routing": {"budget_bytes": 4096}}}]
        )
        detailed = encode_config_wire(
            [{"project": {"runtime": {"profile": "detailed"}, "routing": {"budget_bytes": 8192}}}]
        )
        fallback = encode_config_wire(
            [
                {"project": {"runtime": {"profile": "compact"}}},
                {"project": {"runtime": {"profile": "invalid-profile"}}},
            ]
        )

        write = _compare_single(
            project=project,
            project_id=project_id,
            wire=compact,
            baseline={},
        )
        write_baseline = _state_snapshot(project)
        already_current = _compare_single(
            project=project,
            project_id=project_id,
            wire=compact,
            baseline=write_baseline,
        )
        replace = _compare_single(
            project=project,
            project_id=project_id,
            wire=detailed,
            baseline=write_baseline,
        )

        snapshot = dict(resolve_config_phases(decode_config_wire(fallback)))
        snapshot["loaded_at"] = 1.0
        legacy_payload = (
            json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            + "\n"
        ).encode("utf-8")
        legacy_baseline = {
            ".syntavra/pre-release/config-last-good.json": legacy_payload,
        }
        retain = _compare_single(
            project=project,
            project_id=project_id,
            wire=fallback,
            baseline=legacy_baseline,
        )

        lock_payload = (
            json.dumps(
                {
                    "contract_version": 1,
                    "project_id": project_id,
                    "target": ".syntavra/pre-release/config-last-good.json",
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")
        lock_held = _compare_single(
            project=project,
            project_id=project_id,
            wire=compact,
            baseline={".syntavra/pre-release/config-last-good.lock": lock_payload},
        )
        crash_temp = _compare_crash_recovery(
            project=project,
            project_id=project_id,
            wire=compact,
            fault="after-temp-sync",
        )
        crash_replace = _compare_crash_recovery(
            project=project,
            project_id=project_id,
            wire=detailed,
            fault="after-replace",
        )

    return {
        "ok": True,
        "phase": "R25",
        "command": "config.last-good.lifecycle.apply",
        "stage": "bounded-shadow",
        "apply_authority": "bounded-shadow",
        "public_routing": "blocked",
        "fixtures": {
            "write": write,
            "already_current": already_current,
            "replace": replace,
            "retain_legacy": retain,
            "lock_held": lock_held,
            "crash_after_temp_sync": crash_temp,
            "crash_after_replace": crash_replace,
        },
        "claim": "CONFIG_LAST_GOOD_APPLY_PARITY_PROVEN_R25",
        "full_parity_claim": "FULL_PARITY_NOT_PROVEN",
    }


def main() -> int:
    print(json.dumps(verify(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
