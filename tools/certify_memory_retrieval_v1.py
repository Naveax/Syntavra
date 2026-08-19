#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from syntavra_runtime.memory_intelligence import MemoryIntelligenceStore, MemoryRetrievalV1, MemoryScope
from syntavra_runtime.session_memory import SessionMemory

CONTRACT = Path("contracts/python/memory-retrieval-v1.json")
WORKFLOW = Path(".github/workflows/memory-retrieval.yml")
RELEASE_GATE = Path(".github/workflows/release-main-merge-gate.yml")
PIN_POLICY = Path("tests/runtime/test_release_action_pins.py")


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _head(repo: Path) -> str:
    proc = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    return proc.stdout.strip() if proc.returncode == 0 else ""


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    _require(isinstance(value, dict), f"expected JSON object: {path}")
    return value


def certify(repo: Path) -> dict[str, Any]:
    repo = repo.resolve()
    _require(repo == ROOT, f"memory certifier must run against its own checkout: {repo} != {ROOT}")

    contract = _read_json(repo / CONTRACT)
    _require(contract.get("schema_version") == 1, "memory contract schema drift")
    _require(contract.get("family") == "memory-retrieval", "memory contract family drift")
    _require(contract.get("claim") == "MEMORY_RETRIEVAL_V1", "memory contract claim drift")
    _require(contract.get("phase") == "python-first", "memory contract phase drift")

    storage = contract.get("storage") or {}
    _require(storage.get("reuse_existing_memory_sqlite") is True, "existing memory store reuse disabled")
    _require(storage.get("parallel_database_forbidden") is True, "parallel memory database unexpectedly allowed")
    _require(storage.get("forgetting_deletes_exact_payload") is False, "forgetting may not delete exact payload")

    lifecycle = contract.get("lifecycle") or {}
    for key in (
        "provenance_required",
        "conflicts_preserved",
        "supersession_explicit",
        "consolidation_parent_lineage",
        "forgetting_is_logical_not_destructive",
        "observation_timeline",
        "cross_agent_handoff_receipt",
    ):
        _require(lifecycle.get(key) is True, f"memory lifecycle guarantee disabled: {key}")

    retrieval = contract.get("retrieval") or {}
    for key in (
        "hybrid_bm25_vector",
        "deterministic_query_expansion",
        "importance_confidence_validity_recency_rerank",
        "progressive_preview_then_exact_recovery",
        "session_event_and_summary_retrieval",
        "deterministic_receipt",
    ):
        _require(retrieval.get(key) is True, f"memory retrieval guarantee disabled: {key}")

    for relative in (WORKFLOW, RELEASE_GATE, PIN_POLICY):
        _require((repo / relative).is_file(), f"missing memory enforcement surface: {relative}")

    workflow = (repo / WORKFLOW).read_text(encoding="utf-8")
    _require("tests.runtime.test_memory_retrieval_v1" in workflow, "memory workflow lost regression suite")
    _require("tools/certify_memory_retrieval_v1.py" in workflow, "memory workflow lost certifier")
    _require(
        "actions/checkout@11d5960a326750d5838078e36cf38b85af677262" in workflow,
        "memory workflow checkout pin drift",
    )
    _require(
        "actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065" in workflow,
        "memory workflow setup-python pin drift",
    )
    _require(
        "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02" in workflow,
        "memory workflow upload pin drift",
    )

    release_gate = (repo / RELEASE_GATE).read_text(encoding="utf-8")
    _require("tests.runtime.test_memory_retrieval_v1" in release_gate, "release-main gate lost memory regression")
    _require("tools/certify_memory_retrieval_v1.py" in release_gate, "release-main gate lost memory certifier")
    pin_policy = (repo / PIN_POLICY).read_text(encoding="utf-8")
    _require('".github/workflows/memory-retrieval.yml"' in pin_policy, "immutable action policy lost memory workflow")

    with tempfile.TemporaryDirectory(prefix="syntavra-memory-cert-") as td:
        root = Path(td)
        store = MemoryIntelligenceStore(root / "memory.sqlite3")
        sessions = SessionMemory(root / "session.sqlite3", project_id="syntavra-cert")
        sessions.open("session-cert")
        sessions.append("session-cert", "decision", {"decision": "preserve exact evidence", "importance": 1.0})
        engine = MemoryRetrievalV1(store, session_memory=sessions)
        scope = MemoryScope(project_id="syntavra-cert", user_id="certifier", session_id="session-cert")

        first = engine.remember(
            "Preserve exact evidence across memory retrieval",
            kind="procedural",
            scope=scope,
            provenance_refs=("evidence:certifier",),
        )
        second = engine.remember(
            "Preserve exact evidence and deterministic receipts",
            kind="semantic",
            scope=scope,
            provenance_refs=("evidence:certifier-2",),
            conflicts_with=(first["memory_id"],),
        )
        retrieval_report = engine.retrieve("exact evidence", scope=scope)
        _require(retrieval_report.get("ok") is True, "memory retrieval smoke failed")
        _require(retrieval_report.get("exact_recovery") is True, "memory exact recovery smoke failed")
        _require(bool(retrieval_report.get("receipt_hash")), "memory retrieval receipt missing")
        _require(bool((retrieval_report.get("session") or {}).get("exact_recovery")), "session exact recovery missing")
        _require(engine.recover(first["memory_id"]).get("exact_recovery") is True, "memory recovery failed")
        _require(second["memory_id"] in engine.recover(first["memory_id"])["conflicts_with"], "conflict link lost")

        status = engine.status()
        _require(status.get("memory_intelligence_store_reused") is True, "memory store authority not reused")
        _require(status.get("session_memory_authority_reused") is True, "session memory authority not reused")
        _require(status.get("new_persistent_database") is False, "parallel memory database introduced")
        _require(status.get("exact_recovery") is True, "runtime exact recovery disabled")

    boundary = contract.get("claim_boundary") or {}
    _require(boundary.get("python_complete") is False, "memory milestone cannot claim Python COMPLETE")
    _require(boundary.get("rust_resume_allowed") is False, "memory milestone cannot resume Rust")
    _require(boundary.get("rust_production_promoted") == 174, "Rust promotion baseline drift")
    _require(boundary.get("rust_remaining_parity_promotion") == 71, "Rust remaining baseline drift")
    _require(boundary.get("external_superiority_claimed") is False, "external superiority cannot be self-certified")

    exact_head = _head(repo)
    _require(len(exact_head) == 40, "unable to resolve exact git head")
    return {
        "ok": True,
        "schema_version": 1,
        "claim": "MEMORY_RETRIEVAL_V1",
        "exact_head": exact_head,
        "admission_ready": True,
        "python_complete_ready": False,
        "rust_resume_allowed": False,
        "runtime": MemoryRetrievalV1.status(),
        "rust": {
            "production_promoted": 174,
            "remaining_parity_promotion": 71,
            "feature_development_frozen": True,
        },
        "claim_boundary": (
            "This certificate proves the Python Memory Retrieval v1 runtime and exact-head enforcement surfaces. "
            "It does not claim Python COMPLETE, external superiority, or Rust Remaining-71 promotion."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Certify Syntavra Memory Retrieval v1")
    parser.add_argument("--repo", default=".", help="Repository root")
    parser.add_argument("--out", help="Optional JSON output path")
    args = parser.parse_args()
    try:
        report = certify(Path(args.repo))
    except Exception as exc:  # pragma: no cover
        report = {
            "ok": False,
            "schema_version": 1,
            "claim": "MEMORY_RETRIEVAL_V1",
            "error": f"{type(exc).__name__}: {exc}",
            "traceback": traceback.format_exc(),
        }
    payload = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.out:
        output = Path(args.out)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(payload, encoding="utf-8")
    print(payload, end="")
    return 0 if report.get("ok") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
