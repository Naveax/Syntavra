#!/usr/bin/env python3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

REPLACEMENTS = (
    (
        ROOT / "tests" / "runtime" / "test_pre_release_publication_visibility.py",
        "actions/upload-artifact@v4",
        "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02",
        2,
    ),
    (
        ROOT / "tests" / "runtime" / "test_pre_release_publication_attempt_ledger.py",
        "actions/download-artifact@v4",
        "actions/download-artifact@d3f86a106a0bac45b974a628896c90dbdf5c8093",
        1,
    ),
)

for path, old, new, expected_count in REPLACEMENTS:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != expected_count:
        raise SystemExit(
            f"{path}: expected exactly {expected_count} occurrence(s) of {old!r}, found {count}"
        )
    text = text.replace(old, new)
    if old in text:
        raise SystemExit(f"{path}: mutable action assertion remains: {old}")
    path.write_text(text, encoding="utf-8", newline="\n")
