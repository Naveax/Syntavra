#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNTIME_REPAIR = ROOT / "tools" / "repair_r38_runtime_regressions.py"

TARGETS = (
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
        raise RuntimeError(
            json.dumps(
                {
                    "code": "R38_TARGETED_VALIDATION_FAILED",
                    "label": label,
                    "argv": argv,
                    "returncode": completed.returncode,
                    "stdout": completed.stdout[-20000:],
                    "stderr": completed.stderr[-20000:],
                },
                sort_keys=True,
            )
        )


def main() -> int:
    run_checked([sys.executable, str(RUNTIME_REPAIR)], "repair")
    run_checked(["cargo", "fmt", "--all"], "rustfmt")
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
