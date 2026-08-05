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
NATIVE_CONTEXT_GOVERNOR = RUST_ROOT / "native_context_governor.rs"
NATIVE_EVIDENCE_STATS = RUST_ROOT / "native_evidence_stats.rs"
NATIVE_HOST = RUST_ROOT / "native_host.rs"
NATIVE_STATIC_SURFACES = RUST_ROOT / "native_static_surfaces.rs"
NATIVE_STATS = RUST_ROOT / "native_stats.rs"
PRERELEASE_CLI = ROOT / "syntavra_runtime" / "prerelease_cli.py"
EVIDENCE_STATS_TEST = ROOT / "tests" / "runtime" / "test_native_evidence_stats_r38.py"
SESSION_PUBLIC_TEST = ROOT / "tests" / "runtime" / "test_native_session_public_r38.py"
STRUCTURAL_TEST = ROOT / "tests" / "runtime" / "test_native_structural_r38.py"

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
    'Some("rollout-tail" | "context-stress" | "claim" | "context" | "init")'
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

BENCHMARK_SCORE_20X_LEGACY = '"20X" => Ok(38.337_350_566_771_11),'
BENCHMARK_SCORE_20X_CANONICAL = '"20X" => Ok(38.337_350_566_771_08),'
BENCHMARK_SCORE_30X_LEGACY = '"30X" => Ok(63.345_278_851_520_476),'
BENCHMARK_SCORE_30X_CANONICAL = '"30X" => Ok(63.345_278_851_520_46),'

STATS_IMPORT_LEGACY = "use serde_json::{json, Map, Value};"
STATS_IMPORT_CANONICAL = "use serde_json::{json, Value};"

CONTEXT_DEFAULT_DISPATCH_LEGACY = '''        [group] if group == "context" => evaluate(arguments),'''
CONTEXT_DEFAULT_DISPATCH_CANONICAL = '''        [group] if group == "context" => {
            if arguments.windows(2).any(|window| {
                window[0] == "context" && window[1] == "pack"
            }) {
                pack(arguments)
            } else {
                evaluate(arguments)
            }
        }'''

HOST_REPAIRS = (
    (
        '''            &[".vscode", ".github/copilot-instructions.md"],
            &[],
            ".vscode/mcp.json",''',
        '''            &[".vscode/mcp.json"],
            &[],
            ".vscode/mcp.json",''',
        "VS Code project marker parity",
    ),
    (
        '''            &[".pi"],
            &[".pi/agent"],
            ".pi/settings.json",''',
        '''            &[".pi"],
            &[".pi/agent"],
            "",''',
        "Pi config path parity",
    ),
    (
        '''            &[".omp"],
            &[".omp/agent"],
            ".omp/agent/config.yml",''',
        '''            &[".omp"],
            &[".omp/agent"],
            "",''',
        "Oh My Pi config path parity",
    ),
    (
        '''            &[".openclaw", "openclaw.json"],
            &[".openclaw"],
            "openclaw.json",
            "skills/syntavra",''',
        '''            &[".openclaw", "openclaw.json"],
            &[".openclaw"],
            "",
            "skills/syntavra",''',
        "OpenClaw config path parity",
    ),
    (
        '''            &[".idea"],
            &[".config/JetBrains"],
            ".idea/mcp.json",''',
        '''            &[".idea/mcp.json"],
            &[],
            ".idea/mcp.json",''',
        "JetBrains marker parity",
    ),
)

STATS_FLOAT_HELPER_LEGACY = '''    } else {
        Err("ANALYTICS_FLOAT_NONFINITE".to_owned())
    }
}

fn identity_string(value: &Value) -> Option<String> {'''
STATS_FLOAT_HELPER_CANONICAL = '''    } else {
        Err("ANALYTICS_FLOAT_NONFINITE".to_owned())
    }
}

fn python_json_float(number: f64) -> Value {
    if number == 0.0 {
        Value::from(0)
    } else {
        Value::from(number)
    }
}

fn identity_string(value: &Value) -> Option<String> {'''
STATS_USAGE_FLOATS_LEGACY = '''            "output_tokens": output_tokens,
            "wall_time_ms": wall_time_ms,
            "cost_usd": cost_usd,'''
STATS_USAGE_FLOATS_CANONICAL = '''            "output_tokens": output_tokens,
            "wall_time_ms": python_json_float(wall_time_ms),
            "cost_usd": python_json_float(cost_usd),'''
