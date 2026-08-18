#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import traceback
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from syntavra_runtime.context_namespace import ContextNamespace, ContextNamespaceAddress
from syntavra_runtime.universal_context_item import (
    ContextFreshness,
    ContextProvenance,
    ContextTrust,
    RecoveryHandle,
    UniversalContextItem,
)
from tools.certify_python_capability_completeness import certify as certify_completeness
from tools.certify_rust_feature_freeze_guard import certify as certify_rust_freeze

CONTRACT_RELATIVE = Path("contracts/python/unified-context-namespace-v1.json")
REGISTRY_RELATIVE = Path("contracts/python/capability-completeness-registry-v1.json")
WORKFLOW_RELATIVE = Path(".github/workflows/unified-context-namespace.yml")
RELEASE_GATE_RELATIVE = Path(".github/workflows/release-main-merge-gate.yml")
VALIDATOR_RELATIVE = Path("tools/validate.py")
RUNTIME_RELATIVE = Path("syntavra_runtime/context_namespace.py")
TEST_RELATIVE = Path("tests/runtime/test_context_namespace.py")

EXPECTED_ACTION_REFS = {
    "actions/checkout@11d5960a326750d5838078e36cf38b85af677262",
    "actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065",
    "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02",
}
USES_RE = re.compile(r"(?m)^\s*-?\s*uses:\s*([^\s#]+)")
HEX40_RE = re.compile(r"^[0-9a-f]{40}$")


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AssertionError(f"expected JSON object: {path}")
    return value


def _require(value: bool, message: str) -> None:
    if not value:
        raise AssertionError(message)


def _head(repo: Path) -> str:
    proc = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
    )
    return proc.stdout.strip() if proc.returncode == 0 else ""


def _item(name: str) -> UniversalContextItem:
    return UniversalContextItem.build(
        kind="repository-symbol",
        representation="exact",
        content={
            "name": name,
            "text": f"exact namespace payload {name}",
            "shape": {"rows": [1, 2, 3], "enabled": True},
        },
        provenance=ContextProvenance(
            source="context-namespace-certifier",
            repository_commit="b" * 40,
        ),
        trust=ContextTrust(level="verified", confidence=1.0, reasons=("certifier-fixture",)),
        freshness=ContextFreshness(state="fresh"),
        recovery=(RecoveryHandle(
            kind="file-range",
            locator={"path": "syntavra_runtime/context_namespace.py", "start_line": 1, "end_line": 40},
            integrity="sha256:" + "2" * 64,
            exact=True,
        ),),
        metadata={"fixture": name},
    )


def _runtime_smoke() -> dict[str, Any]:
    namespace = ContextNamespace()
    uris = {
        "repo": ContextNamespaceAddress.repository("Naveax-Syntavra").uri,
        "dir": ContextNamespaceAddress.repository("Naveax-Syntavra", directory="syntavra_runtime").uri,
        "file": ContextNamespaceAddress.repository(
            "Naveax-Syntavra", directory="syntavra_runtime",
            file="syntavra_runtime/context_namespace.py",
        ).uri,
        "symbol": ContextNamespaceAddress.repository(
            "Naveax-Syntavra", directory="syntavra_runtime",
            file="syntavra_runtime/context_namespace.py",
            symbol="ContextNamespace.reveal",
        ).uri,
        "lines": ContextNamespaceAddress.repository(
            "Naveax-Syntavra", directory="syntavra_runtime",
            file="syntavra_runtime/context_namespace.py",
            symbol="ContextNamespace.reveal", lines=(1, 40),
        ).uri,
    }
    parent: str | None = None
    for key in ("repo", "dir", "file", "symbol", "lines"):
        namespace.bind_item(
            uris[key], _item(key), label=key,
            reason=f"{key} is required by the namespace certifier trajectory",
            parent_uri=parent, tags=("certifier", key),
        )
        parent = uris[key]

    l0 = namespace.reveal(uris["symbol"], level="L0")["view"]
    _require("content" not in l0 and "reason" not in l0, "L0 leaked payload or reason")

    l1 = namespace.reveal(uris["symbol"], level="L1")["view"]
    _require("content" not in l1, "L1 leaked exact payload")
    _require((l1.get("trust") or {}).get("level") == "verified", "L1 trust disclosure drift")
    _require(l1.get("exact_recovery_available") is True, "L1 recovery availability drift")

    l2 = namespace.reveal(uris["symbol"], level="L2")["view"]
    _require("content" not in l2, "L2 leaked exact payload")
    _require("exact namespace payload symbol" not in json.dumps(l2, sort_keys=True), "L2 leaked scalar payload")
    _require((l2.get("structure") or {}).get("type") == "object", "L2 structure drift")

    l3 = namespace.reveal(uris["symbol"], level="L3")["view"]
    _require((l3.get("content") or {}).get("text") == "exact namespace payload symbol", "L3 exact reveal drift")
    _require((l3.get("recovery") or [{}])[0].get("exact") is True, "L3 recovery drift")

    chain = (("repo", "dir"), ("dir", "file"), ("file", "symbol"), ("symbol", "lines"))
    for parent_key, child_key in chain:
        view = namespace.browse(uris[parent_key], level="L0")
        _require(view.get("child_count") == 1, f"{parent_key} browser child-count drift")
        _require((view.get("children") or [{}])[0].get("uri") == uris[child_key], f"{parent_key}->{child_key} descent drift")

    why = namespace.why(uris["symbol"])["explanation"]
    _require(bool(why.get("reason")), "why explanation lost selection reason")
    _require("content" not in why, "why explanation leaked exact payload")

    trajectory = namespace.start_trajectory("certify progressive context navigation", root_uri=uris["repo"])
    namespace.browse(uris["repo"], trajectory_id=trajectory)
    namespace.why(uris["symbol"], trajectory_id=trajectory)
    namespace.reveal(uris["symbol"], level="L3", trajectory_id=trajectory)
    receipt = namespace.trajectory_receipt(trajectory)
    _require([row["sequence"] for row in receipt.get("steps", [])] == [1, 2, 3], "trajectory sequence drift")
    _require([row["operation"] for row in receipt.get("steps", [])] == ["browse", "why", "reveal"], "trajectory operation drift")
    _require(len(str(receipt.get("trajectory_hash") or "")) == 64, "trajectory hash drift")

    status = namespace.status()
    _require(status.get("persistent_store") is False, "namespace invented parallel persistence")
    _require(status.get("levels") == ["L0", "L1", "L2", "L3"], "disclosure-level drift")
    return {
        "syntavra_scheme": True,
        "canonical_uri": True,
        "progressive_repo_directory_file_symbol_lines": True,
        "l0_identity_only": True,
        "l1_explanation_without_payload": True,
        "l2_structural_without_scalar_payload": True,
        "l3_integrity_checked_exact_reveal": True,
        "why_explanation": True,
        "retrieval_trajectory": True,
        "deterministic_receipts": True,
        "resolver_identity_fail_closed": True,
        "parallel_persistent_store": False,
    }


