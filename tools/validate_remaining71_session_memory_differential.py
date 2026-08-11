#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


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
    result = subprocess.run(
        command,
        cwd=repo,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"{engine} emitted non-JSON for {' '.join(args)}\n"
            f"exit={result.returncode}\nstdout={result.stdout}\nstderr={result.stderr}"
        ) from exc
    return {"exit": result.returncode, "value": value, "stderr": result.stderr}


def strip_times(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: strip_times(child)
            for key, child in value.items()
            if key not in {"created_at", "updated_at"}
        }
    if isinstance(value, list):
        return [strip_times(item) for item in value]
    return value


def normalize_open(value: dict[str, Any], *, random_id: bool = False) -> dict[str, Any]:
    result = strip_times(value)
    if random_id:
        result["session_id"] = "<generated>"
    return result


def normalize_fork(value: dict[str, Any]) -> dict[str, Any]:
    result = strip_times(value)
    child = dict(result.get("child") or {})
    child["session_id"] = "<generated>"
    result["child"] = child
    return result


def normalize_merge(value: dict[str, Any]) -> dict[str, Any]:
    result = strip_times(value)
    merged = dict(result.get("merged") or {})
    merged["session_id"] = "<generated>"
    result["merged"] = merged
    return result


def call_factory(engine: str, *, repo: Path, rust_bin: Path, project: Path, state: Path):
    def call(*args: str) -> dict[str, Any]:
        result = run_engine(
            engine,
            list(args),
            repo=repo,
            rust_bin=rust_bin,
            project=project,
            state_root=state,
        )
        if result["exit"] != 0:
            raise RuntimeError(f"{engine} {' '.join(args)} failed: {result}")
        return result["value"]

    return call


def exercise(engine: str, *, repo: Path, rust_bin: Path, project: Path, state: Path) -> dict[str, Any]:
    call = call_factory(engine, repo=repo, rust_bin=rust_bin, project=project, state=state)

    opened = call(
        "run",
        "memory-open",
        "--session-id",
        "session-a",
        "--metadata",
        '{"task":"sandbox parity","owner":"syntavra"}',
    )
    restored_open = call("run", "memory-open", "--session-id", "session-a")

    events = [
        call(
            "run",
            "memory-append",
            "session-a",
            "decision",
            '{"decision":"keep sandbox policy","importance":0.8,"pinned":true}',
        ),
        call(
            "run",
            "memory-append",
            "session-a",
            "test",
            '{"test":"sandbox verify","result":"passed","coverage":91}',
        ),
        call(
            "run",
            "memory-append",
            "session-a",
            "security",
            '{"security":"capability sandbox","authorization":"required"}',
        ),
    ]
    verified_before = call("run", "memory-verify", "session-a")
    compact = call(
        "run",
        "memory-compact",
        "session-a",
        "--view",
        "decision",
        "--view",
        "test",
        "--view",
        "security",
    )
    retrieve = call("run", "memory-retrieve", "session-a", "sandbox verify", "--limit", "8")
    checkpoint = call("run", "memory-checkpoint", "session-a", "--label", "stable")

    post_checkpoint = call(
        "run",
        "memory-append",
        "session-a",
        "change",
        '{"change":"after checkpoint","file":"src/example.py"}',
    )
    restore = call("run", "memory-restore", str(checkpoint["checkpoint_id"]))
    verified_after = call("run", "memory-verify", "session-a")

    fork = call("run", "memory-fork", "session-a", "--label", "child")
    child_id = str((fork.get("child") or {}).get("session_id") or "")
    if not child_id:
        raise RuntimeError(f"{engine} fork did not return a child session")
    child_append = call(
        "run",
        "memory-append",
        child_id,
        "task",
        '{"task":"child branch work","goal":"verify fork"}',
    )
    child_verify = call("run", "memory-verify", child_id)

    session_b = call(
        "run",
        "memory-open",
        "--session-id",
        "session-b",
        "--metadata",
        '{"task":"secondary"}',
    )
    session_b_append = call(
        "run",
        "memory-append",
        "session-b",
        "decision",
        '{"decision":"merge later"}',
    )
    merge = call("run", "memory-merge", "session-a", "session-b", "--label", "combined")
    merged_id = str((merge.get("merged") or {}).get("session_id") or "")
    if not merged_id:
        raise RuntimeError(f"{engine} merge did not return a merged session")
    merged_verify = call("run", "memory-verify", merged_id)

    return {
        "opened": normalize_open(opened),
        "restored_open": normalize_open(restored_open),
        "events": [strip_times(item) for item in events],
        "verified_before": strip_times(verified_before),
        "compact": strip_times(compact),
        "retrieve": strip_times(retrieve),
        "checkpoint": strip_times(checkpoint),
        "post_checkpoint": strip_times(post_checkpoint),
        "restore": strip_times(restore),
        "verified_after": strip_times(verified_after),
        "fork": normalize_fork(fork),
        "child_append": {
            **strip_times(child_append),
            "session_id": "<generated>",
        },
        "child_verify": {
            **strip_times(child_verify),
            "session_id": "<generated>",
        },
        "session_b": normalize_open(session_b),
        "session_b_append": strip_times(session_b_append),
        "merge": normalize_merge(merge),
        "merged_verify": {
            **strip_times(merged_verify),
            "session_id": "<generated>",
        },
    }


def compare(python_result: dict[str, Any], rust_result: dict[str, Any]) -> dict[str, Any]:
    mismatches: list[dict[str, Any]] = []
    keys = sorted(set(python_result) | set(rust_result))
    for key in keys:
        py = python_result.get(key)
        rs = rust_result.get(key)
        if py != rs:
            mismatches.append({"path": key, "python": py, "rust": rs})

    invariants: list[tuple[str, Any, Any]] = [
        ("verified_before.ok", python_result["verified_before"].get("ok"), True),
        ("verified_after.ok", python_result["verified_after"].get("ok"), True),
        ("restore.exact_recovery", python_result["restore"].get("exact_recovery"), True),
        ("child_verify.ok", python_result["child_verify"].get("ok"), True),
        ("merged_verify.ok", python_result["merged_verify"].get("ok"), True),
        ("compact.exact_history_preserved", python_result["compact"].get("exact_history_preserved"), True),
    ]
    for path, actual, expected in invariants:
        if actual != expected:
            mismatches.append({"path": path, "expected": expected, "python": actual, "rust": None})

    return {
        "ok": not mismatches,
        "mismatch_count": len(mismatches),
        "mismatches": mismatches,
        "claim_boundary": "deterministic local session-memory lifecycle and exact-hash parity only",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate Python/Rust session-memory parity")
    parser.add_argument("--repo", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--rust-bin", default="target/debug/syntavra")
    parser.add_argument("--output")
    args = parser.parse_args()
    repo = Path(args.repo).resolve(strict=True)
    rust_bin = Path(args.rust_bin)
    if not rust_bin.is_absolute():
        rust_bin = (repo / rust_bin).resolve(strict=True)

    with tempfile.TemporaryDirectory(prefix="syntavra-session-memory-diff-") as directory:
        root = Path(directory)
        project = root / "project"
        project.mkdir()
        (project / ".git").mkdir()
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
        differential = compare(python_result, rust_result)
        result = {
            "ok": differential["ok"],
            "python": python_result,
            "rust": rust_result,
            "differential": differential,
        }

    rendered = json.dumps(result, indent=2, sort_keys=True)
    if args.output:
        Path(args.output).write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
