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

SECRET_NAME = "TEST_API_KEY"
SECRET_VALUE = "sandbox-secret-must-not-leak"


def run_engine(
    engine: str,
    args: list[str],
    *,
    repo: Path,
    rust_bin: Path,
    project: Path,
    state_root: Path,
    timeout: float = 20.0,
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
            SECRET_NAME: SECRET_VALUE,
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
        timeout=timeout,
        check=False,
    )
    value: Any = None
    if result.stdout.strip():
        try:
            value = json.loads(result.stdout)
        except json.JSONDecodeError:
            value = {"_non_json_stdout": result.stdout}
    return {
        "exit": result.returncode,
        "value": value,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


def bool_backends(value: dict[str, Any]) -> dict[str, bool]:
    return {name: bool(value.get(name)) for name in ("docker", "podman", "bwrap", "local-restricted")}


def norm_backend(value: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": value.get("name"),
        "platform": value.get("platform"),
        "available": value.get("available"),
        "enforced": sorted(value.get("enforced") or []),
        "unsupported": sorted(value.get("unsupported") or []),
    }


def norm_direct_plan(value: dict[str, Any]) -> dict[str, Any]:
    return {
        "backend": value.get("backend"),
        "command": value.get("command"),
        "cwd": value.get("cwd"),
        "guarantees": value.get("guarantees"),
        "degraded_reasons": list(value.get("degraded_reasons") or []),
        "policy": {
            "backend": (value.get("policy") or {}).get("backend"),
            "network": (value.get("policy") or {}).get("network"),
            "strict": (value.get("policy") or {}).get("strict"),
        },
    }


def norm_direct_execute(result: dict[str, Any]) -> dict[str, Any]:
    value = result.get("value") or {}
    return {
        "exit": result["exit"],
        "backend": value.get("backend"),
        "exit_code": value.get("exit_code"),
        "timed_out": value.get("timed_out"),
        "stdout_bytes": value.get("stdout_bytes"),
        "stderr_bytes": value.get("stderr_bytes"),
        "guarantees": value.get("guarantees"),
        "degraded_reasons": list(value.get("degraded_reasons") or []),
        "evidence_shape": str(value.get("evidence_handle") or "").startswith("sc://sha256/"),
    }


def norm_status(result: dict[str, Any]) -> dict[str, Any]:
    value = result.get("value") or {}
    return {
        "exit": result["exit"],
        "ok": value.get("ok"),
        "backend": norm_backend(value.get("backend") or {}),
        "strict_ready": value.get("strict_ready"),
        "fail_closed": value.get("fail_closed"),
    }


def norm_platform_run(result: dict[str, Any]) -> dict[str, Any]:
    value = result.get("value") or {}
    stdout = value.get("stdout") or ""
    parsed_stdout: Any = None
    try:
        parsed_stdout = json.loads(stdout.strip()) if stdout.strip() else None
    except json.JSONDecodeError:
        parsed_stdout = stdout.strip()
    return {
        "exit": result["exit"],
        "ok": value.get("ok"),
        "command": value.get("command"),
        "cwd": value.get("cwd"),
        "backend": norm_backend(value.get("backend") or {}),
        "exit_code": value.get("exit_code"),
        "timed_out": value.get("timed_out"),
        "output_limit_exceeded": value.get("output_limit_exceeded"),
        "stdout_bytes_seen": value.get("stdout_bytes_seen"),
        "stderr_bytes_seen": value.get("stderr_bytes_seen"),
        "stdout_sha256": value.get("stdout_sha256"),
        "stderr_sha256": value.get("stderr_sha256"),
        "stdout_value": parsed_stdout,
        "environment_keys": sorted(value.get("environment_keys") or []),
        "policy": {
            "timeout_seconds": (value.get("policy") or {}).get("timeout_seconds"),
            "allow_child_processes": (value.get("policy") or {}).get("allow_child_processes"),
            "strict_native": (value.get("policy") or {}).get("strict_native"),
        },
        "receipt_shape": str(value.get("receipt_id") or "").startswith("sha256:"),
    }


def exercise(engine: str, *, repo: Path, rust_bin: Path, project: Path, state: Path) -> dict[str, Any]:
    python = sys.executable
    env_probe_code = (
        "import json,os;"
        "print(json.dumps({'sandbox':os.getenv('SYNTAVRA_SANDBOX'),'workspace':bool(os.getenv('SYNTAVRA_WORKSPACE')),'secret':os.getenv('TEST_API_KEY')},sort_keys=True))"
    )
    direct_command = [python, "-c", env_probe_code]
    direct_args = [
        "sandbox",
        "plan",
        "--backend",
        "local-restricted",
        "--network",
        "inherit",
        "--allow-degraded",
        "--",
        *direct_command,
    ]
    direct_exec_args = direct_args.copy()
    direct_exec_args[1] = "execute"

    backends = run_engine(engine, ["sandbox", "backends"], repo=repo, rust_bin=rust_bin, project=project, state_root=state)
    plan = run_engine(engine, direct_args, repo=repo, rust_bin=rust_bin, project=project, state_root=state)
    execute = run_engine(engine, direct_exec_args, repo=repo, rust_bin=rust_bin, project=project, state_root=state)
    status = run_engine(engine, ["run", "sandbox-status"], repo=repo, rust_bin=rust_bin, project=project, state_root=state)

    platform_command = json.dumps([python, "-c", env_probe_code], separators=(",", ":"))
    platform_run = run_engine(
        engine,
        ["run", "sandbox-run", platform_command, "--timeout", "5"],
        repo=repo,
        rust_bin=rust_bin,
        project=project,
        state_root=state,
    )
    timeout_command = json.dumps([python, "-c", "import time; time.sleep(2)"], separators=(",", ":"))
    timed_out = run_engine(
        engine,
        ["run", "sandbox-run", timeout_command, "--timeout", "0.2"],
        repo=repo,
        rust_bin=rust_bin,
        project=project,
        state_root=state,
        timeout=10,
    )

    return {
        "backends": {"exit": backends["exit"], "available": bool_backends(backends.get("value") or {})},
        "direct_plan": {"exit": plan["exit"], **norm_direct_plan(plan.get("value") or {})},
        "direct_execute": norm_direct_execute(execute),
        "platform_status": norm_status(status),
        "platform_run": norm_platform_run(platform_run),
        "platform_timeout": norm_platform_run(timed_out),
    }


def compare(python_result: dict[str, Any], rust_result: dict[str, Any], project: Path) -> dict[str, Any]:
    mismatches: list[dict[str, Any]] = []

    def equal(path: str, py: Any, rs: Any, expected: Any = None, has_expected: bool = False) -> None:
        if py != rs or (has_expected and (py != expected or rs != expected)):
            mismatches.append({"path": path, "expected": expected if has_expected else "python==rust", "python": py, "rust": rs})

    for field in ("exit", "available"):
        equal(f"backends.{field}", python_result["backends"][field], rust_result["backends"][field])
    equal("backends.exit.expected", python_result["backends"]["exit"], rust_result["backends"]["exit"], 0, True)

    for field in ("exit", "backend", "command", "cwd", "guarantees", "degraded_reasons", "policy"):
        equal(f"direct_plan.{field}", python_result["direct_plan"].get(field), rust_result["direct_plan"].get(field))
    equal("direct_plan.backend.expected", python_result["direct_plan"]["backend"], rust_result["direct_plan"]["backend"], "local-restricted", True)
    equal("direct_plan.exit.expected", python_result["direct_plan"]["exit"], rust_result["direct_plan"]["exit"], 0, True)

    for field in ("exit", "backend", "exit_code", "timed_out", "stdout_bytes", "stderr_bytes", "guarantees", "degraded_reasons", "evidence_shape"):
        equal(f"direct_execute.{field}", python_result["direct_execute"].get(field), rust_result["direct_execute"].get(field))
    equal("direct_execute.exit.expected", python_result["direct_execute"]["exit"], rust_result["direct_execute"]["exit"], 0, True)
    equal("direct_execute.evidence.expected", python_result["direct_execute"]["evidence_shape"], rust_result["direct_execute"]["evidence_shape"], True, True)

    for field in ("exit", "ok", "backend", "strict_ready", "fail_closed"):
        equal(f"platform_status.{field}", python_result["platform_status"].get(field), rust_result["platform_status"].get(field))
    equal("platform_status.exit.expected", python_result["platform_status"]["exit"], rust_result["platform_status"]["exit"], 0, True)
    equal("platform_status.fail_closed.expected", python_result["platform_status"]["fail_closed"], rust_result["platform_status"]["fail_closed"], True, True)

    for field in (
        "exit",
        "ok",
        "command",
        "cwd",
        "backend",
        "exit_code",
        "timed_out",
        "output_limit_exceeded",
        "stdout_bytes_seen",
        "stderr_bytes_seen",
        "stdout_sha256",
        "stderr_sha256",
        "stdout_value",
        "environment_keys",
        "policy",
        "receipt_shape",
    ):
        equal(f"platform_run.{field}", python_result["platform_run"].get(field), rust_result["platform_run"].get(field))
    equal("platform_run.exit.expected", python_result["platform_run"]["exit"], rust_result["platform_run"]["exit"], 0, True)
    equal("platform_run.receipt.expected", python_result["platform_run"]["receipt_shape"], rust_result["platform_run"]["receipt_shape"], True, True)
    for engine, result in (("python", python_result), ("rust", rust_result)):
        env_value = result["platform_run"].get("stdout_value")
        if not isinstance(env_value, dict) or env_value.get("secret") is not None or env_value.get("sandbox") != "1" or not env_value.get("workspace"):
            mismatches.append({"path": f"platform_run.{engine}.environment", "expected": {"secret": None, "sandbox": "1", "workspace": True}, "actual": env_value})

    for field in ("ok", "command", "cwd", "backend", "timed_out", "output_limit_exceeded", "environment_keys", "policy", "receipt_shape"):
        equal(f"platform_timeout.{field}", python_result["platform_timeout"].get(field), rust_result["platform_timeout"].get(field))
    # Public CLI exit behavior is part of parity. Do not assume 124 vs 3 here;
    # compare the two engines and let the reference behavior decide.
    equal("platform_timeout.exit", python_result["platform_timeout"].get("exit"), rust_result["platform_timeout"].get("exit"))
    equal("platform_timeout.timed_out.expected", python_result["platform_timeout"]["timed_out"], rust_result["platform_timeout"]["timed_out"], True, True)

    for engine, result in (("python", python_result), ("rust", rust_result)):
        if result["direct_plan"].get("cwd") != str(project):
            mismatches.append({"path": f"direct_plan.{engine}.cwd", "expected": str(project), "actual": result["direct_plan"].get("cwd")})

    return {
        "ok": not mismatches,
        "mismatch_count": len(mismatches),
        "mismatches": mismatches,
        "claim_boundary": "local deterministic sandbox parity; native backend availability remains host-dependent",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate Python/Rust sandbox parity")
    parser.add_argument("--repo", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--rust-bin", default="target/debug/syntavra")
    parser.add_argument("--output")
    args = parser.parse_args()
    repo = Path(args.repo).resolve(strict=True)
    rust_bin = Path(args.rust_bin)
    if not rust_bin.is_absolute():
        rust_bin = (repo / rust_bin).resolve(strict=True)

    with tempfile.TemporaryDirectory(prefix="syntavra-sandbox-diff-") as directory:
        root = Path(directory)
        project = root / "project"
        project.mkdir()
        (project / ".git").mkdir()
        python_state = root / "python-state"
        rust_state = root / "rust-state"
        python_result = exercise("python", repo=repo, rust_bin=rust_bin, project=project, state=python_state)
        rust_result = exercise("rust", repo=repo, rust_bin=rust_bin, project=project, state=rust_state)
        differential = compare(python_result, rust_result, project)
        result = {"ok": differential["ok"], "python": python_result, "rust": rust_result, "differential": differential}

    rendered = json.dumps(result, indent=2, sort_keys=True)
    if args.output:
        Path(args.output).write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
