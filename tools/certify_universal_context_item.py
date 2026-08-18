#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import traceback
from dataclasses import fields
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from syntavra_runtime.context_pack import ContextPackItem
from syntavra_runtime.runtime_evidence import EvidenceEdge, EvidenceNode
from syntavra_runtime.universal_context_item import (
    ContextFreshness,
    ContextProvenance,
    ContextTrust,
    RecoveryHandle,
    UniversalContextItem,
)
from tools.certify_python_capability_completeness import certify as certify_completeness
from tools.certify_rust_feature_freeze_guard import certify as certify_rust_freeze

CONTRACT_RELATIVE = Path("contracts/python/universal-context-item-v1.json")
REGISTRY_RELATIVE = Path("contracts/python/capability-completeness-registry-v1.json")
WORKFLOW_RELATIVE = Path(".github/workflows/universal-context-item.yml")
RELEASE_GATE_RELATIVE = Path(".github/workflows/release-main-merge-gate.yml")
PIN_POLICY_RELATIVE = Path("tests/runtime/test_release_action_pins.py")

EXPECTED_REPRESENTATIONS = ["exact", "structural", "semantic", "bounded-preview"]
EXPECTED_TRUST = ["unknown", "untrusted", "observed", "verified"]
EXPECTED_FRESHNESS = ["unknown", "fresh", "stale", "expired"]
EXPECTED_RECOVERY = ["file-range", "evidence-node", "evidence-edge", "artifact", "memory", "tool-result"]
EXPECTED_CONTEXT_PACK_FIELDS = [
    "tier", "kind", "path", "start_line", "end_line", "text", "tokens", "token_confidence", "file_hash", "reason"
]
EXPECTED_EVIDENCE_NODE_FIELDS = ["node_id", "kind", "label", "source", "confidence", "repository_commit", "metadata"]
EXPECTED_EVIDENCE_EDGE_FIELDS = ["source", "target", "relation", "evidence", "confidence", "repository_commit", "observed_at", "metadata"]


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


def _validate_compatibility() -> None:
    _require([field.name for field in fields(ContextPackItem)] == EXPECTED_CONTEXT_PACK_FIELDS, "ContextPackItem field schema changed")
    _require([field.name for field in fields(EvidenceNode)] == EXPECTED_EVIDENCE_NODE_FIELDS, "EvidenceNode field schema changed")
    _require([field.name for field in fields(EvidenceEdge)] == EXPECTED_EVIDENCE_EDGE_FIELDS, "EvidenceEdge field schema changed")


def _validate_runtime_roundtrip() -> dict[str, Any]:
    item = UniversalContextItem.build(
        kind="repository-definition",
        representation="exact",
        content={"path": "example.py", "start_line": 1, "end_line": 2, "text": "x = 1\n"},
        provenance=ContextProvenance(
            source="certifier",
            repository_commit="fixture",
            parent_item_ids=("sha256:" + "a" * 64,),
            metadata={"fixture": True},
        ),
        trust=ContextTrust(level="verified", confidence=1.0, taint=("fixture",)),
        freshness=ContextFreshness(state="fresh", lease_id="fixture-lease"),
        recovery=(RecoveryHandle(
            kind="file-range",
            locator={"path": "example.py", "start_line": 1, "end_line": 2},
            integrity="b" * 64,
        ),),
        metadata={"tokens": 4},
    )
    decoded = UniversalContextItem.from_dict(item.to_dict())
    _require(decoded == item, "UniversalContextItem deterministic roundtrip failed")
    _require(decoded.verify_integrity(), "UniversalContextItem integrity verification failed")

    tampered = item.to_dict()
    tampered["content"]["text"] = "x = 2\n"
    tamper_closed = False
    try:
        UniversalContextItem.from_dict(tampered)
    except ValueError:
        tamper_closed = True
    _require(tamper_closed, "UniversalContextItem tamper detection did not fail closed")

    legacy_context = ContextPackItem(
        tier="mandatory", kind="definition", path="example.py", start_line=1, end_line=2,
        text="x = 1\n", tokens=4, token_confidence="high", file_hash="c" * 64, reason="fixture",
    )
    context_adapter = UniversalContextItem.from_context_pack_item(legacy_context, repository_commit="fixture")
    _require(context_adapter.verify_integrity(), "ContextPackItem adapter integrity failed")
    _require(context_adapter.recovery[0].kind == "file-range", "ContextPackItem exact recovery handle missing")

    legacy_node = EvidenceNode(
        node_id="d" * 64, kind="file", label="example.py", source="coverage",
        confidence=0.8, repository_commit="fixture", metadata={"fixture": True},
    )
    node_adapter = UniversalContextItem.from_evidence_node(legacy_node)
    _require(node_adapter.verify_integrity(), "EvidenceNode adapter integrity failed")
    _require(node_adapter.provenance.source == "coverage", "EvidenceNode provenance lost")

    legacy_edge = EvidenceEdge(
        source="d" * 64, target="e" * 64, relation="COVERS", evidence="sha256:" + "f" * 64,
        confidence=0.9, repository_commit="fixture", observed_at="2026-08-18T00:00:00+00:00",
        metadata={"fixture": True},
    )
    edge_adapter = UniversalContextItem.from_evidence_edge(legacy_edge)
    _require(edge_adapter.verify_integrity(), "EvidenceEdge adapter integrity failed")
    _require(edge_adapter.recovery[0].kind == "evidence-edge", "EvidenceEdge recovery handle missing")

    return {
        "roundtrip": True,
        "tamper_fail_closed": True,
        "context_pack_adapter": True,
        "evidence_node_adapter": True,
        "evidence_edge_adapter": True,
        "legacy_field_schemas_preserved": True,
    }


