from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import subprocess
import sys
import time
from functools import lru_cache
from pathlib import Path
from typing import Any

import pytest

from syntavra_runtime.memory import PersistentMemory
from syntavra_runtime.util import stable_project_id

ROOT = Path(__file__).resolve().parents[2]


@lru_cache(maxsize=1)
def _selector_binary() -> Path:
    if shutil.which("cargo") is None:
        pytest.skip("Cargo is required for the real Rust binary differential")
    completed = subprocess.run(
        ["cargo", "build", "--quiet", "--locked", "--bins"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert completed.returncode == 0, {
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }
    suffix = ".exe" if sys.platform == "win32" else ""
    selector = ROOT / "target" / "debug" / f"syntavra{suffix}"
    assert selector.is_file(), selector
    return selector


def _run(engine: str, project: Path, state_root: Path, *arguments: str) -> tuple[int, Any]:
    prefix = (
        [sys.executable, "-m", "syntavra_runtime.engine_entry"]
        if engine == "python"
        else [str(_selector_binary())]
    )
    completed = subprocess.run(
        [
            *prefix,
            "--engine",
            engine,
            "--project",
            str(project),
            "--state-root",
            str(state_root),
            *arguments,
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        encoding="utf-8",
        errors="strict",
        timeout=240,
    )
    assert completed.stdout.strip(), {
        "engine": engine,
        "arguments": arguments,
        "returncode": completed.returncode,
        "stderr": completed.stderr,
    }
    return completed.returncode, json.loads(completed.stdout)


def _stable_memory(value: dict[str, Any], *, include_score: bool = False) -> dict[str, Any]:
    output = dict(value)
    output.pop("memory_id", None)
    output.pop("created_at", None)
    if not include_score:
        output.pop("score", None)
    return output


def _python_content_hash(memory_class: str, text: str, tags: tuple[str, ...]) -> str:
    payload = json.dumps(
        {"class": memory_class, "text": text, "tags": tags},
        ensure_ascii=False,
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def test_native_memory_add_matches_python_hash_and_deduplication(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    outputs: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}

    for engine in ("python", "rust"):
        state_root = tmp_path / f"{engine}-state"
        first_code, first = _run(
            engine,
            project,
            state_root,
            "memory",
            "add",
            "fact",
            "  Kalıcı alpha bilgisi  ",
            "--confidence",
            "0.75",
            "--source",
            "differential",
            "--expires-at",
            "4102444800",
            "--tag",
            "zeta",
            "--tag",
            "alpha",
            "--tag",
            "zeta",
        )
        second_code, second = _run(
            engine,
            project,
            state_root,
            "memory",
            "add",
            "fact",
            "Kalıcı alpha bilgisi",
            "--confidence",
            "0.25",
            "--source",
            "ignored-by-dedup",
            "--expires-at",
            "1",
            "--tag",
            "alpha",
            "--tag",
            "zeta",
        )
        assert first_code == second_code == 0
        assert second == first
        assert first["tags"] == ["alpha", "zeta"]
        assert first["text"] == "Kalıcı alpha bilgisi"
        outputs[engine] = (first, second)

        database = sqlite3.connect(state_root / "memory.sqlite3")
        try:
            row = database.execute(
                "SELECT content_hash,COUNT(*) FROM memories GROUP BY content_hash"
            ).fetchone()
        finally:
            database.close()
        assert row == (
            _python_content_hash(
                "fact", "Kalıcı alpha bilgisi", ("alpha", "zeta")
            ),
            1,
        )

    assert _stable_memory(outputs["rust"][0]) == _stable_memory(outputs["python"][0])


def _memory_fixture(project: Path, state_root: Path) -> dict[str, str]:
    database_path = state_root / "memory.sqlite3"
    memory = PersistentMemory(
        database_path,
        project_id=stable_project_id(project),
        user_id="default",
    )
    active = memory.add(
        "fact",
        "alpha active",
        confidence=0.8,
        provenance={"source": "fixture"},
        tags=("active",),
    )
    expired = memory.add(
        "fact",
        "alpha expired",
        confidence=0.95,
        provenance={"source": "fixture"},
        expires_at=1.0,
        tags=("expired",),
    )
    note = memory.add(
        "note",
        "alpha note",
        confidence=0.4,
        provenance={"source": "fixture"},
    )
    source = memory.add("graph", "graph source", provenance={"source": "fixture"})
    target_low = memory.add("graph", "graph low", provenance={"source": "fixture"})
    target_high = memory.add("graph", "graph high", provenance={"source": "fixture"})
    alice = PersistentMemory(
        database_path,
        project_id=stable_project_id(project),
        user_id="alice",
    ).add("fact", "alpha alice", provenance={"source": "fixture"})

    fixed = {
        active.memory_id: 1_700_000_300.0,
        expired.memory_id: 1_700_000_200.0,
        note.memory_id: 1_700_000_100.0,
        source.memory_id: 1_700_000_000.0,
        target_low.memory_id: 1_699_999_900.0,
        target_high.memory_id: 1_699_999_800.0,
        alice.memory_id: 1_699_999_700.0,
    }
    database = sqlite3.connect(database_path)
    try:
        database.executemany(
            "UPDATE memories SET created_at=? WHERE memory_id=?",
            [(created_at, memory_id) for memory_id, created_at in fixed.items()],
        )
        database.commit()
        database.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        database.close()
    return {
        "active": active.memory_id,
        "expired": expired.memory_id,
        "note": note.memory_id,
        "source": source.memory_id,
        "target_low": target_low.memory_id,
        "target_high": target_high.memory_id,
        "alice": alice.memory_id,
    }


def _clone_fixture(source: Path, python_state: Path, rust_state: Path) -> None:
    shutil.copytree(source, python_state)
    shutil.copytree(source, rust_state)


def _assert_search_equivalent(python_value: dict[str, Any], rust_value: dict[str, Any]) -> None:
    assert rust_value["mode"] == python_value["mode"]
    assert [row["memory_id"] for row in rust_value["results"]] == [
        row["memory_id"] for row in python_value["results"]
    ]
    for python_row, rust_row in zip(
        python_value["results"], rust_value["results"], strict=True
    ):
        assert _stable_memory(rust_row) == _stable_memory(python_row)
        assert rust_row["score"] == pytest.approx(python_row["score"], abs=1e-7)


def test_native_memory_search_matches_filters_scope_and_scoring(tmp_path: Path) -> None:
    project = tmp_path / "project"
    source = tmp_path / "source"
    python_state = tmp_path / "python-state"
    rust_state = tmp_path / "rust-state"
    project.mkdir()
    ids = _memory_fixture(project, source)
    _clone_fixture(source, python_state, rust_state)

    python_code, python_result = _run(
        "python",
        project,
        python_state,
        "memory",
        "search",
        "alpha",
        "--memory-class",
        "fact",
        "--limit",
        "10",
    )
    rust_code, rust_result = _run(
        "rust",
        project,
        rust_state,
        "memory",
        "search",
        "alpha",
        "--memory-class",
        "fact",
        "--limit",
        "10",
    )
    assert rust_code == python_code == 0
    _assert_search_equivalent(python_result, rust_result)
    assert [row["memory_id"] for row in rust_result["results"]] == [ids["active"]]

    python_all = _run(
        "python",
        project,
        python_state,
        "memory",
        "search",
        "alpha",
        "--memory-class",
        "fact",
        "--include-expired",
    )[1]
    rust_all = _run(
        "rust",
        project,
        rust_state,
        "memory",
        "search",
        "alpha",
        "--memory-class",
        "fact",
        "--include-expired",
    )[1]
    _assert_search_equivalent(python_all, rust_all)
    assert {row["memory_id"] for row in rust_all["results"]} == {
        ids["active"],
        ids["expired"],
    }

    python_alice = _run(
        "python",
        project,
        python_state,
        "memory",
        "search",
        "alpha",
        "--user-id",
        "alice",
    )[1]
    rust_alice = _run(
        "rust",
        project,
        rust_state,
        "memory",
        "search",
        "alpha",
        "--user-id",
        "alice",
    )[1]
    _assert_search_equivalent(python_alice, rust_alice)
    assert [row["memory_id"] for row in rust_alice["results"]] == [ids["alice"]]


def test_native_memory_link_and_neighbors_match_python(tmp_path: Path) -> None:
    project = tmp_path / "project"
    source = tmp_path / "source"
    python_state = tmp_path / "python-state"
    rust_state = tmp_path / "rust-state"
    project.mkdir()
    ids = _memory_fixture(project, source)
    _clone_fixture(source, python_state, rust_state)

    for engine, state_root in (("python", python_state), ("rust", rust_state)):
        assert _run(
            engine,
            project,
            state_root,
            "memory",
            "link",
            ids["source"],
            "supports",
            ids["target_low"],
            "--weight",
            "1.0",
        ) == (0, {"ok": True})
        assert _run(
            engine,
            project,
            state_root,
            "memory",
            "link",
            ids["source"],
            "supports",
            ids["target_high"],
            "--weight",
            "3.0",
        ) == (0, {"ok": True})

    python_code, python_result = _run(
        "python",
        project,
        python_state,
        "memory",
        "neighbors",
        ids["source"],
        "--relation",
        "supports",
    )
    rust_code, rust_result = _run(
        "rust",
        project,
        rust_state,
        "memory",
        "neighbors",
        ids["source"],
        "--relation",
        "supports",
    )
    assert rust_code == python_code == 0
    assert rust_result == python_result
    assert [row["memory"]["memory_id"] for row in rust_result["results"]] == [
        ids["target_high"],
        ids["target_low"],
    ]
    assert [row["weight"] for row in rust_result["results"]] == [3.0, 1.0]


def test_native_memory_empty_search_and_neighbors_match_python(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    python_state = tmp_path / "python-state"
    rust_state = tmp_path / "rust-state"

    python_search = _run(
        "python", project, python_state, "memory", "search", "missing"
    )
    rust_search = _run("rust", project, rust_state, "memory", "search", "missing")
    assert rust_search == python_search
    assert rust_search[1]["results"] == []

    python_neighbors = _run(
        "python",
        project,
        python_state,
        "memory",
        "neighbors",
        "missing-memory",
    )
    rust_neighbors = _run(
        "rust",
        project,
        rust_state,
        "memory",
        "neighbors",
        "missing-memory",
    )
    assert rust_neighbors == python_neighbors == (0, {"results": []})
