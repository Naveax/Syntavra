#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path
from typing import Any

from syntavra_runtime.command_rewriter import CommandRewriteEngine
from syntavra_runtime.context_governor import DEFAULT_THRESHOLDS, pack_context
from syntavra_runtime.models import ContextItem
from syntavra_runtime.session_runtime import SessionRuntime
from syntavra_runtime.util import stable_project_id
from tools import report_missing_native_public_routes as public_surface
from tools import report_python_public_execution_contract as execution_contract

FIXTURE_RELATIVE = Path("contracts/python/context-compaction-reference-v1.json")


def _head(repo: Path) -> str:
    proc = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
    )
    return proc.stdout.strip() if proc.returncode == 0 else ""


def _run(repo: Path, project: Path, state: Path, argv: list[str]) -> dict[str, Any]:
    env = os.environ.copy()
    env.update({"PYTHONPATH": str(repo), "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"})
    env.pop("SYNTAVRA_BULK_PARITY_PROBE", None)
    proc = subprocess.run(
        [
            sys.executable, "-m", "syntavra_runtime.engine_entry",
            "--engine", "python", "--project", str(project), "--state-root", str(state),
            *argv,
        ],
        cwd=repo,
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=90,
        check=False,
    )
    try:
        value = json.loads(proc.stdout) if proc.stdout.strip() else None
    except json.JSONDecodeError:
        value = None
    return {
        "exit": proc.returncode,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
        "value": value,
    }


def _json(label: str, result: dict[str, Any], exit_code: int = 0) -> dict[str, Any]:
    if result["exit"] != exit_code or result["stderr"]:
        raise AssertionError(f"{label}: expected clean exit {exit_code}, got {result}")
    if not isinstance(result["value"], dict):
        raise AssertionError(f"{label}: expected JSON object stdout, got {result}")
    return result["value"]


def _error4(label: str, result: dict[str, Any]) -> dict[str, Any]:
    value = _json(label, result, 4)
    if value.get("ok") is not False or value.get("error", {}).get("code") != "PYTHON_PUBLIC_COMMAND_FAILED":
        raise AssertionError(f"{label}: public failure envelope drift: {value}")
    return value


def _routes(fixture: dict[str, Any]) -> dict[str, Any]:
    authority = public_surface.python_public_route_sources()
    expected = fixture["public_routes"]
    observed = sorted(route for route in authority if route in set(expected))
    if observed != expected:
        raise AssertionError(f"context/compaction route inventory drift: {observed}")
    manifest = {row["route"]: row for row in execution_contract.route_execution_manifest()}
    owners: dict[str, str] = {}
    for route in expected:
        row = manifest.get(route)
        if not row or len(row["entrypoints"]) != 1 or row["unknown_sources"]:
            raise AssertionError(f"context/compaction execution ownership drift: {row}")
        owners[route] = row["entrypoint"]
    return {
        "routes": expected,
        "route_count": len(expected),
        "route_sha256": public_surface._digest(expected),
        "ownership": owners,
    }