def _validate_enforcement(repo: Path) -> dict[str, str]:
    for relative in (WORKFLOW_RELATIVE, RELEASE_GATE_RELATIVE, PIN_POLICY_RELATIVE):
        _require((repo / relative).is_file(), f"missing Universal Context Item enforcement surface: {relative.as_posix()}")
    workflow = (repo / WORKFLOW_RELATIVE).read_text(encoding="utf-8")
    _require("group: universal-context-item-${{ github.event.pull_request.number || github.ref }}" in workflow, "Universal Context Item concurrency is not PR/ref scoped")
    _require("tests.runtime.test_universal_context_item" in workflow, "Universal Context Item workflow lost regression suite")
    _require("tools/certify_universal_context_item.py" in workflow, "Universal Context Item workflow lost certifier")
    _require("actions/checkout@11d5960a326750d5838078e36cf38b85af677262" in workflow, "Universal Context Item checkout pin drift")
    _require("actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065" in workflow, "Universal Context Item setup-python pin drift")

    release_gate = (repo / RELEASE_GATE_RELATIVE).read_text(encoding="utf-8")
    _require("tests.runtime.test_universal_context_item" in release_gate, "release-main gate lost Universal Context Item regression")
    _require("tools/certify_universal_context_item.py" in release_gate, "release-main gate lost Universal Context Item certifier")

    pin_policy = (repo / PIN_POLICY_RELATIVE).read_text(encoding="utf-8")
    _require('".github/workflows/universal-context-item.yml"' in pin_policy, "immutable action policy does not cover Universal Context Item workflow")
    return {
        "exact_head_workflow": WORKFLOW_RELATIVE.as_posix(),
        "release_main_gate": RELEASE_GATE_RELATIVE.as_posix(),
        "immutable_action_pin_policy": PIN_POLICY_RELATIVE.as_posix(),
    }


