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

from syntavra_runtime.contract_version_graph import RuntimeContractVersionGraph

CONTRACT = Path("contracts/python/runtime-contract-version-graph-v1.json")
REGISTRY = Path("contracts/python/capability-completeness-registry-v1.json")
WORKFLOW = Path(".github/workflows/runtime-contract-version-graph.yml")
RELEASE_GATE = Path(".github/workflows/release-main-merge-gate.yml")
PIN_POLICY = Path("tests/runtime/test_release_action_pins.py")
TEST = Path("tests/runtime/test_runtime_contract_version_graph_v1.py")


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    _require(isinstance(value, dict), f"expected JSON object: {path}")
    return value


def _head(repo: Path) -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()


def _validate_contract(repo: Path) -> dict[str, Any]:
    contract = _read_json(repo / CONTRACT)
    _require(contract.get("schema_version") == 1, "contract version graph schema drift")
    _require(contract.get("family") == "runtime-contract-version-graph", "contract version graph family drift")
    _require(contract.get("phase") == "python-first", "contract version graph phase drift")
    _require(contract.get("claim") == "RUNTIME_CONTRACT_VERSION_GRAPH_V1", "contract version graph claim drift")
    _require(contract.get("strict") is True, "contract version graph must remain strict")
    _require(contract.get("runtime") == "syntavra_runtime/contract_version_graph.py", "runtime path drift")
    _require(contract.get("roots") == ["contracts/python"], "contract roots drift")

    dependency = contract.get("dependency_policy") or {}
    for key in (
        "recursive_contract_reference_discovery",
        "referenced_external_contracts_are_metadata_only_leaves",
        "missing_dependency_fails_closed",
        "path_escape_forbidden",
        "schema_version_required",
        "canonical_json_sha256_identity",
        "duplicate_nodes_forbidden",
        "deterministic_ordering",
        "self_references_are_not_dependency_edges",
    ):
        _require(dependency.get(key) is True, f"dependency policy disabled: {key}")

    invalidation = contract.get("invalidation_policy") or {}
    for key in (
        "changed_schema_or_content_invalidates_dependents",
        "added_contract_invalidates_dependents",
        "removed_contract_invalidates_dependents",
        "transitive_reverse_dependency_closure",
        "cycles_are_bounded_by_visited_identity",
        "invalidation_receipt_content_addressed",
        "silent_invalidation_forbidden",
    ):
        _require(invalidation.get(key) is True, f"invalidation policy disabled: {key}")

    ownership = contract.get("ownership_policy") or {}
    for key in (
        "metadata_only",
        "no_persistent_store",
        "no_public_cli_route",
        "does_not_own_contract_contents",
        "does_not_mutate_contracts",
        "python_product_authority_preserved",
        "rust_feature_work_forbidden",
    ):
        _require(ownership.get(key) is True, f"ownership policy disabled: {key}")
    return contract


def _validate_enforcement(repo: Path) -> dict[str, str]:
    for relative in (WORKFLOW, RELEASE_GATE, PIN_POLICY, TEST):
        _require((repo / relative).is_file(), f"missing contract version graph enforcement surface: {relative}")

    workflow = (repo / WORKFLOW).read_text(encoding="utf-8")
    for token in (
        "runtime-contract-version-graph-${{ github.event.pull_request.number || github.ref }}",
        "tests.runtime.test_runtime_contract_version_graph_v1",
        "tools/certify_runtime_contract_version_graph_v1.py",
        "actions/checkout@11d5960a326750d5838078e36cf38b85af677262",
        "actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065",
        "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02",
    ):
        _require(token in workflow, f"contract version graph workflow drift: {token}")

    release = (repo / RELEASE_GATE).read_text(encoding="utf-8")
    _require("tests.runtime.test_runtime_contract_version_graph_v1" in release, "Release Main lost contract version graph regression")
    _require("tools/certify_runtime_contract_version_graph_v1.py" in release, "Release Main lost contract version graph certifier")

    pins = (repo / PIN_POLICY).read_text(encoding="utf-8")
    _require('".github/workflows/runtime-contract-version-graph.yml"' in pins, "pin policy lost contract version graph workflow")
    return {
        "exact_head_workflow": WORKFLOW.as_posix(),
        "release_main_gate": RELEASE_GATE.as_posix(),
        "immutable_action_pin_policy": PIN_POLICY.as_posix(),
    }