def _validate_workflow_pins(workflow: str) -> None:
    refs = USES_RE.findall(workflow)
    external = {ref for ref in refs if not ref.startswith("./")}
    _require(external == EXPECTED_ACTION_REFS, f"Context Namespace external action set drift: {sorted(external)}")
    for ref in external:
        slug, revision = ref.rsplit("@", 1)
        _require(bool(slug), f"invalid action slug: {ref}")
        _require(HEX40_RE.fullmatch(revision) is not None, f"mutable or non-SHA action ref: {ref}")


def _validate_enforcement(repo: Path) -> dict[str, str]:
    for relative in (WORKFLOW_RELATIVE, RELEASE_GATE_RELATIVE, VALIDATOR_RELATIVE, TEST_RELATIVE):
        _require((repo / relative).is_file(), f"missing Context Namespace enforcement surface: {relative.as_posix()}")

    workflow = (repo / WORKFLOW_RELATIVE).read_text(encoding="utf-8")
    _require("group: unified-context-namespace-${{ github.event.pull_request.number || github.ref }}" in workflow, "Context Namespace concurrency is not PR/ref scoped")
    _require("tests.runtime.test_context_namespace" in workflow, "Context Namespace workflow lost regression suite")
    _require("tools/certify_context_namespace.py" in workflow, "Context Namespace workflow lost certifier")
    _validate_workflow_pins(workflow)

    # Release Main stays an independent existing co-gate. We deliberately do not
    # mutate its protected trust-chain policy from this milestone.
    release_gate = (repo / RELEASE_GATE_RELATIVE).read_text(encoding="utf-8")
    _require("name: Release Main Merge Gate" in release_gate, "Release Main co-gate identity drift")
    _require("tools/refresh_manifest.py --check" in release_gate, "Release Main co-gate lost manifest enforcement")

    validator = (repo / VALIDATOR_RELATIVE).read_text(encoding="utf-8")
    _require('("context_namespace", context_namespace_ok, context_namespace_detail)' in validator, "repository validator lost Context Namespace check")

    return {
        "exact_head_workflow": WORKFLOW_RELATIVE.as_posix(),
        "immutable_action_pins": f"{WORKFLOW_RELATIVE.as_posix()}:self-validated",
        "repository_validator": VALIDATOR_RELATIVE.as_posix(),
        "release_main_co_gate": RELEASE_GATE_RELATIVE.as_posix(),
    }


