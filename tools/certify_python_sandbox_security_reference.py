#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import traceback
from dataclasses import asdict
from pathlib import Path
from typing import Any

from syntavra_runtime.evidence import EvidenceStore
from syntavra_runtime.execution_sandbox import SandboxBackend, SandboxPolicy as PlatformSandboxPolicy
from syntavra_runtime.sandbox import SandboxError, SandboxManager, SandboxPolicy as DirectSandboxPolicy
from syntavra_runtime.sandbox_runtime import HardenedSandboxBroker
from syntavra_runtime.util import stable_project_id
from tools import report_missing_native_public_routes as public_surface
from tools import report_python_public_execution_contract as execution_contract


FIXTURE_RELATIVE = Path("contracts/python/sandbox-security-reference-v1.json")
SECRET_NAME = "TEST_API_KEY"
SECRET_VALUE = "sandbox-secret-must-not-leak"


def _head(repo: Path) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def _run(
    repo: Path,
    project: Path,
    state: Path,
    args: list[str],
    *,
    extra_env: dict[str, str] | None = None,
    timeout: float = 30.0,
) -> dict[str, Any]:
    env = os.environ.copy()
    env.update({"PYTHONPATH": str(repo), "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"})
    env.pop("SYNTAVRA_BULK_PARITY_PROBE", None)
    env.update(extra_env or {})
    result = subprocess.run(
        [sys.executable, "-m", "syntavra_runtime.engine_entry", "--engine", "python",
         "--project", str(project), "--state-root", str(state), *args],
        cwd=repo, env=env, text=True, encoding="utf-8", errors="replace",
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout, check=False,
    )
    try:
        value = json.loads(result.stdout) if result.stdout.strip() else None
    except json.JSONDecodeError:
        value = None
    return {"exit": result.returncode, "stdout": result.stdout, "stderr": result.stderr, "value": value}


def _json_result(label: str, result: dict[str, Any], *, exit_code: int) -> dict[str, Any]:
    if result["exit"] != exit_code or result["stderr"]:
        raise AssertionError(f"{label}: expected clean exit {exit_code}, got {result}")
    value = result.get("value")
    if not isinstance(value, dict):
        raise AssertionError(f"{label}: expected JSON object stdout, got {result}")
    return value


def _public_failure(label: str, result: dict[str, Any], *, contains: str) -> dict[str, Any]:
    value = _json_result(label, result, exit_code=4)
    if value.get("ok") is not False:
        raise AssertionError(f"{label}: application error envelope drift: {value}")
    error = value.get("error")
    if not isinstance(error, dict) or error.get("code") != "PYTHON_PUBLIC_COMMAND_FAILED":
        raise AssertionError(f"{label}: application error code drift: {value}")
    details = error.get("details")
    rendered = str((details or {}).get("error") or "")
    if contains not in rendered:
        raise AssertionError(f"{label}: expected {contains!r} in {rendered!r}")
    return {
        "exit": 4,
        "code": error["code"],
        "error_type": rendered.split(":", 1)[0],
        "fallback": (details or {}).get("fallback"),
        "message_contains": contains,
    }


def _routes(fixture: dict[str, Any]) -> dict[str, Any]:
    all_routes = public_surface.python_public_route_sources()
    routes = sorted(route for route in all_routes if route.startswith("sandbox ") or route in {"run sandbox-run", "run sandbox-status"})
    if routes != fixture["public_routes"]:
        raise AssertionError(f"sandbox public route inventory drift: {routes}")
    execution = {row["route"]: row for row in execution_contract.route_execution_manifest()}
    owners: dict[str, str] = {}
    for route in routes:
        row = execution[route]
        if len(row["entrypoints"]) != 1 or row["unknown_sources"]:
            raise AssertionError(f"sandbox route ownership drift: {row}")
        owners[route] = row["entrypoint"]
    return {
        "routes": routes,
        "route_count": len(routes),
        "route_sha256": public_surface._digest(routes),
        "ownership": owners,
    }


