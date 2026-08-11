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
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
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


def norm_decision(result: dict[str, Any]) -> dict[str, Any]:
    value = result["value"]
    return {
        "exit": result["exit"],
        "allowed": value.get("allowed"),
        "category": value.get("category"),
        "reason": value.get("reason"),
        "requirements": list(value.get("requirements") or []),
        "arguments_hash": value.get("arguments_hash"),
        "resource": value.get("resource"),
    }


def norm_verify(result: dict[str, Any]) -> dict[str, Any]:
    value = result["value"]
    capability = value.get("capability") if isinstance(value.get("capability"), dict) else {}
    return {
        "exit": result["exit"],
        "ok": value.get("ok"),
        "reason": value.get("reason"),
        "capability": {
            "version": capability.get("version"),
            "channel": capability.get("channel"),
            "session_id": capability.get("session_id"),
            "tool": capability.get("tool"),
            "arguments_hash": capability.get("arguments_hash"),
            "resource": capability.get("resource"),
            "permissions": list(capability.get("permissions") or []),
            "single_use": capability.get("single_use"),
            "has_nonce": bool(capability.get("nonce")),
            "time_order_valid": isinstance(capability.get("issued_at"), int)
            and isinstance(capability.get("expires_at"), int)
            and capability.get("expires_at") >= capability.get("issued_at"),
        },
    }


def exercise(engine: str, *, repo: Path, rust_bin: Path, root: Path) -> dict[str, Any]:
    project = root / engine / "project"
    state = root / engine / "state"
    project.mkdir(parents=True, exist_ok=True)
    state.mkdir(parents=True, exist_ok=True)
    (project / ".git").mkdir(exist_ok=True)

    def call(*items: str) -> dict[str, Any]:
        return run_engine(engine, list(items), repo=repo, rust_bin=rust_bin, project=project, state_root=state)

    read_args = json.dumps({"path": "README.md"}, separators=(",", ":"))
    exec_args = json.dumps({"argv": ["python", "-c", "print('ok')"]}, separators=(",", ":"))
    destructive_args = json.dumps({"argv": ["git", "reset", "--hard"]}, separators=(",", ":"))
    network_args = json.dumps({"host": "blocked.example"}, separators=(",", ":"))
    token_args = json.dumps({"path": "a.txt", "content": "x"}, separators=(",", ":"))
    changed_token_args = json.dumps({"path": "b.txt", "content": "x"}, separators=(",", ":"))

    decisions = {
        "read": norm_decision(call("run", "capability-decide", "workspace.read", read_args)),
        "execute_authorization_required": norm_decision(
            call("run", "capability-decide", "shell_run", exec_args, "--sandboxed")
        ),
        "execute_sandbox_required": norm_decision(
            call("run", "capability-decide", "shell_run", exec_args, "--user-authorized")
        ),
        "execute_allowed": norm_decision(
            call(
                "run",
                "capability-decide",
                "shell_run",
                exec_args,
                "--sandboxed",
                "--user-authorized",
            )
        ),
        "destructive_denied": norm_decision(
            call(
                "run",
                "capability-decide",
                "shell_run",
                destructive_args,
                "--sandboxed",
                "--user-authorized",
            )
        ),
        "outside_workspace_denied": norm_decision(
            call(
                "run",
                "capability-decide",
                "workspace.write",
                token_args,
                "--resource",
                "file:/tmp/outside",
                "--user-authorized",
            )
        ),
        "network_allowlist_denied": norm_decision(
            call(
                "run",
                "capability-decide",
                "http_request",
                network_args,
                "--user-authorized",
                "--network-host",
                "allowed.example",
            )
        ),
    }

    issue = call(
        "run",
        "capability-issue",
        "session-1",
        "workspace.write",
        token_args,
        "--resource",
        "workspace:/",
        "--permission",
        "write",
        "--permission",
        "evidence",
        "--ttl",
        "300",
    )
    token = str(issue["value"].get("token") or "")
    if not token:
        raise RuntimeError(f"{engine} capability-issue returned no token: {issue}")
    first_verify = norm_verify(
        call(
            "run",
            "capability-verify",
            token,
            "workspace.write",
            token_args,
            "--resource",
            "workspace:/",
        )
    )
    consumed_verify = norm_verify(
        call(
            "run",
            "capability-verify",
            token,
            "workspace.write",
            token_args,
            "--resource",
            "workspace:/",
        )
    )

    issue_binding = call(
        "run",
        "capability-issue",
        "session-2",
        "workspace.write",
        token_args,
        "--resource",
        "workspace:/",
        "--permission",
        "write",
    )
    binding_token = str(issue_binding["value"].get("token") or "")
    binding_mismatch = norm_verify(
        call(
            "run",
            "capability-verify",
            binding_token,
            "workspace.write",
            changed_token_args,
            "--resource",
            "workspace:/",
        )
    )
    malformed = norm_verify(
        call(
            "run",
            "capability-verify",
            "not-a-token",
            "workspace.write",
            token_args,
            "--resource",
            "workspace:/",
        )
    )

    return {
        "decisions": decisions,
        "issue": {
            "exit": issue["exit"],
            "ok": issue["value"].get("ok"),
            "single_use": issue["value"].get("single_use"),
            "token_shape": token.count(".") == 1,
        },
        "first_verify": first_verify,
        "consumed_verify": consumed_verify,
        "binding_mismatch": binding_mismatch,
        "malformed": malformed,
    }


