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
from typing import Any

from syntavra_runtime.platform_common import canonical_json
from tools import report_missing_native_public_routes as public_surface
from tools import report_python_public_execution_contract as execution_contract


FIXTURE_RELATIVE = Path("contracts/python/capability-inventory-reference-v1.json")


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _head(repo: Path) -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True, check=False,
    )
    return completed.stdout.strip() if completed.returncode == 0 else ""


def _run(repo: Path, project: Path, state: Path, args: list[str]) -> dict[str, Any]:
    env = os.environ.copy()
    env.update({"PYTHONPATH": str(repo), "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"})
    env.pop("SYNTAVRA_BULK_PARITY_PROBE", None)
    completed = subprocess.run(
        [sys.executable, "-m", "syntavra_runtime.engine_entry", "--engine", "python",
         "--project", str(project), "--state-root", str(state), *args],
        cwd=repo, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace", timeout=30, check=False,
    )
    try:
        value = json.loads(completed.stdout) if completed.stdout.strip() else None
    except json.JSONDecodeError:
        value = None
    return {"exit": completed.returncode, "stdout": completed.stdout, "stderr": completed.stderr, "value": value}


def _json_result(label: str, result: dict[str, Any], *, expected_exit: int) -> dict[str, Any]:
    if result["exit"] != expected_exit or result["stderr"]:
        raise AssertionError(f"{label}: expected clean exit {expected_exit}, got {result}")
    value = result.get("value")
    if not isinstance(value, dict):
        raise AssertionError(f"{label}: expected JSON object stdout, got {result}")
    return value


def _ok(label: str, result: dict[str, Any]) -> dict[str, Any]:
    return _json_result(label, result, expected_exit=0)


def _verification_failure(label: str, result: dict[str, Any]) -> dict[str, Any]:
    return _json_result(label, result, expected_exit=3)


def _argparse_error(label: str, result: dict[str, Any]) -> dict[str, Any]:
    if result["exit"] != 2 or result["stdout"] or "usage:" not in result["stderr"].casefold():
        raise AssertionError(f"{label}: expected argparse usage error, got {result}")
    return {"exit": 2, "stdout_format": "empty", "stderr_format": "argparse-usage-error"}


def _decision(
    repo: Path, project: Path, state: Path, tool: str, arguments: dict[str, Any], *,
    resource: str = "workspace:/", sandboxed: bool = False,
    user_authorized: bool = False, network_hosts: tuple[str, ...] = (),
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
    value: dict[str, Any], *, allowed: bool, category: str, reason: str,
    requirements: list[str], resource: str,
) -> None:
    expected_keys = {"allowed", "arguments_hash", "category", "reason", "requirements", "resource"}
    if set(value) != expected_keys:
        raise AssertionError(f"capability decision schema drift: {sorted(value)}")
    if value.get("allowed") is not allowed or value.get("category") != category or value.get("reason") != reason or value.get("resource") != resource:
        raise AssertionError(f"capability decision drift: {value}")
    if value.get("requirements") != requirements:
        raise AssertionError(f"capability requirements drift: {value}")
    digest = str(value.get("arguments_hash") or "")
    if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
        raise AssertionError(f"arguments_hash shape drift: {digest!r}")


