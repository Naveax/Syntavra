#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNTIME_REPAIR = ROOT / "tools" / "repair_r38_runtime_regressions.py"
SELECTOR_REPAIR = ROOT / "tools" / "repair_r38_selector_option_values.py"

TARGETS = (
    "tests/runtime/test_native_install_r38.py",
    "tests/runtime/test_native_init_r38.py",
    "tests/runtime/test_native_operator_lifecycle_r38.py",
    "tests/runtime/test_native_uninstall_r38.py",
    "tests/runtime/test_manifest_refresh_contract.py",
    "tests/runtime/test_native_context_governor_r38.py",
    "tests/runtime/test_native_host_r38.py",
    "tests/runtime/test_native_stats_r38.py",
    "tests/runtime/test_native_session_public_r38.py::test_native_session_import_explicit_id_matches_python_without_quarantine",
    "tests/runtime/test_native_structural_r38.py::test_native_structural_fresh_python_symbol_index_matches_python",
    "tests/runtime/test_native_memory_r38.py",
)


def run_checked(argv: list[str], label: str) -> None:
    completed = subprocess.run(
        argv,
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=1800,
    )
    if completed.returncode != 0:
        payload = {
            "code": "R38_TARGETED_VALIDATION_FAILED",
            "returncode": completed.returncode,
            "stdout_tail": completed.stdout[-3000:],
            "stderr_tail": completed.stderr[-3000:],
            "failed_target": label,
        }
        raise RuntimeError(json.dumps(payload, sort_keys=True))


def main() -> int:
    run_checked([sys.executable, str(SELECTOR_REPAIR)], "selector-repair")
    run_checked([sys.executable, str(RUNTIME_REPAIR)], "repair")
    run_checked(["cargo", "fmt", "--all"], "rustfmt")
    run_checked([sys.executable, str(SELECTOR_REPAIR)], "selector-repair-idempotence")
    run_checked([sys.executable, str(RUNTIME_REPAIR), "--check"], "repair-idempotence")
    for target in TARGETS:
        run_checked(
            [sys.executable, "-m", "pytest", "-q", target],
            f"target:{target}",
        )
    print(
        json.dumps(
            {
                "ok": True,
                "targets": list(TARGETS),
                "claim": "R38_KNOWN_REGRESSION_DIFFERENTIALS_PASS",
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
