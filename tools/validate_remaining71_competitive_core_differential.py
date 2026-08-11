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

ROUTES = [
    "run rewrite",
    "run transcript-mine",
    "run cache-plan",
    "run cache-health",
    "run provider-route",
    "run delegate",
]


def run_engine(
    engine: str,
    args: list[str],
    *,
    repo: Path,
    rust_bin: Path,
    project: Path,
    state_root: Path,
    timeout: int = 90,
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
        timeout=timeout,
        check=False,
    )
    try:
        value = json.loads(completed.stdout)
    except json.JSONDecodeError:
        value = None
    return {
        "exit": completed.returncode,
        "value": value,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }


def require_object(result: dict[str, Any], *, engine: str, label: str) -> dict[str, Any]:
    if result["exit"] != 0:
        raise RuntimeError(f"{engine} {label} failed: {result}")
    value = result["value"]
    if not isinstance(value, dict):
        raise RuntimeError(f"{engine} {label} returned non-object JSON: {result}")
    return value


def deep_mismatches(left: Any, right: Any, path: str = "") -> list[dict[str, Any]]:
    if type(left) is not type(right):
        return [{"path": path or "<root>", "python": left, "rust": right}]
    if isinstance(left, dict):
        rows: list[dict[str, Any]] = []
        for key in sorted(set(left) | set(right)):
            child = f"{path}.{key}" if path else key
            if key not in left:
                rows.append({"path": child, "python": None, "rust": right[key]})
            elif key not in right:
                rows.append({"path": child, "python": left[key], "rust": None})
            else:
                rows.extend(deep_mismatches(left[key], right[key], child))
        return rows
    if isinstance(left, list):
        rows = []
        if len(left) != len(right):
            rows.append({"path": f"{path}.length", "python": len(left), "rust": len(right)})
        for index, (a, b) in enumerate(zip(left, right)):
            rows.extend(deep_mismatches(a, b, f"{path}[{index}]"))
        return rows
    if isinstance(left, float):
        if math.isclose(left, right, rel_tol=1e-12, abs_tol=1e-12):
            return []
    if left != right:
        return [{"path": path or "<root>", "python": left, "rust": right}]
    return []


def validate_cache_clock(value: dict[str, Any], *, engine: str) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    expires = value.get("expires_at")
    refresh = value.get("refresh_after")
    ttl = value.get("ttl_seconds")
    for key, item in (("expires_at", expires), ("refresh_after", refresh)):
        if not isinstance(item, (int, float)) or isinstance(item, bool) or not math.isfinite(float(item)) or item <= 0:
            issues.append({"path": f"{engine}.cache_plan.{key}.finite_positive", "expected": True, "actual": item})
    if (
        isinstance(expires, (int, float))
        and not isinstance(expires, bool)
        and isinstance(refresh, (int, float))
        and not isinstance(refresh, bool)
        and isinstance(ttl, int)
    ):
        expected_gap = float(ttl) * 0.25
        actual_gap = float(expires) - float(refresh)
        if not math.isclose(actual_gap, expected_gap, rel_tol=0.0, abs_tol=0.05):
            issues.append(
                {
                    "path": f"{engine}.cache_plan.refresh_window",
                    "expected": expected_gap,
                    "actual": actual_gap,
                }
            )
    return issues


def normalize_cache_plan(value: dict[str, Any]) -> dict[str, Any]:
    result = json.loads(json.dumps(value))
    result.pop("expires_at", None)
    result.pop("refresh_after", None)
    return result


