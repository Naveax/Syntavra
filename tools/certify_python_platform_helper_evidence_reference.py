#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path
from typing import Any

from syntavra_runtime.evidence import EvidenceStore
from syntavra_runtime.runtime_evidence import RuntimeEvidenceGraph
from syntavra_runtime.util import stable_project_id
from tools import report_missing_native_public_routes as public_surface
from tools import report_python_public_execution_contract as execution_contract

FIXTURE_RELATIVE = Path("contracts/python/platform-helper-evidence-reference-v1.json")
K_SOURCE_MODULES = (
    "syntavra_runtime/platform.py",
    "syntavra_runtime/platform_cli.py",
    "syntavra_runtime/artifacts.py",
    "syntavra_runtime/runtime_evidence.py",
    "syntavra_runtime/evidence.py",
    "syntavra_runtime/evidence_rotation.py",
    "syntavra_runtime/unified_cli.py",
    "syntavra_runtime/cli.py",
)


def _head(repo: Path) -> str:
    value = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    return value.stdout.strip() if value.returncode == 0 else ""


def _run(repo: Path, project: Path, state: Path, argv: list[str]) -> dict[str, Any]:
    env = os.environ.copy()
    env.update({"PYTHONPATH": str(repo), "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"})
    env.pop("SYNTAVRA_BULK_PARITY_PROBE", None)
    result = subprocess.run(
        [sys.executable, "-m", "syntavra_runtime.engine_entry", "--engine", "python", "--project", str(project), "--state-root", str(state), *argv],
        cwd=repo, env=env, text=True, encoding="utf-8", errors="replace", stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30, check=False,
    )
    try:
        parsed = json.loads(result.stdout) if result.stdout.strip() else None
    except json.JSONDecodeError:
        parsed = None
    return {"exit": result.returncode, "stdout": result.stdout, "stderr": result.stderr, "value": parsed}


def _json(label: str, result: dict[str, Any], exit_code: int = 0) -> dict[str, Any]:
    if result["exit"] != exit_code or result["stderr"]:
        raise AssertionError(f"{label}: expected clean exit {exit_code}, got {result}")
    if not isinstance(result["value"], dict):
        raise AssertionError(f"{label}: expected JSON object stdout, got {result}")
    return result["value"]


def _failure(label: str, result: dict[str, Any], *, error_type: str, contains: str) -> dict[str, Any]:
    value = _json(label, result, 4)
    error = value.get("error")
    details = error.get("details") if isinstance(error, dict) else None
    rendered = str((details or {}).get("error") or "")
    if value.get("ok") is not False or not isinstance(error, dict) or error.get("code") != "PYTHON_PUBLIC_COMMAND_FAILED":
        raise AssertionError(f"{label}: public failure envelope drift: {value}")
    if not rendered.startswith(error_type + ":") or contains not in rendered:
        raise AssertionError(f"{label}: public failure detail drift: {rendered!r}")
    return {"exit": 4, "code": error["code"], "error_type": error_type, "fallback": (details or {}).get("fallback"), "message_contains": contains}


def _routes(fixture: dict[str, Any]) -> dict[str, Any]:
    wanted = set(fixture["public_routes"])
    sources = public_surface.python_public_route_sources()
    routes = sorted(route for route in sources if route in wanted)
    if routes != fixture["public_routes"]:
        raise AssertionError(f"K public route inventory drift: {routes}")
    execution = {row["route"]: row for row in execution_contract.route_execution_manifest()}
    owners: dict[str, str] = {}
    for route in routes:
        row = execution[route]
        if len(row["entrypoints"]) != 1 or row["unknown_sources"]:
            raise AssertionError(f"K route ownership drift: {row}")
        owners[route] = row["entrypoint"]
    return {"routes": routes, "route_count": len(routes), "route_sha256": public_surface._digest(routes), "ownership": owners}


def _canonical_language_alias(value: Any) -> Any:
    if isinstance(value, list):
        return sorted({"csharp" if str(item) == "c_sharp" else str(item) for item in value})
    return value


