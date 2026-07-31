from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "tools" / "verify_dual_engine_public_surface.py"
SPEC = importlib.util.spec_from_file_location("verify_dual_engine_public_surface", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_dual_engine_inventory_is_complete_and_fail_closed() -> None:
    result = MODULE.verify()
    assert result["ok"] is True
    assert result["claim"] == "DUAL_ENGINE_PARITY_INCOMPLETE"
    assert result["full"] is False
    assert result["python"]["public_command_count"] == 257
    assert result["rust"]["native_public_command_count"] == 12
    assert result["rust"]["missing_native_public_command_count"] == 245
    assert result["policy"]["hidden_fallback_forbidden"] is True
    assert result["policy"]["one_install_contains_python_and_rust"] is True


def test_full_claim_cannot_open_while_native_commands_are_missing() -> None:
    with pytest.raises(RuntimeError, match="full dual-engine parity not reached"):
        MODULE.verify(require_full=True)