def fixture_transcript() -> str:
    output = "\n".join(f"line-{index:02d}" for index in range(1, 51))
    return json.dumps(
        [
            {"command": "git status", "output": output},
            {"command": "pytest tests/test_sample.py", "stdout": "1 passed\n"},
            {"role": "assistant", "content": "not a command"},
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    )


def cache_messages() -> str:
    return json.dumps(
        [
            {"role": "user", "content": "volatile-first", "timestamp": 123456},
            {"role": "system", "content": "stable-system", "request_id": "drop-me"},
            {"role": "developer", "content": "stable-developer"},
            {"role": "tool", "content": "stable-tool", "cache_control": "stable"},
            {"role": "assistant", "content": "volatile-tail"},
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    )


def provider_candidates() -> str:
    return json.dumps(
        [
            {
                "provider": "subscription-provider",
                "model": "pro",
                "account": "paid",
                "subscription": True,
                "quality": 0.82,
                "quota_remaining": 0.9,
                "input_cost_per_million": 4.0,
                "output_cost_per_million": 12.0,
                "latency_ms": 350.0,
                "priority": 3,
                "available": True,
                "rate_limited_until": 0.0,
                "max_complexity": "reasoning",
                "context_window": 200000,
            },
            {
                "provider": "metered-provider",
                "model": "max",
                "account": "default",
                "subscription": False,
                "quality": 0.94,
                "quota_remaining": 1.0,
                "input_cost_per_million": 9.0,
                "output_cost_per_million": 30.0,
                "latency_ms": 900.0,
                "priority": 0,
                "available": True,
                "rate_limited_until": 0.0,
                "max_complexity": "reasoning",
                "context_window": 200000,
            },
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    )


def run_suite(engine: str, *, repo: Path, rust_bin: Path, root: Path) -> dict[str, Any]:
    project = root / "project"
    state = root / "state"
    project.mkdir(parents=True)
    state.mkdir(parents=True)
    (project / "sample.py").write_text("VALUE = 1\n", encoding="utf-8")

    def run(label: str, args: list[str]) -> dict[str, Any]:
        return require_object(
            run_engine(
                engine,
                args,
                repo=repo,
                rust_bin=rust_bin,
                project=project,
                state_root=state,
            ),
            engine=engine,
            label=label,
        )

    results: dict[str, Any] = {}
    results["rewrite_git_status"] = run("rewrite-git-status", ["run", "rewrite", "git", "status"])
    results["rewrite_conflict"] = run(
        "rewrite-conflict", ["run", "rewrite", "git", "status", "--short"]
    )
    results["rewrite_unsafe"] = run(
        "rewrite-unsafe", ["run", "rewrite", "git", "status", "&&", "echo", "unsafe"]
    )
    results["transcript_mine"] = run(
        "transcript-mine", ["run", "transcript-mine", fixture_transcript()]
    )
    results["cache_plan"] = run(
        "cache-plan",
        [
            "run",
            "cache-plan",
            cache_messages(),
            "--provider",
            "anthropic",
            "--model",
            "claude-parity",
        ],
    )
    results["cache_health"] = run("cache-health", ["run", "cache-health"])
    results["provider_route"] = run(
        "provider-route",
        [
            "run",
            "provider-route",
            "security migration root cause across repositories",
            provider_candidates(),
            "--changed-files",
            "12",
            "--tokens",
            "24000",
        ],
    )
    results["delegate"] = run(
        "delegate",
        [
            "run",
            "delegate",
            "Refactor authentication module, preserve compatibility, and add regression tests",
            "--context-path",
            "src/auth.py",
            "--context-path",
            "tests/test_auth.py",
            "--max-tasks",
            "4",
        ],
    )
    return results


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rust-bin", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    repo = Path(__file__).resolve().parents[1]
    rust_bin = Path(args.rust_bin).resolve(strict=True)
    output = Path(args.output)
    mismatches: list[dict[str, Any]] = []

    with tempfile.TemporaryDirectory(prefix="syntavra-competitive-core-") as raw:
        root = Path(raw)
        python = run_suite("python", repo=repo, rust_bin=rust_bin, root=root / "python")
        rust = run_suite("rust", repo=repo, rust_bin=rust_bin, root=root / "rust")

    mismatches.extend(validate_cache_clock(python["cache_plan"], engine="python"))
    mismatches.extend(validate_cache_clock(rust["cache_plan"], engine="rust"))

    python_normalized = json.loads(json.dumps(python))
    rust_normalized = json.loads(json.dumps(rust))
    python_normalized["cache_plan"] = normalize_cache_plan(python_normalized["cache_plan"])
    rust_normalized["cache_plan"] = normalize_cache_plan(rust_normalized["cache_plan"])
    mismatches.extend(deep_mismatches(python_normalized, rust_normalized))

    report = {
        "ok": not mismatches,
        "differential": {
            "ok": not mismatches,
            "mismatch_count": len(mismatches),
            "mismatches": mismatches,
            "routes": ROUTES,
            "claim_boundary": (
                "cache-plan expires_at and refresh_after are validated as finite positive values with the exact "
                "TTL-derived refresh window, then normalized; all rewrite safety decisions, transcript analysis/hash, "
                "cache structure/health, provider routing receipt, delegation plan, exit-success semantics, and all "
                "other public fields remain exact"
            ),
        },
        "python": python,
        "rust": rust,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(
        json.dumps(
            {
                "ok": report["ok"],
                "mismatch_count": len(mismatches),
                "routes": ROUTES,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    if mismatches:
        for index, mismatch in enumerate(mismatches[:80]):
            print(f"MISMATCH[{index}] {json.dumps(mismatch, ensure_ascii=False, sort_keys=True)}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
