#!/usr/bin/env python3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "tests" / "runtime" / "test_pre_release_publication_visibility.py"
OLD = "actions/upload-artifact@v4"
NEW = "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02"

text = PATH.read_text(encoding="utf-8")
count = text.count(OLD)
if count != 2:
    raise SystemExit(f"expected exactly 2 mutable upload-artifact assertions, found {count}")
text = text.replace(OLD, NEW)
if OLD in text:
    raise SystemExit("mutable upload-artifact assertion remains")
PATH.write_text(text, encoding="utf-8", newline="\n")
