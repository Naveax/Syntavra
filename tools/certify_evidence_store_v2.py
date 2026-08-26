#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import sys
import tempfile
import traceback
from dataclasses import fields
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from syntavra_runtime.evidence_store import EvidenceStoreV2
from syntavra_runtime.runtime_evidence import EvidenceEdge, EvidenceNode, RuntimeEvidenceGraph
from syntavra_runtime.universal_context_item import (
    ContextFreshness,
    ContextProvenance,
    ContextTrust,
    RecoveryHandle,
    UniversalContextItem,
)
from tools.certify_python_capability_completeness import certify as certify_completeness
from tools.certify_rust_feature_freeze_guard import certify as certify_rust_freeze
from tools.certify_universal_context_item import certify as certify_universal_context_item

CONTRACT_RELATIVE = Path("contracts/python/evidence-store-v2.json")
REGISTRY_RELATIVE = Path("contracts/python/capability-completeness-registry-v1.json")
WORKFLOW_RELATIVE = Path(".github/workflows/evidence-store-v2.yml")
RELEASE_GATE_RELATIVE = Path(".github/workflows/release-main-merge-gate.yml")
PIN_POLICY_RELATIVE = Path("tests/runtime/test_release_action_pins.py")
VALIDATOR_RELATIVE = Path("tools/validate.py")

EXPECTED_NODE_FIELDS = ["node_id", "kind", "label", "source", "confidence", "repository_commit", "metadata"]
EXPECTED_EDGE_FIELDS = ["source", "target", "relation", "evidence", "confidence", "repository_commit", "observed_at", "metadata"]
EXPECTED_JOURNAL_ACTIONS = [
    "put-new",
    "observe-existing",
    "evaluation-update",
    "pin",
    "unpin",
    "retention-update",
    "prune-expired",
]


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AssertionError(f"expected JSON object: {path}")
    return value


def _require(value: bool, message: str) -> None:
    if not value:
        raise AssertionError(message)


def _head(repo: Path) -> str:
    proc = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    return proc.stdout.strip() if proc.returncode == 0 else ""


def _runtime_smoke() -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "evidence.sqlite3"
        store = EvidenceStoreV2(path)
        parent = "sha256:" + "b" * 64
        item = UniversalContextItem.build(
            kind="certifier-evidence",
            representation="exact",
            content={"value": 1},
            provenance=ContextProvenance(
                source="evidence-store-certifier",
                repository_commit="fixture",
                parent_item_ids=(parent,),
            ),
            trust=ContextTrust(level="observed", confidence=0.5),
            freshness=ContextFreshness(state="fresh"),
            recovery=(RecoveryHandle(kind="artifact", locator={"artifact": "fixture"}, integrity="a" * 64),),
        )
        first = store.put(item, observed_at="2026-08-18T00:00:00+00:00")
        _require(first["action"] == "put-new", "Evidence Store did not create exact new-item event")
        loaded = store.require(item.item_id)
        _require(loaded == item and loaded.verify_integrity(), "Evidence Store roundtrip failed")
        _require(store.lineage(item.item_id)[0]["item_id"] == parent, "Evidence Store lineage lost parent")
        _require(store.verify_item(item.item_id)["ok"] is True, "Evidence Store item integrity proof failed")

        updated = UniversalContextItem.build(
            kind="certifier-evidence",
            representation="exact",
            content={"value": 1},
            provenance=ContextProvenance(
                source="evidence-store-certifier",
                repository_commit="fixture",
                parent_item_ids=(parent,),
            ),
            trust=ContextTrust(level="verified", confidence=1.0, reasons=("certified",)),
            freshness=ContextFreshness(state="stale"),
            recovery=(RecoveryHandle(kind="artifact", locator={"artifact": "fixture"}, integrity="a" * 64),),
        )
        _require(updated.item_id == item.item_id, "evaluation layer unexpectedly changed stable evidence identity")
        second = store.put(updated, observed_at="2026-08-18T01:00:00+00:00")
        _require(second["action"] == "evaluation-update", "trust/freshness update was not journaled")
        _require(store.verify_journal()["ok"] is True, "Evidence Store journal chain failed")

        secret_item = UniversalContextItem.build(
            kind="secret-fixture",
            representation="exact",
            content={"token": "sk-proj-" + "A" * 32},
            provenance=ContextProvenance(source="evidence-store-certifier"),
        )
        secret_rejected = False
        try:
            store.put(secret_item)
        except ValueError:
            secret_rejected = True
        _require(secret_rejected, "secret-bearing evidence was not rejected")

        expired = UniversalContextItem.build(
            kind="retention-fixture",
            representation="exact",
            content={"id": "expired"},
            provenance=ContextProvenance(source="evidence-store-certifier"),
        )
        store.put(expired, expires_at="2026-08-17T00:00:00+00:00")
        pruned = store.prune_expired(before="2026-08-18T00:00:00+00:00")
        _require(pruned["removed"] == [expired.item_id], "expired unpinned evidence was not pruned exactly")
        _require(store.get(expired.item_id) is None, "pruned evidence remains readable")
        _require(store.verify_journal()["ok"] is True, "journal chain failed after prune tombstone")

        with sqlite3.connect(path) as db:
            db.execute("UPDATE evidence_journal SET actor = 'tampered' WHERE sequence = 1")
            db.commit()
        _require(store.verify_journal()["ok"] is False, "journal tamper was not detected")

        stats = store.stats()
        return {
            "content_addressed_roundtrip": True,
            "evaluation_update_journaled": True,
            "lineage": True,
            "recovery_integrity": True,
            "secret_reject_default": True,
            "retention_gc": True,
            "journal_chain": True,
            "journal_tamper_detected": True,
            "items_after_smoke": stats["items"],
        }


