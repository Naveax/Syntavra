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

from syntavra_runtime.context_namespace import ContextNamespaceAddress
from syntavra_runtime.multi_graph_retrieval import (
    GRAPH_KINDS,
    GraphEdge,
    GraphNode,
    MultiGraphRetrieval,
)
from tools.certify_python_capability_completeness import certify as certify_completeness
from tools.certify_rust_feature_freeze_guard import certify as certify_rust_freeze

CONTRACT_RELATIVE = Path("contracts/python/multi-graph-retrieval-v1.json")
REGISTRY_RELATIVE = Path("contracts/python/capability-completeness-registry-v1.json")
WORKFLOW_RELATIVE = Path(".github/workflows/multi-graph-retrieval.yml")
RELEASE_GATE_RELATIVE = Path(".github/workflows/release-main-merge-gate.yml")
RUNTIME_RELATIVE = Path("syntavra_runtime/multi_graph_retrieval.py")
TEST_RELATIVE = Path("tests/runtime/test_multi_graph_retrieval.py")
SECURITY_TEST_RELATIVE = Path("tests/runtime/test_multi_graph_security_hardening.py")
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
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    return proc.stdout.strip() if proc.returncode == 0 else ""


def _runtime_smoke() -> dict[str, Any]:
    engine = MultiGraphRetrieval()
    shared_uri = ContextNamespaceAddress.repository(
        "syntavra-certifier",
        directory="syntavra_runtime",
        file="syntavra_runtime/multi_graph_retrieval.py",
        symbol="MultiGraphRetrieval.retrieve",
    ).uri
    for graph_kind in sorted(GRAPH_KINDS):
        engine.add_layer(
            graph_kind,
            graph_kind,
            [
                GraphNode(
                    graph_kind=graph_kind,
                    node_id=f"{graph_kind}-shared",
                    label=f"multi graph retrieval {graph_kind}",
                    node_type="certifier-fixture",
                    namespace_uri=shared_uri,
                    item_id="certifier-shared-context",
                    evidence_refs=(f"certifier:{graph_kind}",),
                    trust_level="verified",
                    metadata={
                        "freshness": "current",
                        "disposition": "allow",
                    }
                    if graph_kind == "security"
                    else {"freshness": "current"},
                )
            ],
            source_refs=(f"source:{graph_kind}",),
        )
    result = engine.retrieve(
        "multi graph retrieval",
        required_graphs=GRAPH_KINDS,
        limit=8,
        max_hops=2,
    )
    _require(
        result["candidate_count"] == 1,
        "all graph views did not fuse to one context identity",
    )
    candidate = result["candidates"][0]
    _require(
        candidate["graph_kinds"] == sorted(GRAPH_KINDS),
        "eight-graph fusion coverage drift",
    )
    _require(
        candidate["item_id"] == "certifier-shared-context",
        "item identity fusion drift",
    )
    _require(
        len(result["receipt"]["receipt_hash"]) == 64,
        "retrieval receipt hash drift",
    )

    deny = MultiGraphRetrieval()
    deny.add_layer(
        "code",
        "code",
        [
            GraphNode(
                "code",
                "code",
                "sensitive migration",
                namespace_uri=shared_uri,
                trust_level="verified",
            )
        ],
    )
    deny.add_layer(
        "security",
        "security",
        [
            GraphNode(
                "security",
                "deny",
                "security deny",
                namespace_uri=shared_uri,
                trust_level="verified",
                metadata={"disposition": "deny"},
            )
        ],
    )
    denied = deny.retrieve("sensitive migration")
    _require(
        denied["candidate_count"] == 0,
        "security deny did not fail closed",
    )

    alias_deny = MultiGraphRetrieval()
    alias_deny.add_layer(
        "code",
        "code",
        [
            GraphNode(
                "code",
                "uri-only",
                "credential rotation",
                namespace_uri=shared_uri,
                trust_level="verified",
            )
        ],
    )
    alias_deny.add_layer(
        "security",
        "security",
        [
            GraphNode(
                "security",
                "item-and-uri",
                "credential deny",
                namespace_uri=shared_uri,
                item_id="certifier-denied-item",
                trust_level="verified",
                metadata={"disposition": "deny"},
            )
        ],
    )
    alias_denied = alias_deny.retrieve(
        "credential rotation",
        include_tainted=True,
    )
    _require(
        alias_denied["candidate_count"] == 0,
        "security deny alias closure drift",
    )
    _require(
        alias_denied["blocked_item_id_count"] == 1
        and alias_denied["blocked_namespace_uri_count"] == 1,
        "blocked alias receipt drift",
    )

    no_propagation = MultiGraphRetrieval()
    no_propagation.add_layer(
        "security",
        "security",
        [
            GraphNode(
                "security",
                "denied-source",
                "forbidden trigger phrase",
                trust_level="verified",
                metadata={"disposition": "deny"},
            ),
            GraphNode(
                "security",
                "allowed-neighbor",
                "unrelated safe neighbor",
                trust_level="verified",
                metadata={"disposition": "allow"},
            ),
        ],
        [
            GraphEdge(
                "denied-source",
                "allowed-neighbor",
                "influences",
                confidence=1.0,
            )
        ],
    )
    blocked_propagation = no_propagation.retrieve(
        "forbidden trigger phrase",
        max_hops=4,
    )
    _require(
        blocked_propagation["candidate_count"] == 0,
        "blocked node influenced retrieval through graph propagation",
    )

    propagated = MultiGraphRetrieval()
    propagated.add_layer(
        "causal",
        "causal",
        [
            GraphNode(
                "causal",
                "symptom",
                "request timeout",
                trust_level="verified",
            ),
            GraphNode(
                "causal",
                "cause",
                "pool exhaustion",
                trust_level="verified",
            ),
        ],
        [
            GraphEdge(
                "symptom",
                "cause",
                "caused-by",
                confidence=0.95,
                evidence_refs=("trace:fixture",),
            )
        ],
    )
    propagated_result = propagated.retrieve("request timeout", max_hops=2)
    _require(
        any(
            row["identity"] == "node:causal:cause"
            for row in propagated_result["candidates"]
        ),
        "bounded graph propagation drift",
    )

    status = engine.status()
    _require(
        status["persistent_store"] is False,
        "Multi-Graph invented a persistent store",
    )
    _require(
        status["payload_authority"] is False,
        "Multi-Graph claimed payload authority",
    )
    return {
        "graph_kinds": sorted(GRAPH_KINDS),
        "eight_graph_fusion": True,
        "task_aware": True,
        "identity_fusion": True,
        "security_deny_fail_closed": True,
        "security_deny_alias_closure": True,
        "blocked_nodes_cannot_seed_or_propagate": True,
        "temporal_freshness_weighted": True,
        "trust_weighted": True,
        "bounded_graph_propagation": True,
        "deterministic_receipt": True,
        "persistent_store": False,
        "payload_authority": False,
    }