STATS_COMPACTION_FLOAT_LEGACY = '''        "continuity": {
            "restores": continuity,
            "compaction_wall_time_ms": compaction_ms,
        },'''
STATS_COMPACTION_FLOAT_CANONICAL = '''        "continuity": {
            "restores": continuity,
            "compaction_wall_time_ms": python_json_float(compaction_ms),
        },'''


SESSION_DIAGNOSTIC_LEGACY = '''    assert rust_code == python_code == 0
    assert _session_shape(rust_result) == _session_shape(python_result)'''
SESSION_DIAGNOSTIC_CANONICAL = '''    assert rust_code == python_code == 0, {
        "python": {"code": python_code, "result": python_result},
        "rust": {"code": rust_code, "result": rust_result},
    }
    assert _session_shape(rust_result) == _session_shape(python_result)'''

STRUCTURAL_DIAGNOSTIC_LEGACY = '''    assert rust_code == python_code == 0
    assert rust_result == python_result
    assert rust_result == {
        "query": "helper",'''
STRUCTURAL_DIAGNOSTIC_CANONICAL = '''    assert rust_code == python_code == 0, {
        "python": {"code": python_code, "result": python_result},
        "rust": {"code": rust_code, "result": rust_result},
    }
    assert rust_result == python_result
    assert rust_result == {
        "query": "helper",'''


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


def repair_stats_imports(source: str) -> tuple[str, int]:
    return exact_repair(
        source,
        STATS_IMPORT_LEGACY,
        STATS_IMPORT_CANONICAL,
        "native stats imports",
    )


def repair_context_dispatch(source: str) -> tuple[str, int]:
    canonical_tokens = (
        'window[0] == "context"',
        'window[1] == "pack"',
        "pack(arguments)",
        "evaluate(arguments)",
    )
    if all(token in source for token in canonical_tokens):
        return source, 0
    return exact_repair(
        source,
        CONTEXT_DEFAULT_DISPATCH_LEGACY,
        CONTEXT_DEFAULT_DISPATCH_CANONICAL,
        "context pack selector dispatch",
    )


def repair_host_registry(source: str) -> tuple[str, int]:
    return exact_repairs(source, HOST_REPAIRS)


def repair_stats_numeric_types(source: str) -> tuple[str, int]:
    rendered, changed = exact_repair(
        source,
        STATS_FLOAT_HELPER_LEGACY,
        STATS_FLOAT_HELPER_CANONICAL,
        "stats Python-compatible float renderer",
    )
    rendered, usage_count = exact_repair(
        rendered,
        STATS_USAGE_FLOATS_LEGACY,
        STATS_USAGE_FLOATS_CANONICAL,
        "stats usage numeric type parity",
    )
    rendered, compaction_count = exact_repair(
        rendered,
        STATS_COMPACTION_FLOAT_LEGACY,
        STATS_COMPACTION_FLOAT_CANONICAL,
        "stats compaction numeric type parity",
    )
    return rendered, changed + usage_count + compaction_count


def repair_session_diagnostic(source: str) -> tuple[str, int]:
    return exact_repair(
        source,
        SESSION_DIAGNOSTIC_LEGACY,
        SESSION_DIAGNOSTIC_CANONICAL,
        "session import differential diagnostic",
    )


