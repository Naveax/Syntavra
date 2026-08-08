from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from functools import lru_cache
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]


@lru_cache(maxsize=1)
def _selector_binary() -> Path:
    configured = os.environ.get("SYNTAVRA_R38_SELECTOR")
    if configured:
        selector = Path(configured)
        assert selector.is_file(), selector
        return selector
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


def _run(
    engine: str,
    project: Path,
    state_root: Path,
    *arguments: str,
) -> tuple[int, Any]:
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
        text=True,
        timeout=300,
    )
    assert completed.stdout.strip(), {
        "engine": engine,
        "arguments": arguments,
        "returncode": completed.returncode,
        "stderr": completed.stderr,
    }
    return completed.returncode, json.loads(completed.stdout)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _initialize_snapshot(state_root: Path, project: Path) -> None:
    state_root.mkdir(parents=True)
    database = state_root / "structural.sqlite3"
    with sqlite3.connect(database) as db:
        db.executescript(
            """
            CREATE TABLE structural_files(
                path TEXT PRIMARY KEY,
                content_hash TEXT NOT NULL,
                language TEXT NOT NULL,
                parser TEXT NOT NULL DEFAULT '',
                semantic INTEGER NOT NULL DEFAULT 0,
                diagnostics_json TEXT NOT NULL DEFAULT '[]',
                indexed_at REAL NOT NULL
            );
            CREATE TABLE structural_symbols(
                symbol_id INTEGER PRIMARY KEY AUTOINCREMENT,
                path TEXT NOT NULL,
                name TEXT NOT NULL,
                qualified_name TEXT NOT NULL DEFAULT '',
                kind TEXT NOT NULL,
                line INTEGER NOT NULL,
                end_line INTEGER NOT NULL DEFAULT 0,
                signature TEXT NOT NULL DEFAULT '',
                confidence REAL NOT NULL DEFAULT 1.0,
                parser TEXT NOT NULL DEFAULT '',
                UNIQUE(path,qualified_name,kind,line)
            );
            CREATE INDEX structural_symbol_name_idx
                ON structural_symbols(name,qualified_name);
            CREATE INDEX structural_symbol_path_idx
                ON structural_symbols(path,line);
            CREATE TABLE structural_edges(
                source_path TEXT NOT NULL,
                source_symbol TEXT NOT NULL,
                edge_type TEXT NOT NULL,
                target TEXT NOT NULL,
                target_path TEXT NOT NULL DEFAULT '',
                line INTEGER NOT NULL,
                confidence REAL NOT NULL,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                UNIQUE(source_path,source_symbol,edge_type,target,line)
            );
            CREATE INDEX structural_edge_target_idx
                ON structural_edges(target,edge_type);
            CREATE INDEX structural_edge_source_idx
                ON structural_edges(source_symbol,edge_type);
            CREATE INDEX structural_edge_path_idx
                ON structural_edges(source_path,target_path);
            """
        )
        now = time.time()
        files = [
            ("src/service.py", "python", "python-ast-v3", 1),
            ("tests/test_service.py", "python", "python-ast-v3", 1),
            ("src/types.rs", "rust", "regex-structural-v3", 0),
        ]
        db.executemany(
            """
            INSERT INTO structural_files(
                path,content_hash,language,parser,semantic,diagnostics_json,indexed_at
            ) VALUES(?,?,?,?,?,'[]',?)
            """,
            [
                (relative, _sha256(project / relative), language, parser, semantic, now)
                for relative, language, parser, semantic in files
            ],
        )
        db.executemany(
            """
            INSERT INTO structural_symbols(
                path,name,qualified_name,kind,line,end_line,signature,confidence,parser
            ) VALUES(?,?,?,?,?,?,?,?,?)
            """,
            [
                ("src/service.py", "helper", "helper", "function", 1, 2, "(value)", 1.0, "python-ast-v3"),
                ("src/service.py", "target", "target", "function", 4, 5, "(value)", 1.0, "python-ast-v3"),
                ("tests/test_service.py", "test_target", "test_target", "function", 1, 2, "()", 1.0, "python-ast-v3"),
                ("src/types.rs", "RustThing", "RustThing", "struct", 1, 1, "", 0.82, "regex-structural-v3"),
            ],
        )
        db.executemany(
            """
            INSERT INTO structural_edges(
                source_path,source_symbol,edge_type,target,target_path,line,confidence,metadata_json
            ) VALUES(?,?,?,?,?,?,?,'{}')
            """,
            [
                ("src/service.py", "target", "calls", "helper", "src/service.py", 5, 0.99),
                ("tests/test_service.py", "test_target", "calls", "target", "src/service.py", 2, 0.99),
            ],
        )


def _snapshot_project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    (project / "src").mkdir(parents=True)
    (project / "tests").mkdir()
    (project / "src" / "service.py").write_text(
        "def helper(value):\n    return value\n\ndef target(value):\n    return helper(value)\n",
        encoding="utf-8",
    )
    (project / "tests" / "test_service.py").write_text(
        "def test_target():\n    target(1)\n",
        encoding="utf-8",
    )
    (project / "src" / "types.rs").write_text(
        "pub struct RustThing;\n",
        encoding="utf-8",
    )
    return project


@pytest.mark.parametrize(
    "arguments",
    [
        ("inspect", "symbol", "helper", "--limit", "20"),
        ("inspect", "impact", "helper", "--max-depth", "4"),
        ("inspect", "paths", "src/service.py", "--max-depth", "4"),
        ("inspect", "map", "helper", "--token-budget", "2000", "--max-depth", "4"),
        ("inspect", "stats"),
    ],
)
def test_native_structural_queries_match_python_snapshot(
    tmp_path: Path,
    arguments: tuple[str, ...],
) -> None:
    project = _snapshot_project(tmp_path)
    python_state = tmp_path / "python-state"
    rust_state = tmp_path / "rust-state"
    _initialize_snapshot(python_state, project)
    _initialize_snapshot(rust_state, project)

    python_code, python_result = _run("python", project, python_state, *arguments)
    rust_code, rust_result = _run("rust", project, rust_state, *arguments)

    assert rust_code == python_code == 0
    assert rust_result == python_result


def test_native_structural_fresh_python_symbol_index_matches_python(tmp_path: Path) -> None:
    project = tmp_path / "fresh-project"
    project.mkdir()
    (project / "module.py").write_text(
        "def helper(value) -> int:\n    return value\n",
        encoding="utf-8",
    )

    python_code, python_result = _run(
        "python",
        project,
        tmp_path / "fresh-python-state",
        "inspect",
        "symbol",
        "helper",
    )
    rust_code, rust_result = _run(
        "rust",
        project,
        tmp_path / "fresh-rust-state",
        "inspect",
        "symbol",
        "helper",
    )

    assert rust_code == python_code == 0, {
        "python": {"code": python_code, "result": python_result},
        "rust": {"code": rust_code, "result": rust_result},
    }
    assert rust_result == python_result
    assert rust_result == {
        "query": "helper",
        "symbols": [
            {
                "confidence": 1.0,
                "end_line": 2,
                "kind": "function",
                "line": 1,
                "name": "helper",
                "parser": "python-ast-v3",
                "path": "module.py",
                "qualified_name": "helper",
                "signature": "(value) -> int",
            }
        ],
    }
