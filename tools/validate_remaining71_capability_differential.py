#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

FIXTURE_RELATIVE = Path("contracts/python/capability-inventory-reference-v1.json")


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
        command = [
            sys.executable,
            "-m",
            "syntavra_runtime.engine_entry",
            "--engine",
            "python",
            *common,
        ]
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
    value: Any = None
    if result.stdout.strip():
        try:
            value = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"{engine} emitted non-JSON for {' '.join(args)}\n"
                f"exit={result.returncode}\nstdout={result.stdout}\nstderr={result.stderr}"
            ) from exc
    return {
        "exit": result.returncode,
        "value": value,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


def require_json(result: dict[str, Any], *, label: str) -> dict[str, Any]:
    value = result.get("value")
    if not isinstance(value, dict):
        raise RuntimeError(f"{label} emitted no JSON object: {result}")
    return value


def norm_decision(result: dict[str, Any], *, label: str) -> dict[str, Any]:
    value = require_json(result, label=label)
    return {
        "exit": result["exit"],
        "stderr": result["stderr"],
        "allowed": value.get("allowed"),
        "category": value.get("category"),
        "reason": value.get("reason"),
        "requirements": list(value.get("requirements") or []),
        "arguments_hash": value.get("arguments_hash"),
        "resource": value.get("resource"),
        "keys": sorted(value),
    }


def norm_verify(result: dict[str, Any], *, label: str) -> dict[str, Any]:
    value = require_json(result, label=label)
    capability = value.get("capability") if isinstance(value.get("capability"), dict) else {}
    return {
        "exit": result["exit"],
        "stderr": result["stderr"],
        "ok": value.get("ok"),
        "reason": value.get("reason"),
        "keys": sorted(value),
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
            "issued_at_is_int": isinstance(capability.get("issued_at"), int),
            "expires_at_is_int": isinstance(capability.get("expires_at"), int),
            "time_order_valid": isinstance(capability.get("issued_at"), int)
            and isinstance(capability.get("expires_at"), int)
            and capability.get("expires_at") >= capability.get("issued_at"),
            "keys": sorted(capability),
        },
    }


def invalid_signature_token(token: str) -> str:
    payload, signature = token.split(".", 1)
    if not signature:
        raise RuntimeError("capability signature is empty")
    first = "A" if signature[0] != "A" else "B"
    return f"{payload}.{first}{signature[1:]}"


def expired_token(token: str, *, signing_key: bytes) -> str:
    payload_text, _ = token.split(".", 1)
    payload_raw = base64.urlsafe_b64decode(payload_text + "=" * (-len(payload_text) % 4))
    body = json.loads(payload_raw)
    if not isinstance(body, dict):
        raise RuntimeError("capability payload is not an object")
    body["expires_at"] = 0
    canonical = json.dumps(
        body,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    payload = base64.urlsafe_b64encode(canonical).rstrip(b"=")
    signature = hmac.new(signing_key, payload, hashlib.sha256).digest()
    return (
        payload.decode("ascii")
        + "."
        + base64.urlsafe_b64encode(signature).rstrip(b"=").decode("ascii")
    )


def parser_error_summary(result: dict[str, Any]) -> dict[str, Any]:
    stderr = str(result.get("stderr") or "")
    return {
        "exit": result.get("exit"),
        "stdout_empty": not bool(str(result.get("stdout") or "")),
        "stderr_kind": "argparse-usage-error" if "usage:" in stderr.casefold() else "other",
    }


def issue_token(
    call: Any,
    *,
    session_id: str,
    arguments: str,
) -> tuple[dict[str, Any], str]:
    result = call(
        "run",
        "capability-issue",
        session_id,
        "repo.patch",
        arguments,
        "--resource",
        "workspace:/module.py",
        "--permission",
        "write",
        "--permission",
        "evidence",
        "--ttl",
        "300",
    )
    value = require_json(result, label=f"capability-issue {session_id}")
    token = str(value.get("token") or "")
    if not token or token.count(".") != 1:
        raise RuntimeError(f"capability-issue returned invalid token: {result}")
    summary = {
        "exit": result["exit"],
        "stderr": result["stderr"],
        "ok": value.get("ok"),
        "single_use": value.get("single_use"),
        "token_shape": token.count(".") == 1,
        "keys": sorted(value),
    }
    return summary, token


def exercise(engine: str, *, repo: Path, rust_bin: Path, root: Path) -> dict[str, Any]:
    project = root / engine / "project"
    state = root / engine / "state"
    project.mkdir(parents=True, exist_ok=True)
    state.mkdir(parents=True, exist_ok=True)
    (project / ".git").mkdir(exist_ok=True)

    def call(*items: str) -> dict[str, Any]:
        return run_engine(
            engine,
            list(items),
            repo=repo,
            rust_bin=rust_bin,
            project=project,
            state_root=state,
        )

    read_args = json.dumps({"path": "module.py"}, separators=(",", ":"))
    write_args = json.dumps({"path": "module.py", "content": "x"}, separators=(",", ":"))
    changed_write_args = json.dumps({"path": "other.py", "content": "x"}, separators=(",", ":"))
    exec_args = json.dumps({"argv": ["python", "-m", "unittest"]}, separators=(",", ":"))
    destructive_args = json.dumps({"argv": ["git", "reset", "--hard"]}, separators=(",", ":"))
    network_args = json.dumps({"host": "blocked.example"}, separators=(",", ":"))
    unknown_args = json.dumps({"x": 1}, separators=(",", ":"))

    decisions = {
        "read": norm_decision(
            call(
                "run",
                "capability-decide",
                "repo.read",
                read_args,
                "--resource",
                "workspace:/module.py",
            ),
            label="read",
        ),
        "write_authorization_required": norm_decision(
            call(
                "run",
                "capability-decide",
                "repo.patch",
                write_args,
                "--resource",
                "workspace:/module.py",
            ),
            label="write_authorization_required",
        ),
        "write_allowed": norm_decision(
            call(
                "run",
                "capability-decide",
                "repo.patch",
                write_args,
                "--resource",
                "workspace:/module.py",
                "--user-authorized",
            ),
            label="write_allowed",
        ),
        "execute_authorization_required": norm_decision(
            call(
                "run",
                "capability-decide",
                "test.run",
                exec_args,
                "--sandboxed",
            ),
            label="execute_authorization_required",
        ),
        "execute_sandbox_required": norm_decision(
            call(
                "run",
                "capability-decide",
                "test.run",
                exec_args,
                "--user-authorized",
            ),
            label="execute_sandbox_required",
        ),
        "execute_allowed": norm_decision(
            call(
                "run",
                "capability-decide",
                "test.run",
                exec_args,
                "--sandboxed",
                "--user-authorized",
            ),
            label="execute_allowed",
        ),
        "destructive_denied": norm_decision(
            call(
                "run",
                "capability-decide",
                "shell_run",
                destructive_args,
                "--sandboxed",
                "--user-authorized",
            ),
            label="destructive_denied",
        ),
        "outside_workspace_denied": norm_decision(
            call(
                "run",
                "capability-decide",
                "repo.patch",
                write_args,
                "--resource",
                "file:/tmp/outside",
                "--user-authorized",
            ),
            label="outside_workspace_denied",
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
            ),
            label="network_allowlist_denied",
        ),
        "unknown_fail_closed": norm_decision(
            call("run", "capability-decide", "totally.unknown.tool", unknown_args),
            label="unknown_fail_closed",
        ),
    }

    issue, token = issue_token(call, session_id="session-1", arguments=write_args)
    first_verify = norm_verify(
        call(
            "run",
            "capability-verify",
            token,
            "repo.patch",
            write_args,
            "--resource",
            "workspace:/module.py",
        ),
        label="first_verify",
    )
    consumed_verify = norm_verify(
        call(
            "run",
            "capability-verify",
            token,
            "repo.patch",
            write_args,
            "--resource",
            "workspace:/module.py",
        ),
        label="consumed_verify",
    )

    _, second_token = issue_token(call, session_id="session-2", arguments=write_args)
    binding_mismatch = norm_verify(
        call(
            "run",
            "capability-verify",
            second_token,
            "repo.patch",
            changed_write_args,
            "--resource",
            "workspace:/module.py",
            "--no-consume",
        ),
        label="binding_mismatch",
    )
    malformed = norm_verify(
        call(
            "run",
            "capability-verify",
            "not-a-token",
            "repo.patch",
            write_args,
            "--resource",
            "workspace:/module.py",
            "--no-consume",
        ),
        label="malformed",
    )
    invalid_signature = norm_verify(
        call(
            "run",
            "capability-verify",
            invalid_signature_token(second_token),
            "repo.patch",
            write_args,
            "--resource",
            "workspace:/module.py",
            "--no-consume",
        ),
        label="invalid_signature",
    )

    key_path = state / "capability.key"
    db_path = state / "capability.sqlite3"
    signing_key = key_path.read_bytes()
    expired = norm_verify(
        call(
            "run",
            "capability-verify",
            expired_token(second_token, signing_key=signing_key),
            "repo.patch",
            write_args,
            "--resource",
            "workspace:/module.py",
            "--no-consume",
        ),
        label="expired",
    )

    with sqlite3.connect(db_path) as database:
        consumed_rows = database.execute("SELECT COUNT(*) FROM consumed").fetchone()[0]

    parser_error = parser_error_summary(
        call("run", "capability-decide", "repo.read")
    )

    return {
        "decisions": decisions,
        "issue": issue,
        "verification": {
            "verified": first_verify,
            "already_consumed": consumed_verify,
            "binding_mismatch": binding_mismatch,
            "malformed": malformed,
            "invalid_signature": invalid_signature,
            "expired": expired,
        },
        "parser_error": parser_error,
        "durable_state": {
            "signing_key_bytes": len(signing_key),
            "database_exists": db_path.is_file(),
            "consumed_rows": consumed_rows,
        },
    }


def expected_decisions() -> dict[str, dict[str, Any]]:
    base = ["signed-capability", "exact-evidence"]
    authorized = [*base, "explicit-user-authorization"]
    execute = [*authorized, "sandbox"]
    return {
        "read": {
            "exit": 0,
            "allowed": True,
            "category": "read",
            "reason": "policy-allowed",
            "requirements": base,
            "resource": "workspace:/module.py",
        },
        "write_authorization_required": {
            "exit": 0,
            "allowed": False,
            "category": "write",
            "reason": "authorization-required",
            "requirements": authorized,
            "resource": "workspace:/module.py",
        },
        "write_allowed": {
            "exit": 0,
            "allowed": True,
            "category": "write",
            "reason": "policy-allowed",
            "requirements": authorized,
            "resource": "workspace:/module.py",
        },
        "execute_authorization_required": {
            "exit": 0,
            "allowed": False,
            "category": "execute",
            "reason": "authorization-required",
            "requirements": execute,
            "resource": "workspace:/",
        },
        "execute_sandbox_required": {
            "exit": 0,
            "allowed": False,
            "category": "execute",
            "reason": "sandbox-required",
            "requirements": execute,
            "resource": "workspace:/",
        },
        "execute_allowed": {
            "exit": 0,
            "allowed": True,
            "category": "execute",
            "reason": "policy-allowed",
            "requirements": execute,
            "resource": "workspace:/",
        },
        "destructive_denied": {
            "exit": 0,
            "allowed": False,
            "category": "execute",
            "reason": "destructive-command-denied",
            "requirements": execute,
            "resource": "workspace:/",
        },
        "outside_workspace_denied": {
            "exit": 0,
            "allowed": False,
            "category": "write",
            "reason": "resource-outside-workspace",
            "requirements": authorized,
            "resource": "file:/tmp/outside",
        },
        "network_allowlist_denied": {
            "exit": 0,
            "allowed": False,
            "category": "network",
            "reason": "network-host-not-allowlisted",
            "requirements": authorized,
            "resource": "workspace:/",
        },
        "unknown_fail_closed": {
            "exit": 0,
            "allowed": False,
            "category": "unknown",
            "reason": "unknown-tool-fail-closed",
            "requirements": base,
            "resource": "workspace:/",
        },
    }


def append_mismatch(
    mismatches: list[dict[str, Any]],
    *,
    path: str,
    expected: Any,
    python: Any,
    rust: Any,
) -> None:
    if python != expected or rust != expected or python != rust:
        mismatches.append(
            {
                "path": path,
                "expected": expected,
                "python": python,
                "rust": rust,
            }
        )


def compare(
    python_result: dict[str, Any],
    rust_result: dict[str, Any],
    *,
    fixture: dict[str, Any],
) -> dict[str, Any]:
    mismatches: list[dict[str, Any]] = []
    decisions = expected_decisions()
    decision_fields = [
        "exit",
        "allowed",
        "category",
        "reason",
        "requirements",
        "resource",
    ]
    for name, contract in decisions.items():
        py = python_result["decisions"][name]
        rs = rust_result["decisions"][name]
        for field in decision_fields:
            append_mismatch(
                mismatches,
                path=f"decisions.{name}.{field}",
                expected=contract[field],
                python=py.get(field),
                rust=rs.get(field),
            )
        append_mismatch(
            mismatches,
            path=f"decisions.{name}.stderr",
            expected="",
            python=py.get("stderr"),
            rust=rs.get("stderr"),
        )
        append_mismatch(
            mismatches,
            path=f"decisions.{name}.keys",
            expected=[
                "allowed",
                "arguments_hash",
                "category",
                "reason",
                "requirements",
                "resource",
            ],
            python=py.get("keys"),
            rust=rs.get("keys"),
        )
        if py.get("arguments_hash") != rs.get("arguments_hash"):
            mismatches.append(
                {
                    "path": f"decisions.{name}.arguments_hash",
                    "expected": "equal",
                    "python": py.get("arguments_hash"),
                    "rust": rs.get("arguments_hash"),
                }
            )

    observed_categories = sorted(
        {row["category"] for row in python_result["decisions"].values()}
    )
    observed_reasons = sorted(
        {row["reason"] for row in python_result["decisions"].values()}
    )
    observed_requirements = sorted(
        {
            requirement
            for row in python_result["decisions"].values()
            for requirement in row["requirements"]
        }
    )
    rust_categories = sorted(
        {row["category"] for row in rust_result["decisions"].values()}
    )
    rust_reasons = sorted(
        {row["reason"] for row in rust_result["decisions"].values()}
    )
    rust_requirements = sorted(
        {
            requirement
            for row in rust_result["decisions"].values()
            for requirement in row["requirements"]
        }
    )
    frozen_capability = fixture["capability"]
    for path, expected, py, rs in [
        (
            "vocabulary.categories",
            frozen_capability["category_vocabulary"],
            observed_categories,
            rust_categories,
        ),
        (
            "vocabulary.decision_reasons",
            frozen_capability["decision_reason_vocabulary"],
            observed_reasons,
            rust_reasons,
        ),
        (
            "vocabulary.requirements",
            frozen_capability["requirement_vocabulary"],
            observed_requirements,
            rust_requirements,
        ),
    ]:
        append_mismatch(
            mismatches,
            path=path,
            expected=expected,
            python=py,
            rust=rs,
        )

    issue_expected = {
        "exit": 0,
        "stderr": "",
        "ok": True,
        "single_use": True,
        "token_shape": True,
        "keys": ["ok", "single_use", "token"],
    }
    for field, expected in issue_expected.items():
        append_mismatch(
            mismatches,
            path=f"issue.{field}",
            expected=expected,
            python=python_result["issue"].get(field),
            rust=rust_result["issue"].get(field),
        )

    verification_expected = {
        "verified": (0, True, "verified", ["capability", "ok", "reason"]),
        "already_consumed": (3, False, "already-consumed", ["capability", "ok", "reason"]),
        "binding_mismatch": (3, False, "binding-mismatch", ["capability", "ok", "reason"]),
        "malformed": (3, False, "malformed-token", ["ok", "reason"]),
        "invalid_signature": (3, False, "invalid-signature", ["ok", "reason"]),
        "expired": (3, False, "expired", ["capability", "ok", "reason"]),
    }
    for name, (exit_code, ok, reason, keys) in verification_expected.items():
        py = python_result["verification"][name]
        rs = rust_result["verification"][name]
        for field, expected in {
            "exit": exit_code,
            "stderr": "",
            "ok": ok,
            "reason": reason,
            "keys": keys,
        }.items():
            append_mismatch(
                mismatches,
                path=f"verification.{name}.{field}",
                expected=expected,
                python=py.get(field),
                rust=rs.get(field),
            )

    py_verify_reasons = sorted(
        {row["reason"] for row in python_result["verification"].values()}
    )
    rs_verify_reasons = sorted(
        {row["reason"] for row in rust_result["verification"].values()}
    )
    append_mismatch(
        mismatches,
        path="vocabulary.verification_reasons",
        expected=frozen_capability["verification_reason_vocabulary"],
        python=py_verify_reasons,
        rust=rs_verify_reasons,
    )

    capability_fields = [
        "version",
        "channel",
        "session_id",
        "tool",
        "arguments_hash",
        "resource",
        "permissions",
        "single_use",
        "has_nonce",
        "issued_at_is_int",
        "expires_at_is_int",
        "time_order_valid",
        "keys",
    ]
    py_capability = python_result["verification"]["verified"]["capability"]
    rs_capability = rust_result["verification"]["verified"]["capability"]
    for field in capability_fields:
        if py_capability.get(field) != rs_capability.get(field):
            mismatches.append(
                {
                    "path": f"verification.verified.capability.{field}",
                    "expected": "equal",
                    "python": py_capability.get(field),
                    "rust": rs_capability.get(field),
                }
            )

    append_mismatch(
        mismatches,
        path="parser_error",
        expected={
            "exit": 2,
            "stdout_empty": True,
            "stderr_kind": "argparse-usage-error",
        },
        python=python_result["parser_error"],
        rust=rust_result["parser_error"],
    )
    append_mismatch(
        mismatches,
        path="durable_state",
        expected={
            "signing_key_bytes": 32,
            "database_exists": True,
            "consumed_rows": 1,
        },
        python=python_result["durable_state"],
        rust=rust_result["durable_state"],
    )

    return {
        "ok": not mismatches,
        "mismatch_count": len(mismatches),
        "mismatches": mismatches,
        "frozen_public_routes": frozen_capability["public_routes"],
        "category_vocabulary": frozen_capability["category_vocabulary"],
        "decision_reason_vocabulary": frozen_capability["decision_reason_vocabulary"],
        "requirement_vocabulary": frozen_capability["requirement_vocabulary"],
        "verification_reason_vocabulary": frozen_capability["verification_reason_vocabulary"],
        "claim_boundary": (
            "frozen Phase-1 capability decision/verification vocabulary, parser-error, "
            "single-use durable state and Python/Rust behavior"
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate frozen Python/Rust capability security parity"
    )
    parser.add_argument("--repo", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--rust-bin", default="target/debug/syntavra")
    parser.add_argument("--output")
    args = parser.parse_args()
    repo = Path(args.repo).resolve(strict=True)
    rust_bin = Path(args.rust_bin)
    if not rust_bin.is_absolute():
        rust_bin = (repo / rust_bin).resolve(strict=True)
    fixture = json.loads((repo / FIXTURE_RELATIVE).read_text(encoding="utf-8"))

    with tempfile.TemporaryDirectory(prefix="syntavra-capability-diff-") as directory:
        root = Path(directory)
        python_result = exercise("python", repo=repo, rust_bin=rust_bin, root=root)
        rust_result = exercise("rust", repo=repo, rust_bin=rust_bin, root=root)
        differential = compare(python_result, rust_result, fixture=fixture)
        result = {
            "ok": differential["ok"],
            "schema_version": 2,
            "family": "capability-inventory",
            "python": python_result,
            "rust": rust_result,
            "differential": differential,
        }

    rendered = json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False)
    if args.output:
        Path(args.output).write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
