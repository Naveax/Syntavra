#!/usr/bin/env python3
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"


def replace_exact(path: Path, old: str, new: str, label: str) -> bool:
    source = path.read_text(encoding="utf-8")
    old_count = source.count(old)
    new_count = source.count(new)
    if old_count == 0 and new_count >= 1:
        return False
    if old_count != 1:
        raise RuntimeError(f"{label}: expected one legacy fragment in {path}, found {old_count}")
    rendered = source.replace(old, new, 1)
    path.write_text(rendered, encoding="utf-8", newline="\n")
    return True


def repair_selector_option_contract() -> bool:
    path = ROOT / "tools" / "repair_r38_selector_option_values.py"
    changed = False
    changed |= replace_exact(
        path,
        '''CANONICAL_TRUNCATION = """    if matches!(\n        positional.first().map(String::as_str),\n        Some("rollout-tail" | "context-stress")\n    ) {\n        positional.truncate(1);\n"""''',
        '''CANONICAL_TRUNCATION = """    if matches!(\n        positional.first().map(String::as_str),\n        Some("rollout-tail" | "context-stress" | "claim" | "context")\n    ) {\n        positional.truncate(1);\n"""''',
        "selector canonical truncation",
    )
    changed |= replace_exact(
        path,
        '''    if command_path.count('"rollout-tail"') != 1:\n        raise RuntimeError(f"rollout-tail truncation invariant failed in {path}")\n    if command_path.count('"context-stress"') != 1:\n        raise RuntimeError(f"context-stress truncation invariant failed in {path}")\n    if command_path.count("positional.truncate(1);") != 1:\n        raise RuntimeError(f"single-segment truncation must have one branch in {path}")\n    if 'Some("rollout-tail" | "context-stress")' not in command_path:\n        raise RuntimeError(f"nested selector truncation pattern missing in {path}")''',
        '''    for route in ("rollout-tail", "context-stress", "claim", "context"):\n        if command_path.count(f'"{route}"') != 1:\n            raise RuntimeError(f"{route} truncation invariant failed in {path}")\n    if command_path.count("positional.truncate(1);") != 1:\n        raise RuntimeError(f"single-segment truncation must have one branch in {path}")\n    if 'Some("rollout-tail" | "context-stress" | "claim" | "context")' not in command_path:\n        raise RuntimeError(f"canonical selector truncation pattern missing in {path}")''',
        "selector validation invariant",
    )
    return changed


def repair_benchmark_parity() -> bool:
    changed = False
    source_path = ROOT / "crates" / "syntavra-cli" / "src" / "native_static_surfaces.rs"
    changed |= replace_exact(
        source_path,
        '        "20X" => Ok(38.337_350_566_771_11),',
        '        "20X" => Ok(38.337_350_566_771_08),',
        "20X benchmark parity",
    )
    changed |= replace_exact(
        source_path,
        '        "30X" => Ok(63.345_278_851_520_476),',
        '        "30X" => Ok(63.345_278_851_520_46),',
        "30X benchmark parity",
    )

    repair_path = ROOT / "tools" / "repair_r38_runtime_regressions.py"
    replacements = (
        (
            "BENCHMARK_SCORE_20X_LEGACY = '\"20X\" => Ok(38.337_350_566_771_08),'\nBENCHMARK_SCORE_20X_CANONICAL = '\"20X\" => Ok(38.337_350_566_771_11),'",
            "BENCHMARK_SCORE_20X_LEGACY = '\"20X\" => Ok(38.337_350_566_771_11),'\nBENCHMARK_SCORE_20X_CANONICAL = '\"20X\" => Ok(38.337_350_566_771_08),'",
            "20X benchmark repair direction",
        ),
        (
            "BENCHMARK_SCORE_30X_LEGACY = '\"30X\" => Ok(63.345_278_851_520_46),'\nBENCHMARK_SCORE_30X_CANONICAL = '\"30X\" => Ok(63.345_278_851_520_476),'",
            "BENCHMARK_SCORE_30X_LEGACY = '\"30X\" => Ok(63.345_278_851_520_476),'\nBENCHMARK_SCORE_30X_CANONICAL = '\"30X\" => Ok(63.345_278_851_520_46),'",
            "30X benchmark repair direction",
        ),
    )
    for old, new, label in replacements:
        changed |= replace_exact(repair_path, old, new, label)
    return changed


