#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


DYNAMIC_FIELDS = {"created_at", "updated_at", "indexed_at", "imported_at"}


def run_engine(
    engine: str,
    args: list[str],
    *,
    repo: Path,
    rust_bin: Path,
    project: Path,
    state_root: Path,
) -> dict[str, Any]:
    common = ["--project", str(project), "--state-root", str(state_root), *args]
    if engine == "python":
        command = [sys.executable, "-m", "syntavra_runtime.engine_entry", "--engine", "python", *common]
    elif engine == "rust":
        command = [str(rust_bin), "--engine", "rust", *common]
    else:
        raise ValueError(engine)
    env = os.environ.copy()
    env.update(
        {
            "PYTHONPATH": str(repo),
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUTF8": "1",
            "SYNTAVRA_BULK_PARITY_PROBE": "1",
        }
    )
    completed = subprocess.run(
        command,
        cwd=repo,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        check=False,
    )
    try:
        value = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"{engine} emitted non-JSON for {' '.join(args)}\n"
            f"exit={completed.returncode}\nstdout={completed.stdout}\nstderr={completed.stderr}"
        ) from exc
    if completed.returncode != 0:
        raise RuntimeError(
            f"{engine} {' '.join(args)} failed\n"
            f"exit={completed.returncode}\nvalue={value}\nstderr={completed.stderr}"
        )
    return value


def normalize(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: normalize(child)
            for key, child in value.items()
            if key not in DYNAMIC_FIELDS
        }
    if isinstance(value, list):
        return [normalize(item) for item in value]
    return value


def compare_values(path: str, left: Any, right: Any, mismatches: list[dict[str, Any]]) -> None:
    if isinstance(left, bool) or isinstance(right, bool):
        if left != right:
            mismatches.append({"path": path, "python": left, "rust": right})
        return
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        if not math.isclose(float(left), float(right), rel_tol=1e-10, abs_tol=1e-12):
            mismatches.append({"path": path, "python": left, "rust": right})
        return
    if isinstance(left, dict) and isinstance(right, dict):
        for key in sorted(set(left) | set(right)):
            child = f"{path}.{key}" if path else key
            if key not in left or key not in right:
                mismatches.append({"path": child, "python": left.get(key), "rust": right.get(key)})
            else:
                compare_values(child, left[key], right[key], mismatches)
        return
    if isinstance(left, list) and isinstance(right, list):
        if len(left) != len(right):
            mismatches.append({"path": f"{path}.length", "python": len(left), "rust": len(right)})
            return
        for index, (left_item, right_item) in enumerate(zip(left, right)):
            compare_values(f"{path}[{index}]", left_item, right_item, mismatches)
        return
    if left != right:
        mismatches.append({"path": path, "python": left, "rust": right})


def seed_project(project: Path) -> None:
    project.mkdir(parents=True)
    (project / ".git").mkdir()
    (project / "alpha.py").write_text(
        "def helper(value: int) -> int:\n"
        "    return value + 1\n\n"
        "def alpha(value: int) -> int:\n"
        "    return helper(value)\n",
        encoding="utf-8",
    )
    (project / "test_alpha.py").write_text(
        "from alpha import alpha\n\n"
        "def test_alpha():\n"
        "    assert alpha(1) == 2\n",
        encoding="utf-8",
    )
    manifest = project / ".syntavra" / "languages"
    manifest.mkdir(parents=True)
    (manifest / "fixture.json").write_text(
        json.dumps(
            {
                "id": "fixturelang",
                "suffixes": [".fixture"],
                "capabilities": ["lexical"],
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    (project / "sample.fixture").write_text("fixture token alpha\n", encoding="utf-8")


def exercise(
    engine: str,
    *,
    repo: Path,
    rust_bin: Path,
    project: Path,
    state: Path,
) -> dict[str, Any]:
    def call(*parts: str) -> Any:
        return normalize(
            run_engine(
                engine,
                list(parts),
                repo=repo,
                rust_bin=rust_bin,
                project=project,
                state_root=state,
            )
        )

    graph_index = call("run", "graph-index", "--max-file-bytes", "2000000")
    graph_query = call("run", "graph-query", "alpha", "--limit", "20")
    results = graph_query.get("results", []) if isinstance(graph_query, dict) else []
    if not results:
        raise RuntimeError(f"{engine} graph-query returned no alpha results: {graph_query}")
    first_node = results[0].get("node_id") if isinstance(results[0], dict) else None
    if not isinstance(first_node, str) or not first_node:
        raise RuntimeError(f"{engine} graph-query first result has no node_id: {results[0]}")
    graph_impact = call("run", "graph-impact", first_node, "--max-depth", "4")

    language_detect = call("run", "language", "detect", "alpha.py")
    fixture_detect = call("run", "language", "detect", "sample.fixture")
    language_inventory = call("run", "language", "inventory")
    language_index = call("run", "language", "index", "--max-file-bytes", "2000000")
    language_query = call("run", "language", "query", "helper", "--limit", "20")
    language_doctor = call("run", "language", "doctor")
    semantic_services = call("run", "semantic-services")

    return {
        "graph_index": graph_index,
        "graph_query": graph_query,
        "graph_impact": graph_impact,
        "language_detect": language_detect,
        "fixture_detect": fixture_detect,
        "language_inventory": language_inventory,
        "language_index": language_index,
        "language_query": language_query,
        "language_doctor": language_doctor,
        "semantic_services": semantic_services,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate graph/language core Python-Rust parity")
    parser.add_argument("--repo", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--rust-bin", default="target/debug/syntavra")
    parser.add_argument("--output")
    args = parser.parse_args()
    repo = Path(args.repo).resolve(strict=True)
    rust_bin = Path(args.rust_bin)
    if not rust_bin.is_absolute():
        rust_bin = (repo / rust_bin).resolve(strict=True)

    result: dict[str, Any]
    try:
        with tempfile.TemporaryDirectory(prefix="syntavra-graph-language-core-") as directory:
            root = Path(directory)
            project = root / "project"
            seed_project(project)
            python_result = exercise(
                "python",
                repo=repo,
                rust_bin=rust_bin,
                project=project,
                state=root / "python-state",
            )
            rust_result = exercise(
                "rust",
                repo=repo,
                rust_bin=rust_bin,
                project=project,
                state=root / "rust-state",
            )
            mismatches: list[dict[str, Any]] = []
            compare_values("", python_result, rust_result, mismatches)
            result = {
                "ok": not mismatches,
                "python": python_result,
                "rust": rust_result,
                "differential": {
                    "ok": not mismatches,
                    "mismatch_count": len(mismatches),
                    "mismatches": mismatches,
                    "covered_routes": [
                        "run graph-index",
                        "run graph-query",
                        "run graph-impact",
                        "run language detect",
                        "run language inventory",
                        "run language index",
                        "run language query",
                        "run language doctor",
                        "run semantic-services",
                    ],
                    "claim_boundary": "graph/language core public JSON behavior and durable index state; volatile timestamps normalized only",
                },
            }
    except Exception as exc:
        result = {
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
            "differential": {
                "ok": False,
                "mismatch_count": 1,
                "mismatches": [{"path": "validator_execution", "error": f"{type(exc).__name__}: {exc}"}],
            },
        }

    rendered = json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False)
    if args.output:
        Path(args.output).write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if result.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