def _direct_contract(repo: Path, project: Path, state: Path, fixture: dict[str, Any]) -> dict[str, Any]:
    backends = _json_result("sandbox backends", _run(repo, project, state, ["sandbox", "backends"]), exit_code=0)
    if sorted(backends) != ["bwrap", "docker", "local-restricted", "podman"] or not backends.get("local-restricted"):
        raise AssertionError(f"direct sandbox backend inventory drift: {backends}")

    probe = (
        "import json,os;"
        "print(json.dumps({'sandbox':os.getenv('SYNTAVRA_SANDBOX'),'workspace':bool(os.getenv('SYNTAVRA_WORKSPACE')),'secret':os.getenv('TEST_API_KEY')},sort_keys=True))"
    )
    base = ["--backend", "local-restricted", "--network", "inherit", "--allow-degraded", "--"]
    plan = _json_result(
        "sandbox plan",
        _run(repo, project, state, ["sandbox", "plan", *base, sys.executable, "-c", probe], extra_env={SECRET_NAME: SECRET_VALUE}),
        exit_code=0,
    )
    if plan.get("backend") != fixture["direct"]["degraded_backend"]:
        raise AssertionError(f"direct sandbox backend drift: {plan}")
    if list(plan.get("degraded_reasons") or []) != fixture["direct"]["degraded_reasons"]:
        raise AssertionError(f"direct degraded reasons drift: {plan}")
    guarantees = plan.get("guarantees")
    if not isinstance(guarantees, dict) or sorted(guarantees) != fixture["direct"]["guarantee_keys"]:
        raise AssertionError(f"direct guarantee schema drift: {plan}")
    if guarantees.get("secret_filtered") is not True or guarantees.get("process_tree_controlled") is not True:
        raise AssertionError(f"direct portable guarantees drift: {plan}")
    if guarantees.get("network_isolated") is not False or guarantees.get("filesystem_isolated") is not False:
        raise AssertionError(f"direct degraded backend overstated isolation: {plan}")

    executed = _json_result(
        "sandbox execute",
        _run(repo, project, state, ["sandbox", "execute", *base, sys.executable, "-c", probe], extra_env={SECRET_NAME: SECRET_VALUE}),
        exit_code=0,
    )
    expected_result_keys = [
        "backend", "degraded_reasons", "duration_seconds", "evidence_handle", "exit_code",
        "guarantees", "sandbox_id", "stderr_bytes", "stdout_bytes", "summary", "timed_out",
    ]
    if sorted(executed) != expected_result_keys:
        raise AssertionError(f"direct SandboxResult schema drift: {sorted(executed)}")
    if executed.get("exit_code") != 0 or executed.get("timed_out") is not False:
        raise AssertionError(f"direct allowed execution drift: {executed}")
    handle = str(executed.get("evidence_handle") or "")
    if not handle.startswith("sc://sha256/"):
        raise AssertionError(f"direct evidence handle drift: {executed}")
    evidence = EvidenceStore(state / "evidence", project_id=stable_project_id(project))
    raw = evidence.get(handle).decode("utf-8", errors="replace")
    child = json.loads(raw.splitlines()[0])
    if child != {"sandbox": "1", "secret": None, "workspace": True}:
        raise AssertionError(f"direct environment filtering drift: {child}")

    child_failure = _json_result(
        "direct child exit passthrough",
        _run(repo, project, state, ["sandbox", "execute", *base, sys.executable, "-c", "raise SystemExit(7)"]),
        exit_code=7,
    )
    if child_failure.get("exit_code") != 7 or child_failure.get("timed_out") is not False:
        raise AssertionError(f"direct child exit passthrough drift: {child_failure}")

    strict_denial = _public_failure(
        "strict local network denial",
        _run(repo, project, state, [
            "sandbox", "plan", "--backend", "local-restricted", "--network", "none", "--",
            sys.executable, "-c", "print('must-not-run')",
        ]),
        contains="strict network-disabled execution requires docker, podman, or bwrap",
    )

    manager = SandboxManager(
        state / "component-sandbox",
        project=project,
        evidence=EvidenceStore(state / "component-evidence", project_id=stable_project_id(project)),
    )
    allowed_dir = project / "allowed"
    allowed_dir.mkdir()
    policy = DirectSandboxPolicy(
        backend="local-restricted",
        network="inherit",
        strict=False,
        writable_paths=("allowed",),
    )
    write = manager.write("allowed/file.txt", b"sandbox-write-fixture", policy=policy)
    if write["bytes"] != len(b"sandbox-write-fixture") or not (allowed_dir / "file.txt").is_file():
        raise AssertionError(f"direct allowed write drift: {write}")
    try:
        manager.write("blocked.txt", b"blocked", policy=policy)
    except SandboxError as exc:
        blocked_reason = str(exc)
    else:
        raise AssertionError("direct sandbox allowed a write outside writable_paths")
    try:
        manager.read("../outside.txt")
    except SandboxError as exc:
        escape_reason = str(exc)
    else:
        raise AssertionError("direct sandbox allowed a project-escape read")
    if "path is not writable by policy" not in blocked_reason or "path escapes project" not in escape_reason:
        raise AssertionError(f"direct filesystem denial vocabulary drift: {blocked_reason!r}, {escape_reason!r}")

    return {
        "backends": {"keys": sorted(backends), "local_restricted_available": bool(backends["local-restricted"])},
        "plan": {
            "keys": sorted(plan),
            "backend": plan["backend"],
            "degraded_reasons": list(plan["degraded_reasons"]),
            "guarantees": guarantees,
        },
        "execute": {
            "keys": sorted(executed),
            "exit": 0,
            "exit_code": executed["exit_code"],
            "timed_out": executed["timed_out"],
            "evidence_shape": True,
            "environment": child,
        },
        "child_exit_passthrough": {"public_exit": 7, "receipt_exit_code": child_failure["exit_code"]},
        "strict_denial": strict_denial,
        "filesystem": {
            "allowed_write": {"bytes": write["bytes"], "sha256": write["sha256"]},
            "blocked_write_reason": "path is not writable by policy",
            "escape_read_reason": "path escapes project",
        },
    }


