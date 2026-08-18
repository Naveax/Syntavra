#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import traceback
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.certify_python_authority import _assert_no_route_identity_copy
from tools.certify_python_authority import certify as certify_python_authority

CONTRACT_RELATIVE = Path("contracts/python/capability-completeness-registry-v1.json")
WORKFLOW_RELATIVE = Path(".github/workflows/python-capability-completeness.yml")
RELEASE_GATE_RELATIVE = Path(".github/workflows/release-main-merge-gate.yml")
PIN_POLICY_RELATIVE = Path("tests/runtime/test_release_action_pins.py")
EXPECTED_STATES = ["planned", "partial", "implemented", "verified", "certified", "external"]
EXPECTED_CLASSIFICATIONS = ["EXISTS", "HARDEN", "UNIFY", "NEW", "CERTIFY", "EXTERNAL"]
EXPECTED_MILESTONE_PREFIX = [
    "python_authority_v1",
    "capability_completeness_registry_v1",
    "rust_feature_freeze_guard_v1",
    "universal_context_item_v1",
    "evidence_store_v2",
    "typed_context_object_store_v1",
    "programmatic_execution_v1",
    "deferred_tool_discovery_v1",
    "unified_context_namespace_v1",
    "multi_graph_retrieval_v1",
    "adaptive_context_policy_v1",
]
ADVANCED_STATES = {"partial", "implemented", "verified", "certified"}


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AssertionError(f"expected JSON object: {path}")
    return value


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


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _require_evidence_paths(repo: Path, capability: dict[str, Any]) -> None:
    capability_id = str(capability.get("id") or "")
    state = str(capability.get("state") or "")
    implementation = capability.get("implementation_evidence")
    certification = capability.get("certification_evidence")
    _require(isinstance(implementation, list), f"{capability_id}: implementation_evidence must be a list")
    _require(isinstance(certification, list), f"{capability_id}: certification_evidence must be a list")

    if state in ADVANCED_STATES:
        _require(bool(implementation), f"{capability_id}: advanced state requires implementation evidence")
    if state == "certified":
        _require(bool(certification), f"{capability_id}: certified state requires certification evidence")
    if state == "planned":
        _require(not certification, f"{capability_id}: planned capability cannot claim certification evidence")

    for relative in [*implementation, *certification]:
        _require(isinstance(relative, str) and relative, f"{capability_id}: invalid evidence path")
        _require((repo / relative).is_file(), f"{capability_id}: missing evidence path: {relative}")


def _validate_capabilities(repo: Path, contract: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, int], dict[str, int]]:
    capabilities = contract.get("capabilities")
    _require(isinstance(capabilities, list) and capabilities, "capability registry is empty")
    _assert_no_route_identity_copy(capabilities)

    ids: list[str] = []
    for raw in capabilities:
        _require(isinstance(raw, dict), "capability entry must be an object")
        capability_id = raw.get("id")
        state = raw.get("state")
        classification = raw.get("classification")
        _require(isinstance(capability_id, str) and capability_id, "capability id missing")
        _require(state in EXPECTED_STATES, f"{capability_id}: unknown state {state!r}")
        _require(classification in EXPECTED_CLASSIFICATIONS, f"{capability_id}: unknown classification {classification!r}")
        _require(isinstance(raw.get("required_for_python_complete"), bool), f"{capability_id}: required flag must be bool")
        _require(isinstance(raw.get("acceptance"), str) and raw["acceptance"].strip(), f"{capability_id}: acceptance criteria missing")

        if state == "external" or classification == "EXTERNAL":
            _require(state == "external" and classification == "EXTERNAL", f"{capability_id}: external state/classification must match")
            _require(raw["required_for_python_complete"] is False, f"{capability_id}: external proof cannot block Python COMPLETE")
            _require(not raw.get("implementation_evidence"), f"{capability_id}: external proof cannot masquerade as internal implementation evidence")
            _require(not raw.get("certification_evidence"), f"{capability_id}: external proof cannot be self-certified")
        else:
            _require_evidence_paths(repo, raw)
        ids.append(capability_id)

    _require(len(ids) == len(set(ids)), "duplicate capability ids")
    by_id = {item["id"]: item for item in capabilities}
    _require("python_authority_v1" in by_id, "python_authority_v1 missing from registry")
    _require(by_id["python_authority_v1"]["state"] == "certified", "python_authority_v1 must be certified")
    _require("capability_completeness_registry_v1" in by_id, "registry milestone missing from itself")
    _require(
        by_id["capability_completeness_registry_v1"]["state"] in {"implemented", "verified", "certified"},
        "registry self-state is invalid",
    )

    state_counts = Counter(str(item["state"]) for item in capabilities)
    classification_counts = Counter(str(item["classification"]) for item in capabilities)
    return capabilities, dict(sorted(state_counts.items())), dict(sorted(classification_counts.items()))