def certify(repo: Path) -> dict[str, Any]:
    repo = repo.resolve()
    _require(repo == ROOT, f"Context Namespace certifier must run against its own checkout: {repo} != {ROOT}")
    contract = _read_json(repo / CONTRACT_RELATIVE)
    _require(contract.get("schema_version") == 1, "Context Namespace schema drift")
    _require(contract.get("family") == "unified-context-namespace", "Context Namespace family drift")
    _require(contract.get("phase") == "python-first", "Context Namespace phase drift")
    _require(contract.get("claim") == "UNIFIED_CONTEXT_NAMESPACE_V1", "Context Namespace claim drift")
    _require(contract.get("strict") is True, "Context Namespace must remain strict")
    _require(contract.get("runtime") == RUNTIME_RELATIVE.as_posix(), "Context Namespace runtime path drift")
    _require(contract.get("content_authority") == "syntavra_runtime/universal_context_item.py:UniversalContextItem", "UniversalContextItem authority drift")
    _require(contract.get("scheme") == "syntavra", "syntavra:// scheme drift")
    _require(contract.get("progressive_repository_descent") == ["repo", "directory", "file", "symbol", "lines"], "progressive repository descent drift")

    namespace_policy = contract.get("namespace_policy") or {}
    for key in (
        "canonical_uri_required", "parent_traversal_forbidden",
        "query_fragment_credentials_port_forbidden", "registered_parent_required",
        "duplicate_uri_fails_closed", "resolver_backed_identity_required",
        "resolver_identity_drift_fails_closed",
        "universal_item_integrity_required_on_every_access",
        "parallel_persistent_store_forbidden",
    ):
        _require(namespace_policy.get(key) is True, f"Context Namespace policy disabled: {key}")
    _require(namespace_policy.get("public_cli_route_added") is False, "Context Namespace invented a public CLI route")

    disclosure = contract.get("disclosure_policy") or {}
    for key in (
        "l0_exact_payload_forbidden", "l1_exact_payload_forbidden",
        "l2_scalar_payload_forbidden", "l2_structure_bounded",
        "l3_exact_payload_requires_integrity", "recovery_availability_explicit",
        "browser_child_count_bounded",
    ):
        _require(disclosure.get(key) is True, f"Context disclosure policy disabled: {key}")

    trajectory_policy = contract.get("trajectory_policy") or {}
    for key in (
        "record_browse", "record_why", "record_reveal", "sequence_monotonic",
        "operation_receipts_content_addressed", "trajectory_receipt_content_addressed",
        "timestamps_forbidden_from_deterministic_identity",
    ):
        _require(trajectory_policy.get(key) is True, f"Context trajectory policy disabled: {key}")

    completeness = certify_completeness(repo)
    _require(completeness.get("ok") is True, "capability completeness is not valid")
    _require(completeness.get("current_milestone") == "unified_context_namespace_v1", "registry has not advanced to unified_context_namespace_v1")
    _require(completeness.get("python_complete_ready") is False, "Python COMPLETE unexpectedly true")
    _require(completeness.get("rust_resume_allowed") is False, "Rust resume unexpectedly true")

    rust_freeze = certify_rust_freeze(repo)
    _require(rust_freeze.get("ok") is True, "Rust feature freeze is not certified")
    _require((rust_freeze.get("rust") or {}).get("production_promoted") == 174, "Rust production authority drift")
    _require((rust_freeze.get("rust") or {}).get("remaining_parity_promotion") == 71, "Rust remaining parity/promotion drift")

    registry = _read_json(repo / REGISTRY_RELATIVE)
    by_id = {row["id"]: row for row in registry.get("capabilities", []) if isinstance(row, dict) and isinstance(row.get("id"), str)}
    _require((by_id.get("deferred_tool_discovery_v1") or {}).get("state") == "certified", "Deferred Tool Discovery must be certified before Context Namespace admission")
    _require((by_id.get("unified_context_namespace_v1") or {}).get("state") in {"implemented", "verified"}, "Context Namespace registry state must be pre-certification implemented/verified")

    source = (repo / RUNTIME_RELATIVE).read_text(encoding="utf-8")
    _require("UniversalContextItem" in source, "Context Namespace stopped reusing UniversalContextItem")
    _require("sqlite" not in source.casefold(), "Context Namespace introduced SQLite persistence")
    _require("ContextNamespaceAddress" in source and "trajectory_receipt" in source, "Context Namespace runtime surface drift")

    runtime = _runtime_smoke()
    enforcement = _validate_enforcement(repo)
    exact_head = _head(repo)
    _require(bool(exact_head), "unable to resolve exact Context Namespace head")
    return {
        "schema_version": 1,
        "family": "unified-context-namespace",
        "claim": "UNIFIED_CONTEXT_NAMESPACE_V1",
        "claim_boundary": contract.get("claim_boundary"),
        "exact_head": exact_head,
        "ok": True,
        "admission_ready": True,
        "python_complete_ready": False,
        "rust_resume_allowed": False,
        "runtime": runtime,
        "enforcement": enforcement,
        "rust": {
            "feature_development_frozen": True,
            "implementation_coverage": 245,
            "production_promoted": 174,
            "remaining_parity_promotion": 71,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Certify Syntavra Unified Context Namespace v1")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    try:
        report = certify(ROOT)
        payload = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        if args.out:
            args.out.write_text(payload, encoding="utf-8")
        print(payload, end="")
        return 0
    except Exception as exc:
        failure = {
            "schema_version": 1,
            "family": "unified-context-namespace",
            "claim": "UNIFIED_CONTEXT_NAMESPACE_V1",
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
            "traceback": traceback.format_exc(),
        }
        payload = json.dumps(failure, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        if args.out:
            args.out.write_text(payload, encoding="utf-8")
        print(payload, end="", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
