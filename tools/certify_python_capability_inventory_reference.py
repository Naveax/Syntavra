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
from pathlib import Path
from typing import Any

from tools import report_missing_native_public_routes as public_surface
from tools import report_python_public_execution_contract as execution_contract


FIXTURE_RELATIVE = Path("contracts/python/capability-inventory-reference-v1.json")


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _head(repo: Path) -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    return completed.stdout.strip() if completed.returncode == 0 else ""


def _run(repo: Path, project: Path, state: Path, args: list[str]) -> dict[str, Any]:
    env = os.environ.copy()
    env.update({"PYTHONPATH": str(repo), "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"})
    env.pop("SYNTAVRA_BULK_PARITY_PROBE", None)
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "syntavra_runtime.engine_entry",
            "--engine",
            "python",
            "--project",
            str(project),
            "--state-root",
            str(state),
            *args,
        ],
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
        value = json.loads(completed.stdout) if completed.stdout.strip() else None
    except json.JSONDecodeError:
        value = None
    return {"exit": completed.returncode, "stdout": completed.stdout, "stderr": completed.stderr, "value": value}


def _ok(label: str, result: dict[str, Any]) -> dict[str, Any]:
    if result["exit"] != 0 or result["stderr"]:
        raise AssertionError(f"{label}: expected clean exit 0, got {result}")
    value = result.get("value")
    if not isinstance(value, dict):
        raise AssertionError(f"{label}: expected JSON object stdout, got {result}")
    return value


def _argparse_error(label: str, result: dict[str, Any]) -> dict[str, Any]:
    if result["exit"] != 2 or result["stdout"] or "usage:" not in result["stderr"].casefold():
        raise AssertionError(f"{label}: expected argparse usage error, got {result}")
    return {"exit": 2, "stdout_format": "empty", "stderr_format": "argparse-usage-error"}


def _decision(
    repo: Path,
    project: Path,
    state: Path,
    tool: str,
    arguments: dict[str, Any],
    *,
    resource: str = "workspace:/",
    sandboxed: bool = False,
    user_authorized: bool = False,
    network_hosts: tuple[str, ...] = (),
) -> dict[str, Any]:
    args = ["run", "capability-decide", tool, json.dumps(arguments, separators=(",", ":")), "--resource", resource]
    if sandboxed:
        args.append("--sandboxed")
    if user_authorized:
        args.append("--user-authorized")
    for host in network_hosts:
        args.extend(["--network-host", host])
    return _ok(f"capability-decide {tool}", _run(repo, project, state, args))


def _expected_decision(
    value: dict[str, Any],
    *,
    allowed: bool,
    category: str,
    reason: str,
    requirements: list[str],
) -> None:
    expected_keys = {"allowed", "arguments_hash", "category", "reason", "requirements", "resource", "tool"}
    if set(value) != expected_keys:
        raise AssertionError(f"capability decision schema drift: {sorted(value)}")
    if value.get("allowed") is not allowed or value.get("category") != category or value.get("reason") != reason:
        raise AssertionError(f"capability decision drift: {value}")
    if value.get("requirements") != requirements:
        raise AssertionError(f"capability requirements drift: {value}")
    digest = str(value.get("arguments_hash") or "")
    if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
        raise AssertionError(f"arguments_hash shape drift: {digest!r}")