def _context_contract(repo: Path, project: Path, state: Path, root: Path, fixture: dict[str, Any]) -> dict[str, Any]:
    observed_thresholds = [[value, list(actions)] for value, actions in DEFAULT_THRESHOLDS]
    if observed_thresholds != fixture["context_thresholds"]:
        raise AssertionError(f"context threshold drift: {observed_thresholds}")

    implicit = _json("context implicit evaluate", _run(repo, project, state, ["context", "--used", "50", "--window", "100"]))
    explicit = _json("context explicit evaluate", _run(repo, project, state, ["context", "evaluate", "--used", "50", "--window", "100"]))
    if implicit != explicit:
        raise AssertionError(f"context implicit alias drift: {implicit} / {explicit}")
    if explicit != {
        "actions": ["evict_duplicates", "drop_raw_success_logs"],
        "level": 1,
        "mandatory_split": False,
        "pressure_score": 0.5,
        "utilization": 0.5,
    }:
        raise AssertionError(f"context threshold semantics drift: {explicit}")

    pressure = _json(
        "context pressure",
        _run(repo, project, state, [
            "context", "evaluate", "--used", "80", "--window", "100",
            "--churn", "1", "--evidence-pressure", "1",
        ]),
    )
    if pressure.get("level") != 6 or pressure.get("mandatory_split") is not True or pressure.get("pressure_score") != 1.0:
        raise AssertionError(f"context pressure accounting drift: {pressure}")

    pack_path = root / "context-pack.json"
    pack_document = {
        "items": [
            {"item_id": "sys", "role": "system", "text": "system rules", "tokens": 30, "utility": 5.0, "mandatory": True, "stable": True},
            {"item_id": "dep", "role": "evidence", "text": "exact evidence", "tokens": 20, "utility": 4.0, "stable": True},
            {"item_id": "task", "role": "task", "text": "task context", "tokens": 25, "utility": 10.0, "dependencies": ["dep"]},
            {"item_id": "noise", "role": "log", "text": "low value log", "tokens": 50, "utility": 1.0},
        ]
    }
    pack_path.write_text(json.dumps(pack_document, separators=(",", ":")), encoding="utf-8")
    packed = _json(
        "context pack",
        _run(repo, project, state, ["context", "pack", "--input", str(pack_path), "--budget", "80", "--mandatory-role", "system"]),
    )
    direct_items = [ContextItem(**row) for row in pack_document["items"]]
    direct = pack_context(direct_items, budget=80, mandatory_roles=("system",))
    if packed.get("budget") != 80 or packed.get("used") != 75:
        raise AssertionError(f"context pack token accounting drift: {packed}")
    if packed.get("selected_ids") != list(direct.selected_ids) or packed.get("dropped_ids") != list(direct.dropped_ids):
        raise AssertionError(f"context pack selection drift: {packed} / {direct}")
    if packed.get("selected_ids") != ["dep", "sys", "task"] or packed.get("mandatory_satisfied") is not True:
        raise AssertionError(f"context pack ordering drift: {packed}")
    if packed.get("stable_prefix_hash") != direct.stable_prefix_hash or len(str(packed.get("stable_prefix_hash"))) != 64:
        raise AssertionError(f"context pack stable-prefix drift: {packed}")

    over_budget = pack_context(
        (ContextItem("mandatory", "system", "required", 90, 1.0, mandatory=True, stable=True),),
        budget=50,
        mandatory_roles=("system",),
    )
    if over_budget.selected_ids or over_budget.mandatory_satisfied or over_budget.reasons != ("mandatory-over-budget:90>50",):
        raise AssertionError(f"mandatory over-budget fail-closed drift: {over_budget}")

    malformed_path = root / "context-pack-malformed.json"
    malformed_path.write_text('{"items":[{"item_id":"broken"}]}\n', encoding="utf-8")
    malformed = _error4(
        "context malformed item",
        _run(repo, project, state, ["context", "pack", "--input", str(malformed_path), "--budget", "80"]),
    )
    invalid_window = _error4(
        "context invalid window",
        _run(repo, project, state, ["context", "evaluate", "--used", "1", "--window", "0"]),
    )
    parser_error = _run(repo, project, state, ["context", "evaluate", "--used", "not-an-int", "--window", "10"])
    if parser_error["exit"] != 2 or not parser_error["stderr"]:
        raise AssertionError(f"context argparse semantics drift: {parser_error}")

    return {
        "implicit_alias_equal": True,
        "thresholds": observed_thresholds,
        "evaluate_50_percent": explicit,
        "pressure_all_thresholds": pressure,
        "pack": {
            "budget": packed["budget"],
            "used": packed["used"],
            "selected_ids": packed["selected_ids"],
            "dropped_ids": packed["dropped_ids"],
            "stable_prefix_hash": packed["stable_prefix_hash"],
            "mandatory_satisfied": packed["mandatory_satisfied"],
        },
        "mandatory_over_budget": {
            "selected_ids": list(over_budget.selected_ids),
            "mandatory_satisfied": over_budget.mandatory_satisfied,
            "reasons": list(over_budget.reasons),
        },
        "malformed_item": {"exit": 4, "error_type": malformed["error"]["details"]["error"].split(":", 1)[0]},
        "invalid_window": {"exit": 4, "error_type": invalid_window["error"]["details"]["error"].split(":", 1)[0]},
        "argparse_error": {"exit": 2, "stderr_nonempty": True},
    }


