#!/usr/bin/env python3
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
RUNTIME_REPAIR = ROOT / "tools" / "repair_r38_runtime_regressions.py"
BENCHMARK_HARNESS = ROOT / "syntavra_runtime" / "benchmark_harness.py"
MEMORY_DIFFERENTIAL = ROOT / "tests" / "runtime" / "test_native_memory_r38.py"
NATIVE_STRUCTURAL = ROOT / "crates" / "syntavra-cli" / "src" / "native_structural.rs"
CONTRACT = ROOT / "contracts" / "engine" / "dual-engine-public-surface-v2.json"
SELECTOR = ROOT / "crates" / "syntavra-cli" / "src" / "bin" / "syntavra.rs"
INVENTORY_TEST = ROOT / "tests" / "runtime" / "test_dual_engine_public_surface_r38.py"


def replace_exact(path: Path, old: str, new: str, label: str) -> bool:
    source = path.read_text(encoding="utf-8")
    old_count = source.count(old)
    new_count = source.count(new)
    if old_count == 0 and new_count == 1:
        return False
    if old_count != 1 or new_count != 0:
        raise RuntimeError(
            f"{label}: expected one legacy or one canonical fragment in {path}; "
            f"legacy={old_count}, canonical={new_count}"
        )
    path.write_text(source.replace(old, new, 1), encoding="utf-8", newline="\n")
    return True


def replace_pattern(path: Path, pattern: str, replacement: str, label: str) -> bool:
    source = path.read_text(encoding="utf-8")
    rendered, count = re.subn(pattern, replacement, source, count=1)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one pattern match in {path}, found {count}")
    if rendered == source:
        return False
    path.write_text(rendered, encoding="utf-8", newline="\n")
    return True


def synchronize_generated_count_handoff() -> bool:
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    rust = contract["rust_surface"]
    native_count = int(rust["native_public_command_count"])
    missing_count = int(rust["missing_native_public_command_count"])
    changed = False
    changed |= replace_pattern(
        SELECTOR,
        r"const NATIVE_COMMAND_COUNT: u64 = [0-9]+;",
        f"const NATIVE_COMMAND_COUNT: u64 = {native_count};",
        "selector native command count",
    )
    changed |= replace_pattern(
        INVENTORY_TEST,
        r'assert result\["rust"\]\["native_public_command_count"\] == [0-9]+',
        f'assert result["rust"]["native_public_command_count"] == {native_count}',
        "inventory native command count",
    )
    changed |= replace_pattern(
        INVENTORY_TEST,
        r'assert result\["rust"\]\["missing_native_public_command_count"\] == [0-9]+',
        f'assert result["rust"]["missing_native_public_command_count"] == {missing_count}',
        "inventory missing command count",
    )
    return changed


def repair_init_selector_contract() -> bool:
    changed = False
    legacy_single = 'Some("rollout-tail" | "context-stress" | "claim" | "context")'
    canonical_single = (
        'Some("rollout-tail" | "context-stress" | "claim" | "context" | "init")'
    )
    changed |= replace_exact(
        RUNTIME_REPAIR,
        f"SINGLE_SEGMENT_PATH_CANONICAL = (\n    '{legacy_single}'\n)",
        f"SINGLE_SEGMENT_PATH_CANONICAL = (\n    '{canonical_single}'\n)",
        "init single-segment repair contract",
    )

    source = SELECTOR.read_text(encoding="utf-8")
    legacy_count = source.count(legacy_single)
    canonical_count = source.count(canonical_single)
    if legacy_count == 1 and canonical_count == 0:
        source = source.replace(legacy_single, canonical_single, 1)
        SELECTOR.write_text(source, encoding="utf-8", newline="\n")
        changed = True
    elif legacy_count != 0 or canonical_count != 1:
        raise RuntimeError(
            "init single-segment selector invariant failed: "
            f"legacy={legacy_count}, canonical={canonical_count}"
        )

    source = SELECTOR.read_text(encoding="utf-8")
    required_tokens = (
        '"--skill-root"',
        '"--host"',
        '"--mcp-profile"',
        'value.starts_with("--skill-root=")',
        'value.starts_with("--host=")',
        'value.starts_with("--mcp-profile=")',
        'value.starts_with("--codex-home=")',
        'value.starts_with("--rollout=")',
        'value.starts_with("--state-file=")',
        'value.starts_with("--session-hint=")',
    )
    missing = [token for token in required_tokens if token not in source]
    if missing:
        raise RuntimeError(f"selector canonical option tokens missing: {missing}")
    return changed