def _inventory_contract(repo: Path, fixture: dict[str, Any]) -> dict[str, Any]:
    surface_contract_path = repo / str(fixture["canonical_surface_contract"])
    surface_contract = json.loads(surface_contract_path.read_text(encoding="utf-8"))
    expected_surface = surface_contract["python_surface"]

    route_sources = public_surface.python_public_route_sources()
    manifest = public_surface.python_public_manifest()
    execution = execution_contract.route_execution_manifest()

    if manifest["route_count"] != expected_surface["route_count"] or manifest["route_count"] != 245:
        raise AssertionError(f"canonical Python public route count drift: {manifest['route_count']}")
    if manifest["digest_sha256"] != expected_surface["command_paths_sha256"]:
        raise AssertionError("canonical Python public route digest drift")
    if manifest["duplicate_routes"]:
        raise AssertionError(f"duplicate canonical public routes: {manifest['duplicate_routes']}")
    if manifest["namespace_dest_collisions"]:
        raise AssertionError(f"argparse namespace collisions returned: {manifest['namespace_dest_collisions']}")
    if manifest["paths"] != list(route_sources):
        raise AssertionError("manifest path order no longer follows the parser-derived route authority")

    manifest_records = [
        {"route": route, "sources": list(route_sources[route])}
        for route in manifest["paths"]
    ]
    manifest_record_keys = sorted(manifest_records[0]) if manifest_records else []
    if manifest_record_keys != fixture["inventory"]["manifest_record_keys"]:
        raise AssertionError(f"manifest metadata schema drift: {manifest_record_keys}")

    if len(execution) != 245:
        raise AssertionError(f"execution ownership row count drift: {len(execution)}")
    execution_record_keys = sorted(execution[0]) if execution else []
    if execution_record_keys != fixture["inventory"]["execution_record_keys"]:
        raise AssertionError(f"execution ownership schema drift: {execution_record_keys}")
    if [row["route"] for row in execution] != manifest["paths"]:
        raise AssertionError("execution ownership rows no longer align with the canonical parser route order")
    if any(row["unknown_sources"] for row in execution):
        raise AssertionError("execution ownership contains unknown parser source labels")
    if any(row["success_exit"] != 0 for row in execution):
        raise AssertionError("success exit policy drifted away from 0")
    for row in execution:
        expected_parser_error = 2 if row["parser_owned"] else None
        if row["parser_error_exit"] != expected_parser_error:
            raise AssertionError(f"parser error exit drift for {row['route']}: {row}")
        if len(row["entrypoints"]) != 1 or row["entrypoint"] != row["entrypoints"][0]:
            raise AssertionError(f"route no longer has exactly one Python entrypoint: {row}")

    source_vocabulary = sorted({source for row in execution for source in row["sources"]})
    if source_vocabulary != fixture["inventory"]["source_vocabulary"]:
        raise AssertionError(f"public source vocabulary drift: {source_vocabulary}")
    entrypoint_vocabulary = sorted({str(row["entrypoint"]) for row in execution})
    if entrypoint_vocabulary != fixture["inventory"]["entrypoint_vocabulary"]:
        raise AssertionError(f"public entrypoint vocabulary drift: {entrypoint_vocabulary}")

    capability_routes = sorted(route for route in manifest["paths"] if "capability-" in route)
    if capability_routes != fixture["capability"]["public_routes"]:
        raise AssertionError(f"capability public route inventory drift: {capability_routes}")
    capability_owners = {
        row["route"]: row["entrypoint"]
        for row in execution
        if row["route"] in capability_routes
    }
    if set(capability_owners.values()) != {"syntavra_runtime.prerelease_cli.main"}:
        raise AssertionError(f"capability route ownership drift: {capability_owners}")

    ownership_projection = [
        {
            "route": row["route"],
            "sources": row["sources"],
            "entrypoint": row["entrypoint"],
            "parser_owned": row["parser_owned"],
            "parser_error_exit": row["parser_error_exit"],
            "success_exit": row["success_exit"],
        }
        for row in execution
    ]
    return {
        "route_count": 245,
        "command_paths_sha256": manifest["digest_sha256"],
        "manifest_record_keys": manifest_record_keys,
        "execution_record_keys": execution_record_keys,
        "source_vocabulary": source_vocabulary,
        "entrypoint_vocabulary": entrypoint_vocabulary,
        "ownership_sha256": hashlib.sha256(_canonical(ownership_projection)).hexdigest(),
        "capability_routes": capability_routes,
        "capability_owners": capability_owners,
        "duplicate_routes": 0,
        "namespace_dest_collisions": 0,
        "unknown_source_rows": 0,
        "unowned_routes": 0,
    }