class _ForcedUnavailableBroker(HardenedSandboxBroker):
    def backend(self, policy: PlatformSandboxPolicy) -> SandboxBackend:
        del policy
        return SandboxBackend(
            name="forced-unavailable-reference",
            platform="fixture",
            available=False,
            enforced=("cwd-boundary", "environment-filter", "timeout", "process-group"),
            unsupported=("filesystem-boundary", "network-namespace"),
            detail="deterministic strict-native fail-closed fixture",
        )


def _platform_contract(repo: Path, project: Path, state: Path, fixture: dict[str, Any]) -> dict[str, Any]:
    status = _json_result("sandbox status", _run(repo, project, state, ["run", "sandbox-status"]), exit_code=0)
    backend = status.get("backend")
    if status.get("ok") is not True or status.get("fail_closed") is not True or not isinstance(backend, dict):
        raise AssertionError(f"platform sandbox status drift: {status}")
    if sorted(backend) != ["available", "command_prefix", "detail", "enforced", "name", "platform", "unsupported"]:
        raise AssertionError(f"platform backend schema drift: {backend}")
    strict_ready = bool(backend.get("available")) and not bool(backend.get("unsupported"))
    if status.get("strict_ready") is not strict_ready:
        raise AssertionError(f"strict_ready no longer reflects backend claims: {status}")

    probe = (
        "import json,os;"
        "print(json.dumps({'sandbox':os.getenv('SYNTAVRA_SANDBOX'),'workspace':bool(os.getenv('SYNTAVRA_WORKSPACE')),'secret':os.getenv('TEST_API_KEY')},sort_keys=True))"
    )
    command = json.dumps([sys.executable, "-c", probe], separators=(",", ":"))
    run_result = _run(
        repo, project, state, ["run", "sandbox-run", command, "--timeout", "5"],
        extra_env={SECRET_NAME: SECRET_VALUE},
    )
    receipt = _json_result("platform sandbox run", run_result, exit_code=0)
    if receipt.get("ok") is not True:
        raise AssertionError(f"platform sandbox allowed execution drift: {receipt}")
    receipt_without_ok = {key: value for key, value in receipt.items() if key != "ok"}
    if sorted(receipt_without_ok) != fixture["platform"]["receipt_keys"]:
        raise AssertionError(f"ExecutionReceipt schema drift: {sorted(receipt_without_ok)}")
    if sorted((receipt.get("policy") or {})) != fixture["platform"]["policy_keys"]:
        raise AssertionError(f"platform policy schema drift: {receipt.get('policy')}")
    child = json.loads(str(receipt.get("stdout") or "").strip())
    if child != {"sandbox": "1", "secret": None, "workspace": True}:
        raise AssertionError(f"platform environment filtering drift: {child}")
    environment_keys = set(receipt.get("environment_keys") or [])
    if SECRET_NAME in environment_keys or not set(fixture["platform"]["environment_markers"]).issubset(environment_keys):
        raise AssertionError(f"platform environment key contract drift: {sorted(environment_keys)}")
    if not str(receipt.get("receipt_id") or "").startswith("sha256:"):
        raise AssertionError(f"platform receipt ID shape drift: {receipt}")
    persisted = list((state / "unified" / "execution-receipts").glob("*.json"))
    if len(persisted) != 1:
        raise AssertionError(f"platform durable receipt count drift: {persisted}")
    stored = json.loads(persisted[0].read_text(encoding="utf-8"))
    if stored != receipt_without_ok:
        raise AssertionError("persisted platform receipt no longer equals public receipt payload")

    failed_command = json.dumps([sys.executable, "-c", "raise SystemExit(7)"], separators=(",", ":"))
    failed = _json_result(
        "platform child failure",
        _run(repo, project, state, ["run", "sandbox-run", failed_command, "--timeout", "5"]),
        exit_code=3,
    )
    if failed.get("ok") is not False or failed.get("exit_code") != 7 or failed.get("timed_out") is not False:
        raise AssertionError(f"platform receipt failure exit semantics drift: {failed}")

    cwd_denial = _public_failure(
        "platform cwd escape",
        _run(repo, project, state, ["run", "sandbox-run", command, "--cwd", "../outside", "--timeout", "5"]),
        contains="working directory escapes workspace",
    )
    writable_denial = _public_failure(
        "platform writable escape",
        _run(repo, project, state, ["run", "sandbox-run", command, "--writable-path", "../outside", "--timeout", "5"]),
        contains="writable path escapes workspace",
    )
    malformed = _public_failure(
        "platform malformed argv",
        _run(repo, project, state, ["run", "sandbox-run", "[]"]),
        contains="sandbox command must be a non-empty JSON argv list",
    )

    forced = _ForcedUnavailableBroker(state / "forced-broker")
    try:
        forced.run(
            [sys.executable, "-c", "print('must-not-run')"],
            policy=PlatformSandboxPolicy(workspace=project, strict_native=True),
        )
    except RuntimeError as exc:
        strict_native_reason = str(exc)
    else:
        raise AssertionError("strict_native did not fail closed with an unavailable backend")
    if "required native sandbox controls unavailable" not in strict_native_reason:
        raise AssertionError(f"strict_native denial vocabulary drift: {strict_native_reason}")

    try:
        forced.run(
            [sys.executable, "-c", "print('must-not-run')"],
            policy=PlatformSandboxPolicy(workspace=project),
            environment={"FIXTURE_API_KEY": "secret"},
        )
    except PermissionError as exc:
        secret_env_reason = str(exc)
    else:
        raise AssertionError("platform sandbox accepted an explicit secret-like environment key")
    if "secret-like environment key is not agent-visible" not in secret_env_reason:
        raise AssertionError(f"secret env denial vocabulary drift: {secret_env_reason}")

    return {
        "status": {
            "keys": sorted(status),
            "backend_keys": sorted(backend),
            "backend_name": backend["name"],
            "backend_available": backend["available"],
            "enforced": sorted(backend.get("enforced") or []),
            "unsupported": sorted(backend.get("unsupported") or []),
            "strict_ready": status["strict_ready"],
            "fail_closed": status["fail_closed"],
        },
        "allowed_run": {
            "receipt_keys": sorted(receipt_without_ok),
            "policy_keys": sorted(receipt["policy"]),
            "public_exit": 0,
            "ok": receipt["ok"],
            "receipt_id_shape": True,
            "environment": child,
            "secret_key_absent": True,
            "durable_receipt_count": len(persisted),
            "durable_receipt_exact": True,
        },
        "child_failure": {
            "public_exit": 3,
            "ok": failed["ok"],
            "receipt_exit_code": failed["exit_code"],
            "timed_out": failed["timed_out"],
        },
        "denials": {
            "cwd_escape": cwd_denial,
            "writable_escape": writable_denial,
            "malformed_argv": malformed,
            "strict_native_reason": "required native sandbox controls unavailable",
            "secret_environment_reason": "secret-like environment key is not agent-visible",
        },
    }