def _validate_compatibility() -> None:
    _require([field.name for field in fields(EvidenceNode)] == EXPECTED_NODE_FIELDS, "EvidenceNode schema changed")
    _require([field.name for field in fields(EvidenceEdge)] == EXPECTED_EDGE_FIELDS, "EvidenceEdge schema changed")
    _require(hasattr(RuntimeEvidenceGraph, "put_node"), "RuntimeEvidenceGraph.put_node removed")
    _require(hasattr(RuntimeEvidenceGraph, "put_edge"), "RuntimeEvidenceGraph.put_edge removed")
    _require(hasattr(RuntimeEvidenceGraph, "neighbors"), "RuntimeEvidenceGraph.neighbors removed")


def _validate_enforcement(repo: Path) -> dict[str, str]:
    for relative in (WORKFLOW_RELATIVE, RELEASE_GATE_RELATIVE, PIN_POLICY_RELATIVE, VALIDATOR_RELATIVE):
        _require((repo / relative).is_file(), f"missing Evidence Store enforcement surface: {relative.as_posix()}")
    workflow = (repo / WORKFLOW_RELATIVE).read_text(encoding="utf-8")
    _require("group: evidence-store-v2-${{ github.event.pull_request.number || github.ref }}" in workflow, "Evidence Store concurrency is not PR/ref scoped")
    _require("tests.runtime.test_evidence_store_v2" in workflow, "Evidence Store workflow lost regression suite")
    _require("tools/certify_evidence_store_v2.py" in workflow, "Evidence Store workflow lost certifier")
    _require("actions/checkout@11d5960a326750d5838078e36cf38b85af677262" in workflow, "Evidence Store checkout pin drift")
    _require("actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065" in workflow, "Evidence Store setup-python pin drift")

    release_gate = (repo / RELEASE_GATE_RELATIVE).read_text(encoding="utf-8")
    _require("tests.runtime.test_evidence_store_v2" in release_gate, "release-main gate lost Evidence Store regression")
    _require("tools/certify_evidence_store_v2.py" in release_gate, "release-main gate lost Evidence Store certifier")

    pin_policy = (repo / PIN_POLICY_RELATIVE).read_text(encoding="utf-8")
    _require('".github/workflows/evidence-store-v2.yml"' in pin_policy, "immutable action policy does not cover Evidence Store workflow")

    validator = (repo / VALIDATOR_RELATIVE).read_text(encoding="utf-8")
    _require('("evidence_store_v2", evidence_ok, evidence_detail)' in validator, "repository validator lost Evidence Store check")
    return {
        "exact_head_workflow": WORKFLOW_RELATIVE.as_posix(),
        "release_main_gate": RELEASE_GATE_RELATIVE.as_posix(),
        "immutable_action_pin_policy": PIN_POLICY_RELATIVE.as_posix(),
        "repository_validator": VALIDATOR_RELATIVE.as_posix(),
    }


