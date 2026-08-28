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
    ContextPolicyState,
)
from syntavra_runtime.context_decision_trace import REQUIRED_TRACE_DECISIONS, ContextDecisionTrace

CONTRACT = Path("contracts/python/context-decision-trace-v1.json")
REGISTRY = Path("contracts/python/capability-completeness-registry-v1.json")
WORKFLOW = Path(".github/workflows/context-decision-trace.yml")
RELEASE_GATE = Path(".github/workflows/release-main-merge-gate.yml")
PIN_POLICY = Path("tests/runtime/test_release_action_pins.py")
TEST = Path("tests/runtime/test_context_decision_trace_v1.py")
CAPABILITY_ID = "context_decision_trace_v1"
UPSTREAM_ID = "runtime_contract_version_graph_v1"


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
    _require(contract.get("schema_version") == 1, "context decision trace schema drift")
    _require(contract.get("family") == "context-decision-trace", "context decision trace family drift")
    _require(contract.get("phase") == "python-first-post-completion", "context decision trace phase drift")
    _require(contract.get("claim") == "CONTEXT_DECISION_TRACE_V1", "context decision trace claim drift")
    _require(contract.get("strict") is True, "context decision trace must remain strict")
    _require(contract.get("runtime") == "syntavra_runtime/context_decision_trace.py", "context decision trace runtime drift")
    _require(
        set(contract.get("required_decision_types") or []) == REQUIRED_TRACE_DECISIONS,
        "required roadmap decision vocabulary drift",
    )
    trace_policy = contract.get("trace_policy") or {}
    for key in (
        "deterministic",
        "timestamp_free_identity",
        "policy_receipt_hash_verified_before_trace",
        "recommended_and_effective_decisions_separate",
        "item_and_session_decisions_traced",
        "retrieval_recorded_as_explicit_later_event",
        "event_sequence_contiguous",
        "event_hash_integrity",
        "previous_event_hash_integrity",
        "trace_hash_integrity",
        "tamper_fails_closed",
    ):
        _require(trace_policy.get(key) is True, f"trace policy disabled: {key}")
    ownership = contract.get("ownership_policy") or {}
    for key in (
        "reference_only",
        "context_payload_storage_forbidden",
        "persistent_journal_forbidden",
        "policy_snapshot_ownership_forbidden",
        "policy_recomputation_forbidden",
        "side_effect_authority_forbidden",
        "no_public_cli_route",
        "rust_feature_work_forbidden",
    ):
        _require(ownership.get(key) is True, f"trace ownership policy disabled: {key}")
    for relative in (contract.get("authorities") or {}).values():
        _require((repo / relative).is_file(), f"missing context trace authority: {relative}")
    return contract


def _validate_enforcement(repo: Path) -> dict[str, str]:
    for relative in (WORKFLOW, RELEASE_GATE, PIN_POLICY, TEST):
        _require((repo / relative).is_file(), f"missing context decision trace enforcement: {relative}")
    workflow = (repo / WORKFLOW).read_text(encoding="utf-8")
    for token in (
        "context-decision-trace-${{ github.event.pull_request.number || github.ref }}",
        "tests.runtime.test_context_decision_trace_v1",
        "tools/certify_context_decision_trace_v1.py",
        "actions/checkout@11d5960a326750d5838078e36cf38b85af677262",
        "actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065",
        "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02",
    ):
        _require(token in workflow, f"context decision trace workflow drift: {token}")
    release = (repo / RELEASE_GATE).read_text(encoding="utf-8")
    _require("tests.runtime.test_context_decision_trace_v1" in release, "Release Main lost context decision trace regression")
    _require("tools/certify_context_decision_trace_v1.py" in release, "Release Main lost context decision trace certifier")
    pins = (repo / PIN_POLICY).read_text(encoding="utf-8")
    _require('".github/workflows/context-decision-trace.yml"' in pins, "pin policy lost context decision trace workflow")
    return {
        "exact_head_workflow": WORKFLOW.as_posix(),
        "release_main_gate": RELEASE_GATE.as_posix(),
        "immutable_action_pin_policy": PIN_POLICY.as_posix(),
    }


