#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


DYNAMIC_FIELDS = {"created_at", "updated_at"}


def run_engine(
    engine: str,
    args: list[str],
    *,
    repo: Path,
    rust_bin: Path,
    project: Path,
    state_root: Path,
    extra_env: dict[str, str] | None = None,
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
    if extra_env:
        env.update(extra_env)
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


def strip_dynamic(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: strip_dynamic(child) for key, child in value.items() if key not in DYNAMIC_FIELDS}
    if isinstance(value, list):
        return [strip_dynamic(item) for item in value]
    return value


def normalized_notifications(state: Path) -> list[dict[str, Any]]:
    path = state / "notifications" / "events.jsonl"
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        row.pop("created_at", None)
        row.pop("event_hash", None)
        rows.append(row)
    return rows


def normalized_export(path: Path) -> list[Any]:
    rows: list[Any] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(strip_dynamic(json.loads(line)))
    return rows


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def create_external_extractor(root: Path) -> tuple[Path, str]:
    helper = root / "memory_extractor_fixture.py"
    helper.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        "request = json.load(open(sys.argv[1], encoding='utf-8'))\n"
        "assert 'transcript' in request\n"
        "payload = {'observations': [{'text': 'external extractor observation', 'kind': 'decision', 'importance': 0.61, 'confidence': 0.73, 'validity': 1.0, 'metadata': {'source': 'fixture'}}]}\n"
        "with open(sys.argv[2], 'w', encoding='utf-8') as handle: json.dump(payload, handle, ensure_ascii=False, sort_keys=True)\n",
        encoding="utf-8",
    )
    command = json.dumps([sys.executable, str(helper), "{request}", "{output}"])
    return helper, command


def seed_missing_embedding(state: Path) -> None:
    database = state / "memory-intelligence.sqlite3"
    with sqlite3.connect(database) as db:
        db.execute(
            "UPDATE observations SET embedding_json=NULL "
            "WHERE observation_id=(SELECT observation_id FROM observations ORDER BY observation_id LIMIT 1)"
        )


def exercise(
    engine: str,
    *,
    repo: Path,
    rust_bin: Path,
    project: Path,
    state: Path,
    extractor_command: str,
) -> dict[str, Any]:
    def call(*args: str, extra_env: dict[str, str] | None = None) -> dict[str, Any]:
        result = run_engine(
            engine,
            list(args),
            repo=repo,
            rust_bin=rust_bin,
            project=project,
            state_root=state,
            extra_env=extra_env,
        )
        if result["exit"] != 0:
            raise RuntimeError(f"{engine} {' '.join(args)} failed: {result}")
        return result["value"]

    unicode_add = call(
        "run",
        "memory-add",
        "Straße cache decision",
        "--kind",
        "decision",
        "--importance",
        "0.70",
        "--confidence",
        "0.80",
    )
    heuristic_extract = call(
        "run",
        "memory-extract",
        "Decision: Keep deterministic cache\nFailure: Timeout in worker\nConstraint: Never expose secret\nPreference: Prefer local index",
    )
    critical_add = call(
        "run",
        "memory-add",
        "critical memory parity observation",
        "--kind",
        "security",
        "--importance",
        "0.95",
        "--confidence",
        "0.90",
    )
    external_extract = call(
        "run",
        "memory-extract",
        "plain transcript without heuristic markers",
        extra_env={"SYNTAVRA_MEMORY_EXTRACTOR_COMMAND_JSON": extractor_command},
    )
    unicode_search = call("run", "memory-search", "STRASSE", "--limit", "10")

    seed_missing_embedding(state)
    backfill = call("run", "memory-backfill", "--limit", "1000")
    status = call("run", "memory-intelligence-status")

    export_path = state / "memory-export.jsonl"
    export = call("run", "memory-export", str(export_path))
    export_integrity = {
        "reported_sha_matches_file": export.get("sha256") == sha256_file(export_path),
        "reported_observations_matches_file": export.get("observations") == len(normalized_export(export_path)),
    }
    export = dict(export)
    export["path"] = "<export>"
    export["sha256"] = "<engine-local-timestamped-export>"

    return {
        "unicode_add": strip_dynamic(unicode_add),
        "heuristic_extract": strip_dynamic(heuristic_extract),
        "critical_add": strip_dynamic(critical_add),
        "external_extract": strip_dynamic(external_extract),
        "unicode_search": strip_dynamic(unicode_search),
        "backfill": strip_dynamic(backfill),
        "status": strip_dynamic(status),
        "export": strip_dynamic(export),
        "export_rows": normalized_export(export_path),
        "export_integrity": export_integrity,
        "notifications": normalized_notifications(state),
    }


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


def compare(python_result: dict[str, Any], rust_result: dict[str, Any]) -> dict[str, Any]:
    mismatches: list[dict[str, Any]] = []
    compare_values("", python_result, rust_result, mismatches)
    for engine, result in (("python", python_result), ("rust", rust_result)):
        if result["backfill"] != {"embedded": 1, "remaining": 0}:
            mismatches.append(
                {"path": f"{engine}.backfill_invariant", "expected": {"embedded": 1, "remaining": 0}, "actual": result["backfill"]}
            )
        if result["export_integrity"] != {
            "reported_sha_matches_file": True,
            "reported_observations_matches_file": True,
        }:
            mismatches.append({"path": f"{engine}.export_integrity", "actual": result["export_integrity"]})
        if not result["external_extract"].get("observations"):
            mismatches.append({"path": f"{engine}.external_extractor_invariant", "actual": result["external_extract"]})
        if not result["notifications"]:
            mismatches.append({"path": f"{engine}.critical_notification_invariant", "actual": result["notifications"]})
    return {
        "ok": not mismatches,
        "mismatch_count": len(mismatches),
        "mismatches": mismatches,
        "claim_boundary": "memory-intelligence public behavior, ranking, durable state side effects and local export integrity; timestamps normalized only",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate Python/Rust memory-intelligence parity")
    parser.add_argument("--repo", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--rust-bin", default="target/debug/syntavra")
    parser.add_argument("--output")
    args = parser.parse_args()
    repo = Path(args.repo).resolve(strict=True)
    rust_bin = Path(args.rust_bin)
    if not rust_bin.is_absolute():
        rust_bin = (repo / rust_bin).resolve(strict=True)

    with tempfile.TemporaryDirectory(prefix="syntavra-memory-intelligence-diff-") as directory:
        root = Path(directory)
        project = root / "project"
        project.mkdir()
        (project / ".git").mkdir()
        _, extractor_command = create_external_extractor(root)
        python_result = exercise(
            "python",
            repo=repo,
            rust_bin=rust_bin,
            project=project,
            state=root / "python-state",
            extractor_command=extractor_command,
        )
        rust_result = exercise(
            "rust",
            repo=repo,
            rust_bin=rust_bin,
            project=project,
            state=root / "rust-state",
            extractor_command=extractor_command,
        )
        differential = compare(python_result, rust_result)
        result = {"ok": differential["ok"], "python": python_result, "rust": rust_result, "differential": differential}

    rendered = json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False)
    if args.output:
        Path(args.output).write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