def certify(repo: Path) -> dict[str, Any]:
    fixture_path = repo / FIXTURE_RELATIVE
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory(prefix="syntavra-python-sandbox-security-") as directory:
        root = Path(directory)
        project = root / "project"
        state = root / "state"
        project.mkdir()
        state.mkdir()
        (project / ".git").mkdir()
        routes = _routes(fixture)
        direct = _direct_contract(repo, project, state, fixture)
        platform = _platform_contract(repo, project, state, fixture)
    return {
        "ok": True,
        "schema_version": 1,
        "family": "sandbox-security",
        "engine": "python",
        "exact_head": _head(repo),
        "fixture": str(FIXTURE_RELATIVE).replace("\\", "/"),
        "fixture_sha256": hashlib.sha256(fixture_path.read_bytes()).hexdigest(),
        "routes": routes,
        "direct": direct,
        "platform": platform,
        "command_policy": fixture["command_policy"],
        "exit_policy": fixture["exit_policy"],
        "host_dependent_fields": [
            "sandbox backend executable paths",
            "native backend name and availability",
            "native backend enforced/unsupported capability sets",
            "strict_ready derived from the probed host backend",
        ],
        "nondeterministic_fields": [
            "sandbox IDs",
            "execution receipt IDs",
            "started_at timestamps",
            "execution durations",
            "temporary project/state paths",
        ],
        "network_boundary": "no live external network required; executed fixture commands are local child processes",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Certify the Python sandbox/security reference contract")
    parser.add_argument("--repo", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--output")
    args = parser.parse_args()
    repo = Path(args.repo).resolve(strict=True)
    try:
        result = certify(repo)
    except Exception as exc:
        result = {
            "ok": False,
            "schema_version": 1,
            "family": "sandbox-security",
            "engine": "python",
            "exact_head": _head(repo),
            "error": f"{type(exc).__name__}: {exc}",
            "traceback": traceback.format_exc(),
        }
    rendered = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True, default=str)
    if args.output:
        Path(args.output).write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
