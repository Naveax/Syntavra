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
COMPONENTS = [
    "context-compiler",
    "terminal-output-engine",
    "output-firewall",
    "artifact-store",
    "canonical-repository-graph",
    "indexed-repository-query",
    "tree-sitter-syntax-adapter",
    "semantic-intelligence",
    "runtime-evidence",
    "universal-language-platform",
    "sandboxed-language-services",
    "generic-lsp-bridge",
    "semantic-index-import",
    "session-memory",
    "capability-security",
    "execution-sandbox",
    "provider-gateway",
    "adapter-platform",
    "model-gateway",
    "agent-runtime",
    "coding-agent",
    "headless-runtime",
    "interactive-console",
    "reliability-laboratory",
    "distribution-manager",
    "signalbench",
]


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


def _run(
    engine: str,
    project: Path,
    state_root: Path,
    action: str,
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
            "run",
            action,
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert completed.stdout.strip(), {
        "engine": engine,
        "action": action,
        "returncode": completed.returncode,
        "stderr": completed.stderr,
    }
    return completed.returncode, json.loads(completed.stdout)


@pytest.mark.parametrize("action", ["platform-manifest", "competitive-manifest"])
def test_native_platform_manifest_matches_python(
    tmp_path: Path,
    action: str,
) -> None:
    project = tmp_path / "project"
    project.mkdir()

    python_code, python_result = _run(
        "python",
        project,
        tmp_path / "python-state",
        action,
    )
    rust_code, rust_result = _run(
        "rust",
        project,
        tmp_path / "rust-state",
        action,
    )

    assert rust_code == python_code == 0
    assert rust_result == python_result
    assert rust_result == {
        "adapter_contract": {
            "adapters": 20,
            "invalid": [],
            "inventory_gate": True,
            "levels": {"A": 4, "B": 10, "C": 5, "D": 1},
            "live_boundary": "live certification requires external execution receipts",
            "live_certified": 0,
            "non_cli_adapters": 12,
            "ok": True,
            "surfaces": {
                "cli": 8,
                "ide": 7,
                "ide-extension": 3,
                "platform": 2,
            },
        },
        "channel": "pre-release",
        "components": COMPONENTS,
        "external_claims": "NOT_PROVEN_WITHOUT_EXTERNAL_RECEIPTS",
        "product": "Syntavra",
        "runtime": "unified",
        "version": "0.0.1",
    }