def _legacy_session_contract(repo: Path, project: Path, state: Path, root: Path) -> dict[str, Any]:
    opened_a = _json("session open a", _run(repo, project, state, ["session", "open", "--session-id", "legacy-a", "--task", "fixture"]))
    opened_b = _json("session open b", _run(repo, project, state, ["session", "open", "--session-id", "legacy-b"]))
    if opened_a.get("session_id") != "legacy-a" or opened_a.get("state") != "ACTIVE" or opened_b.get("session_id") != "legacy-b":
        raise AssertionError(f"session open drift: {opened_a} / {opened_b}")

    first = _json(
        "session append",
        _run(repo, project, state, ["session", "append", "legacy-a", "decision", '{"decision":"first"}']),
    )
    if first.get("sequence") != 1 or first.get("previous_hash") != "0" * 64:
        raise AssertionError(f"session append hash-chain drift: {first}")

    runtime = SessionRuntime(state / "sessions.sqlite3", project_id=stable_project_id(project))
    for sequence in range(2, 31):
        runtime.append("legacy-a", "decision", {"decision": f"fixture-{sequence}"})

    listed = _json("session list", _run(repo, project, state, ["session", "list"]))
    if len(listed.get("sessions") or []) != 2:
        raise AssertionError(f"session list drift: {listed}")

    context = _json(
        "session context",
        _run(repo, project, state, ["session", "context", "legacy-a", "--token-budget", "256", "--recent-events", "4"]),
    )
    if context.get("budget") != 256 or context.get("exact_history_events") != 30 or context.get("recent_event_count") != 4 or not context.get("root_summary_id"):
        raise AssertionError(f"session active context drift: {context}")
    if not 0 <= int(context.get("used", -1)) <= 256:
        raise AssertionError(f"session token accounting drift: {context}")

    compact1 = _json(
        "session compact first",
        _run(repo, project, state, ["session", "compact", "legacy-a", "--leaf-size", "4", "--fanout", "3"]),
    )
    compact2 = _json(
        "session compact idempotent",
        _run(repo, project, state, ["session", "compact", "legacy-a", "--leaf-size", "4", "--fanout", "3"]),
    )
    if not compact1.get("root_summary_id") or compact1.get("root_summary_id") != compact2.get("root_summary_id"):
        raise AssertionError(f"session compaction idempotency drift: {compact1} / {compact2}")

    checkpoint = _json("session checkpoint", _run(repo, project, state, ["session", "checkpoint", "legacy-a", "--label", "fixture-cp"]))
    if checkpoint.get("session_id") != "legacy-a" or checkpoint.get("through_sequence") != 30 or checkpoint.get("metadata") != {"label": "fixture-cp"}:
        raise AssertionError(f"session checkpoint drift: {checkpoint}")

    forked = _json("session fork", _run(repo, project, state, ["session", "fork", "legacy-a", "--label", "fixture-fork"]))
    if forked.get("parent_ids") != ["legacy-a"] or forked.get("state") != "ACTIVE":
        raise AssertionError(f"session fork drift: {forked}")

    merged = _json("session merge", _run(repo, project, state, ["session", "merge", "legacy-a", "legacy-b", "--label", "fixture-merge"]))
    if merged.get("parent_ids") != ["legacy-a", "legacy-b"] or merged.get("state") != "ACTIVE":
        raise AssertionError(f"session merge drift: {merged}")

    verified = _json("session verify", _run(repo, project, state, ["session", "verify", "legacy-a"]))
    if verified != {"events": 30, "last_hash": verified.get("last_hash"), "ok": True, "reasons": []} or len(str(verified.get("last_hash"))) != 64:
        raise AssertionError(f"session verification drift: {verified}")

    export_path = root / "legacy-a-export.json"
    exported = _json("session export", _run(repo, project, state, ["session", "export", "legacy-a", "--output", str(export_path)]))
    if exported.get("events") != 30 or not export_path.is_file() or len(str(exported.get("hash"))) != 64:
        raise AssertionError(f"session export drift: {exported}")
    imported = _json("session import", _run(repo, project, state, ["session", "import", "--input", str(export_path), "--session-id", "legacy-imported"]))
    if imported.get("session_id") != "legacy-imported" or imported.get("metadata", {}).get("imported_from") != "legacy-a":
        raise AssertionError(f"session import drift: {imported}")

    closed = _json("session close", _run(repo, project, state, ["session", "close", "legacy-b"]))
    if closed.get("state") != "CLOSED":
        raise AssertionError(f"session close drift: {closed}")
    recovered = _json("session recover", _run(repo, project, state, ["session", "recover"]))
    if recovered.get("ok") is not True or recovered.get("database_integrity") is not True:
        raise AssertionError(f"session recover drift: {recovered}")

    missing = _error4("session missing context", _run(repo, project, state, ["session", "context", "missing-session"]))
    malformed_append = _error4(
        "session malformed append",
        _run(repo, project, state, ["session", "append", "legacy-a", "decision", "{"]),
    )

    with sqlite3.connect(state / "sessions.sqlite3") as db:
        counts = {
            "sessions": db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0],
            "events": db.execute("SELECT COUNT(*) FROM session_events").fetchone()[0],
            "summaries": db.execute("SELECT COUNT(*) FROM session_summaries WHERE invalidated_at IS NULL").fetchone()[0],
            "checkpoints": db.execute("SELECT COUNT(*) FROM session_checkpoints").fetchone()[0],
        }
    if counts["sessions"] < 5 or counts["events"] < 62 or counts["summaries"] < 1 or counts["checkpoints"] < 3:
        raise AssertionError(f"session durable-state drift: {counts}")

    return {
        "initial_ids": [opened_a["session_id"], opened_b["session_id"]],
        "append_sequence": first["sequence"],
        "context": {
            "budget": context["budget"], "used": context["used"],
            "recent_event_count": context["recent_event_count"],
            "exact_history_events": context["exact_history_events"],
            "root_summary_present": bool(context["root_summary_id"]),
        },
        "compact_idempotent": compact1["root_summary_id"] == compact2["root_summary_id"],
        "checkpoint": {"through_sequence": checkpoint["through_sequence"], "metadata": checkpoint["metadata"]},
        "fork_parent_ids": forked["parent_ids"],
        "merge_parent_ids": merged["parent_ids"],
        "verify": {"ok": verified["ok"], "events": verified["events"], "reasons": verified["reasons"]},
        "export": {"events": exported["events"], "hash_shape": len(exported["hash"]) == 64},
        "imported_from": imported["metadata"]["imported_from"],
        "closed_state": closed["state"],
        "recover_ok": recovered["ok"],
        "missing_context": {"exit": 4, "error_type": missing["error"]["details"]["error"].split(":", 1)[0]},
        "malformed_append": {"exit": 4, "error_type": malformed_append["error"]["details"]["error"].split(":", 1)[0]},
        "sqlite_counts": counts,
    }


