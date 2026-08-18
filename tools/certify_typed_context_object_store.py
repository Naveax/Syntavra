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

from syntavra_runtime.typed_context_objects import TYPE_SPECS, TypedContextObject, TypedContextObjectStore
from syntavra_runtime.universal_context_item import ContextProvenance, RecoveryHandle
from tools.certify_evidence_store_v2 import certify as certify_evidence_store
from tools.certify_python_capability_completeness import certify as certify_completeness
from tools.certify_rust_feature_freeze_guard import certify as certify_rust_freeze
from tools.certify_universal_context_item import certify as certify_universal

CONTRACT_RELATIVE = Path("contracts/python/typed-context-object-store-v1.json")
REGISTRY_RELATIVE = Path("contracts/python/capability-completeness-registry-v1.json")
WORKFLOW_RELATIVE = Path(".github/workflows/typed-context-object-store.yml")
RELEASE_GATE_RELATIVE = Path(".github/workflows/release-main-merge-gate.yml")
PIN_POLICY_RELATIVE = Path("tests/runtime/test_release_action_pins.py")
VALIDATOR_RELATIVE = Path("tools/validate.py")

EXPECTED_TYPES = {
    "GitDiff": ["base", "head", "files", "patch"],
    "TestRun": ["command", "exit_code", "tests"],
    "CompilerDiagnostics": ["tool", "diagnostics"],
    "ASTGraph": ["language", "nodes", "edges"],
    "DependencyGraph": ["nodes", "edges"],
    "SearchResultSet": ["query", "results"],
    "LogStream": ["source", "entries"],
    "BrowserDOM": ["url", "nodes"],
    "TraceSet": ["traces"],
    "MetricSeries": ["name", "points"],
    "DataFrame": ["columns", "rows"],
    "FileSnapshot": ["path", "content"],
    "SymbolSnapshot": ["symbol", "kind", "path"],
    "ToolSchemaSet": ["tools"],
    "MemoryObservation": ["observation", "scope"],
    "TaskStateSnapshot": ["task_id", "state"],
}
EXPECTED_REPRESENTATIONS = ["exact", "structural", "semantic", "bounded-preview"]


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


def _fixture(object_type: str) -> dict[str, Any]:
    values: dict[str, Any] = {
        "base": "a", "head": "b", "files": [], "patch": "",
        "command": ["test"], "exit_code": 0, "tests": [],
        "tool": "compiler", "diagnostics": [],
        "language": "python", "nodes": [], "edges": [],
        "query": "q", "results": [],
        "source": "log", "entries": [],
        "url": "https://example.invalid",
        "traces": [], "name": "metric", "points": [],
        "columns": [], "rows": [],
        "path": "fixture.txt", "content": "fixture",
        "symbol": "fixture", "kind": "symbol",
        "tools": [], "observation": "fixture", "scope": "project",
        "task_id": "task", "state": {},
    }
    return {name: values[name] for name in TYPE_SPECS[object_type]}


