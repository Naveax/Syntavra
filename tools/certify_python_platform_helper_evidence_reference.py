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
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def _run(repo: Path, project: Path, state: Path, args: list[str]) -> dict[str, Any]:
    env = os.environ.copy()
    env.update({"PYTHONPATH": str(repo), "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"})
    env.pop("SYNTAVRA_BULK_PARITY_PROBE", None)
    result = subprocess.run(
        [sys.executable, "-m", "syntavra_runtime.engine_entry", "--engine", "python",
         "--project", str(project), "--state-root", str(state), *args],
        cwd=repo, env=env, text=True, encoding="utf-8", errors="replace",
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30, check=False,
    )
    try:
        value = json.loads(result.stdout) if result.stdout.strip() else None
    except json.JSONDecodeError:
        value = None
    return {"exit": result.returncode, "stdout": result.stdout, "stderr": result.stderr, "value": value}


def _json(label: str, result: dict[str, Any], *, exit_code: int = 0) -> dict[str, Any]:
    if result["exit"] != exit_code or result["stderr"]:
        raise AssertionError(f"{label}: expected exit={exit_code} and empty stderr, got {result}")
    value = result.get("value")
    if not isinstance(value, dict):
        raise AssertionError(f"{label}: expected JSON object stdout, got {result}")
    return value


def _failure(label: str, result: dict[str, Any], *, error_type: str, contains: str) -> dict[str, Any]:
    value = _json(label, result, exit_code=4)
    if value.get("ok") is not False:
        raise AssertionError(f"{label}: failure envelope drift: {value}")
    error = value.get("error")
    if not isinstance(error, dict) or error.get("code") != "PYTHON_PUBLIC_COMMAND_FAILED":
        raise AssertionError(f"{label}: public error code drift: {value}")
    details = error.get("details")
    rendered = str((details or {}).get("error") or "")
    if not rendered.startswith(error_type + ":") or contains not in rendered:
        raise AssertionError(f"{label}: error detail drift: {rendered!r}")
    return {
        "exit": 4,
        "code": error["code"],
        "error_type": error_type,
        "fallback": (details or {}).get("fallback"),
        "message_contains": contains,
    }


def _routes(fixture: dict[str, Any]) -> dict[str, Any]:
    all_routes = public_surface.python_public_route_sources()
    selected = sorted(
        route for route in all_routes
        if route in {
            "run platform-status", "run platform-doctor", "run platform-manifest",
            "run competitive-status", "run competitive-doctor", "run competitive-manifest",
            "run output-capture", "run artifact-put", "run artifact-query", "run artifact-verify", "run artifact-stats",
            "run semantic-import", "run evidence-stats", "run evidence-neighbors",
            "evidence get", "evidence describe", "evidence stats", "evidence gc", "evidence rotate-key",
        }
    )
    if selected != fixture["public_routes"]:
        raise AssertionError(f"K public route inventory drift: {selected}")
    execution = {row["route"]: row for row in execution_contract.route_execution_manifest()}
    ownership: dict[str, str] = {}
    for route in selected:
        row = execution[route]
        if len(row["entrypoints"]) != 1 or row["unknown_sources"]:
            raise AssertionError(f"K route ownership drift: {row}")
        ownership[route] = row["entrypoint"]
    return {
        "routes": selected,
        "route_count": len(selected),
        "route_sha256": public_surface._digest(selected),
        "ownership": ownership,
    }


