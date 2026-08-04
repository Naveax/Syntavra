from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "tools" / "repair_r38_windows_rollout_identity.py"
SPEC = importlib.util.spec_from_file_location(
    "repair_r38_windows_rollout_identity",
    MODULE_PATH,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def test_windows_rollout_identity_repair_is_complete_and_idempotent(
    tmp_path: Path,
) -> None:
    target = tmp_path / "native_rollout_tail.rs"
    target.write_text(
        "prefix\n" + MODULE.LEGACY + "suffix\n",
        encoding="utf-8",
    )

    assert MODULE.repair(target) is True
    first = target.read_text(encoding="utf-8")
    assert MODULE.LEGACY not in first
    assert first.count(MODULE.CANONICAL) == 1

    assert MODULE.repair(target) is False
    assert target.read_text(encoding="utf-8") == first