def _continuity_contract(repo: Path, project: Path, state: Path) -> dict[str, Any]:
    resumed = _json(
        "run session-open resume",
        _run(repo, project, state, ["run", "session-open", "--session-id", "legacy-a", "--metadata", '{"ignored":true}']),
    )
    if resumed.get("continuity_restored") is not True or resumed.get("verification", {}).get("events") != 30:
        raise AssertionError(f"session continuity resume drift: {resumed}")

    appended = _json(
        "run session-append",
        _run(repo, project, state, ["run", "session-append", "legacy-a", "decision", '{"decision":"wrapper"}']),
    )
    if appended.get("ok") is not True or appended.get("event", {}).get("sequence") != 31:
        raise AssertionError(f"session product append drift: {appended}")

    compacted = _json("run session-compact", _run(repo, project, state, ["run", "session-compact", "legacy-a", "--force"]))
    if compacted.get("ok") is not True or compacted.get("events") != 31 or compacted.get("exact_history_events") != 31 or not compacted.get("root_summary_id"):
        raise AssertionError(f"session product compact drift: {compacted}")

    receipt = _json(
        "run session-continuity",
        _run(repo, project, state, ["run", "session-continuity", "legacy-a", "--token-budget", "512"]),
    )
    if (
        receipt.get("events") != 31
        or receipt.get("token_budget") != 512
        or receipt.get("exact_recovery") is not True
        or receipt.get("continuity_restored") is not True
        or receipt.get("forced_restart") is not False
        or receipt.get("claim") != "SESSION_CONTINUITY_INTERNALLY_VERIFIED"
    ):
        raise AssertionError(f"session continuity receipt drift: {receipt}")

    status = _json("run session-status", _run(repo, project, state, ["run", "session-status"]))
    if status.get("worker_alive") is not False or not isinstance(status.get("sessions"), list):
        raise AssertionError(f"session status drift: {status}")
    if not any(row.get("session_id") == "legacy-a" for row in status["sessions"]):
        raise AssertionError(f"legacy/product session storage no longer shared: {status}")

    bad_metadata = _error4(
        "run session-open malformed metadata",
        _run(repo, project, state, ["run", "session-open", "--session-id", "bad-meta", "--metadata", "[]"]),
    )
    bad_payload = _error4(
        "run session-append malformed payload",
        _run(repo, project, state, ["run", "session-append", "legacy-a", "decision", "[]"]),
    )

    return {
        "resume": {"continuity_restored": resumed["continuity_restored"], "events": resumed["verification"]["events"]},
        "append_sequence": appended["event"]["sequence"],
        "compact": {
            "ok": compacted["ok"], "events": compacted["events"],
            "exact_history_events": compacted["exact_history_events"],
            "root_summary_present": bool(compacted["root_summary_id"]),
        },
        "continuity": {
            "events": receipt["events"], "token_budget": receipt["token_budget"],
            "exact_recovery": receipt["exact_recovery"], "forced_restart": receipt["forced_restart"],
            "continuity_restored": receipt["continuity_restored"], "claim": receipt["claim"],
        },
        "status": {"worker_alive": status["worker_alive"], "session_count": len(status["sessions"])},
        "malformed_metadata": {"exit": 4, "error_type": bad_metadata["error"]["details"]["error"].split(":", 1)[0]},
        "malformed_payload": {"exit": 4, "error_type": bad_payload["error"]["details"]["error"].split(":", 1)[0]},
    }