def _platform_contract(repo: Path, project: Path, state: Path, fixture: dict[str, Any]) -> dict[str, Any]:
    pairs = (
        ("status", ["run", "platform-status"], ["run", "competitive-status"]),
        ("doctor", ["run", "platform-doctor"], ["run", "competitive-doctor"]),
        ("manifest", ["run", "platform-manifest"], ["run", "competitive-manifest"]),
    )
    results: dict[str, Any] = {}
    for label, canonical_argv, compat_argv in pairs:
        canonical = _json(f"platform {label}", _run(repo, project, state, canonical_argv))
        compat = _json(f"competitive {label}", _run(repo, project, state, compat_argv))
        if canonical != compat:
            raise AssertionError(f"platform compatibility drift for {label}: {canonical} != {compat}")
        results[label] = canonical

    status = results["status"]
    manifest = results["manifest"]
    doctor = results["doctor"]
    expected = fixture["platform"]
    if status.get("product") != expected["product"] or status.get("version") != expected["version"] or status.get("channel") != expected["channel"]:
        raise AssertionError(f"platform identity drift: {status}")
    if manifest.get("product") != expected["product"] or manifest.get("version") != expected["version"] or manifest.get("channel") != expected["channel"]:
        raise AssertionError(f"platform manifest identity drift: {manifest}")
    if manifest.get("external_claims") != expected["manifest_external_claims"]:
        raise AssertionError(f"platform external claim boundary drift: {manifest}")
    sandbox = doctor.get("sandbox")
    language = doctor.get("language_platform")
    if not isinstance(sandbox, dict) or not isinstance(language, dict):
        raise AssertionError(f"platform doctor nested status drift: {doctor}")
    if doctor.get("strict_native_sandbox_ready") != sandbox.get("strict_ready"):
        raise AssertionError(f"doctor strict sandbox derived field drift: {doctor}")

    return {
        "compatibility_exact": True,
        "status_keys": sorted(status),
        "doctor_keys": sorted(doctor),
        "manifest_keys": sorted(manifest),
        "product": status["product"],
        "version": status["version"],
        "channel": status["channel"],
        "capability_count": len(status.get("capabilities") or {}),
        "manifest_component_count": len(manifest.get("components") or []),
        "manifest_external_claims": manifest["external_claims"],
        "nested_host_dependent": {
            "sandbox_backend": str((sandbox.get("backend") or {}).get("name") or ""),
            "sandbox_strict_ready": bool(sandbox.get("strict_ready")),
            "language_declared": language.get("declared"),
            "language_installed": language.get("installed"),
        },
    }


def _artifact_contract(repo: Path, project: Path, state: Path) -> dict[str, Any]:
    raw = "progress line\nERROR tests/test_reference.py:20 assertion failed\nprogress line\n"
    capture = _json(
        "output capture",
        _run(repo, project, state, ["run", "output-capture", "pytest", raw, "--exit-code", "1", "--duration-ms", "12.5"]),
    )
    expected_receipt_keys = [
        "artifact_id", "compact_view", "critical_lines", "estimated_original_tokens",
        "estimated_visible_tokens", "exact_recovery", "kind", "original_bytes", "query_modes",
        "savings_ratio", "visible_bytes",
    ]
    if sorted(capture) != expected_receipt_keys:
        raise AssertionError(f"FirewallReceipt schema drift: {sorted(capture)}")
    artifact_id = str(capture.get("artifact_id") or "")
    if capture.get("kind") != "test" or capture.get("exact_recovery") is not True or not artifact_id.startswith("sha256:"):
        raise AssertionError(f"output capture semantics drift: {capture}")
    if not any("ERROR tests/test_reference.py:20" in str(line) for line in capture.get("critical_lines") or []):
        raise AssertionError(f"critical line extraction drift: {capture}")

    errors = _json(
        "artifact errors query",
        _run(repo, project, state, ["run", "artifact-query", artifact_id, "--mode", "errors", "--limit", "10"]),
    )
    if errors.get("ok") is not True or errors.get("matched_lines") != 1 or "ERROR tests/test_reference.py:20" not in str(errors.get("view") or ""):
        raise AssertionError(f"artifact errors query drift: {errors}")
    verify_capture = _json("artifact verify capture", _run(repo, project, state, ["run", "artifact-verify", artifact_id]))
    if verify_capture != {"ok": True, "checked": 1, "failures": []}:
        raise AssertionError(f"artifact verify drift: {verify_capture}")

    literal = "artifact-reference-value"
    put = _json("artifact put", _run(repo, project, state, ["run", "artifact-put", literal, "--kind", "reference", "--media-type", "text/plain"]))
    expected_record_keys = ["artifact_id", "byte_count", "created_at", "kind", "media_type", "metadata", "object_path", "sha256"]
    if sorted(put) != expected_record_keys or put.get("kind") != "reference" or put.get("byte_count") != len(literal.encode()):
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
    objects = list((state / "unified" / "artifacts" / "objects").rglob("*"))
    object_files = [path for path in objects if path.is_file()]
    if not db_path.is_file() or len(object_files) != 2:
        raise AssertionError(f"artifact durable state drift: db={db_path.is_file()} files={object_files}")

    return {
        "firewall_receipt_keys": sorted(capture),
        "artifact_record_keys": sorted(put),
        "query_keys": sorted(errors),
        "capture_kind": capture["kind"],
        "capture_exact_recovery": capture["exact_recovery"],
        "errors_matched": errors["matched_lines"],
        "artifact_stats": stats,
        "verify_all": verify_all,
        "durable": {"sqlite": True, "object_files": len(object_files)},
    }


