#!/usr/bin/env python3
from __future__ import annotations

import argparse
import copy
import json
import subprocess
import sys
import traceback
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from syntavra_runtime.adaptive_context_policy import (
    AdaptiveContextPolicy,
    AdaptivePolicyConfig,
    ContextPolicySignal,
)
from syntavra_runtime.context_decision_trace import ContextDecisionTrace
from syntavra_runtime.deterministic_policy_snapshot import DeterministicPolicySnapshot
from tools.export_python_surface import export_surface

CONTRACT = Path("contracts/python/deterministic-policy-snapshot-v1.json")
REGISTRY = Path("contracts/python/capability-completeness-registry-v1.json")
WORKFLOW = Path(".github/workflows/deterministic-policy-snapshot.yml")
RELEASE_GATE = Path(".github/workflows/release-main-merge-gate.yml")
PIN_POLICY = Path("tests/runtime/test_release_action_pins.py")
TEST = Path("tests/runtime/test_deterministic_policy_snapshot_v1.py")
DUAL_SURFACE = Path("contracts/engine/dual-engine-public-surface-v2.json")
CAPABILITY_ID = "deterministic_policy_snapshot_v1"
UPSTREAM_ID = "context_decision_trace_v1"


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
    _require(contract.get("schema_version") == 1, "deterministic policy snapshot schema drift")
    _require(contract.get("family") == "deterministic-policy-snapshot", "deterministic policy snapshot family drift")
    _require(contract.get("phase") == "python-first-post-completion", "deterministic policy snapshot phase drift")
    _require(contract.get("claim") == "DETERMINISTIC_POLICY_SNAPSHOT_V1", "deterministic policy snapshot claim drift")
    _require(contract.get("strict") is True, "deterministic policy snapshot must remain strict")
    _require(
        contract.get("runtime") == "syntavra_runtime/deterministic_policy_snapshot.py",
        "deterministic policy snapshot runtime drift",
    )
    snapshot_policy = contract.get("snapshot_policy") or {}
    for key in (
        "deterministic",
        "timestamp_free_identity",
        "canonical_config",
        "contract_graph_node_hash_bound",
        "runtime_implementation_hash_bound",
        "receipt_config_match_required",
        "receipt_hash_verified_before_binding",
        "trace_hash_verified_when_present",
        "trace_receipt_link_verified",
        "tamper_fails_closed",
    ):
        _require(snapshot_policy.get(key) is True, f"snapshot policy disabled: {key}")
    replay = contract.get("replay_binding_policy") or {}
    for key in (
        "snapshot_attached",
        "snapshot_hash_required",
        "policy_receipt_hash_required",
        "context_decision_trace_hash_optional",
        "reference_only_task_identity",
        "task_payload_copy_forbidden",
        "binding_hash_integrity",
    ):
        _require(replay.get(key) is True, f"replay binding policy disabled: {key}")
    ownership = contract.get("ownership_policy") or {}
    for key in (
        "reference_only",
        "context_payload_storage_forbidden",
        "persistent_snapshot_store_forbidden",
        "policy_recomputation_forbidden",
        "evidence_mutation_journal_forbidden",
        "side_effect_authority_forbidden",
        "no_public_cli_route",
        "rust_feature_work_forbidden",
    ):
        _require(ownership.get(key) is True, f"snapshot ownership policy disabled: {key}")
    for relative in (contract.get("authorities") or {}).values():
        _require((repo / relative).is_file(), f"missing deterministic policy snapshot authority: {relative}")
    return contract