def _stable_platform_projection(value: dict[str, Any]) -> dict[str, Any]:
    cloned = json.loads(json.dumps(value, sort_keys=True, default=str))
    language = cloned.get("language_platform")
    if isinstance(language, dict):
        tree_sitter = language.get("tree_sitter")
        if isinstance(tree_sitter, dict) and isinstance(tree_sitter.get("available_languages"), list):
            tree_sitter["available_languages"] = _canonical_language_alias(tree_sitter["available_languages"])
        registry = language.get("language_registry")
        if isinstance(registry, dict) and isinstance(registry.get("adapters"), list):
            registry["adapters"] = _canonical_language_alias(registry["adapters"])
    sandbox = cloned.get("sandbox")
    if isinstance(sandbox, dict):
        sandbox.pop("probe_cached", None)
        backend = sandbox.get("backend")
        if isinstance(backend, dict):
            backend["detail"] = "<host-dependent>"
            backend["command_prefix"] = ["<host-dependent>"] if backend.get("command_prefix") else []
    return cloned


def _platform_contract(repo: Path, project: Path, state: Path, fixture: dict[str, Any]) -> dict[str, Any]:
    pairs = (
        ("status", ["run", "platform-status"], ["run", "competitive-status"]),
        ("doctor", ["run", "platform-doctor"], ["run", "competitive-doctor"]),
        ("manifest", ["run", "platform-manifest"], ["run", "competitive-manifest"]),
    )
    results: dict[str, dict[str, Any]] = {}
    for label, canonical_argv, compat_argv in pairs:
        canonical = _json(f"platform {label}", _run(repo, project, state, canonical_argv))
        compat = _json(f"competitive {label}", _run(repo, project, state, compat_argv))
        if sorted(canonical) != sorted(compat):
            raise AssertionError(f"platform compatibility schema drift for {label}")
        if _stable_platform_projection(canonical) != _stable_platform_projection(compat):
            raise AssertionError(f"platform stable compatibility drift for {label}")
        results[label] = canonical

    status, doctor, manifest = results["status"], results["doctor"], results["manifest"]
    expected = fixture["platform"]
    if (status.get("product"), status.get("version"), status.get("channel")) != (expected["product"], expected["version"], expected["channel"]):
        raise AssertionError(f"platform identity drift: {status}")
    if (manifest.get("product"), manifest.get("version"), manifest.get("channel")) != (expected["product"], expected["version"], expected["channel"]):
        raise AssertionError(f"platform manifest identity drift: {manifest}")
    if manifest.get("external_claims") != expected["manifest_external_claims"]:
        raise AssertionError(f"platform external claim boundary drift: {manifest}")
    sandbox = doctor.get("sandbox")
    language = doctor.get("language_platform")
    if not isinstance(sandbox, dict) or not isinstance(language, dict) or doctor.get("strict_native_sandbox_ready") != sandbox.get("strict_ready"):
        raise AssertionError(f"platform doctor nested contract drift: {doctor}")
    return {
        "compatibility_stable_projection_exact": True,
        "compatibility_normalization": ["c_sharp/csharp tree-sitter alias canonicalization delegated to F", "sandbox backend detail/command-prefix/probe-cache delegated to J"],
        "status_keys": sorted(status), "doctor_keys": sorted(doctor), "manifest_keys": sorted(manifest),
        "product": status["product"], "version": status["version"], "channel": status["channel"],
        "capability_count": len(status.get("capabilities") or {}), "manifest_component_count": len(manifest.get("components") or []),
        "manifest_external_claims": manifest["external_claims"],
        "nested_host_dependent": {
            "sandbox_backend": str((sandbox.get("backend") or {}).get("name") or ""),
            "sandbox_strict_ready": bool(sandbox.get("strict_ready")),
            "language_declared": language.get("declared"),
            "language_available": language.get("available"),
        },
    }


