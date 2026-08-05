#!/usr/bin/env python3
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
RUNTIME_REPAIR = ROOT / "tools" / "repair_r38_runtime_regressions.py"
BENCHMARK_HARNESS = ROOT / "syntavra_runtime" / "benchmark_harness.py"


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
        '''            and canonical_stats.count(STATS_USAGE_FLOATS_CANONICAL) == 1
            and STATS_USAGE_FLOATS_LEGACY not in canonical_stats
            and canonical_stats.count(STATS_COMPACTION_FLOAT_CANONICAL) == 1
            and STATS_COMPACTION_FLOAT_LEGACY not in canonical_stats''',
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


def workflow_by_name(name: str) -> Path:
    matches = []
    for path in sorted(WORKFLOWS.glob("*.y*ml")):
        source = path.read_text(encoding="utf-8")
        if re.search(rf"(?m)^name:\s*[\"']?{re.escape(name)}[\"']?\s*$", source):
            matches.append(path)
    if len(matches) != 1:
        raise RuntimeError(f"workflow {name!r}: expected one file, found {matches}")
    return matches[0]


def validate_status_handoff(path: Path) -> None:
    source = path.read_text(encoding="utf-8")
    if "pytest-exit-code.txt" in source:
        raise RuntimeError(f"legacy pytest status file remains in {path}")
    stale = (
        "$LASTEXITCODE" in source
        or "$pytestStatus" in source
        or re.search(r"(?<![A-Za-z0-9_])\$\?(?![A-Za-z0-9_])", source) is not None
    )
    if stale:
        raise RuntimeError(f"orphaned pytest exit token remains in {path}")


def main() -> int:
    changed = []
    if repair_stats_contract():
        changed.append("runtime-stats-contract")
    if repair_benchmark_newlines():
        changed.append("benchmark-newlines")
    for workflow_name in ("Validate Syntavra Package", "Syntavra Repository Hardening"):
        validate_status_handoff(workflow_by_name(workflow_name))
    print(json.dumps({"ok": True, "changed": changed}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