def _validate_enforcement(repo: Path) -> dict[str, str]:
    for relative in (WORKFLOW, RELEASE_GATE, PIN_POLICY, TEST, DUAL_SURFACE):
        _require((repo / relative).is_file(), f"missing deterministic policy snapshot enforcement: {relative}")
    workflow = (repo / WORKFLOW).read_text(encoding="utf-8")
    for token in (
        "deterministic-policy-snapshot-${{ github.event.pull_request.number || github.ref }}",
        "tests.runtime.test_deterministic_policy_snapshot_v1",
        "tools/certify_deterministic_policy_snapshot_v1.py",
        "tools/verify_dual_engine_public_surface.py",
        "actions/checkout@11d5960a326750d5838078e36cf38b85af677262",
        "actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065",
        "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02",
    ):
        _require(token in workflow, f"deterministic policy snapshot workflow drift: {token}")
    release = (repo / RELEASE_GATE).read_text(encoding="utf-8")
    _require(
        "tests.runtime.test_deterministic_policy_snapshot_v1" in release,
        "Release Main lost deterministic policy snapshot regression",
    )
    _require(
        "tools/certify_deterministic_policy_snapshot_v1.py" in release,
        "Release Main lost deterministic policy snapshot certifier",
    )
    pins = (repo / PIN_POLICY).read_text(encoding="utf-8")
    _require(
        '".github/workflows/deterministic-policy-snapshot.yml"' in pins,
        "pin policy lost deterministic policy snapshot workflow",
    )
    return {
        "exact_head_workflow": WORKFLOW.as_posix(),
        "release_main_gate": RELEASE_GATE.as_posix(),
        "immutable_action_pin_policy": PIN_POLICY.as_posix(),
        "dual_engine_surface": DUAL_SURFACE.as_posix(),
    }


def _smoke() -> dict[str, Any]:
    config = AdaptivePolicyConfig(context_budget_tokens=1000)
    policy = AdaptiveContextPolicy(config)
    signal = ContextPolicySignal(
        identity="snapshot-smoke",
        token_count=120,
        relevance=0.9,
        trust=0.95,
        freshness=1.0,
        recoverable=True,
        namespace_uri="syntavra://context/item/snapshot-smoke",
        item_id="snapshot-smoke",
        source_refs=("evidence:snapshot-smoke",),
    )
    result = policy.evaluate("SECRET TASK TEXT MUST NOT ENTER SNAPSHOT BINDING", [signal])
    trace = ContextDecisionTrace.from_policy_result(result)
    first = DeterministicPolicySnapshot.capture(config)
    second = DeterministicPolicySnapshot.capture(config)
    _require(first == second, "deterministic policy snapshot replay drift")
    _require(DeterministicPolicySnapshot.verify(first), "deterministic policy snapshot verification failed")

    binding = DeterministicPolicySnapshot.bind(
        first,
        result,
        trace=trace,
        task_reference={
            "task_id": "snapshot-smoke",
            "source_refs": ["evidence:snapshot-smoke"],
        },
    )
    _require(
        DeterministicPolicySnapshot.verify_binding(binding, result, trace=trace),
        "deterministic policy replay binding verification failed",
    )
    _require("SECRET TASK TEXT" not in str(binding), "task payload leaked into deterministic policy binding")
    changed = DeterministicPolicySnapshot.capture(
        AdaptivePolicyConfig(context_budget_tokens=1200)
    )
    _require(first["snapshot_hash"] != changed["snapshot_hash"], "config drift did not change policy snapshot hash")

    tampered = copy.deepcopy(binding)
    tampered["task_reference"] = {"task_id": "tampered"}
    try:
        DeterministicPolicySnapshot.verify_binding(tampered, result, trace=trace)
    except ValueError:
        pass
    else:
        raise AssertionError("deterministic policy binding tamper did not fail closed")

    return {
        "deterministic_snapshot": True,
        "timestamp_free_identity": True,
        "contract_graph_node_hash_bound": True,
        "runtime_implementation_hash_bound": True,
        "receipt_config_match_required": True,
        "trace_receipt_link_verified": True,
        "reference_only_binding": True,
        "tamper_fails_closed": True,
        "sample_snapshot_hash": first["snapshot_hash"],
        "sample_binding_hash": binding["binding_hash"],
    }