def _smoke() -> dict[str, Any]:
    policy = AdaptiveContextPolicy(AdaptivePolicyConfig(context_budget_tokens=1000))

    def signal(identity: str, **overrides: Any) -> ContextPolicySignal:
        values: dict[str, Any] = {
            "identity": identity,
            "token_count": 120,
            "relevance": 0.8,
            "trust": 0.95,
            "freshness": 1.0,
            "recoverable": True,
            "namespace_uri": f"syntavra://context/item/{identity}",
            "item_id": identity,
            "source_refs": (f"evidence:{identity}",),
        }
        values.update(overrides)
        return ContextPolicySignal(**values)

    include = ContextDecisionTrace.from_policy_result(policy.evaluate("include", [signal("include", relevance=1.0)]))
    compress = ContextDecisionTrace.from_policy_result(
        policy.evaluate("compress", [signal("compress", relevance=0.25, trust=0.65, freshness=0.7)])
    )
    omit_result = policy.evaluate("omit", [signal("omit", relevance=0.0, trust=0.2, freshness=0.2)])
    omit = ContextDecisionTrace.from_policy_result(omit_result)
    retrieve = ContextDecisionTrace.append_retrieval(
        omit,
        identity="omit",
        source_refs=("evidence:omit",),
        namespace_uri="syntavra://context/item/omit",
        item_id="omit",
        visible_tokens=64,
    )
    reset = ContextDecisionTrace.from_policy_result(
        policy.evaluate(
            "reset",
            [signal("reset", token_count=10, relevance=1.0)],
            state=ContextPolicyState(current_context_tokens=970, reset_allowed=True),
        )
    )
    abstain = ContextDecisionTrace.from_policy_result(
        policy.evaluate("abstain", [signal("abstain", security_denied=True, relevance=1.0)])
    )
    shadow = ContextDecisionTrace.from_policy_result(
        policy.evaluate(
            "shadow",
            [signal("shadow", token_count=10, relevance=1.0)],
            state=ContextPolicyState(current_context_tokens=970, reset_allowed=True, shadow_mode=True),
        )
    )

    observed = {
        include["events"][0]["recommended_decision"],
        compress["events"][0]["recommended_decision"],
        omit["events"][0]["recommended_decision"],
        retrieve["events"][-1]["recommended_decision"],
        reset["events"][-1]["recommended_decision"],
        abstain["events"][-1]["recommended_decision"],
    }
    _require(observed == REQUIRED_TRACE_DECISIONS, f"required decision trace coverage drift: {observed}")
    _require(shadow["events"][-1]["recommended_decision"] == "reset", "shadow recommendation lost")
    _require(shadow["events"][-1]["effective_decision"] == "include", "shadow effective action drift")

    replay = ContextDecisionTrace.from_policy_result(omit_result)
    _require(replay["trace_hash"] == omit["trace_hash"], "decision trace replay is not deterministic")
    mutated = copy.deepcopy(omit)
    mutated["events"][0]["reason_codes"] = ("TAMPER",)
    try:
        ContextDecisionTrace.verify(mutated)
    except ValueError:
        pass
    else:
        raise AssertionError("context decision trace tamper did not fail closed")

    return {
        "required_decisions_traced": sorted(observed),
        "deterministic_replay": True,
        "tamper_fails_closed": True,
        "recommended_effective_separation": True,
        "reference_only_retrieval_event": True,
        "sample_trace_hash": retrieve["trace_hash"],
    }


def certify(repo: Path) -> dict[str, Any]:
    repo = repo.resolve()
    _require(repo == ROOT, f"context decision trace certifier must run against its own checkout: {repo} != {ROOT}")
    contract = _validate_contract(repo)
    enforcement = _validate_enforcement(repo)
    registry = _read_json(repo / REGISTRY)
    python_complete = registry.get("python_complete") or {}
    _require(python_complete.get("ready") is True, "Context Decision Trace is post-completion Python hardening")
    _require(python_complete.get("rust_resume_allowed") is False, "Rust must remain retired during Context Decision Trace work")
    _require(python_complete.get("rust_retired") is True, "Rust retirement must remain explicit")

    order = registry.get("post_completion_milestone_order") or []
    _require(UPSTREAM_ID in order and CAPABILITY_ID in order, "post-completion trace order incomplete")
    _require(order.index(CAPABILITY_ID) == order.index(UPSTREAM_ID) + 1, "Context Decision Trace must immediately follow Runtime Contract Version Graph")
    by_id = {row.get("id"): row for row in registry.get("capabilities") or [] if isinstance(row, dict)}
    upstream = by_id.get(UPSTREAM_ID) or {}
    lifecycle = by_id.get(CAPABILITY_ID) or {}
    upstream_state = str(upstream.get("state") or "")
    lifecycle_state = str(lifecycle.get("state") or "")
    _require(upstream_state in {"implemented", "verified", "certified"}, f"invalid upstream graph lifecycle: {upstream_state}")
    _require(lifecycle_state in {"implemented", "verified", "certified"}, f"invalid Context Decision Trace lifecycle: {lifecycle_state}")
    _require(lifecycle.get("required_for_python_complete") is False, "Context Decision Trace cannot reopen Python COMPLETE")
    current = next(
        (milestone for milestone in order if (by_id.get(milestone) or {}).get("state") != "certified"),
        "post_completion_complete",
    )
    implementation_ready = True
    admission_ready = upstream_state == "certified" and lifecycle_state in {"implemented", "verified", "certified"}
    if upstream_state != "certified":
        _require(current == UPSTREAM_ID, f"stacked trace prep must remain blocked on upstream milestone: {current}")
    elif lifecycle_state == "certified":
        _require(current != CAPABILITY_ID, f"certified Context Decision Trace cannot remain the current milestone: {current}")
    else:
        _require(current == CAPABILITY_ID, f"active Context Decision Trace has wrong current milestone: {current}")

    runtime = _smoke()
    exact_head = _head(repo)
    _require(len(exact_head) == 40, "unable to resolve exact git head")
    return {
        "ok": True,
        "schema_version": 1,
        "claim": "CONTEXT_DECISION_TRACE_V1",
        "exact_head": exact_head,
        "implementation_ready": implementation_ready,
        "admission_ready": admission_ready,
        "lifecycle_state": lifecycle_state,
        "upstream_runtime_contract_graph_state": upstream_state,
        "post_completion_current_milestone": current,
        "python_complete_ready": True,
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
    parser = argparse.ArgumentParser(description="Certify Syntavra Context Decision Trace v1")
    parser.add_argument("--repo", default=".")
    parser.add_argument("--out")
    args = parser.parse_args()
    try:
        report = certify(Path(args.repo))
    except Exception as exc:
        report = {
            "ok": False,
            "schema_version": 1,
            "claim": "CONTEXT_DECISION_TRACE_V1",
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