def _runtime_smoke() -> dict[str, Any]:
    digests: set[str] = set()
    universal_ids: set[str] = set()
    with tempfile.TemporaryDirectory() as directory:
        store = TypedContextObjectStore(Path(directory) / "typed.sqlite3")
        for object_type in TYPE_SPECS:
            value = TypedContextObject(
                object_type=object_type,
                representation="exact",
                payload=_fixture(object_type),
                metadata={"certifier": object_type},
            )
            decoded = TypedContextObject.from_dict(value.to_dict())
            _require(decoded == value, f"{object_type}: deterministic typed roundtrip failed")
            digests.add(value.object_sha256)
            receipt = store.put(
                value,
                provenance=ContextProvenance(source="typed-context-certifier", repository_commit="fixture"),
                recovery=(RecoveryHandle(kind="artifact", locator={"object_type": object_type}, integrity="a" * 64),),
                observed_at="2026-08-18T00:00:00+00:00",
            )
            universal_ids.add(str(receipt["item_id"]))
            _require(store.require(str(receipt["item_id"])) == value, f"{object_type}: EvidenceStore facade roundtrip failed")
            _require(store.verify_item(str(receipt["item_id"]))["ok"] is True, f"{object_type}: stored universal integrity failed")

        sample_ids: set[str] = set()
        for representation in EXPECTED_REPRESENTATIONS:
            value = TypedContextObject("SearchResultSet", representation, _fixture("SearchResultSet"))
            item = value.to_universal(provenance=ContextProvenance(source="typed-context-certifier"))
            _require(TypedContextObject.from_universal(item) == value, f"{representation}: Universal roundtrip failed")
            sample_ids.add(item.item_id)
        _require(len(sample_ids) == len(EXPECTED_REPRESENTATIONS), "representations do not produce distinct universal identities")

        secret_rejected = False
        try:
            store.put(
                TypedContextObject("FileSnapshot", "exact", {"path": "secret.txt", "content": "sk-proj-" + "A" * 32}),
                provenance=ContextProvenance(source="typed-context-certifier"),
            )
        except ValueError:
            secret_rejected = True
        _require(secret_rejected, "typed store bypassed Evidence Store secret policy")

        stats = store.stats()
        _require(stats["items"] == len(TYPE_SPECS), "typed store persisted unexpected item count")
    return {
        "declared_object_types": len(TYPE_SPECS),
        "all_types_roundtrip": len(digests) == len(TYPE_SPECS),
        "all_types_persist_via_evidence_store": len(universal_ids) == len(TYPE_SPECS),
        "all_representations_roundtrip": True,
        "representations_have_distinct_identity": True,
        "secret_policy_inherited": True,
        "parallel_storage_engine": False,
    }


def _validate_enforcement(repo: Path) -> dict[str, str]:
    for relative in (WORKFLOW_RELATIVE, RELEASE_GATE_RELATIVE, PIN_POLICY_RELATIVE, VALIDATOR_RELATIVE):
        _require((repo / relative).is_file(), f"missing Typed Context enforcement surface: {relative.as_posix()}")
    workflow = (repo / WORKFLOW_RELATIVE).read_text(encoding="utf-8")
    _require("group: typed-context-object-store-${{ github.event.pull_request.number || github.ref }}" in workflow, "Typed Context concurrency is not PR/ref scoped")
    _require("tests.runtime.test_typed_context_object_store" in workflow, "Typed Context workflow lost regression suite")
    _require("tools/certify_typed_context_object_store.py" in workflow, "Typed Context workflow lost certifier")
    _require("actions/checkout@11d5960a326750d5838078e36cf38b85af677262" in workflow, "Typed Context checkout pin drift")
    _require("actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065" in workflow, "Typed Context setup-python pin drift")

    release_gate = (repo / RELEASE_GATE_RELATIVE).read_text(encoding="utf-8")
    _require("tests.runtime.test_typed_context_object_store" in release_gate, "release-main gate lost Typed Context regression")
    _require("tools/certify_typed_context_object_store.py" in release_gate, "release-main gate lost Typed Context certifier")

    pin_policy = (repo / PIN_POLICY_RELATIVE).read_text(encoding="utf-8")
    _require('".github/workflows/typed-context-object-store.yml"' in pin_policy, "immutable action policy does not cover Typed Context workflow")

    validator = (repo / VALIDATOR_RELATIVE).read_text(encoding="utf-8")
    _require('("typed_context_object_store", typed_ok, typed_detail)' in validator, "repository validator lost Typed Context check")
    return {
        "exact_head_workflow": WORKFLOW_RELATIVE.as_posix(),
        "release_main_gate": RELEASE_GATE_RELATIVE.as_posix(),
        "immutable_action_pin_policy": PIN_POLICY_RELATIVE.as_posix(),
        "repository_validator": VALIDATOR_RELATIVE.as_posix(),
    }


