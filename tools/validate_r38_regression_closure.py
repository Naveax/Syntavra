#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNTIME_REPAIR = ROOT / "tools" / "repair_r38_runtime_regressions.py"
RUNTIME_SELECTOR_REPAIR = ROOT / "tools" / "repair_r38_runtime_selector_contract.py"
SELECTOR_REPAIR = ROOT / "tools" / "repair_r38_selector_option_values.py"
INSTALL_REPAIR = ROOT / "tools" / "repair_r38_native_install.py"
STATUS_REPAIR = ROOT / "tools" / "repair_r38_native_status.py"
STATUS_DEFAULT_REPAIR = ROOT / "tools" / "repair_r38_status_default.py"
STATUS_INVENTORY_ADVANCE = ROOT / "tools" / "advance_r38_status_inventory.py"
HOOK_REPAIR = ROOT / "tools" / "repair_r38_native_hook.py"
HOOK_OUTPUT_REPAIR = ROOT / "tools" / "repair_r38_hook_output.py"
HOOK_INVENTORY_ADVANCE = ROOT / "tools" / "advance_r38_hook_inventory.py"
MCP_CATALOG_SYNC = ROOT / "tools" / "sync_r38_mcp_catalog.py"
MCP_REPAIR = ROOT / "tools" / "repair_r38_native_mcp.py"
MCP_INVENTORY_ADVANCE = ROOT / "tools" / "advance_r38_mcp_inventory.py"
SESSION_HASH_REPAIR = ROOT / "tools" / "repair_r38_session_export_hash.py"
INVENTORY_ADVANCE = ROOT / "tools" / "advance_r38_setup_repair_inventory.py"

TARGETS = (
    "tests/runtime/test_native_status_r38.py",
    "tests/runtime/test_native_hook_r38.py",
    "tests/runtime/test_native_mcp_r38.py",
    "tests/runtime/test_native_setup_repair_r38.py::test_native_setup_empty_dry_run_matches_python",
    "tests/runtime/test_native_setup_repair_r38.py::test_native_setup_codex_apply_matches_python",
    "tests/runtime/test_native_setup_repair_r38.py::test_native_repair_plan_matches_python",
    "tests/runtime/test_native_setup_repair_r38.py::test_native_repair_apply_installs_missing_product",
    "tests/runtime/test_native_setup_repair_r38.py::test_native_repair_restores_only_missing_bundle_files",
    "tests/runtime/test_native_setup_repair_r38.py::test_native_setup_repair_mode_matches_python",
    "tests/runtime/test_native_install_r38.py::test_native_install_empty_dry_run_matches_python",
    "tests/runtime/test_native_install_r38.py::test_native_install_codex_dry_run_matches_python",
    "tests/runtime/test_native_install_r38.py::test_native_install_empty_apply_matches_product_bundle",
    "tests/runtime/test_native_install_r38.py::test_native_install_codex_apply_matches_host_transaction",
    "tests/runtime/test_native_init_r38.py",
    "tests/runtime/test_native_operator_lifecycle_r38.py",
    "tests/runtime/test_native_uninstall_r38.py",
    "tests/runtime/test_manifest_refresh_contract.py",
    "tests/runtime/test_native_context_governor_r38.py",
    "tests/runtime/test_native_host_r38.py",
    "tests/runtime/test_native_stats_r38.py",
    "tests/runtime/test_native_session_import_diagnostic_r38.py",
    "tests/runtime/test_native_session_public_r38.py::test_native_session_import_explicit_id_matches_python_without_quarantine",
    "tests/runtime/test_native_structural_r38.py::test_native_structural_fresh_python_symbol_index_matches_python",
    "tests/runtime/test_native_memory_r38.py",
)


def run_checked(
    argv: list[str],
    label: str,
    *,
    environment: dict[str, str] | None = None,
) -> None:
    completed = subprocess.run(
        argv,
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=1800,
        env=environment,
    )
    if completed.returncode != 0:
        payload = {
            "code": "R38_TARGETED_VALIDATION_FAILED",
            "returncode": completed.returncode,
            "stdout_tail": completed.stdout[-8000:],
            "stderr_tail": completed.stderr[-8000:],
            "failed_target": label,
        }
        raise RuntimeError(json.dumps(payload, sort_keys=True))


def selector_path() -> Path:
    configured = os.environ.get("CARGO_TARGET_DIR")
    target = Path(configured) if configured else ROOT / "target"
    suffix = ".exe" if sys.platform == "win32" else ""
    return target / "debug" / f"syntavra{suffix}"