def _rewrite_contract(repo: Path, project: Path, state: Path, fixture: dict[str, Any]) -> dict[str, Any]:
    engine = CommandRewriteEngine()
    manifest = engine.manifest()
    for key, expected in fixture["rewrite"].items():
        observed = manifest.get({
            "manifest_fail_closed": "fail_closed",
            "shell_composition_rewritten": "shell_composition_rewritten",
            "safe_wrappers": "safe_wrappers",
        }[key])
        if observed != expected:
            raise AssertionError(f"rewrite manifest drift for {key}: {observed!r}")

    rewritten = _json("run rewrite", _run(repo, project, state, ["run", "rewrite", "git", "status"]))
    if rewritten != {
        "changed": True,
        "original": ["git", "status"],
        "reason": "reduce machine-irrelevant output",
        "rewritten": ["git", "status", "--porcelain=v2", "--branch"],
        "rule": "git-status",
        "safe": True,
    }:
        raise AssertionError(f"rewrite happy-path drift: {rewritten}")

    preserved = _json("run rewrite explicit format", _run(repo, project, state, ["run", "rewrite", "git", "status", "--short"]))
    if preserved.get("changed") is not False or preserved.get("safe") is not True or preserved.get("reason") != "explicit user format preserved":
        raise AssertionError(f"rewrite explicit-format drift: {preserved}")

    unsafe = engine.rewrite("git status && rm -rf /")
    if unsafe.changed or unsafe.safe or unsafe.reason != "shell composition is not rewritten":
        raise AssertionError(f"rewrite fail-closed shell composition drift: {unsafe}")
    missing = _error4("run rewrite missing", _run(repo, project, state, ["run", "rewrite"]))

    return {
        "manifest": {
            "count": manifest["count"], "fail_closed": manifest["fail_closed"],
            "shell_composition_rewritten": manifest["shell_composition_rewritten"],
            "safe_wrappers": manifest["safe_wrappers"], "coverage_gate": manifest["coverage_gate"],
        },
        "git_status": rewritten,
        "explicit_format_preserved": {
            "changed": preserved["changed"], "safe": preserved["safe"], "reason": preserved["reason"],
        },
        "unsafe_shell": {"changed": unsafe.changed, "safe": unsafe.safe, "reason": unsafe.reason},
        "missing_command": {"exit": 4, "error_type": missing["error"]["details"]["error"].split(":", 1)[0]},
    }