def _transitive_invalidation_smoke() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="syntavra-contract-graph-cert-") as directory:
        repo = Path(directory)
        contracts = repo / "contracts/python"
        contracts.mkdir(parents=True)
        (contracts / "a.json").write_text(
            json.dumps({"schema_version": 1, "family": "a", "claim": "A", "phase": "python-first", "authority": {"b": "contracts/python/b.json"}}),
            encoding="utf-8",
        )
        (contracts / "b.json").write_text(
            json.dumps({"schema_version": 1, "family": "b", "claim": "B", "phase": "python-first", "authority": {"c": "contracts/python/c.json"}}),
            encoding="utf-8",
        )
        (contracts / "c.json").write_text(
            json.dumps({"schema_version": 1, "family": "c", "claim": "C", "phase": "python-first", "value": 1}),
            encoding="utf-8",
        )

        graph = RuntimeContractVersionGraph(repo)
        before = graph.build()
        (contracts / "c.json").write_text(
            json.dumps({"schema_version": 2, "family": "c", "claim": "C", "phase": "python-first", "value": 2}),
            encoding="utf-8",
        )
        after = graph.build()
        plan = RuntimeContractVersionGraph.invalidation_plan(before, after)
        _require(plan["changed_contracts"] == ["contracts/python/c.json"], f"changed contract smoke drift: {plan}")
        _require(
            plan["invalidated_contracts"] == ["contracts/python/a.json", "contracts/python/b.json", "contracts/python/c.json"],
            f"transitive invalidation smoke drift: {plan}",
        )
        _require(len(plan["invalidation_sha256"]) == 64, "invalidation receipt digest missing")
        return {
            "transitive_reverse_dependency_invalidation": True,
            "schema_and_content_change_detected": True,
            "content_addressed_invalidation_receipt": True,
        }


def certify(repo: Path) -> dict[str, Any]:
    repo = repo.resolve()
    _require(repo == ROOT, f"contract version graph certifier must run against its own checkout: {repo} != {ROOT}")
    contract = _validate_contract(repo)
    enforcement = _validate_enforcement(repo)

    registry = _read_json(repo / REGISTRY)
    python_complete = registry.get("python_complete") or {}
    _require(python_complete.get("rust_resume_allowed") is False, "Rust must remain retired/frozen during Python hardening")

    graph = RuntimeContractVersionGraph(repo, roots=tuple(contract["roots"]))
    first = graph.build()
    second = graph.build()
    _require(first == second, "contract version graph is not deterministic")
    _require(first["node_count"] >= 20, f"unexpectedly small contract graph: {first['node_count']}")
    _require(first["edge_count"] > 0, "contract graph has no dependency edges")
    _require(len(first["graph_sha256"]) == 64, "contract graph digest missing")

    paths = {node["path"] for node in first["nodes"]}
    for required in (
        "contracts/python/capability-completeness-registry-v1.json",
        "contracts/python/python-completion-certificate-v1.json",
        "contracts/python/runtime-contract-version-graph-v1.json",
    ):
        _require(required in paths, f"required graph contract missing: {required}")

    unchanged = RuntimeContractVersionGraph.invalidation_plan(first, second)
    _require(unchanged["changed_count"] == 0, "unchanged graph produced invalidation")
    _require(unchanged["invalidated_count"] == 0, "unchanged graph invalidated contracts")

    smoke = _transitive_invalidation_smoke()
    exact_head = _head(repo)
    _require(len(exact_head) == 40, "unable to resolve exact git head")
    return {
        "ok": True,
        "schema_version": 1,
        "claim": "RUNTIME_CONTRACT_VERSION_GRAPH_V1",
        "exact_head": exact_head,
        "admission_ready": True,
        "graph": {
            "node_count": first["node_count"],
            "edge_count": first["edge_count"],
            "graph_sha256": first["graph_sha256"],
            "roots": first["roots"],
        },
        "runtime": smoke,
        "enforcement": enforcement,
        "rust_resume_allowed": False,
        "claim_boundary": contract["claim_boundary"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Certify Syntavra Runtime Contract Version Graph v1")
    parser.add_argument("--repo", default=".")
    parser.add_argument("--out")
    args = parser.parse_args()
    try:
        report = certify(Path(args.repo))
    except Exception as exc:
        report = {
            "ok": False,
            "schema_version": 1,
            "claim": "RUNTIME_CONTRACT_VERSION_GRAPH_V1",
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
