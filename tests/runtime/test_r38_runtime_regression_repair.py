from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from tools.repair_r38_ci_closure import repair_pytest_status_handoff
from tools.repair_r38_runtime_regressions import (
    BENCHMARK_SCORE_20X_CANONICAL,
    BENCHMARK_SCORE_20X_LEGACY,
    BENCHMARK_SCORE_30X_CANONICAL,
    BENCHMARK_SCORE_30X_LEGACY,
    JSON_ARGUMENT_CANONICAL,
    JSON_ARGUMENT_LEGACY,
    SINGLE_SEGMENT_PATH_CANONICAL,
    SINGLE_SEGMENT_PATH_LEGACY,
    repair_benchmark_scores,
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


def test_benchmark_score_repair_is_exact_and_idempotent() -> None:
    source = "\n".join((BENCHMARK_SCORE_20X_LEGACY, BENCHMARK_SCORE_30X_LEGACY))
    rendered, first_count = repair_benchmark_scores(source)
    repeated, second_count = repair_benchmark_scores(rendered)
    assert first_count == 2
    assert second_count == 0
    assert BENCHMARK_SCORE_20X_LEGACY not in rendered
    assert BENCHMARK_SCORE_30X_LEGACY not in rendered
    assert rendered.count(BENCHMARK_SCORE_20X_CANONICAL) == 1
    assert rendered.count(BENCHMARK_SCORE_30X_CANONICAL) == 1
    assert repeated == rendered


def test_ci_status_handoff_accepts_canonical_status_file_free_workflow(tmp_path: Path) -> None:
    workflow = tmp_path / "canonical.yml"
    source = """name: Canonical\njobs:\n  test:\n    steps:\n      - name: Run tests\n        id: package-tests\n        continue-on-error: true\n        run: python -m pytest -q\n      - name: Enforce tests\n        if: steps.package-tests.outcome == 'failure'\n        run: exit 1\n"""
    workflow.write_text(source, encoding="utf-8", newline="\n")

    assert repair_pytest_status_handoff(workflow) is False
    assert workflow.read_text(encoding="utf-8") == source


def test_ci_status_handoff_rejects_orphaned_legacy_exit_token(tmp_path: Path) -> None:
    workflow = tmp_path / "stale.yml"
    workflow.write_text(
        "name: Stale\njobs:\n  test:\n    steps:\n      - run: |\n          python -m pytest -q\n          echo $?\n",
        encoding="utf-8",
        newline="\n",
    )

    with pytest.raises(RuntimeError, match="legacy pytest status token"):
        repair_pytest_status_handoff(workflow)


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


def test_one_shot_runs_known_regression_differentials() -> None:
    marker = ROOT / ".github" / "r38-runtime-repair"
    if not marker.is_file():
        pytest.skip("one-shot differential validation is not requested")
    completed = subprocess.run(
        [sys.executable, "tools/validate_r38_regression_closure.py"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=1800,
    )
    if completed.returncode != 0:
        if completed.stdout:
            sys.stdout.write(completed.stdout)
            sys.stdout.flush()
        if completed.stderr:
            sys.stderr.write(completed.stderr)
            sys.stderr.flush()
    assert completed.returncode == 0, {
        "stdout_tail": completed.stdout[-2000:],
        "stderr_tail": completed.stderr[-2000:],
    }