def repair_context_contract() -> bool:
    changed = False
    changed |= replace_exact(
        RUNTIME_REPAIR,
        '''def repair_context_dispatch(source: str) -> tuple[str, int]:
    return exact_repair(
        source,
        CONTEXT_DEFAULT_DISPATCH_LEGACY,
        CONTEXT_DEFAULT_DISPATCH_CANONICAL,
        "context pack selector dispatch",
    )''',
        '''def repair_context_dispatch(source: str) -> tuple[str, int]:
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
    )''',
        "context dispatch rustfmt-stable repair",
    )
    changed |= replace_exact(
        RUNTIME_REPAIR,
        '''            canonical_context.count(CONTEXT_DEFAULT_DISPATCH_CANONICAL) == 1
            and CONTEXT_DEFAULT_DISPATCH_LEGACY not in canonical_context''',
        '''            'window[0] == "context"' in canonical_context
            and 'window[1] == "pack"' in canonical_context
            and "pack(arguments)" in canonical_context
            and "evaluate(arguments)" in canonical_context
            and CONTEXT_DEFAULT_DISPATCH_LEGACY not in canonical_context''',
        "context dispatch rustfmt-stable invariant",
    )
    return changed


def repair_stats_contract() -> bool:
    changed = False
    changed |= replace_exact(
        RUNTIME_REPAIR,
        """STATS_FLOAT_HELPER_LEGACY = '''
fn identity_string(value: &Value) -> Option<String> {'''
STATS_FLOAT_HELPER_CANONICAL = '''
fn python_json_float(number: f64) -> Value {
    if number == 0.0 {
        Value::from(0)
    } else {
        Value::from(number)
    }
}

fn identity_string(value: &Value) -> Option<String> {'''""",
        """STATS_FLOAT_HELPER_LEGACY = '''    } else {
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

fn identity_string(value: &Value) -> Option<String> {'''""",
        "stats float helper context",
    )
    changed |= replace_exact(
        RUNTIME_REPAIR,
        """STATS_FLOAT_REPAIRS = (
    (
        '''            "wall_time_ms": wall_time_ms,''',
        '''            "wall_time_ms": python_json_float(wall_time_ms),''',
        "stats wall-time numeric type parity",
    ),
    (
        '''            "cost_usd": cost_usd,''',
        '''            "cost_usd": python_json_float(cost_usd),''',
        "stats cost numeric type parity",
    ),
    (
        '''            "compaction_wall_time_ms": compaction_ms,''',
        '''            "compaction_wall_time_ms": python_json_float(compaction_ms),''',
        "stats compaction numeric type parity",
    ),
)""",
        """STATS_USAGE_FLOATS_LEGACY = '''            "output_tokens": output_tokens,
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
""",
        "stats numeric payload fragments",
    )
    changed |= replace_exact(
        RUNTIME_REPAIR,
        '''    rendered, count = exact_repairs(rendered, STATS_FLOAT_REPAIRS)
    return rendered, changed + count''',
        '''    rendered, usage_count = exact_repair(
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
    return rendered, changed + usage_count + compaction_count''',
        "stats numeric repair implementation",
    )
    changed |= replace_exact(
        RUNTIME_REPAIR,
        '''            and all(
                canonical_stats.count(canonical) == 1 and legacy not in canonical_stats
                for legacy, canonical, _ in STATS_FLOAT_REPAIRS
            )''',
        '''            and canonical_stats.count("fn python_json_float(number: f64) -> Value {") == 1
            and canonical_stats.count(STATS_USAGE_FLOATS_CANONICAL) == 1
            and canonical_stats.count(STATS_COMPACTION_FLOAT_CANONICAL) == 1''',
        "stats numeric repair invariant",
    )
    return changed


