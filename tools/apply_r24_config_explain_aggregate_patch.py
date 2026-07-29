#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: str, old: str, new: str) -> None:
    target = ROOT / path
    source = target.read_text(encoding="utf-8")
    if new in source:
        return
    count = source.count(old)
    if count != 1:
        raise RuntimeError(f"{path}: expected one aggregate patch anchor, found {count}")
    target.write_text(source.replace(old, new, 1), encoding="utf-8", newline="\n")


replace_once(
    "tools/verify_full_parity_catalog.py",
    '    "route.config.resolve": "config.resolve",\n    "route.config.validate": "config.resolve",\n',
    '    "route.config.explain": "config.explain",\n    "route.config.resolve": "config.resolve",\n    "route.config.validate": "config.resolve",\n',
)
replace_once(
    "tests/runtime/test_full_parity_catalog_v1.py",
    '        "route.config.resolve",\n        "route.config.validate",\n',
    '        "route.config.explain",\n        "route.config.resolve",\n        "route.config.validate",\n',
)
replace_once(
    ".github/workflows/full-engine-parity.yml",
    '      - "tools/verify_r24_config_validate.py"\n      - "tools/run_engine_parity_r24.py"\n',
    '      - "tools/verify_r24_config_validate.py"\n      - "tools/verify_r24_config_explain.py"\n      - "tools/run_engine_parity_r24.py"\n',
)
replace_once(
    ".github/workflows/full-engine-parity.yml",
    '      - "tests/runtime/test_config_validate_r24.py"\n      - ".github/workflows/full-engine-parity.yml"\n',
    '      - "tests/runtime/test_config_validate_r24.py"\n      - "tests/runtime/test_config_explain_r24.py"\n      - ".github/workflows/full-engine-parity.yml"\n',
)
replace_once(
    ".github/workflows/full-engine-parity.yml",
    '      - "docs/adr/0025-config-validate-read-only-parity.md"\n',
    '      - "docs/adr/0025-config-validate-read-only-parity.md"\n      - "docs/adr/0026-config-explain-native-parity.md"\n',
)
replace_once(
    ".github/workflows/full-engine-parity.yml",
    '          tests/runtime/test_config_validate_r24.py\n',
    '          tests/runtime/test_config_validate_r24.py\n          tests/runtime/test_config_explain_r24.py\n',
)
