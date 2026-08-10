from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Sequence


TARGETED_TESTS = (
    "tests.test_platforms",
    "tests.runtime.test_codex_integration_regressions",
    "tests.runtime.test_codex_mcp_bridge_v001",
    "tests.runtime.test_codex_toml_preservation_v001",
    "tests.runtime.test_repair_codex_integration_v001",
    "tests.runtime.test_host_installation_v4",
    "tests.runtime.test_mcp_enforcement_v001",
    "tests.runtime.test_product_surface_v001",
    "tests.runtime.test_token_saver_unification_v001",
)


def _run(repo: Path, argv: Sequence[str], *, check: bool = False) -> dict[str, Any]:
    started = time.perf_counter()
    completed = subprocess.run(
        list(argv),
        cwd=repo,
        text=True,
        capture_output=True,
        check=False,
    )
    result = {
        "argv": list(argv),
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "wall_time_ms": (time.perf_counter() - started) * 1000.0,
    }
    if check and completed.returncode != 0:
        raise RuntimeError(json.dumps(result, ensure_ascii=False))
    return result


def _git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repo), *args],
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or f"git {' '.join(args)} failed")
    return completed.stdout.strip()


def validate(repo: Path, *, expected_head: str = "", full_runtime: bool = False) -> dict[str, Any]:
    repo = repo.resolve(strict=True)
    head = _git(repo, "rev-parse", "HEAD")
    if expected_head and head.casefold() != expected_head.casefold():
        return {
            "ok": False,
            "stage": "exact-head",
            "expected_head": expected_head,
            "actual_head": head,
        }

    before_status = _git(repo, "status", "--porcelain=v1", "--untracked-files=all")
    steps: list[dict[str, Any]] = []

    steps.append(_run(repo, [
        sys.executable,
        "-m",
        "compileall",
        "-q",
        "syntavra_runtime",
        "tests/runtime",
        "tests/test_platforms.py",
        "tools/repair_codex_integration.py",
        "tools/validate_codex_integration_closure.py",
    ]))
    if steps[-1]["returncode"] != 0:
        return {
            "ok": False,
            "stage": "compileall",
            "head": head,
            "before_status": before_status,
            "steps": steps,
        }

    steps.append(_run(repo, [sys.executable, "-m", "unittest", "-v", *TARGETED_TESTS]))
    if steps[-1]["returncode"] != 0:
        return {
            "ok": False,
            "stage": "targeted-tests",
            "head": head,
            "before_status": before_status,
            "steps": steps,
        }

    if full_runtime:
        steps.append(_run(repo, [sys.executable, "-m", "unittest", "discover", "-s", "tests/runtime", "-p", "test_*.py", "-v"]))
        if steps[-1]["returncode"] != 0:
            return {
                "ok": False,
                "stage": "full-runtime-tests",
                "head": head,
                "before_status": before_status,
                "steps": steps,
            }

    after_status = _git(repo, "status", "--porcelain=v1", "--untracked-files=all")
    clean_delta = before_status == after_status
    normalized_before = before_status.replace("\\", "/")
    new_syntavra_paths = [
        line[3:].strip().replace("\\", "/")
        for line in after_status.splitlines()
        if len(line) >= 4
        and ".syntavra/" in line[3:].replace("\\", "/")
        and line[3:].strip().replace("\\", "/") not in normalized_before
    ]
    syntavra_pollution = bool(new_syntavra_paths)

    return {
        "ok": clean_delta and not syntavra_pollution,
        "stage": "complete" if clean_delta and not syntavra_pollution else "working-tree-integrity",
        "head": head,
        "expected_head": expected_head or None,
        "targeted_tests": list(TARGETED_TESTS),
        "full_runtime": full_runtime,
        "before_status": before_status,
        "after_status": after_status,
        "working_tree_unchanged": clean_delta,
        "new_syntavra_pollution": syntavra_pollution,
        "new_syntavra_paths": new_syntavra_paths,
        "steps": steps,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate the Syntavra Codex integration/savings closure at an exact Git head")
    parser.add_argument("--repo", default=".")
    parser.add_argument("--expected-head", default="")
    parser.add_argument("--full-runtime", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    result = validate(
        Path(args.repo),
        expected_head=str(args.expected_head).strip(),
        full_runtime=bool(args.full_runtime),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
