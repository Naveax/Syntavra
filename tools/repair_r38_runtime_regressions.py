#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUST_ROOT = ROOT / "crates" / "syntavra-cli" / "src"
SELECTOR = RUST_ROOT / "bin" / "syntavra.rs"

# Rust removes a backslash-newline pair and the indentation that follows it.
# SQL assembled from source such as `scope_idx\` + `ON ...` therefore becomes
# `scope_idxON ...` unless the source contains an explicit space before the
# continuation. Restrict the repair to SQL clause boundaries and fail closed
# on any malformed continuation that remains.
SQL_CLAUSE = (
    r"(?:AND|AS|CREATE|DELETE|FOREIGN|FROM|GROUP|HAVING|INNER|INSERT|JOIN|LEFT|"
    r"LIMIT|OFFSET|ON|OR|ORDER|OUTER|PRIMARY|REFERENCES|RIGHT|SELECT|SET|UNIQUE|"
    r"UPDATE|USING|VALUES|WHERE)"
)
MISSING_SPACE = re.compile(
    rf"(?P<left>[A-Za-z0-9_')?])\\\n(?P<indent>[ \t]+)(?P<clause>{SQL_CLAUSE})\b"
)
MALFORMED_RUNTIME_SQL = re.compile(
    rf"[A-Za-z0-9_')?]\\\n[ \t]+{SQL_CLAUSE}\b"
)

CLAIM_PATH_LEGACY = 'Some("rollout-tail" | "context-stress")'
CLAIM_PATH_CANONICAL = 'Some("rollout-tail" | "context-stress" | "claim")'


def rust_sources() -> list[Path]:
    return sorted(RUST_ROOT.glob("native_*.rs"))


def repaired_source(source: str) -> tuple[str, int]:
    return MISSING_SPACE.subn(
        lambda match: (
            f"{match.group('left')} \\\n"
            f"{match.group('indent')}{match.group('clause')}"
        ),
        source,
    )


def repair_claim_command_path(source: str) -> tuple[str, int]:
    legacy_count = source.count(CLAIM_PATH_LEGACY)
    canonical_count = source.count(CLAIM_PATH_CANONICAL)
    if canonical_count == 1 and legacy_count == 0:
        return source, 0
    if legacy_count != 1 or canonical_count != 0:
        raise RuntimeError(
            "selector claim path state is neither one legacy fragment nor one canonical fragment"
        )
    return source.replace(CLAIM_PATH_LEGACY, CLAIM_PATH_CANONICAL, 1), 1


def inspect(path: Path) -> tuple[str, int]:
    source = path.read_text(encoding="utf-8")
    return repaired_source(source)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--check",
        action="store_true",
        help="fail when a canonical runtime repair is still required",
    )
    arguments = parser.parse_args()

    changed: dict[str, int] = {}
    for path in rust_sources():
        rendered, count = inspect(path)
        if count:
            relative = path.relative_to(ROOT).as_posix()
            changed[relative] = count
            if not arguments.check:
                path.write_text(rendered, encoding="utf-8", newline="\n")

    selector_source = SELECTOR.read_text(encoding="utf-8")
    selector_rendered, selector_count = repair_claim_command_path(selector_source)
    if selector_count:
        relative = SELECTOR.relative_to(ROOT).as_posix()
        changed[relative] = selector_count
        if not arguments.check:
            SELECTOR.write_text(selector_rendered, encoding="utf-8", newline="\n")

    if arguments.check and changed:
        print(
            json.dumps(
                {
                    "ok": False,
                    "code": "R38_RUNTIME_REPAIR_REQUIRED",
                    "files": changed,
                },
                sort_keys=True,
            )
        )
        return 1

    malformed: list[str] = []
    for path in rust_sources():
        source = path.read_text(encoding="utf-8")
        if MALFORMED_RUNTIME_SQL.search(source):
            malformed.append(path.relative_to(ROOT).as_posix())
    if malformed:
        print(
            json.dumps(
                {
                    "ok": False,
                    "code": "R38_RUNTIME_SQL_REPAIR_INCOMPLETE",
                    "files": malformed,
                },
                sort_keys=True,
            )
        )
        return 1

    canonical_selector = SELECTOR.read_text(encoding="utf-8")
    if canonical_selector.count(CLAIM_PATH_CANONICAL) != 1 or CLAIM_PATH_LEGACY in canonical_selector:
        print(
            json.dumps(
                {
                    "ok": False,
                    "code": "R38_CLAIM_COMMAND_PATH_REPAIR_INCOMPLETE",
                },
                sort_keys=True,
            )
        )
        return 1

    print(
        json.dumps(
            {
                "ok": True,
                "changed": changed,
                "mode": "check" if arguments.check else "repair",
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