def _compression_contract(repo: Path, project: Path, state: Path) -> dict[str, Any]:
    source = "api_key=TOPSECRET\n" + "\n".join(f"line-{index} failure at fixture.py:{index}" for index in range(1, 120))
    put = _json(
        "compress put",
        _run(repo, project, state, ["compress", "put", "--text", source, "--hint", "log", "--budget-bytes", "512"]),
    )
    compression_id = str(put.get("compression_id") or "")
    if not compression_id.startswith("ccr-") or put.get("reversible") is not True or put.get("loss_policy") != "exact-externalized":
        raise AssertionError(f"compression put drift: {put}")
    if put.get("original_bytes") != len(source.encode("utf-8")) or int(put.get("visible_bytes", 999999)) > 512:
        raise AssertionError(f"compression byte accounting drift: {put}")
    if "TOPSECRET" in str(put.get("visible_text")):
        raise AssertionError(f"compression visible secret redaction drift: {put}")

    described = _json("compress describe", _run(repo, project, state, ["compress", "describe", compression_id]))
    restored = _json("compress get", _run(repo, project, state, ["compress", "get", compression_id]))
    verified = _json("compress verify", _run(repo, project, state, ["compress", "verify", compression_id]))
    if described.get("compression_id") != compression_id or described.get("chunk_count") != len(described.get("chunks") or []):
        raise AssertionError(f"compression describe drift: {described}")
    chunk_indexes = [row.get("chunk_index") for row in described.get("chunks") or []]
    if chunk_indexes != list(range(len(chunk_indexes))):
        raise AssertionError(f"compression chunk ordering drift: {described}")
    if restored.get("text") != source or restored.get("bytes") != len(source.encode("utf-8")):
        raise AssertionError(f"compression exact restoration drift: {restored}")
    if verified != {"compression_id": compression_id, "ok": True}:
        raise AssertionError(f"compression verification drift: {verified}")

    bad_chunk = _error4("compress invalid chunk", _run(repo, project, state, ["compress", "get", compression_id, "--chunk", "999"]))
    missing = _error4("compress missing id", _run(repo, project, state, ["compress", "describe", "ccr-missing"]))
    malformed_json = _error4(
        "compress malformed json",
        _run(repo, project, state, ["compress", "put", "--text", "{", "--hint", "json"]),
    )

    db_path = state / "compression.sqlite3"
    with sqlite3.connect(db_path) as db:
        compression_rows = db.execute("SELECT COUNT(*) FROM compressions").fetchone()[0]
        chunk_rows = db.execute("SELECT COUNT(*) FROM compression_chunks").fetchone()[0]
        db.execute("UPDATE compressions SET receipt_hash='tampered' WHERE compression_id=?", (compression_id,))
        db.commit()
    tampered = _json("compress tampered verify", _run(repo, project, state, ["compress", "verify", compression_id]), 3)
    if tampered != {"compression_id": compression_id, "ok": False}:
        raise AssertionError(f"compression integrity failure drift: {tampered}")

    return {
        "put": {
            "content_type": put["content_type"], "original_bytes": put["original_bytes"],
            "visible_bytes": put["visible_bytes"], "reversible": put["reversible"],
            "loss_policy": put["loss_policy"], "compression_id_shape": compression_id.startswith("ccr-"),
            "secret_redacted": "TOPSECRET" not in put["visible_text"],
        },
        "describe": {"chunk_count": described["chunk_count"], "chunk_indexes": chunk_indexes},
        "restore_exact": restored["text"] == source,
        "verify_ok": verified["ok"],
        "tampered_verify": {"exit": 3, "ok": tampered["ok"]},
        "invalid_chunk": {"exit": 4, "error_type": bad_chunk["error"]["details"]["error"].split(":", 1)[0]},
        "missing_id": {"exit": 4, "error_type": missing["error"]["details"]["error"].split(":", 1)[0]},
        "malformed_json": {"exit": 4, "error_type": malformed_json["error"]["details"]["error"].split(":", 1)[0]},
        "sqlite_counts": {"compressions": compression_rows, "chunks": chunk_rows},
    }


