from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

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


def test_windows_rollout_identity_repair_migrates_legacy_source(
    tmp_path: Path,
) -> None:
    target = tmp_path / "native_rollout_tail.rs"
    target.write_text(
        "prefix\n"
        + MODULE.LEGACY_FILE_NUMBERS
        + MODULE.LEGACY_FILE_IDENTITY
        + "    object.insert(\n"
        + '        "rollout".to_owned(),\n'
        + f"        {MODULE.LEGACY_OUTPUT_VALUE},\n"
        + "    );\n"
        + "suffix\n",
        encoding="utf-8",
    )

    assert MODULE.repair(target) is True
    first = target.read_text(encoding="utf-8")
    assert MODULE.LEGACY_FILE_NUMBERS not in first
    assert MODULE.LEGACY_FILE_IDENTITY not in first
    assert MODULE.LEGACY_OUTPUT_VALUE not in first
    assert first.count("(metadata.creation_time(), 0)") == 1
    assert first.count("fn identity_path(path: &Path) -> String") == 2
    assert first.count("identity_path(&resolved)") == 1
    assert first.count("identity_path(&selected)") == 1

    assert MODULE.repair(target) is False
    assert target.read_text(encoding="utf-8") == first


def test_windows_rollout_identity_repair_accepts_rustfmt_canonical_source(
    tmp_path: Path,
) -> None:
    target = tmp_path / "native_rollout_tail.rs"
    canonical = (
        "prefix\n"
        + MODULE.CANONICAL_FILE_NUMBERS
        + MODULE.CANONICAL_FILE_IDENTITY
        + "    object.insert(\n"
        + '        "rollout".to_owned(),\n'
        + f"        {MODULE.CANONICAL_OUTPUT_VALUE},\n"
        + "    );\n"
        + "suffix\n"
    )
    target.write_text(canonical, encoding="utf-8")

    assert MODULE.repair(target) is False
    assert target.read_text(encoding="utf-8") == canonical


def test_windows_rollout_identity_repair_rejects_partial_canonical_state(
    tmp_path: Path,
) -> None:
    target = tmp_path / "native_rollout_tail.rs"
    target.write_text(
        "prefix\n"
        + MODULE.CANONICAL_FILE_NUMBERS
        + MODULE.CANONICAL_FILE_IDENTITY
        + "suffix\n",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="invariants failed"):
        MODULE.repair(target)