def _runtime_evidence_contract(repo: Path, project: Path, state: Path) -> dict[str, Any]:
    trace_path = project / "trace.json"
    trace_path.write_text(
        json.dumps([
            {"source": "alpha", "target": "beta", "relation": "RUNTIME_CALL", "confidence": 0.9, "span_id": "fixture-1"}
        ]),
        encoding="utf-8",
    )
    imported = _json(
        "semantic trace import",
        _run(repo, project, state, ["run", "semantic-import", "trace", str(trace_path), "--repository-commit", "abc123"]),
    )
    if imported != {"ok": True, "spans": 1}:
        raise AssertionError(f"runtime trace import drift: {imported}")
    stats = _json("runtime evidence stats", _run(repo, project, state, ["run", "evidence-stats"]))
    if stats.get("ok") is not True or stats.get("nodes") != 2 or stats.get("edges") != 1 or stats.get("relations") != [{"relation": "RUNTIME_CALL", "count": 1}]:
        raise AssertionError(f"runtime evidence stats drift: {stats}")
    source_id = RuntimeEvidenceGraph.identity("runtime-symbol", "alpha", "trace")
    neighbors = _json(
        "runtime evidence neighbors",
        _run(repo, project, state, ["run", "evidence-neighbors", source_id, "--relation", "RUNTIME_CALL"]),
    )
    rows = neighbors.get("neighbors")
    if neighbors.get("ok") is not True or not isinstance(rows, list) or len(rows) != 1:
        raise AssertionError(f"runtime evidence neighbor shape drift: {neighbors}")
    row = rows[0]
    if row.get("source") != source_id or row.get("relation") != "RUNTIME_CALL" or row.get("repository_commit") != "abc123":
        raise AssertionError(f"runtime evidence edge drift: {row}")
    node = row.get("node")
    if not isinstance(node, dict) or node.get("label") != "beta" or node.get("source") != "trace":
        raise AssertionError(f"runtime evidence linked node drift: {row}")
    db_path = state / "unified" / "runtime-evidence.sqlite3"
    if not db_path.is_file():
        raise AssertionError("runtime evidence SQLite side effect missing")
    return {
        "import": imported,
        "stats": stats,
        "neighbor_top_level_keys": sorted(neighbors),
        "edge_keys": sorted(row),
        "linked_node_keys": sorted(node),
        "source_node_id": source_id,
        "durable_sqlite": True,
    }


def _encrypted_evidence_contract(repo: Path, project: Path, state: Path, fixture: dict[str, Any]) -> dict[str, Any]:
    store = EvidenceStore(state / "evidence", project_id=stable_project_id(project))
    payload = b"exact-evidence-reference\nsecond-line\n"
    handle = store.put(payload, kind="k-reference", metadata={"source": "fixture"})
    if not handle.startswith(fixture["evidence"]["handle_prefix"]):
        raise AssertionError(f"evidence handle drift: {handle}")
    digest = handle.rsplit("/", 1)[1]
    object_path = store._object_path(digest)
    raw_ciphertext = object_path.read_bytes()
    if payload in raw_ciphertext:
        raise AssertionError("plaintext evidence leaked into encrypted object bytes")

    describe = _json("evidence describe", _run(repo, project, state, ["evidence", "describe", handle]))
    expected_description_keys = ["bytes", "created_at", "digest", "encryption", "expires_at", "kind", "project_id", "provenance", "schema_version", "stored_bytes"]
    if sorted(describe) != expected_description_keys:
        raise AssertionError(f"evidence description schema drift: {sorted(describe)}")
    if describe.get("schema_version") != fixture["evidence"]["schema_version"] or describe.get("kind") != "k-reference":
        raise AssertionError(f"evidence description semantics drift: {describe}")
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
        raise AssertionError("evidence exact recovery failed after key rotation")

    dry_gc = _json("evidence gc dry run", _run(repo, project, state, ["evidence", "gc", "--ttl-days", "0"]))
    if dry_gc.get("ok") is not True or dry_gc.get("dry_run") is not True or dry_gc.get("objects") != 1 or dry_gc.get("deleted") != 0:
        raise AssertionError(f"evidence dry-run GC drift: {dry_gc}")
    applied_gc = _json("evidence gc apply", _run(repo, project, state, ["evidence", "gc", "--ttl-days", "0", "--apply"]))
    if applied_gc.get("ok") is not True or applied_gc.get("dry_run") is not False or applied_gc.get("deleted") != 1:
        raise AssertionError(f"evidence apply GC drift: {applied_gc}")
    stats_after = _json("evidence stats after gc", _run(repo, project, state, ["evidence", "stats"]))
    if stats_after.get("objects") != 0:
        raise AssertionError(f"evidence GC durable state drift: {stats_after}")

    malformed = _failure(
        "malformed evidence handle",
        _run(repo, project, state, ["evidence", "get", "not-an-evidence-handle"]),
        error_type="EvidenceError",
        contains="invalid evidence handle",
    )

    return {
        "handle_shape": True,
        "description_keys": sorted(describe),
        "encryption": describe["encryption"],
        "ciphertext_excludes_plaintext": True,
        "exact_recovery": True,
        "stats_before": stats_before,
        "rotation": rotation,
        "exact_recovery_after_rotation": True,
        "gc_dry_run": dry_gc,
        "gc_apply": applied_gc,
        "stats_after": stats_after,
        "malformed_handle": malformed,
        "durable": {
            "sqlite": (state / "evidence" / "evidence.sqlite3").is_file(),
            "active_marker": (state / "evidence" / "keys" / "active.json").is_file(),
        },
    }