def repair_runtime_repair_contract() -> bool:
    path = ROOT / "tools" / "repair_r38_runtime_regressions.py"
    changed = False
    changed |= replace_exact(
        path,
        '''STATS_FLOAT_REPAIRS = (
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
)''',
        '''STATS_USAGE_FLOATS_LEGACY = '''            "output_tokens": output_tokens,
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
''',
        "stats numeric repair fragments",
    )
    changed |= replace_exact(
        path,
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
        path,
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


def repair_runtime_matrix() -> bool:
    path = WORKFLOWS / "validate-fusion-runtime.yml"
    old = '''      - uses: actions/setup-python@v5\n        with:\n          python-version: ${{ matrix.python }}\n      - name: Install runtime and test dependencies\n'''
    new = '''      - uses: actions/setup-python@v5\n        with:\n          python-version: ${{ matrix.python }}\n      - uses: dtolnay/rust-toolchain@1.82.0\n      - name: Build native selector for runtime differential tests\n        shell: bash\n        run: |\n          cargo build --locked -p syntavra-cli --bin syntavra\n          extension=""\n          if [[ "${RUNNER_OS}" == "Windows" ]]; then\n            extension=".exe"\n          fi\n          echo "SYNTAVRA_R38_SELECTOR=$CARGO_TARGET_DIR/debug/syntavra${extension}" >> "$GITHUB_ENV"\n      - name: Install runtime and test dependencies\n'''
    return replace_exact(path, old, new, "runtime selector build")


def _workflow_by_name(name: str) -> Path:
    matches = []
    for path in sorted(WORKFLOWS.glob("*.y*ml")):
        source = path.read_text(encoding="utf-8")
        if re.search(rf"(?m)^name:\s*[\"']?{re.escape(name)}[\"']?\s*$", source):
            matches.append(path)
    if len(matches) != 1:
        raise RuntimeError(f"workflow {name!r}: expected one file, found {matches}")
    return matches[0]


def _pytest_command_end(lines: list[str], start: int, powershell: bool) -> int:
    end = start
    marker = "`" if powershell else "\\"
    while lines[end].rstrip().endswith(marker):
        end += 1
        if end >= len(lines):
            raise RuntimeError("unterminated pytest command continuation")
    return end


def repair_pytest_status_handoff(path: Path) -> bool:
    source = path.read_text(encoding="utf-8")
    if "pytest-exit-code.txt" not in source:
        stale_status_tokens = (
            "$LASTEXITCODE" in source
            or "$pytestStatus" in source
            or re.search(r"(?<![A-Za-z0-9_])\$\?(?![A-Za-z0-9_])", source) is not None
        )
        if stale_status_tokens:
            raise RuntimeError(f"legacy pytest status token remains without a status file in {path}")
        return False
    lines = source.splitlines()
    changed = False

    status_indexes = [index for index, line in enumerate(lines) if "pytest-exit-code.txt" in line]
    for status_index in reversed(status_indexes):
        line = lines[status_index]
        powershell = "$LASTEXITCODE" in line or "$pytestStatus" in line
        if powershell:
            replaced = line.replace("$LASTEXITCODE", "$pytestStatus")
            capture = "          $pytestStatus = $LASTEXITCODE"
        else:
            replaced = line.replace('"$?"', '"${pytest_status}"').replace("'$?'", "'${pytest_status}'")
            replaced = re.sub(r"(?<![A-Za-z0-9_])\$\?(?![A-Za-z0-9_])", "${pytest_status}", replaced)
            capture = "          pytest_status=$?"
        if replaced != line:
            lines[status_index] = replaced
            changed = True
        elif ("$pytestStatus" if powershell else "${pytest_status}") not in line:
            continue

        search_start = max(0, status_index - 120)
        pytest_candidates = [
            index
            for index in range(search_start, status_index)
            if re.search(r"(?:python\s+-m\s+pytest|(?<![A-Za-z0-9_-])pytest(?:\s|$))", lines[index])
        ]
        if not pytest_candidates:
            raise RuntimeError(f"pytest command not found before status handoff in {path}:{status_index + 1}")
        command_start = pytest_candidates[-1]
        command_end = _pytest_command_end(lines, command_start, powershell)
        segment = "\n".join(lines[command_start : status_index + 1])
        if capture.strip() not in segment:
            if powershell:
                lines.insert(command_end + 1, capture)
            else:
                indent = lines[command_start][: len(lines[command_start]) - len(lines[command_start].lstrip())]
                if command_start == 0 or lines[command_start - 1].strip() != "set +e":
                    lines.insert(command_start, f"{indent}set +e")
                    command_end += 1
                lines.insert(command_end + 1, f"{indent}pytest_status=$?")
                lines.insert(command_end + 2, f"{indent}set -e")
            changed = True

    rendered = "\n".join(lines) + "\n"
    if changed:
        stale_status_lines = [
            line
            for line in rendered.splitlines()
            if "pytest-exit-code.txt" in line
            and ("$LASTEXITCODE" in line or re.search(r"(?<![A-Za-z0-9_])\$\?(?![A-Za-z0-9_])", line))
        ]
        if stale_status_lines:
            raise RuntimeError(f"stale pytest status handoff remains in {path}: {stale_status_lines}")
        path.write_text(rendered, encoding="utf-8", newline="\n")
    return changed


def main() -> int:
    changed: list[str] = []
    # Self-mutating GitHub App runs cannot push workflow-file changes without
    # the separate workflows permission. Keep source/metadata repair atomic;
    # the runtime-matrix workflow change is handled as an explicit gate.
    operations = (
        ("selector-option-contract", repair_selector_option_contract),
        ("benchmark-parity", repair_benchmark_parity),
        ("runtime-repair-contract", repair_runtime_repair_contract),
    )
    for label, operation in operations:
        if operation():
            changed.append(label)

    for workflow_name in ("Validate Syntavra Package", "Syntavra Repository Hardening"):
        path = _workflow_by_name(workflow_name)
        if repair_pytest_status_handoff(path):
            changed.append(path.relative_to(ROOT).as_posix())

    print(json.dumps({"ok": True, "changed": changed}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
