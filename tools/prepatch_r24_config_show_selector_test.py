#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SELECTOR_TEST = ROOT / "tests/runtime/test_engine_selector_r4.py"
AGGREGATE = ROOT / "tools/run_engine_parity.py"


def replace_once(target: Path, old: str, new: str) -> None:
    source = target.read_text(encoding="utf-8")
    if new in source:
        return
    count = source.count(old)
    if count != 1:
        raise RuntimeError(
            f"{target.relative_to(ROOT)}: expected one exact config.show context, found {count}"
        )
    target.write_text(source.replace(old, new, 1), encoding="utf-8", newline="\n")


replace_once(
    SELECTOR_TEST,
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
    SELECTOR_TEST,
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
replace_once(
    AGGREGATE,
    '''    expected = [
        "config.explain",
        "config.resolve",
''',
    '''    expected = [
        "config.explain",
        "config.resolve",
        "config.show",
''',
)