def _capability_contract(repo: Path, fixture: dict[str, Any], project: Path, state: Path) -> dict[str, Any]:
    base_requirements = ["signed-capability", "exact-evidence"]
    elevated_requirements = ["signed-capability", "exact-evidence", "explicit-user-authorization", "sandbox"]

    read = _decision(repo, project, state, "repo.read", {"path": "module.py"}, resource="workspace:/module.py")
    _expected_decision(read, allowed=True, category="read", reason="policy-allowed", requirements=base_requirements)

    write_auth = _decision(repo, project, state, "repo.patch", {"path": "module.py"}, resource="workspace:/module.py")
    _expected_decision(write_auth, allowed=False, category="write", reason="authorization-required", requirements=elevated_requirements)

    write_sandbox = _decision(
        repo,
        project,
        state,
        "repo.patch",
        {"path": "module.py"},
        resource="workspace:/module.py",
        user_authorized=True,
    )
    _expected_decision(write_sandbox, allowed=False, category="write", reason="sandbox-required", requirements=elevated_requirements)

    execute = _decision(
        repo,
        project,
        state,
        "test.run",
        {"command": ["python", "-m", "unittest"]},
        user_authorized=True,
        sandboxed=True,
    )
    _expected_decision(execute, allowed=True, category="execute", reason="policy-allowed", requirements=elevated_requirements)

    destructive = _decision(
        repo,
        project,
        state,
        "shell_run",
        {"command": ["rm", "-rf", "/"]},
        user_authorized=True,
        sandboxed=True,
    )
    _expected_decision(destructive, allowed=False, category="execute", reason="destructive-command-denied", requirements=elevated_requirements)

    outside = _decision(repo, project, state, "repo.read", {"path": "../secret"}, resource="host:/etc/passwd")
    _expected_decision(outside, allowed=False, category="read", reason="resource-outside-workspace", requirements=base_requirements)

    network = _decision(
        repo,
        project,
        state,
        "http_request",
        {"url": "https://example.invalid"},
        network_hosts=("api.example.invalid",),
    )
    _expected_decision(network, allowed=False, category="network", reason="network-host-not-allowlisted", requirements=base_requirements)

    unknown = _decision(repo, project, state, "totally.unknown.tool", {"x": 1})
    _expected_decision(unknown, allowed=False, category="unknown", reason="unknown-tool-fail-closed", requirements=base_requirements)

    observed_categories = sorted({row["category"] for row in (read, write_auth, write_sandbox, execute, destructive, outside, network, unknown)})
    observed_reasons = sorted({row["reason"] for row in (read, write_auth, write_sandbox, execute, destructive, outside, network, unknown)})
    observed_requirements = sorted({item for row in (read, write_auth, write_sandbox, execute, destructive, outside, network, unknown) for item in row["requirements"]})
    if observed_categories != fixture["capability"]["category_vocabulary"]:
        raise AssertionError(f"capability category vocabulary drift: {observed_categories}")
    if observed_reasons != fixture["capability"]["decision_reason_vocabulary"]:
        raise AssertionError(f"capability decision reason vocabulary drift: {observed_reasons}")
    if observed_requirements != fixture["capability"]["requirement_vocabulary"]:
        raise AssertionError(f"capability requirement vocabulary drift: {observed_requirements}")

    arguments = {"path": "module.py", "patch": "fixture"}
    issue_args = [
        "run", "capability-issue", "session-capability", "repo.patch", json.dumps(arguments, separators=(",", ":")),
        "--resource", "workspace:/module.py", "--permission", "write", "--ttl", "300",
    ]
    issued = _ok("capability issue", _run(repo, project, state, issue_args))
    if set(issued) != {"expires_at", "single_use", "token", "token_hash"} or issued.get("single_use") is not True:
        raise AssertionError(f"capability issue schema drift: {issued}")
    token = str(issued["token"])
    if token.count(".") != 1 or len(str(issued["token_hash"])) != 64:
        raise AssertionError(f"capability token shape drift: {issued}")

    second_issue = _ok("second capability issue", _run(repo, project, state, issue_args))
    second_token = str(second_issue["token"])
    mismatch = _ok(
        "capability binding mismatch",
        _run(
            repo,
            project,
            state,
            ["run", "capability-verify", second_token, "repo.patch", '{"path":"different.py"}', "--resource", "workspace:/module.py", "--no-consume"],
        ),
    )
    if mismatch != {"ok": False, "reason": "binding-mismatch"}:
        raise AssertionError(f"capability binding mismatch drift: {mismatch}")

    verified = _ok(
        "capability verify",
        _run(repo, project, state, ["run", "capability-verify", token, "repo.patch", json.dumps(arguments, separators=(",", ":")), "--resource", "workspace:/module.py"]),
    )
    if verified.get("ok") is not True or verified.get("reason") != "verified" or not isinstance(verified.get("claims"), dict):
        raise AssertionError(f"capability verification schema drift: {verified}")
    replay = _ok(
        "capability replay",
        _run(repo, project, state, ["run", "capability-verify", token, "repo.patch", json.dumps(arguments, separators=(",", ":")), "--resource", "workspace:/module.py"]),
    )
    if replay != {"ok": False, "reason": "already-consumed"}:
        raise AssertionError(f"single-use capability replay drift: {replay}")

    malformed = _ok(
        "malformed capability token",
        _run(repo, project, state, ["run", "capability-verify", "not-a-token", "repo.patch", json.dumps(arguments, separators=(",", ":")), "--resource", "workspace:/module.py"]),
    )
    if malformed != {"ok": False, "reason": "malformed-token"}:
        raise AssertionError(f"malformed capability token drift: {malformed}")
    body, signature = second_token.split(".", 1)
    replacement = "0" if signature[-1] != "0" else "1"
    invalid_token = f"{body}.{signature[:-1]}{replacement}"
    invalid_signature = _ok(
        "invalid capability signature",
        _run(repo, project, state, ["run", "capability-verify", invalid_token, "repo.patch", json.dumps(arguments, separators=(",", ":")), "--resource", "workspace:/module.py", "--no-consume"]),
    )
    if invalid_signature != {"ok": False, "reason": "invalid-signature"}:
        raise AssertionError(f"invalid capability signature drift: {invalid_signature}")

    observed_verify_reasons = sorted({str(item["reason"]) for item in (mismatch, verified, replay, malformed, invalid_signature)})
    if observed_verify_reasons != fixture["capability"]["verification_reason_vocabulary"]:
        raise AssertionError(f"capability verification reason vocabulary drift: {observed_verify_reasons}")

    parser_error = _argparse_error(
        "missing capability decide arguments",
        _run(repo, project, state, ["run", "capability-decide", "repo.read"]),
    )

    secret_files = list(state.rglob("secret.key"))
    consumed_files = list(state.rglob("consumed.jsonl"))
    if len(secret_files) != 1 or len(consumed_files) != 1:
        raise AssertionError(f"capability durable side-effect paths drift: secrets={secret_files} consumed={consumed_files}")
    if len(secret_files[0].read_bytes()) != 32:
        raise AssertionError("capability signing key is no longer 32 bytes")
    consumed_rows = [json.loads(line) for line in consumed_files[0].read_text(encoding="utf-8").splitlines() if line.strip()]
    if len(consumed_rows) != 1 or consumed_rows[0].get("token_hash") != issued["token_hash"]:
        raise AssertionError(f"single-use consumption journal drift: {consumed_rows}")

    return {
        "decisions": {
            "read": read,
            "write_authorization_required": write_auth,
            "write_sandbox_required": write_sandbox,
            "execute_allowed": execute,
            "destructive_denied": destructive,
            "outside_workspace_denied": outside,
            "network_denied": network,
            "unknown_tool_denied": unknown,
        },
        "category_vocabulary": observed_categories,
        "decision_reason_vocabulary": observed_reasons,
        "requirement_vocabulary": observed_requirements,
        "issue": {
            "keys": sorted(issued),
            "single_use": True,
            "token_shape": "base64url-json.hmac-sha256",
            "token_hash_shape": "sha256-hex",
        },
        "verify": {
            "first": {"ok": verified["ok"], "reason": verified["reason"], "claim_keys": sorted(verified["claims"])},
            "replay": replay,
            "binding_mismatch": mismatch,
            "malformed": malformed,
            "invalid_signature": invalid_signature,
            "reason_vocabulary": observed_verify_reasons,
        },
        "parser_error": parser_error,
        "durable_side_effects": {
            "signing_key_bytes": 32,
            "consumed_rows": 1,
            "consumed_token_hash_matches_issue": True,
        },
    }