def certify(repo: Path) -> dict[str, Any]:
    repo = repo.resolve()
    _require(repo == ROOT, f"Typed Context certifier must run against its own checkout: {repo} != {ROOT}")
    contract = _read_json(repo / CONTRACT_RELATIVE)
    _require(contract.get("schema_version") == 1, "Typed Context schema drift")
    _require(contract.get("family") == "typed-context-object-store", "Typed Context family drift")
    _require(contract.get("phase") == "python-first", "Typed Context phase drift")
    _require(contract.get("claim") == "TYPED_CONTEXT_OBJECT_STORE_V1", "Typed Context claim drift")
    _require(contract.get("strict") is True, "Typed Context must remain strict")
    _require(contract.get("runtime") == "syntavra_runtime/typed_context_objects.py", "Typed Context runtime drift")
    _require(contract.get("persistence_authority") == "syntavra_runtime/evidence_store.py:EvidenceStoreV2", "Typed Context persistence authority drift")
    _require(contract.get("identity_authority") == "syntavra_runtime/universal_context_item.py:UniversalContextItem", "Typed Context identity authority drift")
    _require(contract.get("representations") == EXPECTED_REPRESENTATIONS, "Typed Context representation vocabulary drift")
    _require(contract.get("object_types") == EXPECTED_TYPES, "Typed Context object type contract drift")
    _require({key: list(value) for key, value in TYPE_SPECS.items()} == EXPECTED_TYPES, "runtime TYPE_SPECS drift")

    integrity = contract.get("integrity") or {}
    for key in ("deterministic_canonical_json", "typed_object_sha256", "universal_item_integrity_required", "kind_type_match_required", "representation_match_required", "typed_digest_match_required", "roundtrip_required", "tamper_fail_closed"):
        _require(integrity.get(key) is True, f"Typed Context integrity policy disabled: {key}")
    storage = contract.get("storage") or {}
    _require(storage.get("parallel_database_forbidden") is True, "Typed Context allows parallel database")
    for key in ("evidence_store_v2_required", "universal_context_item_required", "inherits_lineage", "inherits_retention_gc", "inherits_secret_policy", "inherits_mutation_journal", "inherits_trust_freshness_evaluation"):
        _require(storage.get(key) is True, f"Typed Context storage inheritance disabled: {key}")

    completeness = certify_completeness(repo)
    _require(completeness.get("ok") is True, "capability completeness is not valid")
    _require(completeness.get("python_complete_ready") is False, "Python COMPLETE unexpectedly true")
    _require(completeness.get("rust_resume_allowed") is False, "Rust resume unexpectedly true")

    evidence = certify_evidence_store(repo)
    _require(evidence.get("ok") is True, "Evidence Store v2 is not certified")
    universal = certify_universal(repo)
    _require(universal.get("ok") is True, "Universal Context Item is not certified")
    rust_freeze = certify_rust_freeze(repo)
    _require(rust_freeze.get("ok") is True, "Rust freeze is not certified")
    _require((rust_freeze.get("rust") or {}).get("production_promoted") == 174, "Rust production authority drift")

    registry = _read_json(repo / REGISTRY_RELATIVE)
    by_id = {row["id"]: row for row in registry.get("capabilities", []) if isinstance(row, dict) and isinstance(row.get("id"), str)}
    _require((by_id.get("evidence_store_v2") or {}).get("state") == "certified", "Evidence Store must be certified before Typed Context admission")
    typed_state = (by_id.get("typed_context_object_store_v1") or {}).get("state")
    _require(typed_state in {"implemented", "verified", "certified"}, "Typed Context registry state is invalid")
    if typed_state != "certified":
        _require(completeness.get("current_milestone") == "typed_context_object_store_v1", "registry has not advanced to Typed Context Object Store v1")

    runtime = _runtime_smoke()
    enforcement = _validate_enforcement(repo)
    exact_head = _head(repo)
    _require(bool(exact_head), "unable to resolve exact HEAD")
    return {
        "ok": True,
        "schema_version": 1,
        "family": "typed-context-object-store",
        "claim": "TYPED_CONTEXT_OBJECT_STORE_V1",
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
        "claim_boundary": "This certificate admits Typed Context Object Store v1. It does not claim Programmatic Execution, Python COMPLETE, or Rust parity/promotion.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Certify Syntavra Typed Context Object Store v1.")
    parser.add_argument("--repo", default=".")
    parser.add_argument("--out")
    args = parser.parse_args()
    try:
        report = certify(Path(args.repo))
    except Exception as exc:  # pragma: no cover
        report = {"ok": False, "schema_version": 1, "family": "typed-context-object-store", "error": f"{type(exc).__name__}: {exc}", "traceback": traceback.format_exc()}
    payload = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.out:
        output = Path(args.out)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(payload, encoding="utf-8")
    print(payload, end="")
    return 0 if report.get("ok") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
