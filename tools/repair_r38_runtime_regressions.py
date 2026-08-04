#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from collections.abc import Callable
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUST_ROOT = ROOT / "crates" / "syntavra-cli" / "src"
SELECTOR = RUST_ROOT / "bin" / "syntavra.rs"
NATIVE_EVIDENCE_STATS = RUST_ROOT / "native_evidence_stats.rs"
NATIVE_STATIC_SURFACES = RUST_ROOT / "native_static_surfaces.rs"
PRERELEASE_CLI = ROOT / "syntavra_runtime" / "prerelease_cli.py"
EVIDENCE_STATS_TEST = ROOT / "tests" / "runtime" / "test_native_evidence_stats_r38.py"

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

SINGLE_SEGMENT_PATH_LEGACY = 'Some("rollout-tail" | "context-stress")'
SINGLE_SEGMENT_PATH_CANONICAL = (
    'Some("rollout-tail" | "context-stress" | "claim" | "context")'
)

JSON_ARGUMENT_LEGACY = '''def _load_json_argument(value: str) -> Any:
    path = Path(value)
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))
    return json.loads(value)
'''
JSON_ARGUMENT_CANONICAL = '''def _load_json_argument(value: str) -> Any:
    path = Path(value)
    try:
        if path.is_file():
            return json.loads(path.read_text(encoding="utf-8"))
    except OSError:
        # Long inline JSON is data, not a filesystem path. Path.is_file() may
        # raise ENAMETOOLONG before the JSON parser gets a chance to consume it.
        pass
    return json.loads(value)
'''

RUNTIME_EVIDENCE_STATS_LEGACY = "runtime_evidence_stats(state_root)"
RUNTIME_EVIDENCE_STATS_CANONICAL = 'runtime_evidence_stats(&state_root.join("unified"))'
RUNTIME_EVIDENCE_NEIGHBORS_LEGACY = "runtime_evidence_neighbors(arguments, state_root)"
RUNTIME_EVIDENCE_NEIGHBORS_CANONICAL = (
    'runtime_evidence_neighbors(arguments, &state_root.join("unified"))'
)
EVIDENCE_TEST_LAYOUT_LEGACY = "    _prepare_runtime_evidence(source)\n"
EVIDENCE_TEST_LAYOUT_CANONICAL = "    _prepare_runtime_evidence(source / \"unified\")\n"

BENCHMARK_SCORE_20X_LEGACY = '"20X" => Ok(38.337_350_566_771_08),'
BENCHMARK_SCORE_20X_CANONICAL = '"20X" => Ok(38.337_350_566_771_11),'
BENCHMARK_SCORE_30X_LEGACY = '"30X" => Ok(63.345_278_851_520_46),'
BENCHMARK_SCORE_30X_CANONICAL = '"30X" => Ok(63.345_278_851_520_476),'


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


def exact_repair(source: str, legacy: str, canonical: str, label: str) -> tuple[str, int]:
    legacy_count = source.count(legacy)
    canonical_count = source.count(canonical)
    if canonical_count == 1 and legacy_count == 0:
        return source, 0
    if legacy_count != 1 or canonical_count != 0:
        raise RuntimeError(
            f"{label} state is neither one legacy fragment nor one canonical fragment"
        )
    return source.replace(legacy, canonical, 1), 1


def exact_repairs(
    source: str,
    replacements: tuple[tuple[str, str, str], ...],
) -> tuple[str, int]:
    rendered = source
    changed = 0
    for legacy, canonical, label in replacements:
        rendered, count = exact_repair(rendered, legacy, canonical, label)
        changed += count
    return rendered, changed


def repair_single_segment_command_paths(source: str) -> tuple[str, int]:
    return exact_repair(
        source,
        SINGLE_SEGMENT_PATH_LEGACY,
        SINGLE_SEGMENT_PATH_CANONICAL,
        "selector single-segment command path",
    )


def repair_inline_json_argument(source: str) -> tuple[str, int]:
    return exact_repair(
        source,
        JSON_ARGUMENT_LEGACY,
        JSON_ARGUMENT_CANONICAL,
        "pre-release inline JSON argument",
    )