def _inventory_contract(repo: Path, fixture: dict[str, Any]) -> dict[str, Any]:
    surface_contract = json.loads((repo / str(fixture["canonical_surface_contract"])).read_text(encoding="utf-8"))
    expected_surface = surface_contract["python_surface"]
    route_sources = public_surface.python_public_route_sources()
    paths = list(route_sources)
    route_count = len(paths)
    route_digest = public_surface._digest(paths)
    duplicates = {route: sources for route, sources in route_sources.items() if len(sources) > 1}
    namespace_collisions = public_surface.python_public_namespace_collisions()
    execution = execution_contract.route_execution_manifest()

    if route_count != int(expected_surface["public_command_count"]) or route_count != 245:
        raise AssertionError(f"canonical Python public route count drift: {route_count}")
    if route_digest != str(expected_surface["command_paths_sha256"]):
        raise AssertionError("canonical Python public route digest drift")
    if duplicates:
        raise AssertionError(f"duplicate canonical public routes: {duplicates}")
    if namespace_collisions:
        raise AssertionError(f"argparse namespace collisions returned: {namespace_collisions}")

    manifest_records = [{"route": route, "sources": list(route_sources[route])} for route in paths]
    manifest_record_keys = sorted(manifest_records[0]) if manifest_records else []
    if manifest_record_keys != fixture["inventory"]["manifest_record_keys"]:
        raise AssertionError(f"manifest metadata schema drift: {manifest_record_keys}")
    if len(execution) != route_count:
        raise AssertionError(f"execution ownership row count drift: {len(execution)}")
    execution_record_keys = sorted(execution[0]) if execution else []
    if execution_record_keys != fixture["inventory"]["execution_record_keys"]:
        raise AssertionError(f"execution ownership schema drift: {execution_record_keys}")
    if [row["route"] for row in execution] != paths:
        raise AssertionError("execution ownership rows no longer align with canonical parser route order")
    if any(row["unknown_sources"] for row in execution):
        raise AssertionError("execution ownership contains unknown parser source labels")
    for row in execution:
        if row["success_exit"] != 0:
            raise AssertionError(f"success exit drift for {row['route']}: {row}")
        expected_parser_error = 2 if row["parser_owned"] else None
        if row["parser_error_exit"] != expected_parser_error:
            raise AssertionError(f"parser error exit drift for {row['route']}: {row}")
        if len(row["entrypoints"]) != 1 or row["entrypoint"] != row["entrypoints"][0]:
            raise AssertionError(f"route no longer has exactly one Python entrypoint: {row}")

    source_vocabulary = sorted({source for row in execution for source in row["sources"]})
    entrypoint_vocabulary = sorted({str(row["entrypoint"]) for row in execution})
    if source_vocabulary != fixture["inventory"]["source_vocabulary"]:
        raise AssertionError(f"public source vocabulary drift: {source_vocabulary}")
    if entrypoint_vocabulary != fixture["inventory"]["entrypoint_vocabulary"]:
        raise AssertionError(f"public entrypoint vocabulary drift: {entrypoint_vocabulary}")

    capability_routes = sorted(route for route in paths if "capability-" in route)
    if capability_routes != fixture["capability"]["public_routes"]:
        raise AssertionError(f"capability public route inventory drift: {capability_routes}")
    capability_owners = {row["route"]: row["entrypoint"] for row in execution if row["route"] in capability_routes}
    if set(capability_owners.values()) != {"syntavra_runtime.prerelease_cli.main"}:
        raise AssertionError(f"capability route ownership drift: {capability_owners}")

    ownership_projection = [
        {"route": row["route"], "sources": row["sources"], "entrypoint": row["entrypoint"],
         "parser_owned": row["parser_owned"], "parser_error_exit": row["parser_error_exit"],
         "success_exit": row["success_exit"]}
        for row in execution
    ]
    return {
        "route_count": route_count,
        "command_paths_sha256": route_digest,
        "manifest_record_keys": manifest_record_keys,
        "execution_record_keys": execution_record_keys,
        "source_vocabulary": source_vocabulary,
        "entrypoint_vocabulary": entrypoint_vocabulary,
        "ownership_sha256": hashlib.sha256(_canonical(ownership_projection)).hexdigest(),
        "capability_routes": capability_routes,
        "capability_owners": capability_owners,
        "duplicate_routes": len(duplicates),
        "namespace_dest_collisions": len(namespace_collisions),
        "unknown_source_rows": sum(bool(row["unknown_sources"]) for row in execution),
        "unowned_routes": sum(len(row["entrypoints"]) != 1 for row in execution),
    }


def _decode_token(token: str) -> dict[str, Any]:
    payload_text, _ = token.split(".", 1)
    payload = base64.urlsafe_b64decode(payload_text + "=" * (-len(payload_text) % 4))
    value = json.loads(payload)
    if not isinstance(value, dict):
        raise AssertionError("capability token payload is not an object")
    return value