def _artifact_contract(repo: Path, project: Path, state: Path) -> dict[str, Any]:
    raw = "progress line\nERROR tests/test_reference.py:20 assertion failed\nprogress line\n"
    capture = _json("output capture", _run(repo, project, state, ["run", "output-capture", "pytest", raw, "--exit-code", "1", "--duration-ms", "12.5"]))
    receipt_keys = ["artifact_id", "compact_view", "critical_lines", "estimated_original_tokens", "estimated_visible_tokens", "exact_recovery", "kind", "original_bytes", "query_modes", "savings_ratio", "visible_bytes"]
    if sorted(capture) != receipt_keys:
        raise AssertionError(f"FirewallReceipt schema drift: {sorted(capture)}")
    artifact_id = str(capture.get("artifact_id") or "")
    if capture.get("kind") != "terminal" or capture.get("exact_recovery") is not True or not artifact_id.startswith("sha256:"):
        raise AssertionError(f"output capture semantics drift: {capture}")
    if not any("ERROR tests/test_reference.py:20" in str(line) for line in capture.get("critical_lines") or []):
        raise AssertionError(f"critical line extraction drift: {capture}")
    errors = _json("artifact errors query", _run(repo, project, state, ["run", "artifact-query", artifact_id, "--mode", "errors", "--limit", "10"]))
    if errors.get("ok") is not True or errors.get("matched_lines") != 1 or "ERROR tests/test_reference.py:20" not in str(errors.get("view") or ""):
        raise AssertionError(f"artifact errors query drift: {errors}")
    if _json("artifact verify capture", _run(repo, project, state, ["run", "artifact-verify", artifact_id])) != {"ok": True, "checked": 1, "failures": []}:
        raise AssertionError("artifact verify single-object drift")

    literal = "artifact-reference-value"
    put = _json("artifact put", _run(repo, project, state, ["run", "artifact-put", literal, "--kind", "reference", "--media-type", "text/plain"]))
    record_keys = ["artifact_id", "byte_count", "created_at", "kind", "media_type", "metadata", "object_path", "sha256"]
    if sorted(put) != record_keys or put.get("kind") != "reference" or put.get("byte_count") != len(literal.encode()):
        raise AssertionError(f"ArtifactRecord schema drift: {put}")
    if put.get("sha256") != hashlib.sha256(literal.encode()).hexdigest() or put.get("artifact_id") != "sha256:" + put["sha256"]:
        raise AssertionError(f"artifact identity drift: {put}")
    head = _json("artifact head query", _run(repo, project, state, ["run", "artifact-query", put["artifact_id"], "--mode", "head"]))
    if head.get("view") != literal or head.get("matched_lines") != 1:
        raise AssertionError(f"artifact exact view drift: {head}")
    stats = _json("artifact stats", _run(repo, project, state, ["run", "artifact-stats"]))
    if stats.get("artifacts") != 2 or stats.get("exact_bytes") != len(raw.encode()) + len(literal.encode()):
        raise AssertionError(f"artifact stats drift: {stats}")
    verify_all = _json("artifact verify all", _run(repo, project, state, ["run", "artifact-verify"]))
    if verify_all != {"ok": True, "checked": 2, "failures": []}:
        raise AssertionError(f"artifact full verify drift: {verify_all}")
    db_path = state / "unified" / "artifacts" / "artifacts.sqlite3"
    object_files = [path for path in (state / "unified" / "artifacts" / "objects").rglob("*") if path.is_file()]
    if not db_path.is_file() or len(object_files) != 2:
        raise AssertionError(f"artifact durable state drift: db={db_path.is_file()} objects={object_files}")
    return {
        "firewall_receipt_keys": sorted(capture), "artifact_record_keys": sorted(put), "query_keys": sorted(errors),
        "capture_kind": capture["kind"], "capture_exact_recovery": capture["exact_recovery"], "errors_matched": errors["matched_lines"],
        "artifact_stats": stats, "verify_all": verify_all, "durable": {"sqlite": True, "object_files": len(object_files)},
    }


def _runtime_evidence_contract(repo: Path, project: Path, state: Path) -> dict[str, Any]:
    trace_path = project / "trace.json"
    trace_path.write_text(json.dumps([{"source": "alpha", "target": "beta", "relation": "RUNTIME_CALL", "confidence": 0.9, "span_id": "fixture-1"}]), encoding="utf-8")
    imported = _json("semantic trace import", _run(repo, project, state, ["run", "semantic-import", "trace", str(trace_path), "--repository-commit", "abc123"]))
    if imported != {"ok": True, "spans": 1}:
        raise AssertionError(f"runtime trace import drift: {imported}")
    stats = _json("runtime evidence stats", _run(repo, project, state, ["run", "evidence-stats"]))
    if stats.get("ok") is not True or stats.get("nodes") != 2 or stats.get("edges") != 1 or stats.get("relations") != [{"relation": "RUNTIME_CALL", "count": 1}]:
        raise AssertionError(f"runtime evidence stats drift: {stats}")
    source_id = RuntimeEvidenceGraph.identity("runtime-symbol", "alpha", "trace")
    neighbors = _json("runtime evidence neighbors", _run(repo, project, state, ["run", "evidence-neighbors", source_id, "--relation", "RUNTIME_CALL"]))
    rows = neighbors.get("neighbors")
    if neighbors.get("ok") is not True or not isinstance(rows, list) or len(rows) != 1:
        raise AssertionError(f"runtime evidence neighbor shape drift: {neighbors}")
    edge = rows[0]
    node = edge.get("node")
    if edge.get("source") != source_id or edge.get("relation") != "RUNTIME_CALL" or edge.get("repository_commit") != "abc123":
        raise AssertionError(f"runtime evidence edge drift: {edge}")
    if not isinstance(node, dict) or node.get("label") != "beta" or node.get("source") != "trace":
        raise AssertionError(f"runtime evidence linked node drift: {edge}")
    if not (state / "unified" / "runtime-evidence.sqlite3").is_file():
        raise AssertionError("runtime evidence SQLite side effect missing")
    return {"import": imported, "stats": stats, "neighbor_top_level_keys": sorted(neighbors), "edge_keys": sorted(edge), "linked_node_keys": sorted(node), "source_node_id": source_id, "durable_sqlite": True}