def repair_runtime_evidence_layout(source: str) -> tuple[str, int]:
    return exact_repairs(
        source,
        (
            (
                RUNTIME_EVIDENCE_STATS_LEGACY,
                RUNTIME_EVIDENCE_STATS_CANONICAL,
                "runtime evidence stats state layout",
            ),
            (
                RUNTIME_EVIDENCE_NEIGHBORS_LEGACY,
                RUNTIME_EVIDENCE_NEIGHBORS_CANONICAL,
                "runtime evidence neighbors state layout",
            ),
        ),
    )


def repair_evidence_test_layout(source: str) -> tuple[str, int]:
    return exact_repair(
        source,
        EVIDENCE_TEST_LAYOUT_LEGACY,
        EVIDENCE_TEST_LAYOUT_CANONICAL,
        "runtime evidence test state layout",
    )


def repair_benchmark_scores(source: str) -> tuple[str, int]:
    return exact_repairs(
        source,
        (
            (
                BENCHMARK_SCORE_20X_LEGACY,
                BENCHMARK_SCORE_20X_CANONICAL,
                "20X benchmark score",
            ),
            (
                BENCHMARK_SCORE_30X_LEGACY,
                BENCHMARK_SCORE_30X_CANONICAL,
                "30X benchmark score",
            ),
        ),
    )


def inspect(path: Path) -> tuple[str, int]:
    source = path.read_text(encoding="utf-8")
    return repaired_source(source)


def repair_file(
    path: Path,
    repair: Callable[[str], tuple[str, int]],
    *,
    check: bool,
    changed: dict[str, int],
) -> None:
    source = path.read_text(encoding="utf-8")
    rendered, count = repair(source)
    if not count:
        return
    changed[path.relative_to(ROOT).as_posix()] = count
    if not check:
        path.write_text(rendered, encoding="utf-8", newline="\n")


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

    for path, repair in (
        (SELECTOR, repair_single_segment_command_paths),
        (PRERELEASE_CLI, repair_inline_json_argument),
        (NATIVE_EVIDENCE_STATS, repair_runtime_evidence_layout),
        (EVIDENCE_STATS_TEST, repair_evidence_test_layout),
        (NATIVE_STATIC_SURFACES, repair_benchmark_scores),
    ):
        repair_file(path, repair, check=arguments.check, changed=changed)

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
    canonical_cli = PRERELEASE_CLI.read_text(encoding="utf-8")
    canonical_evidence = NATIVE_EVIDENCE_STATS.read_text(encoding="utf-8")
    canonical_evidence_test = EVIDENCE_STATS_TEST.read_text(encoding="utf-8")
    canonical_static_surfaces = NATIVE_STATIC_SURFACES.read_text(encoding="utf-8")
    invariants = {
        "R38_SINGLE_SEGMENT_PATH_REPAIR_INCOMPLETE": (
            canonical_selector.count(SINGLE_SEGMENT_PATH_CANONICAL) == 1
            and SINGLE_SEGMENT_PATH_LEGACY not in canonical_selector
        ),
        "R38_INLINE_JSON_ARGUMENT_REPAIR_INCOMPLETE": (
            canonical_cli.count(JSON_ARGUMENT_CANONICAL) == 1
            and JSON_ARGUMENT_LEGACY not in canonical_cli
        ),
        "R38_RUNTIME_EVIDENCE_LAYOUT_REPAIR_INCOMPLETE": (
            canonical_evidence.count(RUNTIME_EVIDENCE_STATS_CANONICAL) == 1
            and canonical_evidence.count(RUNTIME_EVIDENCE_NEIGHBORS_CANONICAL) == 1
            and RUNTIME_EVIDENCE_STATS_LEGACY not in canonical_evidence
            and RUNTIME_EVIDENCE_NEIGHBORS_LEGACY not in canonical_evidence
            and canonical_evidence_test.count(EVIDENCE_TEST_LAYOUT_CANONICAL) == 1
            and EVIDENCE_TEST_LAYOUT_LEGACY not in canonical_evidence_test
        ),
        "R38_BENCHMARK_SCORE_REPAIR_INCOMPLETE": (
            canonical_static_surfaces.count(BENCHMARK_SCORE_20X_CANONICAL) == 1
            and canonical_static_surfaces.count(BENCHMARK_SCORE_30X_CANONICAL) == 1
            and BENCHMARK_SCORE_20X_LEGACY not in canonical_static_surfaces
            and BENCHMARK_SCORE_30X_LEGACY not in canonical_static_surfaces
        ),
    }
    for code, valid in invariants.items():
        if not valid:
            print(json.dumps({"ok": False, "code": code}, sort_keys=True))
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
