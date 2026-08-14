#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "crates" / "syntavra-cli" / "src" / "native_evidence_get.rs"
VALIDATOR = ROOT / "tools" / "validate_r38_regression_closure.py"
TARGET = '    "tests/runtime/test_native_evidence_rotate_key_r38.py",\n'
TARGET_ANCHOR = '    "tests/runtime/test_native_evidence_get_r38.py",\n'


def validate_source() -> None:
    if not SOURCE.is_file():
        raise RuntimeError("native evidence route source is missing")
    source = SOURCE.read_text(encoding="utf-8")
    for marker in (
        'matches!(action.as_str(), "get" | "rotate-key")',
        "struct RotationStore",
        "fn rotate_local_key",
        "fn reencrypt_object",
        "fn rotate_key",
        "EVIDENCE_MANAGED_KEY_ROTATION_FORBIDDEN",
    ):
        if marker not in source:
            raise RuntimeError(f"evidence rotation source contract is missing: {marker}")


def repair_validator() -> bool:
    source = VALIDATOR.read_text(encoding="utf-8")
    if source.count(TARGET) == 1:
        return False
    if source.count(TARGET) != 0 or source.count(TARGET_ANCHOR) != 1:
        raise RuntimeError("evidence rotation validator contract is ambiguous")
    VALIDATOR.write_text(
        source.replace(TARGET_ANCHOR, TARGET_ANCHOR + TARGET, 1),
        encoding="utf-8",
        newline="\n",
    )
    return True


def main() -> int:
    validate_source()
    validator_changed = repair_validator()
    print(
        json.dumps(
            {
                "changed": validator_changed,
                "ok": True,
                "source_changed": False,
                "surface": "native-evidence-rotate-key",
                "validator_changed": validator_changed,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