def _validate_enforcement(repo: Path) -> dict[str, str]:
    for relative in (WORKFLOW_RELATIVE, RELEASE_GATE_RELATIVE, PIN_POLICY_RELATIVE):
        _require((repo / relative).is_file(), f"missing registry enforcement surface: {relative.as_posix()}")

    workflow = (repo / WORKFLOW_RELATIVE).read_text(encoding="utf-8")
    _require(
        "group: python-capability-completeness-${{ github.event.pull_request.number || github.ref }}" in workflow,
        "capability completeness workflow concurrency is not PR/ref scoped",
    )
    _require("tests.runtime.test_python_capability_completeness" in workflow, "capability completeness workflow lost regression suite")
    _require("tools/certify_python_capability_completeness.py" in workflow, "capability completeness workflow lost certifier")
    _require("actions/checkout@11d5960a326750d5838078e36cf38b85af677262" in workflow, "capability completeness checkout pin drift")
    _require("actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065" in workflow, "capability completeness setup-python pin drift")
    _require("actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02" in workflow, "capability completeness upload pin drift")

    release_gate = (repo / RELEASE_GATE_RELATIVE).read_text(encoding="utf-8")
    _require("tests.runtime.test_python_capability_completeness" in release_gate, "release-main gate lost completeness regression")
    _require("tools/certify_python_capability_completeness.py" in release_gate, "release-main gate lost completeness certifier")

    pin_policy = (repo / PIN_POLICY_RELATIVE).read_text(encoding="utf-8")
    _require('".github/workflows/python-capability-completeness.yml"' in pin_policy, "immutable action policy does not cover completeness workflow")

    return {
        "exact_head_workflow": WORKFLOW_RELATIVE.as_posix(),
        "release_main_gate": RELEASE_GATE_RELATIVE.as_posix(),
        "immutable_action_pin_policy": PIN_POLICY_RELATIVE.as_posix(),
    }