def _encrypted_evidence_contract(repo: Path, project: Path, state: Path, fixture: dict[str, Any]) -> dict[str, Any]:
    store = EvidenceStore(state / "evidence", project_id=stable_project_id(project))
    payload = b"exact-evidence-reference\nsecond-line\n"
    handle = store.put(payload, kind="k-reference", metadata={"source": "fixture"})
    if not handle.startswith(fixture["evidence"]["handle_prefix"]):
        raise AssertionError(f"evidence handle drift: {handle}")
    digest = handle.rsplit("/", 1)[1]
    if payload in store._object_path(digest).read_bytes():
        raise AssertionError("plaintext evidence leaked into encrypted object bytes")
    describe = _json("evidence describe", _run(repo, project, state, ["evidence", "describe", handle]))
    description_keys = ["bytes", "created_at", "digest", "encryption", "expires_at", "kind", "project_id", "provenance", "schema_version", "stored_bytes"]
    if sorted(describe) != description_keys or describe.get("schema_version") != fixture["evidence"]["schema_version"] or describe.get("kind") != "k-reference":
        raise AssertionError(f"evidence description drift: {describe}")
    if describe.get("encryption") != {"algorithm": "AES-256-GCM", "key_version": 1, "mode": "encrypted"}:
        raise AssertionError(f"evidence encryption metadata drift: {describe}")
    fetched = _json("evidence get", _run(repo, project, state, ["evidence", "get", handle]))
    if fetched != {"handle": handle, "bytes": len(payload), "text": payload.decode()}:
        raise AssertionError(f"evidence exact recovery drift: {fetched}")
    stats_before = _json("evidence stats", _run(repo, project, state, ["evidence", "stats"]))
    if stats_before.get("objects") != 1 or stats_before.get("encrypted") is not True or stats_before.get("active_key_version") != 1:
        raise AssertionError(f"evidence stats drift: {stats_before}")
    rotation = _json("evidence rotate key", _run(repo, project, state, ["evidence", "rotate-key"]))
    if rotation.get("ok") is not True or rotation.get("previous_key_version") != 1 or rotation.get("active_key_version") != 2 or rotation.get("reencrypted") != 1:
        raise AssertionError(f"evidence rotation drift: {rotation}")
    reopened = EvidenceStore(state / "evidence", project_id=stable_project_id(project))
    if reopened.get(handle) != payload or reopened.describe(handle).get("encryption", {}).get("key_version") != 2:
        raise AssertionError("evidence exact recovery failed after rotation")
    dry_gc = _json("evidence gc dry-run", _run(repo, project, state, ["evidence", "gc", "--ttl-days", "0"]))
    if dry_gc.get("dry_run") is not True or dry_gc.get("objects") != 1 or dry_gc.get("deleted") != 0:
        raise AssertionError(f"evidence dry-run GC drift: {dry_gc}")
    applied_gc = _json("evidence gc apply", _run(repo, project, state, ["evidence", "gc", "--ttl-days", "0", "--apply"]))
    if applied_gc.get("dry_run") is not False or applied_gc.get("deleted") != 1:
        raise AssertionError(f"evidence apply GC drift: {applied_gc}")
    stats_after = _json("evidence stats after gc", _run(repo, project, state, ["evidence", "stats"]))
    if stats_after.get("objects") != 0:
        raise AssertionError(f"evidence GC durable state drift: {stats_after}")
    malformed = _failure("malformed evidence handle", _run(repo, project, state, ["evidence", "get", "not-an-evidence-handle"]), error_type="EvidenceError", contains="invalid evidence handle")
    return {
        "handle_shape": True, "description_keys": sorted(describe), "encryption": describe["encryption"], "ciphertext_excludes_plaintext": True,
        "exact_recovery": True, "stats_before": stats_before, "rotation": rotation, "exact_recovery_after_rotation": True,
        "gc_dry_run": dry_gc, "gc_apply": applied_gc, "stats_after": stats_after, "malformed_handle": malformed,
        "durable": {"sqlite": (state / "evidence" / "evidence.sqlite3").is_file(), "active_marker": (state / "evidence" / "keys" / "active.json").is_file()},
    }