def _expired_token(token: str, *, signing_key: bytes) -> str:
    body = _decode_token(token)
    body["expires_at"] = 0
    payload = base64.urlsafe_b64encode(canonical_json(body)).rstrip(b"=")
    signature = hmac.new(signing_key, payload, hashlib.sha256).digest()
    return payload.decode("ascii") + "." + base64.urlsafe_b64encode(signature).rstrip(b"=").decode("ascii")


def _invalid_signature_token(token: str) -> str:
    body, signature = token.split(".", 1)
    first = "A" if signature[0] != "A" else "B"
    return f"{body}.{first}{signature[1:]}"


def _capability_shape(capability: dict[str, Any]) -> dict[str, Any]:
    expected_keys = ["arguments_hash", "channel", "expires_at", "issued_at", "nonce", "permissions", "resource", "session_id", "single_use", "tool", "version"]
    if sorted(capability) != expected_keys:
        raise AssertionError(f"capability claim schema drift: {sorted(capability)}")
    issued_at = capability.get("issued_at")
    expires_at = capability.get("expires_at")
    return {
        "keys": expected_keys,
        "version": capability.get("version"),
        "channel": capability.get("channel"),
        "session_id": capability.get("session_id"),
        "tool": capability.get("tool"),
        "arguments_hash": capability.get("arguments_hash"),
        "resource": capability.get("resource"),
        "permissions": list(capability.get("permissions") or []),
        "single_use": capability.get("single_use"),
        "nonce_present": bool(capability.get("nonce")),
        "issued_at_is_int": isinstance(issued_at, int),
        "expires_at_is_int": isinstance(expires_at, int),
        "ttl_seconds": expires_at - issued_at if isinstance(issued_at, int) and isinstance(expires_at, int) else None,
    }


def _verify_summary(*, exit_code: int, value: dict[str, Any]) -> dict[str, Any]:
    expected_keys = {"ok", "reason"}
    capability = value.get("capability")
    if capability is not None:
        expected_keys.add("capability")
    if set(value) != expected_keys:
        raise AssertionError(f"capability verify schema drift: {sorted(value)}")
    summary: dict[str, Any] = {"exit": exit_code, "ok": value.get("ok"), "reason": value.get("reason"), "top_level_keys": sorted(value)}
    if isinstance(capability, dict):
        summary["capability"] = _capability_shape(capability)
    return summary