def certify(repo: Path) -> dict[str, Any]:
    fixture = json.loads((repo / FIXTURE_RELATIVE).read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory(prefix="syntavra-python-capability-inventory-") as directory:
        root = Path(directory)
        project = root / "project"
        state = root / "state"
        project.mkdir()
        (project / ".git").mkdir()
        inventory = _inventory_contract(repo, fixture)
        capability = _capability_contract(repo, fixture, project, state)

    return {
        "ok": True,
        "schema_version": 1,
        "family": "capability-inventory",
        "engine": "python",
        "exact_head": _head(repo),
        "fixture": str(FIXTURE_RELATIVE).replace("\\", "/"),
        "fixture_sha256": hashlib.sha256((repo / FIXTURE_RELATIVE).read_bytes()).hexdigest(),
        "exit_policy": {"success": 0, "argument_parser_error": 2},
        "inventory": inventory,
        "capability": capability,
        "nondeterministic_fields": [
            "capability token nonce",
            "capability token issued_at/expires_at",
            "capability signing key bytes",
            "token and token_hash derived from nonce/time/key",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Certify Python capability decisions and canonical public inventory")
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
            "family": "capability-inventory",
            "engine": "python",
            "exact_head": _head(repo),
            "error": f"{type(exc).__name__}: {exc}",
            "traceback": traceback.format_exc(),
        }
    rendered = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True)
    if args.output:
        Path(args.output).write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