def _fabric_contract(repo: Path, project: Path, state: Path) -> dict[str, Any]:
    stdout = "api_key=FABRICSECRET\n" + "\n".join(
        [*(f"PASSED test_{index}" for index in range(80)), "FAILED test_bad", "E AssertionError: fixture failed at test_file.py:42"]
    )
    argv = ["fabric", "compact", "--stdout", stdout, "--budget-bytes", "512", "--", "pytest"]
    first = _json("fabric compact first", _run(repo, project, state, argv))
    second = _json("fabric compact deterministic", _run(repo, project, state, argv))
    if first != second:
        raise AssertionError(f"fabric compaction nondeterminism drift: {first} / {second}")
    if first.get("family") != "test" or int(first.get("visible_bytes", 999999)) > 512 or first.get("exact_required") is not True:
        raise AssertionError(f"fabric compaction accounting drift: {first}")
    if "FABRICSECRET" in str(first.get("visible_text")) or not first.get("secret_types"):
        raise AssertionError(f"fabric security redaction drift: {first}")
    invalid_budget = _error4(
        "fabric compact invalid budget",
        _run(repo, project, state, ["fabric", "compact", "--stdout", "x", "--budget-bytes", "128", "--", "pytest"]),
    )

    insight_path = state / "competitive-fabric.sqlite3"
    with sqlite3.connect(insight_path) as db:
        compact_events = db.execute("SELECT COUNT(*) FROM fabric_events WHERE event_type='compact'").fetchone()[0]
    if compact_events != 2:
        raise AssertionError(f"fabric insight side-effect drift: compact_events={compact_events}")

    return {
        "family": first["family"], "visible_bytes": first["visible_bytes"],
        "original_bytes": first["original_bytes"], "savings_ratio": first["savings_ratio"],
        "exact_required": first["exact_required"], "secret_types": first["secret_types"],
        "injection_risk": first["injection_risk"], "retained_error_lines": first["retained_error_lines"],
        "compactor": first["compactor"], "deterministic": True,
        "invalid_budget": {"exit": 4, "error_type": invalid_budget["error"]["details"]["error"].split(":", 1)[0]},
        "insight_compact_events": compact_events,
    }


def certify(repo: Path) -> dict[str, Any]:
    fixture_path = repo / FIXTURE_RELATIVE
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory(prefix="syntavra-python-context-compaction-") as directory:
        root = Path(directory)
        project = root / "project"
        state = root / "state"
        project.mkdir()
        state.mkdir()
        (project / ".git").mkdir()
        result = {
            "routes": _routes(fixture),
            "context": _context_contract(repo, project, state, root, fixture),
            "legacy_session": _legacy_session_contract(repo, project, state, root),
            "continuity": _continuity_contract(repo, project, state),
            "rewrite": _rewrite_contract(repo, project, state, fixture),
            "compression": _compression_contract(repo, project, state),
            "fabric": _fabric_contract(repo, project, state),
        }
    return {
        "ok": True,
        "schema_version": 1,
        "family": "context-compaction",
        "engine": "python",
        "exact_head": _head(repo),
        "fixture": str(FIXTURE_RELATIVE).replace("\\", "/"),
        "fixture_sha256": hashlib.sha256(fixture_path.read_bytes()).hexdigest(),
        **result,
        "exit_policy": fixture["exit_policy"],
        "ordering": fixture["ordering"],
        "network_boundary": fixture["network_boundary"],
        "ownership_notes": fixture["ownership_notes"],
        "nondeterministic_fields": [
            "session/event/checkpoint creation timestamps",
            "session event hashes where timestamps contribute",
            "generated fork/merge/checkpoint session identifiers",
            "session product wall_time_ms and analytics timestamps",
            "compression_id/evidence handles and compression created_at",
            "fabric analytics latency_ms and created_at"
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Certify Python context/compaction reference behavior")
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
            "family": "context-compaction",
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
