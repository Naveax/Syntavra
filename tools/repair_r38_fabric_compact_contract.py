#!/usr/bin/env python3
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUST = ROOT / "crates" / "syntavra-cli" / "src" / "native_fabric_compact.rs"
TEST = ROOT / "tests" / "runtime" / "test_native_fabric_compact_r38.py"

PACKAGE_PATTERN = re.compile(
    r'''        "npm" \| "pnpm" \| "yarn" if starts\(&arguments, &\["test", "run test"\]\) => \("package-test", Strategy::Test\),\n        "npm" \| "pnpm" \| "yarn" if starts\(&arguments, &\["list", "ls", "outdated", "audit", "info"\]\) => \("package-list", Strategy::JsonOrTable\),'''
)
PACKAGE_REPLACEMENT = '''        "npm" if starts(&arguments, &["test", "run test"]) => ("npm-test", Strategy::Test),
        "npm" if starts(&arguments, &["list", "ls", "outdated", "audit"]) => ("npm-list", Strategy::JsonOrTable),
        "pnpm" if starts(&arguments, &["test", "run test"]) => ("pnpm-test", Strategy::Test),
        "pnpm" if starts(&arguments, &["list", "ls", "outdated", "audit"]) => ("pnpm-list", Strategy::JsonOrTable),
        "yarn" if starts(&arguments, &["test", "run test"]) => ("yarn-test", Strategy::Test),
        "yarn" if starts(&arguments, &["list", "info", "audit"]) => ("yarn-list", Strategy::JsonOrTable),'''

OLD_NEGATIVE = '''    assert rust.returncode == python.returncode != 0
    assert not (rust_project / "state" / "competitive-fabric.sqlite3").exists()
'''
NEW_NEGATIVE = '''    assert python.returncode != 0
    assert rust.returncode != 0
    for project in (python_project, rust_project):
        database = project / "state" / "competitive-fabric.sqlite3"
        assert database.is_file()
        with sqlite3.connect(database) as connection:
            assert connection.execute("SELECT COUNT(*) FROM fabric_events").fetchone()[0] == 0
'''


def repair_rust() -> bool:
    source = RUST.read_text(encoding="utf-8")
    if all(
        token in source
        for token in (
            '("npm-test", Strategy::Test)',
            '("npm-list", Strategy::JsonOrTable)',
            '("pnpm-test", Strategy::Test)',
            '("pnpm-list", Strategy::JsonOrTable)',
            '("yarn-test", Strategy::Test)',
            '("yarn-list", Strategy::JsonOrTable)',
        )
    ):
        if '"package-test"' in source or '"package-list"' in source:
            raise RuntimeError("generic package compact labels remain beside canonical labels")
        return False
    rendered, count = PACKAGE_PATTERN.subn(PACKAGE_REPLACEMENT, source, count=1)
    if count != 1:
        raise RuntimeError(f"fabric compact package label contract not found: {count}")
    RUST.write_text(rendered, encoding="utf-8", newline="\n")
    return True


def repair_test() -> bool:
    source = TEST.read_text(encoding="utf-8")
    if NEW_NEGATIVE in source:
        if OLD_NEGATIVE in source:
            raise RuntimeError("legacy compact exit-code assertion remains")
        return False
    if source.count(OLD_NEGATIVE) != 1:
        raise RuntimeError("compact negative test assertion contract not found")
    TEST.write_text(source.replace(OLD_NEGATIVE, NEW_NEGATIVE, 1), encoding="utf-8", newline="\n")
    return True


def repair() -> bool:
    rust_changed = repair_rust()
    test_changed = repair_test()
    return rust_changed or test_changed


def main() -> int:
    changed = repair()
    print(
        json.dumps(
            {
                "changed": changed,
                "ok": True,
                "surface": "native-fabric-compact-contract",
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