def repair_structural_diagnostic(source: str) -> tuple[str, int]:
    return exact_repair(
        source,
        STRUCTURAL_DIAGNOSTIC_LEGACY,
        STRUCTURAL_DIAGNOSTIC_CANONICAL,
        "structural fresh-index differential diagnostic",
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
        (NATIVE_CONTEXT_GOVERNOR, repair_context_dispatch),
        (NATIVE_EVIDENCE_STATS, repair_runtime_evidence_layout),
        (EVIDENCE_STATS_TEST, repair_evidence_test_layout),
        (NATIVE_HOST, repair_host_registry),
        (NATIVE_STATIC_SURFACES, repair_benchmark_scores),
        (NATIVE_STATS, repair_stats_imports),
        (NATIVE_STATS, repair_stats_numeric_types),
        (SESSION_PUBLIC_TEST, repair_session_diagnostic),
        (STRUCTURAL_TEST, repair_structural_diagnostic),
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
    canonical_context = NATIVE_CONTEXT_GOVERNOR.read_text(encoding="utf-8")
    canonical_evidence = NATIVE_EVIDENCE_STATS.read_text(encoding="utf-8")
    canonical_evidence_test = EVIDENCE_STATS_TEST.read_text(encoding="utf-8")
    canonical_host = NATIVE_HOST.read_text(encoding="utf-8")
    canonical_static_surfaces = NATIVE_STATIC_SURFACES.read_text(encoding="utf-8")
    canonical_stats = NATIVE_STATS.read_text(encoding="utf-8")
    canonical_session_test = SESSION_PUBLIC_TEST.read_text(encoding="utf-8")
    canonical_structural_test = STRUCTURAL_TEST.read_text(encoding="utf-8")
    invariants = {
        "R38_SINGLE_SEGMENT_PATH_REPAIR_INCOMPLETE": (
            canonical_selector.count(SINGLE_SEGMENT_PATH_CANONICAL) == 1
            and SINGLE_SEGMENT_PATH_LEGACY not in canonical_selector
        ),
        "R38_INLINE_JSON_ARGUMENT_REPAIR_INCOMPLETE": (
            canonical_cli.count(JSON_ARGUMENT_CANONICAL) == 1
            and JSON_ARGUMENT_LEGACY not in canonical_cli
        ),
        "R38_CONTEXT_DISPATCH_REPAIR_INCOMPLETE": (
            'window[0] == "context"' in canonical_context
            and 'window[1] == "pack"' in canonical_context
            and "pack(arguments)" in canonical_context
            and "evaluate(arguments)" in canonical_context
            and CONTEXT_DEFAULT_DISPATCH_LEGACY not in canonical_context
        ),
        "R38_RUNTIME_EVIDENCE_LAYOUT_REPAIR_INCOMPLETE": (
            canonical_evidence.count(RUNTIME_EVIDENCE_STATS_CANONICAL) == 1
            and canonical_evidence.count(RUNTIME_EVIDENCE_NEIGHBORS_CANONICAL) == 1
            and RUNTIME_EVIDENCE_STATS_LEGACY not in canonical_evidence
            and RUNTIME_EVIDENCE_NEIGHBORS_LEGACY not in canonical_evidence
            and canonical_evidence_test.count(EVIDENCE_TEST_LAYOUT_CANONICAL) == 1
            and EVIDENCE_TEST_LAYOUT_LEGACY not in canonical_evidence_test
        ),
        "R38_HOST_REGISTRY_REPAIR_INCOMPLETE": all(
            canonical_host.count(canonical) == 1 and legacy not in canonical_host
            for legacy, canonical, _ in HOST_REPAIRS
        ),
        "R38_BENCHMARK_SCORE_REPAIR_INCOMPLETE": (
            canonical_static_surfaces.count(BENCHMARK_SCORE_20X_CANONICAL) == 1
            and canonical_static_surfaces.count(BENCHMARK_SCORE_30X_CANONICAL) == 1
            and BENCHMARK_SCORE_20X_LEGACY not in canonical_static_surfaces
            and BENCHMARK_SCORE_30X_LEGACY not in canonical_static_surfaces
        ),
        "R38_STATS_IMPORT_REPAIR_INCOMPLETE": (
            canonical_stats.count(STATS_IMPORT_CANONICAL) == 1
            and STATS_IMPORT_LEGACY not in canonical_stats
        ),
        "R38_STATS_NUMERIC_TYPE_REPAIR_INCOMPLETE": (
            canonical_stats.count(STATS_FLOAT_HELPER_CANONICAL) == 1
            and STATS_FLOAT_HELPER_LEGACY not in canonical_stats
            and canonical_stats.count("fn python_json_float(number: f64) -> Value {") == 1
            and canonical_stats.count(STATS_USAGE_FLOATS_CANONICAL) == 1
            and canonical_stats.count(STATS_COMPACTION_FLOAT_CANONICAL) == 1
        ),
        "R38_SESSION_DIAGNOSTIC_REPAIR_INCOMPLETE": (
            canonical_session_test.count(SESSION_DIAGNOSTIC_CANONICAL) == 1
            and SESSION_DIAGNOSTIC_LEGACY not in canonical_session_test
        ),
        "R38_STRUCTURAL_DIAGNOSTIC_REPAIR_INCOMPLETE": (
            canonical_structural_test.count(STRUCTURAL_DIAGNOSTIC_CANONICAL) == 1
            and STRUCTURAL_DIAGNOSTIC_LEGACY not in canonical_structural_test
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
