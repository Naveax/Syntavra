#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from syntavra_runtime.evidence import EvidenceStore
from syntavra_runtime.memory import PersistentMemory
from syntavra_runtime.zero_token_memory import ZeroTokenMemoryEngine

CONTRACT_RELATIVE = Path("contracts/python/u5-p0-01-zero-token-memory-engine-v1.json")
FRONTIER_RELATIVE = Path("contracts/python/ultra-low-token-frontier-v5.json")
PLAN_RELATIVE = Path("docs/plans/SYNTAVRA_ULTRA_LOW_TOKEN_FRONTIER_V5.md")
WORKFLOW_RELATIVE = Path(".github/workflows/u5-p0-01-zero-token-memory-engine.yml")
TEST_RELATIVE = Path("tests/runtime/test_zero_token_memory_engine.py")

EXPECTED_OPERATIONS = ["ingest", "dedup", "search", "link", "ttl_maintenance", "reindex"]
EXPECTED_MAINTENANCE_ORDER = [
    "preflight_evidence_metadata",
    "purge_expired_authoritative_memory",
    "release_evidence_references_for_deleted_records",
    "reference_aware_evidence_gc",
    "optional_local_reindex",
]


def _require(value: bool, message: str) -> None:
    if not value:
        raise AssertionError(message)


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    _require(isinstance(value, dict), f"expected JSON object: {path}")
    return value


def _head(repo: Path) -> str:
    proc = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    return proc.stdout.strip() if proc.returncode == 0 else ""


def _assert_zero(receipt: dict[str, Any], operation: str) -> None:
    _require(receipt.get("operation") == operation, f"unexpected operation receipt: {receipt}")
    for key in ("provider_calls", "provider_input_tokens", "provider_output_tokens", "provider_tokens"):
        _require(receipt.get(key) == 0, f"{operation} hid provider work in {key}")


def _runtime_smoke() -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        memory = PersistentMemory(root / "memory.sqlite3", project_id="cert-project", user_id="cert-user")
        evidence = EvidenceStore(root / "evidence", project_id="cert-project")
        engine = ZeroTokenMemoryEngine(memory, evidence)
        now = time.time()

        first = engine.ingest("note", "  exact authority  ", tags=("b", "a", "a"))
        duplicate = engine.ingest("note", "  exact authority  ", tags=("a", "b"))
        distinct = engine.ingest("note", "exact authority", tags=("a", "b"))
        for receipt in (first, duplicate, distinct):
            _assert_zero(receipt, "ingest")
        _require(first["record"]["text"] == "  exact authority  ", "raw exact memory was normalized")
        _require(first["record"]["memory_id"] == duplicate["record"]["memory_id"], "exact dedup is not idempotent")
        _require(first["record"]["memory_id"] != distinct["record"]["memory_id"], "raw-distinct memory collapsed")

        link = engine.link(first["record"]["memory_id"], "related-to", distinct["record"]["memory_id"], weight=2.0)
        _assert_zero(link, "link")
        search = engine.search("exact authority")
        _assert_zero(search, "search")
        _require(bool(search["results"]), "deterministic local retrieval returned no result")

        shared_a = engine.ingest(
            "event",
            "expired evidence owner",
            expires_at=now - 1,
            evidence_data=b"shared exact evidence",
        )
        shared_b = engine.ingest(
            "event",
            "live evidence owner",
            expires_at=now + 20,
            evidence_data=b"shared exact evidence",
        )
        _require(shared_a["evidence_handle"] == shared_b["evidence_handle"], "evidence content dedup failed")
        stats = evidence.stats()
        _require(stats["objects"] == 1, "shared evidence was stored more than once")
        _require(stats["references"] == 2, "shared evidence reference count is not two")

        maintenance_a = engine.maintain(now=now)
        _assert_zero(maintenance_a, "maintain")
        _require(maintenance_a["memory_deleted"] == 1, "expired memory was not purged")
        _require(maintenance_a["evidence_references_released"] == 1, "expired memory evidence ref was not released")
        _require(maintenance_a["evidence_gc"]["deleted"] == 0, "referenced evidence was collected")
        stats = evidence.stats()
        _require(stats["objects"] == 1 and stats["references"] == 1, "live evidence reference was not preserved")

        maintenance_b = engine.maintain(now=now + 30)
        _assert_zero(maintenance_b, "maintain")
        _require(maintenance_b["memory_deleted"] == 1, "second expired memory was not purged")
        _require(maintenance_b["evidence_references_released"] == 1, "second evidence ref was not released")
        _require(maintenance_b["evidence_gc"]["deleted"] == 1, "unreferenced expired evidence was not collected")
        stats = evidence.stats()
        _require(stats["objects"] == 0 and stats["references"] == 0, "evidence GC left stale objects/references")

        predecessor = memory.add("decision", "authoritative predecessor")
        replacement = memory.add("decision", "expired replacement", expires_at=now - 1)
        memory.supersede(predecessor.memory_id, replacement.memory_id)
        maintenance_c = engine.maintain(now=now)
        _assert_zero(maintenance_c, "maintain")
        _require(maintenance_c["reactivated_superseded"] == 1, "expired supersession target did not reactivate predecessor")
        restored = engine.search("authoritative predecessor")
        _assert_zero(restored, "search")
        _require(restored["results"][0]["memory_id"] == predecessor.memory_id, "reactivated predecessor is not retrievable")
        _require(restored["results"][0]["superseded_by"] is None, "predecessor still points at deleted target")

        reindex = engine.rebuild_index()
        _assert_zero(reindex, "rebuild_index")
        _require(reindex["reindex"]["ok"] is True, "local memory reindex failed")

        mismatch_rejected = False
        try:
            ZeroTokenMemoryEngine(memory, EvidenceStore(root / "other-evidence", project_id="other-project"))
        except ValueError:
            mismatch_rejected = True
        _require(mismatch_rejected, "project scope mismatch was not rejected")

        return {
            "raw_exact_authority": True,
            "exact_dedup_idempotent": True,
            "raw_distinct_preserved": True,
            "deterministic_local_retrieval": True,
            "graph_link_local": True,
            "content_addressed_evidence_dedup": True,
            "reference_aware_ttl_gc": True,
            "expired_supersession_reactivation": True,
            "local_reindex": True,
            "project_scope_enforced": True,
            "provider_calls": 0,
            "provider_tokens": 0,
        }


