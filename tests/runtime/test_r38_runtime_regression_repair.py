from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from tools.repair_r38_runtime_regressions import (
    JSON_ARGUMENT_CANONICAL,
    JSON_ARGUMENT_LEGACY,
    SINGLE_SEGMENT_PATH_CANONICAL,
    SINGLE_SEGMENT_PATH_LEGACY,
    repair_inline_json_argument,
    repair_single_segment_command_paths,
    repaired_source,
)

ROOT = Path(__file__).resolve().parents[2]


def test_runtime_sql_repair_inserts_explicit_clause_space() -> None:
    source = (
        '"CREATE INDEX IF NOT EXISTS memories_scope_idx\\\n'
        '  ON memories(project_id);\\\n'
        '  SELECT memory_id\\\n'
        '  FROM memories"'
    )
    rendered, count = repaired_source(source)
    assert count == 2
    assert "memories_scope_idx \\\n  ON memories" in rendered
    assert "SELECT memory_id \\\n  FROM memories" in rendered


def test_runtime_sql_repair_is_idempotent() -> None:
    source = '"SELECT memory_id \\\n  FROM memories"'
    rendered, first_count = repaired_source(source)
    repeated, second_count = repaired_source(rendered)
    assert first_count == 0
    assert second_count == 0
    assert repeated == source


def test_single_segment_command_path_repair_is_exact_and_idempotent() -> None:
    source = f"prefix {SINGLE_SEGMENT_PATH_LEGACY} suffix"
    rendered, first_count = repair_single_segment_command_paths(source)
    repeated, second_count = repair_single_segment_command_paths(rendered)
    assert first_count == 1
    assert second_count == 0
    assert SINGLE_SEGMENT_PATH_LEGACY not in rendered
    assert rendered.count(SINGLE_SEGMENT_PATH_CANONICAL) == 1
    assert repeated == rendered


def test_inline_json_path_probe_repair_is_exact_and_idempotent() -> None:
    source = f"prefix\n{JSON_ARGUMENT_LEGACY}suffix\n"
    rendered, first_count = repair_inline_json_argument(source)
    repeated, second_count = repair_inline_json_argument(rendered)
    assert first_count == 1
    assert second_count == 0
    assert JSON_ARGUMENT_LEGACY not in rendered
    assert rendered.count(JSON_ARGUMENT_CANONICAL) == 1
    assert repeated == rendered


def test_committed_runtime_sources_are_canonical() -> None:
    completed = subprocess.run(
        [sys.executable, "tools/repair_r38_runtime_regressions.py", "--check"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, {
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }
