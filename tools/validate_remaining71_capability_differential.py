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
import traceback
from pathlib import Path
from typing import Any, Callable

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
    command = (
        [sys.executable, "-m", "syntavra_runtime.engine_entry", "--engine", "python", *common]
        if engine == "python"
        else [str(rust_bin), "--engine", "rust", *common]
    )
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
        timeout=30,
        check=False,
    )
    value: Any = None
    if completed.stdout.strip():
        try:
            value = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"{engine} emitted non-JSON for {' '.join(args)}: "
                f"exit={completed.returncode} stdout={completed.stdout!r} "
                f"stderr={completed.stderr!r}"
            ) from exc
    return {
        "exit": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "value": value,
    }


def require_object(result: dict[str, Any], label: str) -> dict[str, Any]:
    value = result.get("value")
    if not isinstance(value, dict):
        raise AssertionError(f"{label}: expected JSON object, got {result}")
    return value


def norm_decision(result: dict[str, Any], label: str) -> dict[str, Any]:
    value = require_object(result, label)
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


def norm_verify(result: dict[str, Any], label: str) -> dict[str, Any]:
    value = require_object(result, label)
    capability = value.get("capability") if isinstance(value.get("capability"), dict) else None
    summary: dict[str, Any] = {
        "exit": result["exit"],
        "stderr": result["stderr"],
        "ok": value.get("ok"),
        "reason": value.get("reason"),
        "keys": sorted(value),
    }
    if capability is not None:
        summary["capability"] = {
            "version": capability.get("version"),
            "channel": capability.get("channel"),
            "session_id": capability.get("session_id"),
            "tool": capability.get("tool"),
            "arguments_hash": capability.get("arguments_hash"),
            "resource": capability.get("resource"),
            "permissions": list(capability.get("permissions") or []),
            "single_use": capability.get("single_use"),
            "nonce_present": bool(capability.get("nonce")),
            "issued_at_is_int": isinstance(capability.get("issued_at"), int),
            "expires_at_is_int": isinstance(capability.get("expires_at"), int),
            "time_order_valid": isinstance(capability.get("issued_at"), int)
            and isinstance(capability.get("expires_at"), int)
            and capability.get("expires_at") >= capability.get("issued_at"),
            "keys": sorted(capability),
        }
    return summary


def invalid_signature_token(token: str) -> str:
    payload, signature = token.split(".", 1)
    if not signature:
        raise AssertionError("empty capability signature")
    first = "A" if signature[0] != "A" else "B"
    return f"{payload}.{first}{signature[1:]}"


def expired_token(token: str, signing_key: bytes) -> str:
    payload_text, _ = token.split(".", 1)
    raw = base64.urlsafe_b64decode(payload_text + "=" * (-len(payload_text) % 4))
    body = json.loads(raw)
    if not isinstance(body, dict):
        raise AssertionError("capability payload is not an object")
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


def unique_state_file(state: Path, name: str) -> Path:
    matches = sorted(path for path in state.rglob(name) if path.is_file())
    if len(matches) != 1:
        raise AssertionError(
            f"expected exactly one {name} below {state}, got {[str(path) for path in matches]}"
        )
    return matches[0]


def issue_token(call: Callable[..., dict[str, Any]], session_id: str, arguments: str) -> tuple[dict[str, Any], str]:
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
    value = require_object(result, f"capability-issue {session_id}")
    token = str(value.get("token") or "")
    if token.count(".") != 1:
        raise AssertionError(f"capability-issue token shape drift: {value}")
    return {
        "exit": result["exit"],
        "stderr": result["stderr"],
        "ok": value.get("ok"),
        "single_use": value.get("single_use"),
        "token_shape": True,
        "keys": sorted(value),
    }, token


def parser_error(result: dict[str, Any]) -> dict[str, Any]:
    stderr = str(result.get("stderr") or "")
    return {
        "exit": result.get("exit"),
        "stdout_empty": not bool(str(result.get("stdout") or "")),
        "stderr_kind": "argparse-usage-error" if "usage:" in stderr.casefold() else "other",
    }


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
    changed_args = json.dumps({"path": "other.py", "content": "x"}, separators=(",", ":"))
    exec_args = json.dumps({"argv": ["python", "-m", "unittest"]}, separators=(",", ":"))
    destructive_args = json.dumps({"argv": ["git", "reset", "--hard"]}, separators=(",", ":"))
    network_args = json.dumps({"host": "blocked.example"}, separators=(",", ":"))
    unknown_args = json.dumps({"x": 1}, separators=(",", ":"))

    def decision(name: str, *args: str) -> tuple[str, dict[str, Any]]:
        return name, norm_decision(call(*args), name)

    decisions = dict(
        [
            decision(
                "read",
                "run", "capability-decide", "repo.read", read_args,
                "--resource", "workspace:/module.py",
            ),
            decision(
                "write_authorization_required",
                "run", "capability-decide", "repo.patch", write_args,
                "--resource", "workspace:/module.py",
            ),
            decision(
                "write_allowed",
                "run", "capability-decide", "repo.patch", write_args,
                "--resource", "workspace:/module.py", "--user-authorized",
            ),
            decision(
                "execute_authorization_required",
                "run", "capability-decide", "test.run", exec_args, "--sandboxed",
            ),
            decision(
                "execute_sandbox_required",
                "run", "capability-decide", "test.run", exec_args, "--user-authorized",
            ),
            decision(
                "execute_allowed",
                "run", "capability-decide", "test.run", exec_args,
                "--sandboxed", "--user-authorized",
            ),
            decision(
                "destructive_denied",
                "run", "capability-decide", "shell_run", destructive_args,
                "--sandboxed", "--user-authorized",
            ),
            decision(
                "outside_workspace_denied",
                "run", "capability-decide", "repo.patch", write_args,
                "--resource", "file:/tmp/outside", "--user-authorized",
            ),
            decision(
                "network_allowlist_denied",
                "run", "capability-decide", "http_request", network_args,
                "--user-authorized", "--network-host", "allowed.example",
            ),
            decision(
                "unknown_fail_closed",
                "run", "capability-decide", "totally.unknown.tool", unknown_args,
            ),
        ]
    )

    issue, first_token = issue_token(call, "session-1", write_args)
    verified = norm_verify(
        call(
            "run", "capability-verify", first_token, "repo.patch", write_args,
            "--resource", "workspace:/module.py",
        ),
        "verified",
    )
    already_consumed = norm_verify(
        call(
            "run", "capability-verify", first_token, "repo.patch", write_args,
            "--resource", "workspace:/module.py",
        ),
        "already_consumed",
    )

    _, second_token = issue_token(call, "session-2", write_args)
    binding_mismatch = norm_verify(
        call(
            "run", "capability-verify", second_token, "repo.patch", changed_args,
            "--resource", "workspace:/module.py", "--no-consume",
        ),
        "binding_mismatch",
    )
    malformed = norm_verify(
        call(
            "run", "capability-verify", "not-a-token", "repo.patch", write_args,
            "--resource", "workspace:/module.py", "--no-consume",
        ),
        "malformed",
    )
    invalid_signature = norm_verify(
        call(
            "run", "capability-verify", invalid_signature_token(second_token),
            "repo.patch", write_args, "--resource", "workspace:/module.py", "--no-consume",
        ),
        "invalid_signature",
    )

    key_path = unique_state_file(state, "capability.key")
    db_path = unique_state_file(state, "capability.sqlite3")
    signing_key = key_path.read_bytes()
    expired = norm_verify(
        call(
            "run", "capability-verify", expired_token(second_token, signing_key),
            "repo.patch", write_args, "--resource", "workspace:/module.py", "--no-consume",
        ),
        "expired",
    )
    with sqlite3.connect(db_path) as database:
        consumed_rows = int(database.execute("SELECT COUNT(*) FROM consumed").fetchone()[0])

    return {
        "decisions": decisions,
        "issue": issue,
        "verification": {
            "verified": verified,
            "already_consumed": already_consumed,
            "binding_mismatch": binding_mismatch,
            "malformed": malformed,
            "invalid_signature": invalid_signature,
            "expired": expired,
        },
        "parser_error": parser_error(call("run", "capability-decide", "repo.read")),
        "durable_state": {
            "signing_key_bytes": len(signing_key),
            "database_exists": db_path.is_file(),
            "consumed_rows": consumed_rows,
            "key_relative": key_path.relative_to(state).as_posix(),
            "database_relative": db_path.relative_to(state).as_posix(),
        },
    }


def expected_decisions() -> dict[str, dict[str, Any]]:
    base = ["signed-capability", "exact-evidence"]
    authorized = [*base, "explicit-user-authorization"]
    execute = [*authorized, "sandbox"]
    return {
        "read": {"exit": 0, "allowed": True, "category": "read", "reason": "policy-allowed", "requirements": base, "resource": "workspace:/module.py"},
        "write_authorization_required": {"exit": 0, "allowed": False, "category": "write", "reason": "authorization-required", "requirements": authorized, "resource": "workspace:/module.py"},
        "write_allowed": {"exit": 0, "allowed": True, "category": "write", "reason": "policy-allowed", "requirements": authorized, "resource": "workspace:/module.py"},
        "execute_authorization_required": {"exit": 0, "allowed": False, "category": "execute", "reason": "authorization-required", "requirements": execute, "resource": "workspace:/"},
        "execute_sandbox_required": {"exit": 0, "allowed": False, "category": "execute", "reason": "sandbox-required", "requirements": execute, "resource": "workspace:/"},
        "execute_allowed": {"exit": 0, "allowed": True, "category": "execute", "reason": "policy-allowed", "requirements": execute, "resource": "workspace:/"},
        "destructive_denied": {"exit": 0, "allowed": False, "category": "execute", "reason": "destructive-command-denied", "requirements": execute, "resource": "workspace:/"},
        "outside_workspace_denied": {"exit": 0, "allowed": False, "category": "write", "reason": "resource-outside-workspace", "requirements": authorized, "resource": "file:/tmp/outside"},
        "network_allowlist_denied": {"exit": 0, "allowed": False, "category": "network", "reason": "network-host-not-allowlisted", "requirements": authorized, "resource": "workspace:/"},
        "unknown_fail_closed": {"exit": 0, "allowed": False, "category": "unknown", "reason": "unknown-tool-fail-closed", "requirements": base, "resource": "workspace:/"},
    }


def mismatch(
    rows: list[dict[str, Any]],
    path: str,
    expected: Any,
    python_value: Any,
    rust_value: Any,
) -> None:
    if python_value != expected or rust_value != expected or python_value != rust_value:
        rows.append(
            {
                "path": path,
                "expected": expected,
                "python": python_value,
                "rust": rust_value,
            }
        )


def compare(python: dict[str, Any], rust: dict[str, Any], fixture: dict[str, Any]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    decision_schema = ["allowed", "arguments_hash", "category", "reason", "requirements", "resource"]
    for name, contract in expected_decisions().items():
        py = python["decisions"][name]
        rs = rust["decisions"][name]
        for field, expected in contract.items():
            mismatch(rows, f"decisions.{name}.{field}", expected, py.get(field), rs.get(field))
        mismatch(rows, f"decisions.{name}.stderr", "", py.get("stderr"), rs.get("stderr"))
        mismatch(rows, f"decisions.{name}.keys", decision_schema, py.get("keys"), rs.get("keys"))
        if py.get("arguments_hash") != rs.get("arguments_hash"):
            rows.append(
                {
                    "path": f"decisions.{name}.arguments_hash",
                    "expected": "equal",
                    "python": py.get("arguments_hash"),
                    "rust": rs.get("arguments_hash"),
                }
            )

    frozen = fixture["capability"]
    for engine_name, result in (("python", python), ("rust", rust)):
        categories = sorted({item["category"] for item in result["decisions"].values()})
        reasons = sorted({item["reason"] for item in result["decisions"].values()})
        requirements = sorted({req for item in result["decisions"].values() for req in item["requirements"]})
        if categories != frozen["category_vocabulary"]:
            rows.append({"path": f"{engine_name}.category_vocabulary", "expected": frozen["category_vocabulary"], "actual": categories})
        if reasons != frozen["decision_reason_vocabulary"]:
            rows.append({"path": f"{engine_name}.decision_reason_vocabulary", "expected": frozen["decision_reason_vocabulary"], "actual": reasons})
        if requirements != frozen["requirement_vocabulary"]:
            rows.append({"path": f"{engine_name}.requirement_vocabulary", "expected": frozen["requirement_vocabulary"], "actual": requirements})

    issue_expected = {"exit": 0, "stderr": "", "ok": True, "single_use": True, "token_shape": True, "keys": ["ok", "single_use", "token"]}
    for field, expected in issue_expected.items():
        mismatch(rows, f"issue.{field}", expected, python["issue"].get(field), rust["issue"].get(field))

    verify_expected = {
        "verified": (0, True, "verified"),
        "already_consumed": (3, False, "already-consumed"),
        "binding_mismatch": (3, False, "binding-mismatch"),
        "malformed": (3, False, "malformed-token"),
        "invalid_signature": (3, False, "invalid-signature"),
        "expired": (3, False, "expired"),
    }
    for name, (exit_code, ok, reason) in verify_expected.items():
        py = python["verification"][name]
        rs = rust["verification"][name]
        for field, expected in (("exit", exit_code), ("stderr", ""), ("ok", ok), ("reason", reason)):
            mismatch(rows, f"verification.{name}.{field}", expected, py.get(field), rs.get(field))
        if py.get("keys") != rs.get("keys"):
            rows.append({"path": f"verification.{name}.keys", "expected": "equal", "python": py.get("keys"), "rust": rs.get("keys")})
        if py.get("capability") != rs.get("capability"):
            rows.append({"path": f"verification.{name}.capability", "expected": "equal normalized capability", "python": py.get("capability"), "rust": rs.get("capability")})

    for engine_name, result in (("python", python), ("rust", rust)):
        verify_reasons = sorted({item["reason"] for item in result["verification"].values()})
        if verify_reasons != frozen["verification_reason_vocabulary"]:
            rows.append({"path": f"{engine_name}.verification_reason_vocabulary", "expected": frozen["verification_reason_vocabulary"], "actual": verify_reasons})

    parser_expected = {"exit": 2, "stdout_empty": True, "stderr_kind": "argparse-usage-error"}
    mismatch(rows, "parser_error", parser_expected, python["parser_error"], rust["parser_error"])

    durable_expected = {"signing_key_bytes": 32, "database_exists": True, "consumed_rows": 1}
    for field, expected in durable_expected.items():
        mismatch(rows, f"durable_state.{field}", expected, python["durable_state"].get(field), rust["durable_state"].get(field))
    if python["durable_state"].get("key_relative") != rust["durable_state"].get("key_relative"):
        rows.append({"path": "durable_state.key_relative", "expected": "equal", "python": python["durable_state"].get("key_relative"), "rust": rust["durable_state"].get("key_relative")})
    if python["durable_state"].get("database_relative") != rust["durable_state"].get("database_relative"):
        rows.append({"path": "durable_state.database_relative", "expected": "equal", "python": python["durable_state"].get("database_relative"), "rust": rust["durable_state"].get("database_relative")})

    return {
        "ok": not rows,
        "mismatch_count": len(rows),
        "mismatches": rows,
        "frozen_public_routes": frozen["public_routes"],
        "category_vocabulary": frozen["category_vocabulary"],
        "decision_reason_vocabulary": frozen["decision_reason_vocabulary"],
        "requirement_vocabulary": frozen["requirement_vocabulary"],
        "verification_reason_vocabulary": frozen["verification_reason_vocabulary"],
        "claim_boundary": "frozen Phase-1 capability/inventory behavior and durable security state",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate frozen Python/Rust capability-inventory parity")
    parser.add_argument("--repo", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--rust-bin", default="target/debug/syntavra")
    parser.add_argument("--output")
    args = parser.parse_args()
    repo = Path(args.repo).resolve(strict=True)
    rust_bin = Path(args.rust_bin)
    if not rust_bin.is_absolute():
        rust_bin = (repo / rust_bin).resolve(strict=True)
    fixture = json.loads((repo / FIXTURE_RELATIVE).read_text(encoding="utf-8"))

    try:
        with tempfile.TemporaryDirectory(prefix="syntavra-capability-diff-") as directory:
            root = Path(directory)
            python_result = exercise("python", repo=repo, rust_bin=rust_bin, root=root)
            rust_result = exercise("rust", repo=repo, rust_bin=rust_bin, root=root)
            differential = compare(python_result, rust_result, fixture)
            result: dict[str, Any] = {
                "ok": differential["ok"],
                "schema_version": 2,
                "family": "capability-inventory",
                "python": python_result,
                "rust": rust_result,
                "differential": differential,
            }
    except Exception as exc:
        result = {
            "ok": False,
            "schema_version": 2,
            "family": "capability-inventory",
            "error": f"{type(exc).__name__}: {exc}",
            "traceback": traceback.format_exc(),
        }

    rendered = json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False)
    if args.output:
        Path(args.output).write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if result.get("ok") is True else 2


if __name__ == "__main__":
    raise SystemExit(main())