def _validate_enforcement(repo: Path) -> dict[str, str]:
    for relative in (WORKFLOW_RELATIVE, TEST_RELATIVE):
        _require((repo / relative).is_file(), f"missing U5-P0-01 enforcement surface: {relative.as_posix()}")
    workflow = (repo / WORKFLOW_RELATIVE).read_text(encoding="utf-8")
    _require("group: u5-p0-01-zero-token-memory-${{ github.event.pull_request.number || github.ref }}" in workflow, "workflow concurrency is not PR/ref scoped")
    _require("tests.runtime.test_zero_token_memory_engine" in workflow, "workflow lost dedicated runtime regressions")
    _require("tools/validate_u5_p0_01_zero_token_memory_engine.py" in workflow, "workflow lost U5-P0-01 certifier")
    _require("actions/checkout@11d5960a326750d5838078e36cf38b85af677262" in workflow, "checkout action pin drift")
    _require("actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065" in workflow, "setup-python action pin drift")
    _require("actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02" in workflow, "upload-artifact action pin drift")
    return {"workflow": WORKFLOW_RELATIVE.as_posix(), "tests": TEST_RELATIVE.as_posix()}


def certify(repo: Path) -> dict[str, Any]:
    repo = repo.resolve()
    _require(repo == ROOT, f"U5-P0-01 certifier must run against its own checkout: {repo} != {ROOT}")
    contract = _read_json(repo / CONTRACT_RELATIVE)
    frontier = _read_json(repo / FRONTIER_RELATIVE)
    plan = (repo / PLAN_RELATIVE).read_text(encoding="utf-8")

    _require(contract.get("schema_version") == "syntavra.u5_p0_01.zero_token_memory_engine.v1", "U5-P0-01 schema drift")
    _require(contract.get("mechanism_id") == "U5-P0-01", "U5-P0-01 mechanism id drift")
    _require(contract.get("wave") == "TE-U30", "U5-P0-01 wave drift")
    _require(contract.get("classification") == "HARDEN_UNIFY", "U5-P0-01 classification drift")
    _require(contract.get("required_operations") == EXPECTED_OPERATIONS, "U5-P0-01 operation surface drift")
    _require(contract.get("maintenance_order") == EXPECTED_MAINTENANCE_ORDER, "U5-P0-01 maintenance ordering drift")

    token_policy = contract.get("token_policy") or {}
    for key in ("default_provider_calls", "default_provider_input_tokens", "default_provider_output_tokens", "default_provider_tokens"):
        _require(token_policy.get(key) == 0, f"zero-default token policy drift: {key}")
    _require(token_policy.get("learned_or_paid_maintenance_default") is False, "paid/learned maintenance became default")

    authority = contract.get("authority") or {}
    _require(authority.get("raw_exact_memory") == "PersistentMemory", "raw memory authority drift")
    _require(authority.get("exact_evidence") == "EvidenceStore", "exact evidence authority drift")
    _require(authority.get("summaries_are_authority") is False, "summary incorrectly became authority")
    _require(authority.get("raw_distinct_values_remain_distinct") is True, "raw-distinct identity invariant disabled")

    mechanisms = {row.get("id"): row for row in frontier.get("mechanisms", []) if isinstance(row, dict)}
    mechanism = mechanisms.get("U5-P0-01") or {}
    _require(mechanism.get("name") == "Zero-Token Memory Engine", "frontier no longer names U5-P0-01 zero-token memory")
    _require(mechanism.get("classification") == "HARDEN_UNIFY", "frontier U5-P0-01 classification drift")
    hard = set(frontier.get("hard_invariants") or [])
    _require("RAW_EXACT_MEMORY_REMAINS_AUTHORITY" in hard, "frontier lost raw exact authority invariant")
    _require("MEMORY_MAINTENANCE_PROVIDER_TOKENS_DEFAULT_TO_ZERO" in hard, "frontier lost zero-default maintenance invariant")
    _require("### U5-P0-01 Zero-Token Memory Engine" in plan, "V5 plan lost U5-P0-01 section")

    runtime = _runtime_smoke()
    enforcement = _validate_enforcement(repo)
    exact_head = _head(repo)
    _require(bool(exact_head), "unable to resolve exact HEAD")
    return {
        "ok": True,
        "schema_version": contract["schema_version"],
        "family": contract["family"],
        "claim": "U5_P0_01_ZERO_TOKEN_MEMORY_ENGINE",
        "mechanism_id": "U5-P0-01",
        "wave": "TE-U30",
        "exact_head": exact_head,
        "admission_ready": True,
        "provider_calls": 0,
        "provider_tokens": 0,
        "runtime": runtime,
        "enforcement": enforcement,
        "claim_boundary": contract["claim_boundary"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate Syntavra U5-P0-01 Zero-Token Memory Engine.")
    parser.add_argument("--repo", default=".")
    parser.add_argument("--out")
    args = parser.parse_args()
    try:
        report = certify(Path(args.repo))
    except Exception as exc:  # pragma: no cover
        report = {
            "ok": False,
            "claim": "U5_P0_01_ZERO_TOKEN_MEMORY_ENGINE",
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