def _source_boundary(repo: Path, fixture: dict[str, Any]) -> dict[str, Any]:
    fd3_patterns = (r"(?:os\.)?write\s*\(\s*3\b", r"(?:os\.)?fdopen\s*\(\s*3\b", r"dup2\s*\([^,]+,\s*3\b", r"pass_fds\s*=.*\b3\b")
    os_patterns = (r"\bsys\.platform\b", r"\bos\.name\b", r"\bplatform\.system\s*\(")
    os_scan = {"syntavra_runtime/artifacts.py", "syntavra_runtime/runtime_evidence.py", "syntavra_runtime/evidence.py", "syntavra_runtime/evidence_rotation.py"}
    fd3_hits: list[str] = []
    os_hits: list[str] = []
    for relative in K_SOURCE_MODULES:
        text = (repo / relative).read_text(encoding="utf-8")
        for pattern in fd3_patterns:
            if re.search(pattern, text):
                fd3_hits.append(f"{relative}:{pattern}")
        if relative in os_scan:
            for pattern in os_patterns:
                if re.search(pattern, text):
                    os_hits.append(f"{relative}:{pattern}")
    if fd3_hits:
        raise AssertionError(f"dedicated FD3 behavior appeared in K-owned sources: {fd3_hits}")
    if os_hits:
        raise AssertionError(f"K-owned evidence/artifact/output code gained uncontracted OS branches: {os_hits}")
    return {
        "fd3": {"applicable": False, "dedicated_channel_hits": fd3_hits, "contract": fixture["fd3"]["contract"]},
        "os_variants": {**fixture["os_variants"], "k_owned_os_branch_hits": os_hits, "scanned_modules": sorted(os_scan)},
    }


def certify(repo: Path) -> dict[str, Any]:
    fixture_path = repo / FIXTURE_RELATIVE
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory(prefix="syntavra-python-platform-helper-evidence-") as directory:
        root = Path(directory)
        project, state = root / "project", root / "state"
        project.mkdir(); state.mkdir(); (project / ".git").mkdir()
        routes = _routes(fixture)
        platform = _platform_contract(repo, project, state, fixture)
        artifacts = _artifact_contract(repo, project, state)
        runtime_evidence = _runtime_evidence_contract(repo, project, state)
        encrypted_evidence = _encrypted_evidence_contract(repo, project, state, fixture)
        boundary = _source_boundary(repo, fixture)
    return {
        "ok": True, "schema_version": 1, "family": "platform-helper-evidence", "engine": "python", "exact_head": _head(repo),
        "fixture": str(FIXTURE_RELATIVE).replace("\\", "/"), "fixture_sha256": hashlib.sha256(fixture_path.read_bytes()).hexdigest(),
        "routes": routes, "platform": platform, "artifacts": artifacts, "runtime_evidence": runtime_evidence, "encrypted_evidence": encrypted_evidence,
        "fd3": boundary["fd3"], "os_variants": boundary["os_variants"], "exit_policy": fixture["exit_policy"],
        "nondeterministic_fields": [
            "artifact created_at timestamps and object paths", "evidence created_at/expires_at and encrypted object bytes/nonces",
            "runtime evidence observed_at timestamps", "temporary project/state paths", "nested sandbox backend host capability details",
            "nested language adapter installed/availability details",
        ],
        "network_boundary": "offline; no live external service or remote network required",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Certify Python platform/helper/evidence reference behavior")
    parser.add_argument("--repo", default=str(Path(__file__).resolve().parents[1])); parser.add_argument("--output")
    args = parser.parse_args(); repo = Path(args.repo).resolve(strict=True)
    try:
        result = certify(repo)
    except Exception as exc:
        result = {"ok": False, "schema_version": 1, "family": "platform-helper-evidence", "engine": "python", "exact_head": _head(repo), "error": f"{type(exc).__name__}: {exc}", "traceback": traceback.format_exc()}
    rendered = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True, default=str)
    if args.output: Path(args.output).write_text(rendered + "\n", encoding="utf-8")
    print(rendered); return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