def certify(repo: Path) -> dict[str, Any]:
    repo = repo.resolve()
    _require(repo == ROOT, f"Universal Context Item certifier must run against its own checkout: {repo} != {ROOT}")
    contract = _read_json(repo / CONTRACT_RELATIVE)
    _require(contract.get("schema_version") == 1, "Universal Context Item schema drift")
    _require(contract.get("family") == "universal-context-item", "Universal Context Item family drift")
    _require(contract.get("phase") == "python-first", "Universal Context Item phase drift")
    _require(contract.get("claim") == "UNIVERSAL_CONTEXT_ITEM_V1", "Universal Context Item claim drift")
    _require(contract.get("strict") is True, "Universal Context Item must remain strict")
    _require(contract.get("runtime") == "syntavra_runtime/universal_context_item.py", "Universal Context Item runtime path drift")
    _require(contract.get("canonical_type") == "UniversalContextItem", "Universal Context Item canonical type drift")
    _require(contract.get("representation_vocabulary") == EXPECTED_REPRESENTATIONS, "representation vocabulary drift")
    _require(contract.get("trust_level_vocabulary") == EXPECTED_TRUST, "trust vocabulary drift")
    _require(contract.get("freshness_state_vocabulary") == EXPECTED_FRESHNESS, "freshness vocabulary drift")
    _require(contract.get("recovery_kind_vocabulary") == EXPECTED_RECOVERY, "recovery vocabulary drift")

    compatibility = contract.get("compatibility") or {}
    for key in ("context_pack_serialization_unchanged", "runtime_evidence_serialization_unchanged", "adapter_is_additive", "no_duplicate_route_identity"):
        _require(compatibility.get(key) is True, f"compatibility policy disabled: {key}")
    integrity = contract.get("integrity") or {}
    for key in ("canonical_json", "content_sha256_required", "item_id_sha256_required", "roundtrip_must_reverify", "tamper_must_fail_closed", "recovery_handle_integrity_required"):
        _require(integrity.get(key) is True, f"integrity policy disabled: {key}")
    policy = contract.get("policy") or {}
    for key in (
        "provenance_required", "trust_explicit", "taint_explicit", "freshness_explicit", "exact_recovery_handle_supported",
        "parent_lineage_supported", "trust_and_freshness_not_part_of_stable_identity", "content_and_provenance_part_of_stable_identity", "no_silent_fallback",
    ):
        _require(policy.get(key) is True, f"Universal Context Item policy disabled: {key}")

    completeness = certify_completeness(repo)
    _require(completeness.get("ok") is True, "capability completeness is not valid")
    _require(completeness.get("current_milestone") == "universal_context_item_v1", "registry has not advanced to universal_context_item_v1")
    _require(completeness.get("python_complete_ready") is False, "Python COMPLETE unexpectedly true")
    _require(completeness.get("rust_resume_allowed") is False, "Rust resume unexpectedly true")

    rust_freeze = certify_rust_freeze(repo)
    _require(rust_freeze.get("ok") is True, "Rust feature freeze is not certified")
    _require(rust_freeze.get("rust_resume_allowed") is False, "Rust resume unexpectedly enabled")
    _require((rust_freeze.get("rust") or {}).get("production_promoted") == 174, "Rust production authority drift")

    _validate_compatibility()
    runtime = _validate_runtime_roundtrip()
    enforcement = _validate_enforcement(repo)
    exact_head = _head(repo)
    _require(bool(exact_head), "unable to resolve exact HEAD")

    registry = _read_json(repo / REGISTRY_RELATIVE)
    by_id = {row["id"]: row for row in registry.get("capabilities", []) if isinstance(row, dict) and isinstance(row.get("id"), str)}
    _require((by_id.get("rust_feature_freeze_guard_v1") or {}).get("state") == "certified", "Rust freeze guard must be certified before Universal Context Item admission")
    _require((by_id.get("universal_context_item_v1") or {}).get("state") in {"implemented", "verified"}, "Universal Context Item registry state must be pre-certification implemented/verified")

    return {
        "ok": True,
        "schema_version": 1,
        "family": "universal-context-item",
        "claim": "UNIVERSAL_CONTEXT_ITEM_V1",
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
        "claim_boundary": "This certificate admits the Universal Context Item contract and additive adapters. It does not claim Evidence Store v2, Python COMPLETE, or Rust parity/promotion.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Certify Syntavra Universal Context Item v1.")
    parser.add_argument("--repo", default=".")
    parser.add_argument("--out")
    args = parser.parse_args()
    try:
        report = certify(Path(args.repo))
    except Exception as exc:  # pragma: no cover
        report = {"ok": False, "schema_version": 1, "family": "universal-context-item", "error": f"{type(exc).__name__}: {exc}", "traceback": traceback.format_exc()}
    payload = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.out:
        output = Path(args.out)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(payload, encoding="utf-8")
    print(payload, end="")
    return 0 if report.get("ok") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
