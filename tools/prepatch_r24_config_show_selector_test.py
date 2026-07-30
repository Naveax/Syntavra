#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "tests/runtime/test_engine_selector_r4.py"


def replace_once(old: str, new: str) -> None:
    source = TARGET.read_text(encoding="utf-8")
    if new in source:
        return
    count = source.count(old)
    if count != 1:
        raise RuntimeError(
            f"test_engine_selector_r4.py: expected one exact selector context, found {count}"
        )
    TARGET.write_text(source.replace(old, new, 1), encoding="utf-8", newline="\n")


replace_once(
    '''                for name in (
                    "config.explain",
                    "config.resolve",
''',
    '''                for name in (
                    "config.explain",
                    "config.resolve",
                    "config.show",
''',
)
replace_once(
    '''    assert verification.capabilities == (
        "config.explain",
        "config.resolve",
''',
    '''    assert verification.capabilities == (
        "config.explain",
        "config.resolve",
        "config.show",
''',
)