def _validate_workflow(workflow: str) -> None:
    refs = USES_RE.findall(workflow)
    external = {ref for ref in refs if not ref.startswith("./")}
    _require(
        external == EXPECTED_ACTION_REFS,
        f"Multi-Graph external action set drift: {sorted(external)}",
    )
    for ref in external:
        slug, revision = ref.rsplit("@", 1)
        _require(bool(slug), f"invalid action slug: {ref}")
        _require(
            HEX40_RE.fullmatch(revision) is not None,
            f"mutable or non-SHA action ref: {ref}",
        )


def _validate_enforcement(repo: Path) -> dict[str, str]:
    for relative in (
        WORKFLOW_RELATIVE,
        RELEASE_GATE_RELATIVE,
        TEST_RELATIVE,
        SECURITY_TEST_RELATIVE,
    ):
        _require(
            (repo / relative).is_file(),
            f"missing Multi-Graph enforcement surface: {relative.as_posix()}",
        )
    workflow = (repo / WORKFLOW_RELATIVE).read_text(encoding="utf-8")
    _require(
        "group: multi-graph-retrieval-${{ github.event.pull_request.number || github.ref }}"
        in workflow,
        "Multi-Graph concurrency is not PR/ref scoped",
    )
    _require(
        "tests.runtime.test_multi_graph_retrieval" in workflow,
        "Multi-Graph workflow lost core regression suite",
    )
    _require(
        "tests.runtime.test_multi_graph_security_hardening" in workflow,
        "Multi-Graph workflow lost security hardening regression suite",
    )
    _require(
        "tools/certify_multi_graph_retrieval.py" in workflow,
        "Multi-Graph workflow lost certifier",
    )
    _validate_workflow(workflow)
    release_gate = (repo / RELEASE_GATE_RELATIVE).read_text(encoding="utf-8")
    _require(
        "name: Release Main Merge Gate" in release_gate,
        "Release Main co-gate identity drift",
    )
    _require(
        "tools/refresh_manifest.py --check" in release_gate,
        "Release Main co-gate lost manifest enforcement",
    )
    return {
        "exact_head_workflow": WORKFLOW_RELATIVE.as_posix(),
        "immutable_action_pins": f"{WORKFLOW_RELATIVE.as_posix()}:self-validated",
        "release_main_co_gate": RELEASE_GATE_RELATIVE.as_posix(),
    }


