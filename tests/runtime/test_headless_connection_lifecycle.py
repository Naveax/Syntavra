from __future__ import annotations

from pathlib import Path

from syntavra_runtime.headless_runtime import HeadlessRuntime
from syntavra_runtime.runtime_evidence import RuntimeEvidenceGraph


def _assert_immediate_rename(database: Path, moved: Path) -> None:
    database.replace(moved)
    moved.replace(database)
    assert database.is_file()


def test_headless_runtime_releases_sqlite_file_handle(tmp_path) -> None:
    database = tmp_path / "headless.sqlite3"
    runtime = HeadlessRuntime(database, tmp_path / "state")

    assert runtime.stats()["ok"] is True
    _assert_immediate_rename(database, tmp_path / "headless.moved.sqlite3")


def test_runtime_evidence_releases_sqlite_file_handle(tmp_path) -> None:
    database = tmp_path / "runtime-evidence.sqlite3"
    graph = RuntimeEvidenceGraph(database)

    assert graph.stats()["ok"] is True
    _assert_immediate_rename(database, tmp_path / "runtime-evidence.moved.sqlite3")