def _source_boundary(repo: Path, fixture: dict[str, Any]) -> dict[str, Any]:
    fd3_patterns = (
        re.compile(r"(?:os\.)?write\s*\(\s*3\b"),
        re.compile(r"(?:os\.)?fdopen\s*\(\s*3\b"),
        re.compile(r"dup2\s*\([^,]+,\s*3\b"),
        re.compile(r"pass_fds\s*=.*\b3\b"),
    )
    os_branch_patterns = (
        re.compile(r"\bsys\.platform\b"),
        re.compile(r"\bos\.name\b"),
        re.compile(r"\bplatform\.system\s*\("),
    )
    fd3_hits: list[str] = []
    os_hits: list[str] = []
    os_scan_modules = {
        "syntavra_runtime/artifacts.py",
        "syntavra_runtime/runtime_evidence.py",
        "syntavra_runtime/evidence.py",
        "syntavra_runtime/evidence_rotation.py",
    }
    for relative in K_SOURCE_MODULES:
        text = (repo / relative).read_text(encoding="utf-8")
        for pattern in fd3_patterns:
            if pattern.search(text):
                fd3_hits.append(f"{relative}:{pattern.pattern}")
        if relative in os_scan_modules:
            for pattern in os_branch_patterns:
                if pattern.search(text):
                    os_hits.append(f"{relative}:{pattern.pattern}")
    if fd3_hits:
        raise AssertionError(f"dedicated FD3 behavior appeared in K-owned sources: {fd3_hits}")
    if os_hits:
        raise AssertionError(f"K-owned evidence/artifact/output code gained uncontracted OS branches: {os_hits}")
    if fixture["fd3"]["applicable"] is not False:
        raise AssertionError("FD3 fixture must remain explicit when behavior is absent")
    return {
        "fd3": {
            "applicable": False,
            "dedicated_channel_hits": fd3_hits,
            "contract": fixture["fd3"]["contract"],
        },
        "os_variants": {
            **fixture["os_variants"],
            "k_owned_os_branch_hits": os_hits,
            "scanned_modules": sorted(os_scan_modules),
        },
    }


def certify(repo: Path) -> dict[str, Any]:
    fixture_path = repo / FIXTURE_RELATIVE
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory(prefix="syntavra-python-platform-helper-evidence-") as directory:
        root = Path(directory)
        project = root / "project"
        state = root / "state"
        project.mkdir()
        state.mkdir()
        (project / ".git").mkdir()
        routes = _routes(fixture)
        platform = _platform_contract(repo, project, state, fixture)
        artifacts = _artifact_contract(repo, project, state)
        runtime_evidence = _runtime_evidence_contract(repo, project, state)
        encrypted_evidence = _encrypted_evidence_contract(repo, project, state, fixture)
        source_boundary = _source_boundary(repo, fixture)
    return {
        "ok": True,
        "schema_version": 1,
        "family": "platform-helper-evidence",
        "engine": "python",
        "exact_head": _head(repo),
        "fixture": str(FIXTURE_RELATIVE).replace("\\", "/"),
        "fixture_sha256": hashlib.sha256(fixture_path.read_bytes()).hexdigest(),
        "routes": routes,
        "platform": platform,
        "artifacts": artifacts,
        "runtime_evidence": runtime_evidence,
        "encrypted_evidence": encrypted_evidence,
        "fd3": source_boundary["fd3"],
        "os_variants": source_boundary["os_variants"],
        "exit_policy": fixture["exit_policy"],
        "nondeterministic_fields": [
            "artifact created_at timestamps and object paths",
            "evidence created_at/expires_at and encrypted object bytes/nonces",
            "runtime evidence observed_at timestamps",
            "temporary project/state paths",
            "nested sandbox backend host capability details",
            "nested language adapter installed/availability details",
        ],
        "network_boundary": "offline; no live external service or remote network required",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Certify Python platform/helper/evidence reference behavior")
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
            "family": "platform-helper-evidence",
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