def certify(repo: Path) -> dict[str, Any]:
    repo = repo.resolve()
    _require(
        repo == ROOT,
        f"Multi-Graph certifier must run against its own checkout: {repo} != {ROOT}",
    )
    contract = _read_json(repo / CONTRACT_RELATIVE)
    _require(contract.get("schema_version") == 1, "Multi-Graph schema drift")
    _require(
        contract.get("family") == "multi-graph-retrieval",
        "Multi-Graph family drift",
    )
    _require(contract.get("phase") == "python-first", "Multi-Graph phase drift")
    _require(
        contract.get("claim") == "MULTI_GRAPH_RETRIEVAL_V1",
        "Multi-Graph claim drift",
    )
    _require(contract.get("strict") is True, "Multi-Graph must remain strict")
    _require(
        contract.get("runtime") == RUNTIME_RELATIVE.as_posix(),
        "Multi-Graph runtime path drift",
    )
    _require(
        set(contract.get("graph_kinds") or ()) == GRAPH_KINDS,
        "Multi-Graph kind coverage drift",
    )

    authorities = contract.get("existing_authorities") or {}
    for value in authorities.values():
        relative = str(value).split(":", 1)[0]
        _require(
            (repo / relative).is_file(),
            f"missing reused authority: {relative}",
        )
    ownership = contract.get("ownership_policy") or {}
    for key in (
        "new_persistent_graph_store_forbidden",
        "exact_payload_storage_forbidden",
        "duplicate_repository_index_forbidden",
        "existing_graph_authorities_reused",
        "namespace_or_item_identity_preferred_for_fusion",
    ):
        _require(
            ownership.get(key) is True,
            f"Multi-Graph ownership policy disabled: {key}",
        )
    _require(
        ownership.get("public_cli_route_added") is False,
        "Multi-Graph invented a public CLI route",
    )

    retrieval = contract.get("retrieval_policy") or {}
    for key in (
        "task_aware",
        "deterministic",
        "required_graph_missing_fails_closed",
        "identity_fusion_enabled",
        "multi_graph_consensus_bonus",
        "temporal_freshness_weighted",
        "trust_weighted",
        "security_deny_fail_closed",
        "security_deny_alias_closure",
        "tainted_context_excluded_by_default",
        "blocked_nodes_cannot_seed_or_propagate",
        "evidence_refs_preserved",
        "reverse_edge_propagation_bounded",
    ):
        _require(
            retrieval.get(key) is True,
            f"Multi-Graph retrieval policy disabled: {key}",
        )
    _require(
        retrieval.get("bounded_limit") == 100,
        "Multi-Graph limit boundary drift",
    )
    _require(
        retrieval.get("bounded_hops") == 4,
        "Multi-Graph hop boundary drift",
    )

    receipt = contract.get("receipt_policy") or {}
    for key in (
        "content_addressed",
        "timestamps_excluded_from_identity",
        "candidate_identities_recorded",
        "candidate_scores_recorded",
        "layer_counts_recorded",
        "required_graphs_recorded",
        "blocked_identity_count_recorded",
        "blocked_item_id_count_recorded",
        "blocked_namespace_uri_count_recorded",
    ):
        _require(
            receipt.get(key) is True,
            f"Multi-Graph receipt policy disabled: {key}",
        )

    completeness = certify_completeness(repo)
    _require(
        completeness.get("ok") is True,
        "capability completeness is not valid",
    )
    _require(
        bool(completeness.get("current_milestone")),
        "capability registry current milestone missing",
    )
    _require(
        isinstance(completeness.get("python_complete_ready"), bool),
        "Python COMPLETE state must be boolean",
    )
    _require(isinstance(completeness.get("rust_resume_allowed"), bool), "Rust resume state must be boolean")
    _require(
        not completeness.get("rust_resume_allowed") or completeness.get("python_complete_ready") is True,
        "Rust resume cannot precede Python COMPLETE",
    )

    registry = _read_json(repo / REGISTRY_RELATIVE)
    by_id = {
        row["id"]: row
        for row in registry.get("capabilities", [])
        if isinstance(row, dict) and isinstance(row.get("id"), str)
    }
    _require(
        (by_id.get("unified_context_namespace_v1") or {}).get("state")
        == "certified",
        "Unified Context Namespace must be certified before Multi-Graph admission",
    )
    _require(
        (by_id.get("multi_graph_retrieval_v1") or {}).get("state")
        in {"implemented", "verified", "certified"},
        "Multi-Graph registry state is not admissible",
    )

    rust_freeze = certify_rust_freeze(repo)
    _require(
        rust_freeze.get("ok") is True,
        "Rust feature freeze is not certified",
    )
    _require(
        (rust_freeze.get("rust") or {}).get("production_promoted") == 174,
        "Rust production authority drift",
    )
    _require(
        (rust_freeze.get("rust") or {}).get("remaining_parity_promotion")
        == 71,
        "Rust remaining parity/promotion drift",
    )

    source = (repo / RUNTIME_RELATIVE).read_text(encoding="utf-8")
    _require(
        "sqlite" not in source.casefold(),
        "Multi-Graph introduced its own SQLite persistence",
    )
    _require(
        "CanonicalRepositoryGraph" not in source,
        "Multi-Graph must consume graph views instead of subclassing repository authority",
    )
    _require(
        "ContextNamespaceAddress" in source,
        "Multi-Graph lost namespace identity integration",
    )
    _require(
        "GraphLayer" in source and "receipt_hash" in source,
        "Multi-Graph runtime surface drift",
    )
    _require(
        "blocked_namespace_uris" in source
        and "blocked_keys" in source
        and "_blocked_reference_sets" in source,
        "Multi-Graph blocked-reference enforcement drift",
    )

    runtime = _runtime_smoke()
    enforcement = _validate_enforcement(repo)
    exact_head = _head(repo)
    _require(bool(exact_head), "unable to resolve exact Multi-Graph head")
    return {
        "schema_version": 1,
        "family": "multi-graph-retrieval",
        "claim": "MULTI_GRAPH_RETRIEVAL_V1",
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
    parser = argparse.ArgumentParser(
        description="Certify Syntavra Multi-Graph Retrieval v1"
    )
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
            "family": "multi-graph-retrieval",
            "claim": "MULTI_GRAPH_RETRIEVAL_V1",
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
