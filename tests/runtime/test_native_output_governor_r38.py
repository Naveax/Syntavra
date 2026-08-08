from __future__ import annotations

import json
import shutil
import subprocess
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any

import pytest

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


def _run(engine: str, *arguments: str, stdin: str | None = None) -> tuple[int, Any]:
    prefix = (
        [sys.executable, "-m", "syntavra_runtime.engine_entry"]
        if engine == "python"
        else [str(_selector_binary())]
    )
    completed = subprocess.run(
        [*prefix, "--engine", engine, *arguments],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        input=stdin,
        timeout=300,
    )
    assert completed.stdout.strip(), {
        "engine": engine,
        "arguments": arguments,
        "returncode": completed.returncode,
        "stderr": completed.stderr,
    }
    return completed.returncode, json.loads(completed.stdout)


def test_native_output_compact_matches_python_priority_and_dedupe() -> None:
    text = "\n".join(
        [
            "Sure, I can help.",
            "normal detail",
            "ERROR src/lib.rs:12:4 failed",
            "normal detail",
            "git status",
            "fn main() {}",
            "warning tests/test_api.py:9",
        ]
    )
    arguments = ("output", "compact", "--profile", "compact", "--text", text)

    python_code, python_result = _run("python", *arguments)
    rust_code, rust_result = _run("rust", *arguments)

    assert rust_code == python_code == 0
    assert rust_result == python_result
    assert rust_result["text"].splitlines() == [
        "ERROR src/lib.rs:12:4 failed",
        "git status",
        "fn main() {}",
        "warning tests/test_api.py:9",
        "normal detail",
    ]
    assert rust_result["removed_lines"] == 2
    assert rust_result["preserved_paths"] == [
        "src/lib.rs:12:4",
        "tests/test_api.py:9",
    ]
    assert rust_result["preserved_critical_lines"] == 2
    assert rust_result["preserved_code_lines"] == 1


def test_native_output_compact_reads_input_file_and_stdin(tmp_path: Path) -> None:
    text = "normal\nFAILED src/main.rs:7\nnormal\npytest -q\n"
    source = tmp_path / "output.txt"
    source.write_text(text, encoding="utf-8")

    file_arguments = ("output", "compact", "--input", str(source))
    python_file = _run("python", *file_arguments)
    rust_file = _run("rust", *file_arguments)
    assert rust_file == python_file

    stdin_arguments = ("output", "compact", "--profile=terse")
    python_stdin = _run("python", *stdin_arguments, stdin=text)
    rust_stdin = _run("rust", *stdin_arguments, stdin=text)
    assert rust_stdin == python_stdin


def test_native_output_govern_matches_python_contract_render(tmp_path: Path) -> None:
    payload = {
        "result": "Native output governance implemented.",
        "changed_files": [
            "crates/syntavra-cli/src/native_output_governor.rs",
            "tests/runtime/test_native_output_governor_r38.py",
        ],
        "behavior": [
            "Critical and path lines are retained.",
            "Duplicate lines are removed.",
            "This third detail is bounded by compact mode.",
        ],
        "verification": [
            "PASS cargo test",
            "PASS cargo test",
            "tests/runtime/test_native_output_governor_r38.py:77",
        ],
        "limitations": ["Not proven on Windows yet."],
        "evidence": ["sha256:pending"],
    }
    source = tmp_path / "payload.json"
    source.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    arguments = (
        "output",
        "govern",
        "--profile=compact",
        "--contract",
        "implementation",
        "--input",
        str(source),
    )

    python_code, python_result = _run("python", *arguments)
    rust_code, rust_result = _run("rust", *arguments)

    assert rust_code == python_code == 0
    assert rust_result == python_result
    assert rust_result["sections"] == [
        "result",
        "changed_files",
        "behavior",
        "verification",
        "limitations",
    ]
    assert rust_result["preserved_paths"] == [
        "tests/runtime/test_native_output_governor_r38.py:77"
    ]
    assert "This third detail" not in rust_result["text"]
    assert "sha256:pending" not in rust_result["text"]
    assert rust_result["bytes"] == len(rust_result["text"].encode("utf-8"))