def certify(repo: Path) -> dict[str, Any]:
    repo = repo.resolve()
    _require(repo == ROOT, f"Evidence Store certifier must run against its own checkout: {repo} != {ROOT}")
    contract = _read_json(repo / CONTRACT_RELATIVE)
    _require(contract.get("schema_version") == 2, "Evidence Store schema drift")
    _require(contract.get("family") == "evidence-store", "Evidence Store family drift")
    _require(contract.get("phase") == "python-first", "Evidence Store phase drift")
    _require(contract.get("claim") == "EVIDENCE_STORE_V2", "Evidence Store claim drift")
    _require(contract.get("strict") is True, "Evidence Store must remain strict")
    _require(contract.get("runtime") == "syntavra_runtime/evidence_store.py", "Evidence Store runtime path drift")
    _require(contract.get("canonical_item") == "syntavra_runtime/universal_context_item.py:UniversalContextItem", "Evidence Store canonical item drift")

    storage = contract.get("storage") or {}
    for key in ("wal", "synchronous_full", "content_addressed_by_universal_item_id", "stable_evidence_immutable", "trust_freshness_evaluation_mutable"):
        _require(storage.get(key) is True, f"Evidence Store storage policy disabled: {key}")
    _require(storage.get("backend") == "sqlite", "Evidence Store backend drift")

    journal = contract.get("journal") or {}
    _require(journal.get("actions") == EXPECTED_JOURNAL_ACTIONS, "Evidence Store journal action vocabulary drift")
    for key in ("append_only", "hash_chained", "previous_hash_required_after_first_event", "tamper_detection_required"):
        _require(journal.get(key) is True, f"Evidence Store journal policy disabled: {key}")

    security = contract.get("security") or {}
    _require(security.get("default_secret_policy") == "reject", "Evidence Store default secret policy drift")
    _require(security.get("secret_detector") == "syntavra_runtime.secret_redaction.SecretRedactor", "Evidence Store secret detector drift")
    _require(security.get("silent_redaction_forbidden") is True, "Evidence Store allows silent redaction")
    _require(security.get("secret_scan_receipt_journaled") is True, "Evidence Store secret scan receipt no longer journaled")

    retention = contract.get("retention") or {}
    for key in ("explicit_expiry", "pinned_items_survive_gc", "only_expired_unpinned_items_are_prunable", "prune_is_journaled_before_delete"):
        _require(retention.get(key) is True, f"Evidence Store retention policy disabled: {key}")

    completeness = certify_completeness(repo)
    _require(completeness.get("ok") is True, "capability completeness is not valid")
    _require(isinstance(completeness.get("python_complete_ready"), bool), "Python COMPLETE state must be boolean")
    _require(
        completeness.get("python_complete_ready") is completeness.get("rust_resume_allowed"),
        "Python COMPLETE/Rust resume state disagreement",
    )

    universal = certify_universal_context_item(repo)
    _require(universal.get("ok") is True, "Universal Context Item is not certified")
    _require(universal.get("rust_resume_allowed") is False, "Universal Context Item cert unexpectedly resumes Rust")

    rust_freeze = certify_rust_freeze(repo)
    _require(rust_freeze.get("ok") is True, "Rust feature freeze is not certified")
    _require((rust_freeze.get("rust") or {}).get("production_promoted") == 174, "Rust production authority drift")

    registry = _read_json(repo / REGISTRY_RELATIVE)
    by_id = {row["id"]: row for row in registry.get("capabilities", []) if isinstance(row, dict) and isinstance(row.get("id"), str)}
    _require((by_id.get("universal_context_item_v1") or {}).get("state") == "certified", "Universal Context Item must be certified before Evidence Store admission")
    evidence_state = (by_id.get("evidence_store_v2") or {}).get("state")
    _require(evidence_state in {"implemented", "verified", "certified"}, "Evidence Store registry state is invalid")
    if evidence_state != "certified":
        _require(completeness.get("current_milestone") == "evidence_store_v2", "registry has not advanced to Evidence Store v2")

    _validate_compatibility()
    runtime = _runtime_smoke()
    enforcement = _validate_enforcement(repo)
    exact_head = _head(repo)
    _require(bool(exact_head), "unable to resolve exact HEAD")
    return {
        "ok": True,
        "schema_version": 2,
        "family": "evidence-store",
        "claim": "EVIDENCE_STORE_V2",
        "exact_head": exact_head,
        "admission_ready": True,
        "python_complete_ready": False,
        "rust_resume_allowed": False,
        "runtime": runtime,
        "enforcement": enforcement,
        "rust": {
            "implementation_coverage": 245,
            "production_promoted": 174,
            "remaining_parity_promotion": 71,
            "feature_development_frozen": True,
        },
        "claim_boundary": "This certificate admits Evidence Store v2. It does not claim Typed Context Object Store, Python COMPLETE, or Rust parity/promotion.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Certify Syntavra Evidence Store v2.")
    parser.add_argument("--repo", default=".")
    parser.add_argument("--out")
    args = parser.parse_args()
    try:
        report = certify(Path(args.repo))
    except Exception as exc:  # pragma: no cover
        report = {"ok": False, "schema_version": 2, "family": "evidence-store", "error": f"{type(exc).__name__}: {exc}", "traceback": traceback.format_exc()}
    payload = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.out:
        output = Path(args.out)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(payload, encoding="utf-8")
    print(payload, end="")
    return 0 if report.get("ok") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