def repair_benchmark_newlines() -> bool:
    changed = False
    changed |= replace_exact(
        BENCHMARK_HARNESS,
        '        file_path.write_text("\\n".join(body) + "\\n", encoding="utf-8")',
        '        file_path.write_text("\\n".join(body) + "\\n", encoding="utf-8", newline="\\n")',
        "benchmark module newline",
    )
    changed |= replace_exact(
        BENCHMARK_HARNESS,
        '        file_path.write_text(f"def fault_{index}():\\n    raise RuntimeError(\'SC_FAULT_{index}\')\\n", encoding="utf-8")',
        '        file_path.write_text(f"def fault_{index}():\\n    raise RuntimeError(\'SC_FAULT_{index}\')\\n", encoding="utf-8", newline="\\n")',
        "benchmark fault newline",
    )
    return changed


def repair_memory_utf8_decode() -> bool:
    return replace_exact(
        MEMORY_DIFFERENTIAL,
        '''        capture_output=True,
        text=True,
        timeout=240,''',
        '''        capture_output=True,
        encoding="utf-8",
        errors="strict",
        timeout=240,''',
        "memory differential UTF-8 decode",
    )


def repair_structural_upsert_sql() -> bool:
    return replace_exact(
        NATIVE_STRUCTURAL,
        "             ON CONFLICT(path) DO UPDATE SET\\\n               content_hash=excluded.content_hash,",
        "             ON CONFLICT(path) DO UPDATE SET \\\n               content_hash=excluded.content_hash,",
        "structural upsert SET separator",
    )


def repair_pytest_status_handoff(path: Path) -> bool:
    source = path.read_text(encoding="utf-8")
    if "pytest-exit-code.txt" in source:
        raise RuntimeError(f"legacy pytest status file remains in {path}")
    stale = (
        "$LASTEXITCODE" in source
        or "$pytestStatus" in source
        or re.search(r"(?<![A-Za-z0-9_])\$\?(?![A-Za-z0-9_])", source) is not None
    )
    if stale:
        raise RuntimeError(f"legacy pytest status token remains in {path}")
    return False


def workflow_by_name(name: str) -> Path | None:
    matches = []
    for path in sorted(WORKFLOWS.glob("*.y*ml")):
        source = path.read_text(encoding="utf-8")
        if re.search(rf"(?m)^name:\s*[\"']?{re.escape(name)}[\"']?\s*$", source):
            matches.append(path)
    if not matches:
        return None
    if len(matches) != 1:
        raise RuntimeError(f"workflow {name!r}: expected at most one file, found {matches}")
    return matches[0]


def validate_status_handoff(path: Path) -> None:
    repair_pytest_status_handoff(path)


def main() -> int:
    changed = []
    if synchronize_generated_count_handoff():
        changed.append("generated-count-handoff")
    if repair_init_selector_contract():
        changed.append("init-selector-contract")
    if repair_context_contract():
        changed.append("runtime-context-contract")
    if repair_stats_contract():
        changed.append("runtime-stats-contract")
    if repair_benchmark_newlines():
        changed.append("benchmark-newlines")
    if repair_memory_utf8_decode():
        changed.append("memory-utf8-decode")
    if repair_structural_upsert_sql():
        changed.append("structural-upsert-sql")
    for workflow_name in ("Validate Syntavra Package", "Syntavra Repository Hardening"):
        path = workflow_by_name(workflow_name)
        if path is not None:
            validate_status_handoff(path)
    print(json.dumps({"ok": True, "changed": changed}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