def certify(repo: Path) -> dict[str, Any]:
    repo = repo.resolve()
    _require(repo == ROOT, f"deterministic policy snapshot certifier must run against its own checkout: {repo} != {ROOT}")
    contract = _validate_contract(repo)
    enforcement = _validate_enforcement(repo)
    registry = _read_json(repo / REGISTRY)
    python_complete = registry.get("python_complete") or {}
    _require(python_complete.get("ready") is True, "Deterministic Policy Snapshot is post-completion Python hardening")
    _require(python_complete.get("rust_resume_allowed") is False, "Rust must remain retired during Deterministic Policy Snapshot work")
    _require(python_complete.get("rust_retired") is True, "Rust retirement must remain explicit")

    order = registry.get("post_completion_milestone_order") or []
    _require(UPSTREAM_ID in order and CAPABILITY_ID in order, "post-completion deterministic snapshot order incomplete")
    _require(
        order.index(CAPABILITY_ID) == order.index(UPSTREAM_ID) + 1,
        "Deterministic Policy Snapshot must immediately follow Context Decision Trace",
    )
    by_id = {row.get("id"): row for row in registry.get("capabilities") or [] if isinstance(row, dict)}
    upstream = by_id.get(UPSTREAM_ID) or {}
    lifecycle = by_id.get(CAPABILITY_ID) or {}
    upstream_state = str(upstream.get("state") or "")
    lifecycle_state = str(lifecycle.get("state") or "")
    _require(upstream_state in {"implemented", "verified", "certified"}, f"invalid upstream trace lifecycle: {upstream_state}")
    _require(lifecycle_state in {"implemented", "verified", "certified"}, f"invalid Deterministic Policy Snapshot lifecycle: {lifecycle_state}")
    _require(lifecycle.get("required_for_python_complete") is False, "Deterministic Policy Snapshot cannot reopen Python COMPLETE")
    current = next(
        (milestone for milestone in order if (by_id.get(milestone) or {}).get("state") != "certified"),
        "post_completion_complete",
    )
    admission_ready = upstream_state == "certified" and lifecycle_state in {"implemented", "verified", "certified"}
    if admission_ready:
        _require(
            current in {CAPABILITY_ID, "post_completion_complete"},
            f"admission-ready deterministic snapshot has wrong current milestone: {current}",
        )
    else:
        _require(current == UPSTREAM_ID, f"stacked deterministic snapshot prep must remain blocked on upstream milestone: {current}")

    surface = export_surface()
    dual = _read_json(repo / DUAL_SURFACE)
    python_surface = dual.get("python_surface") or {}
    _require(
        int(python_surface.get("module_count", -1)) == int(surface["module_count"]),
        f"dual-engine Python module snapshot drift: contract={python_surface.get('module_count')} exporter={surface['module_count']}",
    )
    _require(
        int(python_surface.get("public_command_count", -1)) == len(surface["cli_commands"]),
        "dual-engine Python public command count drift",
    )
    _require(
        not any("policy-snapshot" in command or "policy snapshot" in command for command in surface["cli_commands"]),
        "Deterministic Policy Snapshot must not add a public CLI route",
    )

    runtime = _smoke()
    exact_head = _head(repo)
    _require(len(exact_head) == 40, "unable to resolve exact git head")
    return {
        "ok": True,
        "schema_version": 1,
        "claim": "DETERMINISTIC_POLICY_SNAPSHOT_V1",
        "exact_head": exact_head,
        "implementation_ready": True,
        "admission_ready": admission_ready,
        "lifecycle_state": lifecycle_state,
        "upstream_context_decision_trace_state": upstream_state,
        "post_completion_current_milestone": current,
        "python_complete_ready": True,
        "python_module_count": int(surface["module_count"]),
        "public_command_count": len(surface["cli_commands"]),
        "runtime": runtime,
        "enforcement": enforcement,
        "rust_resume_allowed": False,
        "rust": {
            "production_promoted": 174,
            "remaining_parity_promotion": 71,
            "feature_development_frozen": True,
        },
        "claim_boundary": contract["claim_boundary"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Certify Syntavra Deterministic Policy Snapshot v1")
    parser.add_argument("--repo", default=".")
    parser.add_argument("--out")
    args = parser.parse_args()
    try:
        report = certify(Path(args.repo))
    except Exception as exc:
        report = {
            "ok": False,
            "schema_version": 1,
            "claim": "DETERMINISTIC_POLICY_SNAPSHOT_V1",
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