def main() -> int:
    run_checked([sys.executable, str(SELECTOR_REPAIR)], "selector-repair")
    run_checked([sys.executable, str(INSTALL_REPAIR)], "install-repair")
    run_checked([sys.executable, str(STATUS_REPAIR)], "status-repair")
    run_checked([sys.executable, str(STATUS_DEFAULT_REPAIR)], "status-default-repair")
    run_checked([sys.executable, str(STATUS_INVENTORY_ADVANCE)], "status-inventory-advance")
    run_checked([sys.executable, str(HOOK_REPAIR)], "hook-repair")
    run_checked([sys.executable, str(HOOK_OUTPUT_REPAIR)], "hook-output-repair")
    run_checked([sys.executable, str(HOOK_INVENTORY_ADVANCE)], "hook-inventory-advance")
    run_checked([sys.executable, str(MCP_CATALOG_SYNC)], "mcp-catalog-sync")
    run_checked([sys.executable, str(MCP_REPAIR)], "mcp-repair")
    run_checked([sys.executable, str(MCP_INVENTORY_ADVANCE)], "mcp-inventory-advance")
    run_checked([sys.executable, str(SESSION_HASH_REPAIR)], "session-export-hash-repair")
    run_checked([sys.executable, str(INVENTORY_ADVANCE)], "setup-repair-inventory-advance")
    run_checked([sys.executable, str(RUNTIME_SELECTOR_REPAIR)], "runtime-selector-contract-repair")
    run_checked([sys.executable, str(RUNTIME_REPAIR)], "repair")
    run_checked(["cargo", "fmt", "--all"], "rustfmt")
    run_checked([sys.executable, str(SELECTOR_REPAIR)], "selector-repair-idempotence")
    run_checked([sys.executable, str(INSTALL_REPAIR)], "install-repair-idempotence")
    run_checked([sys.executable, str(STATUS_REPAIR)], "status-repair-idempotence")
    run_checked([sys.executable, str(STATUS_DEFAULT_REPAIR)], "status-default-repair-idempotence")
    run_checked([sys.executable, str(STATUS_INVENTORY_ADVANCE)], "status-inventory-idempotence")
    run_checked([sys.executable, str(HOOK_REPAIR)], "hook-repair-idempotence")
    run_checked([sys.executable, str(HOOK_OUTPUT_REPAIR)], "hook-output-idempotence")
    run_checked([sys.executable, str(HOOK_INVENTORY_ADVANCE)], "hook-inventory-idempotence")
    run_checked([sys.executable, str(MCP_CATALOG_SYNC)], "mcp-catalog-idempotence")
    run_checked([sys.executable, str(MCP_REPAIR)], "mcp-repair-idempotence")
    run_checked([sys.executable, str(MCP_INVENTORY_ADVANCE)], "mcp-inventory-idempotence")
    run_checked([sys.executable, str(SESSION_HASH_REPAIR)], "session-export-hash-repair-idempotence")
    run_checked([sys.executable, str(INVENTORY_ADVANCE)], "setup-repair-inventory-idempotence")
    run_checked([sys.executable, str(RUNTIME_SELECTOR_REPAIR)], "runtime-selector-contract-idempotence")
    run_checked([sys.executable, str(RUNTIME_REPAIR), "--check"], "repair-idempotence")
    run_checked(
        ["cargo", "check", "--locked", "-p", "syntavra-cli", "--bin", "syntavra"],
        "cargo-check:syntavra",
    )
    run_checked(
        ["cargo", "build", "--locked", "-p", "syntavra-cli", "--bin", "syntavra"],
        "cargo-build:syntavra",
    )
    selector = selector_path()
    if not selector.is_file():
        raise RuntimeError(
            json.dumps(
                {
                    "code": "R38_SELECTOR_BINARY_MISSING",
                    "expected": str(selector),
                    "cargo_target_dir": os.environ.get("CARGO_TARGET_DIR"),
                },
                sort_keys=True,
            )
        )
    test_environment = os.environ.copy()
    test_environment["SYNTAVRA_R38_SELECTOR"] = str(selector)
    for target in TARGETS:
        run_checked(
            [sys.executable, "-m", "pytest", "-q", target],
            f"target:{target}",
            environment=test_environment,
        )
    print(
        json.dumps(
            {
                "ok": True,
                "selector": str(selector),
                "targets": list(TARGETS),
                "claim": "R38_KNOWN_REGRESSION_DIFFERENTIALS_PASS",
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