def _capability_contract(repo: Path, fixture: dict[str, Any], project: Path, state: Path) -> dict[str, Any]:
    base = ["signed-capability", "exact-evidence"]
    authorized = [*base, "explicit-user-authorization"]
    execute_requirements = [*authorized, "sandbox"]

    read = _decision(repo, project, state, "repo.read", {"path": "module.py"}, resource="workspace:/module.py")
    _expected_decision(read, allowed=True, category="read", reason="policy-allowed", requirements=base, resource="workspace:/module.py")
    write_auth = _decision(repo, project, state, "repo.patch", {"path": "module.py"}, resource="workspace:/module.py")
    _expected_decision(write_auth, allowed=False, category="write", reason="authorization-required", requirements=authorized, resource="workspace:/module.py")
    write_allowed = _decision(repo, project, state, "repo.patch", {"path": "module.py"}, resource="workspace:/module.py", user_authorized=True)
    _expected_decision(write_allowed, allowed=True, category="write", reason="policy-allowed", requirements=authorized, resource="workspace:/module.py")
    execute_sandbox = _decision(repo, project, state, "test.run", {"argv": ["python", "-m", "unittest"]}, user_authorized=True)
    _expected_decision(execute_sandbox, allowed=False, category="execute", reason="sandbox-required", requirements=execute_requirements, resource="workspace:/")
    execute_allowed = _decision(repo, project, state, "test.run", {"argv": ["python", "-m", "unittest"]}, user_authorized=True, sandboxed=True)
    _expected_decision(execute_allowed, allowed=True, category="execute", reason="policy-allowed", requirements=execute_requirements, resource="workspace:/")
    destructive = _decision(repo, project, state, "shell_run", {"argv": ["git", "reset", "--hard"]}, user_authorized=True, sandboxed=True)
    _expected_decision(destructive, allowed=False, category="execute", reason="destructive-command-denied", requirements=execute_requirements, resource="workspace:/")
    outside = _decision(repo, project, state, "repo.patch", {"path": "../secret"}, resource="file:/tmp/outside", user_authorized=True)
    _expected_decision(outside, allowed=False, category="write", reason="resource-outside-workspace", requirements=authorized, resource="file:/tmp/outside")
    network = _decision(repo, project, state, "http_request", {"host": "blocked.example"}, user_authorized=True, network_hosts=("allowed.example",))
    _expected_decision(network, allowed=False, category="network", reason="network-host-not-allowlisted", requirements=authorized, resource="workspace:/")
    unknown = _decision(repo, project, state, "totally.unknown.tool", {"x": 1})
    _expected_decision(unknown, allowed=False, category="unknown", reason="unknown-tool-fail-closed", requirements=base, resource="workspace:/")

    decisions = (read, write_auth, write_allowed, execute_sandbox, execute_allowed, destructive, outside, network, unknown)
    observed_categories = sorted({row["category"] for row in decisions})
    observed_reasons = sorted({row["reason"] for row in decisions})
    observed_requirements = sorted({item for row in decisions for item in row["requirements"]})
    if observed_categories != fixture["capability"]["category_vocabulary"]:
        raise AssertionError(f"capability category vocabulary drift: {observed_categories}")
    if observed_reasons != fixture["capability"]["decision_reason_vocabulary"]:
        raise AssertionError(f"capability decision reason vocabulary drift: {observed_reasons}")
    if observed_requirements != fixture["capability"]["requirement_vocabulary"]:
        raise AssertionError(f"capability requirement vocabulary drift: {observed_requirements}")

    arguments = {"path": "module.py", "patch": "fixture"}
    encoded_arguments = json.dumps(arguments, separators=(",", ":"))
    issue_args = ["run", "capability-issue", "session-capability", "repo.patch", encoded_arguments,
                  "--resource", "workspace:/module.py", "--permission", "write", "--permission", "evidence", "--ttl", "300"]
    issued = _ok("capability issue", _run(repo, project, state, issue_args))
    if set(issued) != {"ok", "token", "single_use"} or issued.get("ok") is not True or issued.get("single_use") is not True:
        raise AssertionError(f"capability issue schema drift: {issued}")
    token = str(issued["token"])
    if token.count(".") != 1:
        raise AssertionError(f"capability token shape drift: {issued}")
    issued_capability = _decode_token(token)
    issued_shape = _capability_shape(issued_capability)
    if issued_shape["ttl_seconds"] != 300 or issued_shape["permissions"] != ["evidence", "write"]:
        raise AssertionError(f"capability issue claims drift: {issued_shape}")

    second_issue = _ok("second capability issue", _run(repo, project, state, issue_args))
    second_token = str(second_issue["token"])

    mismatch = _verify_summary(exit_code=3, value=_verification_failure(
        "capability binding mismatch",
        _run(repo, project, state, ["run", "capability-verify", second_token, "repo.patch", '{"path":"different.py"}', "--resource", "workspace:/module.py", "--no-consume"]),
    ))
    if mismatch["reason"] != "binding-mismatch":
        raise AssertionError(f"capability binding mismatch drift: {mismatch}")

    verified = _verify_summary(exit_code=0, value=_ok(
        "capability verify",
        _run(repo, project, state, ["run", "capability-verify", token, "repo.patch", encoded_arguments, "--resource", "workspace:/module.py"]),
    ))
    if verified["ok"] is not True or verified["reason"] != "verified":
        raise AssertionError(f"capability verification drift: {verified}")

    replay = _verify_summary(exit_code=3, value=_verification_failure(
        "capability replay",
        _run(repo, project, state, ["run", "capability-verify", token, "repo.patch", encoded_arguments, "--resource", "workspace:/module.py"]),
    ))
    if replay["reason"] != "already-consumed":
        raise AssertionError(f"single-use capability replay drift: {replay}")

    malformed = _verify_summary(exit_code=3, value=_verification_failure(
        "malformed capability token",
        _run(repo, project, state, ["run", "capability-verify", "not-a-token", "repo.patch", encoded_arguments, "--resource", "workspace:/module.py"]),
    ))
    if malformed != {"exit": 3, "ok": False, "reason": "malformed-token", "top_level_keys": ["ok", "reason"]}:
        raise AssertionError(f"malformed capability token drift: {malformed}")

    invalid_signature = _verify_summary(exit_code=3, value=_verification_failure(
        "invalid capability signature",
        _run(repo, project, state, ["run", "capability-verify", _invalid_signature_token(second_token), "repo.patch", encoded_arguments, "--resource", "workspace:/module.py", "--no-consume"]),
    ))
    if invalid_signature != {"exit": 3, "ok": False, "reason": "invalid-signature", "top_level_keys": ["ok", "reason"]}:
        raise AssertionError(f"invalid capability signature drift: {invalid_signature}")

    key_files = list(state.rglob("capability.key"))
    db_files = list(state.rglob("capability.sqlite3"))
    if len(key_files) != 1 or len(db_files) != 1:
        raise AssertionError(f"capability durable side-effect paths drift: keys={key_files} db={db_files}")
    signing_key = key_files[0].read_bytes()
    if len(signing_key) != 32:
        raise AssertionError("capability signing key is no longer 32 bytes")

    expired = _verify_summary(exit_code=3, value=_verification_failure(
        "expired capability",
        _run(repo, project, state, ["run", "capability-verify", _expired_token(second_token, signing_key=signing_key),
                                    "repo.patch", encoded_arguments, "--resource", "workspace:/module.py", "--no-consume"]),
    ))
    if expired["reason"] != "expired":
        raise AssertionError(f"expired capability drift: {expired}")

    observed_verify_reasons = sorted({str(item["reason"]) for item in (mismatch, verified, replay, malformed, invalid_signature, expired)})
    if observed_verify_reasons != fixture["capability"]["verification_reason_vocabulary"]:
        raise AssertionError(f"capability verification reason vocabulary drift: {observed_verify_reasons}")

    parser_error = _argparse_error("missing capability decide arguments", _run(repo, project, state, ["run", "capability-decide", "repo.read"]))
    with sqlite3.connect(db_files[0]) as db:
        consumed_rows = db.execute("SELECT nonce, consumed_at FROM consumed ORDER BY nonce").fetchall()
    if len(consumed_rows) != 1 or consumed_rows[0][0] != issued_capability["nonce"] or not consumed_rows[0][1]:
        raise AssertionError(f"single-use consumption journal drift: {consumed_rows}")

    return {
        "decisions": {
            "read": read,
            "write_authorization_required": write_auth,
            "write_allowed": write_allowed,
            "execute_sandbox_required": execute_sandbox,
            "execute_allowed": execute_allowed,
            "destructive_denied": destructive,
            "outside_workspace_denied": outside,
            "network_denied": network,
            "unknown_tool_denied": unknown,
        },
        "category_vocabulary": observed_categories,
        "decision_reason_vocabulary": observed_reasons,
        "requirement_vocabulary": observed_requirements,
        "issue": {
            "top_level_keys": sorted(issued),
            "single_use": True,
            "token_shape": "base64url-json.hmac-sha256",
            "capability": issued_shape,
        },
        "verify": {
            "first": verified,
            "replay": replay,
            "binding_mismatch": mismatch,
            "malformed": malformed,
            "invalid_signature": invalid_signature,
            "expired": expired,
            "reason_vocabulary": observed_verify_reasons,
        },
        "parser_error": parser_error,
        "durable_side_effects": {
            "signing_key_bytes": 32,
            "consumed_rows": 1,
            "consumed_nonce_matches_issued_token": True,
            "store": "sqlite",
        },
    }


def certify(repo: Path) -> dict[str, Any]:
    fixture_path = repo / FIXTURE_RELATIVE
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
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
        "fixture_sha256": hashlib.sha256(fixture_path.read_bytes()).hexdigest(),
        "exit_policy": {"success": 0, "verification_failure": 3, "argument_parser_error": 2},
        "inventory": inventory,
        "capability": capability,
        "nondeterministic_fields": [
            "capability token nonce",
            "capability token issued_at/expires_at",
            "capability signing key bytes",
            "HMAC signature bytes",
            "consumed_at timestamp",
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