def expected_contract() -> dict[str, Any]:
    common = ["signed-capability", "exact-evidence"]
    execute = [*common, "explicit-user-authorization", "sandbox"]
    return {
        "decisions": {
            "read": {"exit": 0, "allowed": True, "category": "read", "reason": "policy-allowed", "requirements": common, "resource": "workspace:/"},
            "execute_authorization_required": {"exit": 0, "allowed": False, "category": "execute", "reason": "authorization-required", "requirements": execute, "resource": "workspace:/"},
            "execute_sandbox_required": {"exit": 0, "allowed": False, "category": "execute", "reason": "sandbox-required", "requirements": execute, "resource": "workspace:/"},
            "execute_allowed": {"exit": 0, "allowed": True, "category": "execute", "reason": "policy-allowed", "requirements": execute, "resource": "workspace:/"},
            "destructive_denied": {"exit": 0, "allowed": False, "category": "execute", "reason": "destructive-command-denied", "requirements": execute, "resource": "workspace:/"},
            "outside_workspace_denied": {"exit": 0, "allowed": False, "category": "write", "reason": "resource-outside-workspace", "requirements": [*common, "explicit-user-authorization"], "resource": "file:/tmp/outside"},
            "network_allowlist_denied": {"exit": 0, "allowed": False, "category": "network", "reason": "network-host-not-allowlisted", "requirements": [*common, "explicit-user-authorization"], "resource": "workspace:/"},
        },
        "issue": {"exit": 0, "ok": True, "single_use": True, "token_shape": True},
        "first_verify": {"exit": 0, "ok": True, "reason": "verified"},
        "consumed_verify": {"exit": 3, "ok": False, "reason": "already-consumed"},
        "binding_mismatch": {"exit": 3, "ok": False, "reason": "binding-mismatch"},
        "malformed": {"exit": 3, "ok": False, "reason": "malformed-token"},
    }


def compare(python_result: dict[str, Any], rust_result: dict[str, Any]) -> dict[str, Any]:
    expected = expected_contract()
    mismatches: list[dict[str, Any]] = []

    for name, contract in expected["decisions"].items():
        py = python_result["decisions"][name]
        rs = rust_result["decisions"][name]
        for field, expected_value in contract.items():
            if py.get(field) != expected_value or rs.get(field) != expected_value or py.get(field) != rs.get(field):
                mismatches.append({"path": f"decisions.{name}.{field}", "expected": expected_value, "python": py.get(field), "rust": rs.get(field)})
        if py.get("arguments_hash") != rs.get("arguments_hash"):
            mismatches.append({"path": f"decisions.{name}.arguments_hash", "expected": "equal", "python": py.get("arguments_hash"), "rust": rs.get("arguments_hash")})

    for section in ["issue", "first_verify", "consumed_verify", "binding_mismatch", "malformed"]:
        contract = expected[section]
        py = python_result[section]
        rs = rust_result[section]
        for field, expected_value in contract.items():
            if py.get(field) != expected_value or rs.get(field) != expected_value or py.get(field) != rs.get(field):
                mismatches.append({"path": f"{section}.{field}", "expected": expected_value, "python": py.get(field), "rust": rs.get(field)})

    py_cap = python_result["first_verify"]["capability"]
    rs_cap = rust_result["first_verify"]["capability"]
    for field in ["version", "channel", "session_id", "tool", "arguments_hash", "resource", "permissions", "single_use", "has_nonce", "time_order_valid"]:
        if py_cap.get(field) != rs_cap.get(field):
            mismatches.append({"path": f"first_verify.capability.{field}", "expected": "equal", "python": py_cap.get(field), "rust": rs_cap.get(field)})

    return {
        "ok": not mismatches,
        "mismatch_count": len(mismatches),
        "mismatches": mismatches,
        "claim_boundary": "local deterministic capability/security differential only",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate Python/Rust capability security parity")
    parser.add_argument("--repo", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--rust-bin", default="target/debug/syntavra")
    parser.add_argument("--output")
    args = parser.parse_args()
    repo = Path(args.repo).resolve(strict=True)
    rust_bin = Path(args.rust_bin)
    if not rust_bin.is_absolute():
        rust_bin = (repo / rust_bin).resolve(strict=True)

    with tempfile.TemporaryDirectory(prefix="syntavra-capability-diff-") as directory:
        root = Path(directory)
        python_result = exercise("python", repo=repo, rust_bin=rust_bin, root=root)
        rust_result = exercise("rust", repo=repo, rust_bin=rust_bin, root=root)
        differential = compare(python_result, rust_result)
        result = {"ok": differential["ok"], "python": python_result, "rust": rust_result, "differential": differential}

    rendered = json.dumps(result, indent=2, sort_keys=True)
    if args.output:
        Path(args.output).write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