def certify(repo: Path) -> dict[str, Any]:
    repo = repo.resolve()
    _require(repo == ROOT, f"capability completeness certifier must run against its own checkout: {repo} != {ROOT}")

    contract = _read_json(repo / CONTRACT_RELATIVE)
    _require(contract.get("schema_version") == 1, "registry schema drift")
    _require(contract.get("family") == "capability-completeness-registry", "registry family drift")
    _require(contract.get("phase") == "python-first", "registry phase drift")
    _require(contract.get("claim") == "PYTHON_CAPABILITY_COMPLETENESS_TRACKED", "registry claim drift")
    _require(contract.get("strict") is True, "registry must remain strict")
    _assert_no_route_identity_copy(contract)

    _require(contract.get("state_vocabulary") == EXPECTED_STATES, "state vocabulary drift")
    _require(contract.get("classification_vocabulary") == EXPECTED_CLASSIFICATIONS, "classification vocabulary drift")
    milestone_order = contract.get("milestone_order")
    _require(isinstance(milestone_order, list), "milestone order missing")
    _require(milestone_order[: len(EXPECTED_MILESTONE_PREFIX)] == EXPECTED_MILESTONE_PREFIX, "canonical Python milestone order drift")
    _require(len(milestone_order) == len(set(milestone_order)), "duplicate milestone ids")

    authority = contract.get("authority") or {}
    expected_authority = {
        "python_authority": "contracts/python/python-authority-v1.json",
        "capability_inventory_reference": "contracts/python/capability-inventory-reference-v1.json",
        "roadmap_appendix": "docs/SYNTAVRA_PYTHON_FIRST_ROADMAP_APPENDIX.md",
    }
    _require(authority == expected_authority, f"registry authority map drift: {authority!r}")
    for relative in authority.values():
        _require((repo / relative).is_file(), f"missing registry authority: {relative}")

    policy = contract.get("policy") or {}
    required_true = [
        "route_identity_must_not_be_duplicated",
        "required_internal_capabilities_block_python_complete_until_certified",
        "external_claims_cannot_be_fabricated",
        "external_evidence_is_not_internal_implementation_evidence",
        "rust_resume_requires_python_complete",
        "no_silent_fallback",
        "state_advancement_requires_evidence",
        "certified_state_requires_certification_evidence",
    ]
    for key in required_true:
        _require(policy.get(key) is True, f"registry policy disabled: {key}")

    enforcement = _validate_enforcement(repo)

    python_authority = certify_python_authority(repo)
    _require(python_authority.get("ok") is True, "Python authority is not certified on this exact head")
    _require((python_authority.get("rust") or {}).get("production_promoted_routes") == 174, "Rust promotion baseline drift")
    _require((python_authority.get("rust") or {}).get("remaining_routes") == 71, "Rust remaining baseline drift")
    _require((python_authority.get("rust") or {}).get("resume_allowed") is False, "Rust resume unexpectedly allowed")

    capabilities, state_counts, classification_counts = _validate_capabilities(repo, contract)
    required_internal = [
        item for item in capabilities
        if item.get("required_for_python_complete") is True and item.get("classification") != "EXTERNAL"
    ]
    uncertified_required = [item["id"] for item in required_internal if item.get("state") != "certified"]
    computed_python_complete = not uncertified_required

    python_complete = contract.get("python_complete") or {}
    _require(python_complete.get("claim") == "PYTHON_COMPLETE", "Python COMPLETE claim drift")
    _require(python_complete.get("requires_all_internal_required_capabilities_certified") is True, "Python COMPLETE no longer requires all internal capabilities")
    _require(python_complete.get("external_superiority_required") is False, "external superiority must not be manufactured as an implementation gate")
    _require(python_complete.get("ready") is computed_python_complete, "persisted Python COMPLETE readiness disagrees with registry state")
    _require(python_complete.get("rust_resume_allowed") is computed_python_complete, "Rust resume readiness disagrees with Python COMPLETE")
    exact_head = _head(repo)
    _require(bool(exact_head), "unable to resolve exact git HEAD")

    by_id = {item["id"]: item for item in capabilities}
    current_milestone = next(
        (milestone for milestone in milestone_order if (by_id.get(milestone) or {}).get("state") != "certified"),
        "python_complete",
    )
    registry_entry = by_id["capability_completeness_registry_v1"]
    return {
        "ok": True,
        "schema_version": 1,
        "family": "capability-completeness-registry",
        "claim": contract["claim"],
        "exact_head": exact_head,
        "current_milestone": current_milestone,
        "registry_persisted_state": registry_entry["state"],
        "registry_certified": registry_entry["state"] == "certified",
        "registry_admission_ready": registry_entry["state"] in {"implemented", "verified", "certified"},
        "enforcement": enforcement,
        "state_counts": state_counts,
        "classification_counts": classification_counts,
        "required_internal_count": len(required_internal),
        "uncertified_required_count": len(uncertified_required),
        "uncertified_required": uncertified_required,
        "python_complete_ready": computed_python_complete,
        "rust_resume_allowed": computed_python_complete,
        "rust": {
            "implementation_coverage": 245,
            "production_promoted": 174,
            "remaining_parity_promotion": 71,
            "feature_development_frozen": True,
        },
        "claim_boundary": (
            "This certificate proves that Syntavra tracks Python-first capability completeness with evidence-backed lifecycle states. "
            "It admits the registry milestone itself but does not claim Python COMPLETE, external superiority, adoption, marketplace maturity, "
            "or Rust Remaining-71 behavioral parity/promotion."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Certify the Syntavra Python capability completeness registry.")
    parser.add_argument("--repo", default=".", help="Repository root")
    parser.add_argument("--out", help="Optional JSON output path")
    args = parser.parse_args()
    repo = Path(args.repo).resolve()
    try:
        report = certify(repo)
    except Exception as exc:  # pragma: no cover
        report = {
            "ok": False,
            "schema_version": 1,
            "family": "capability-completeness-registry",
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
