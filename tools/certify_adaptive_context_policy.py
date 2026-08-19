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

from syntavra_runtime.adaptive_context_policy import (
    AdaptiveContextPolicy,
    AdaptivePolicyConfig,
    ContextPolicySignal,
    ContextPolicyState,
    ITEM_ACTIONS,
    SESSION_ACTIONS,
)
from syntavra_runtime.optimization_modes import MODES
from tools.certify_python_capability_completeness import certify as certify_completeness
from tools.certify_rust_feature_freeze_guard import certify as certify_rust_freeze

CONTRACT_RELATIVE = Path("contracts/python/adaptive-context-policy-v1.json")
REGISTRY_RELATIVE = Path("contracts/python/capability-completeness-registry-v1.json")
WORKFLOW_RELATIVE = Path(".github/workflows/adaptive-context-policy.yml")
RELEASE_GATE_RELATIVE = Path(".github/workflows/release-main-merge-gate.yml")
RUNTIME_RELATIVE = Path("syntavra_runtime/adaptive_context_policy.py")
TEST_RELATIVE = Path("tests/runtime/test_adaptive_context_policy.py")

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
    policy = AdaptiveContextPolicy(
        AdaptivePolicyConfig.from_optimization_mode(MODES["ultra"])
    )

    def signal(identity: str, **overrides: Any) -> ContextPolicySignal:
        values: dict[str, Any] = {
            "identity": identity,
            "token_count": 180,
            "relevance": 0.8,
            "trust": 0.96,
            "freshness": 1.0,
            "recoverable": True,
        }
        values.update(overrides)
        return ContextPolicySignal(**values)

    normal = policy.evaluate(
        "repair adaptive context policy",
        [
            signal("exact", relevance=1.0, exact_required=True),
            signal("medium", relevance=0.5, trust=0.8, freshness=0.85),
            signal("low", relevance=0.0, trust=0.2, freshness=0.3),
        ],
    )
    actions = {row["identity"]: row["recommended_action"] for row in normal["decisions"]}
    _require(actions["exact"] == "KEEP", "exact-required context was not kept")
    _require(actions["medium"] in {"SUMMARIZE", "COMPRESS"}, "medium context decision drift")
    _require(actions["low"] == "EXTERNALIZE", "low recoverable context was not externalized")
    _require(len(normal["receipt"]["receipt_hash"]) == 64, "policy receipt hash drift")

    denied = policy.evaluate(
        "unsafe context",
        [signal("denied", security_denied=True, relevance=1.0)],
    )
    _require(denied["recommended_session_action"] == "ABSTAIN", "security deny did not abstain")

    branch = policy.evaluate(
        "new task",
        [signal("branch")],
        state=ContextPolicyState(task_drift=0.9, branch_allowed=True),
    )
    _require(branch["recommended_session_action"] == "BRANCH", "task drift did not branch")

    reset = policy.evaluate(
        "continue task",
        [signal("reset", token_count=10)],
        state=ContextPolicyState(
            current_context_tokens=1450,
            reset_allowed=True,
        ),
    )
    _require(reset["recommended_session_action"] == "RESET", "recoverable pressure did not reset")

    shadow = policy.evaluate(
        "continue shadow",
        [signal("shadow", token_count=10)],
        state=ContextPolicyState(
            current_context_tokens=1450,
            reset_allowed=True,
            shadow_mode=True,
        ),
    )
    _require(shadow["recommended_session_action"] == "RESET", "shadow recommendation drift")
    _require(shadow["effective_session_action"] == "KEEP", "shadow mode claimed session enforcement")
    _require(
        all(row["effective_action"] == "KEEP" for row in shadow["decisions"]),
        "shadow mode claimed item enforcement",
    )

    status = policy.status()
    _require(status["payload_authority"] is False, "policy claimed payload authority")
    _require(status["persistent_store"] is False, "policy introduced persistence")
    _require(status["side_effects"] is False, "policy claimed side effects")
    return {
        "item_actions": sorted(ITEM_ACTIONS),
        "session_actions": sorted(SESSION_ACTIONS),
        "deterministic": True,
        "explainable": True,
        "budget_aware": True,
        "security_abstain": True,
        "task_drift_branch": True,
        "recoverable_pressure_reset": True,
        "shadow_non_enforcement": True,
        "content_addressed_receipt": True,
        "payload_authority": False,
        "persistent_store": False,
        "side_effects": False,
    }


def _validate_workflow(workflow: str) -> None:
    refs = USES_RE.findall(workflow)
    external = {ref for ref in refs if not ref.startswith("./")}
    _require(
        external == EXPECTED_ACTION_REFS,
        f"Adaptive Context Policy external action set drift: {sorted(external)}",
    )
    for ref in external:
        slug, revision = ref.rsplit("@", 1)
        _require(bool(slug), f"invalid action slug: {ref}")
        _require(
            HEX40_RE.fullmatch(revision) is not None,
            f"mutable or non-SHA action ref: {ref}",
        )


def _validate_enforcement(repo: Path) -> dict[str, str]:
    for relative in (WORKFLOW_RELATIVE, RELEASE_GATE_RELATIVE, TEST_RELATIVE):
        _require(
            (repo / relative).is_file(),
            f"missing Adaptive Context Policy enforcement surface: {relative.as_posix()}",
        )
    workflow = (repo / WORKFLOW_RELATIVE).read_text(encoding="utf-8")
    _require(
        "group: adaptive-context-policy-${{ github.event.pull_request.number || github.ref }}"
        in workflow,
        "Adaptive Context Policy concurrency is not PR/ref scoped",
    )
    _require(
        "tests.runtime.test_adaptive_context_policy" in workflow,
        "Adaptive Context Policy workflow lost regression suite",
    )
    _require(
        "tools/certify_adaptive_context_policy.py" in workflow,
        "Adaptive Context Policy workflow lost certifier",
    )
    _validate_workflow(workflow)

    release_gate = (repo / RELEASE_GATE_RELATIVE).read_text(encoding="utf-8")
    _require("name: Release Main Merge Gate" in release_gate, "Release Main co-gate identity drift")
    _require(
        "tests.runtime.test_adaptive_context_policy" in release_gate,
        "Release Main co-gate is not bound to Adaptive Context Policy regression",
    )
    _require(
        "tools/certify_adaptive_context_policy.py" in release_gate,
        "Release Main co-gate is not bound to Adaptive Context Policy certifier",
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
        f"Adaptive Context Policy certifier must run against its own checkout: {repo} != {ROOT}",
    )
    contract = _read_json(repo / CONTRACT_RELATIVE)
    _require(contract.get("schema_version") == 1, "Adaptive Context Policy schema drift")
    _require(contract.get("family") == "adaptive-context-policy", "Adaptive Context Policy family drift")
    _require(contract.get("phase") == "python-first", "Adaptive Context Policy phase drift")
    _require(contract.get("claim") == "ADAPTIVE_CONTEXT_POLICY_V1", "Adaptive Context Policy claim drift")
    _require(contract.get("strict") is True, "Adaptive Context Policy must remain strict")
    _require(contract.get("runtime") == RUNTIME_RELATIVE.as_posix(), "Adaptive Context Policy runtime path drift")
    _require(set(contract.get("item_actions") or ()) == ITEM_ACTIONS, "Adaptive item action surface drift")
    _require(set(contract.get("session_actions") or ()) == SESSION_ACTIONS, "Adaptive session action surface drift")

    authorities = contract.get("existing_authorities") or {}
    for value in authorities.values():
        relative = str(value).split(":", 1)[0]
        _require((repo / relative).is_file(), f"missing reused authority: {relative}")

    ownership = contract.get("ownership_policy") or {}
    for key in (
        "reference_only_signals",
        "exact_payload_storage_forbidden",
        "parallel_persistent_store_forbidden",
        "policy_side_effects_forbidden",
        "existing_budget_authority_reused",
        "existing_context_pack_reused",
        "multi_graph_results_consumed_by_reference",
    ):
        _require(ownership.get(key) is True, f"Adaptive ownership policy disabled: {key}")
    _require(ownership.get("public_cli_route_added") is False, "Adaptive policy invented a public CLI route")

    decision = contract.get("decision_policy") or {}
    for key in (
        "deterministic",
        "explainable_reason_codes",
        "utility_scored",
        "risk_scored",
        "budget_aware",
        "exact_required_preserved",
        "impossible_exact_budget_abstains",
        "security_deny_abstains",
        "tainted_exact_required_abstains",
        "irreversible_unresolved_risk_abstains",
        "task_drift_branches_when_allowed",
        "task_drift_without_branch_permission_abstains",
        "reset_requires_recoverable_context",
        "unsafe_reset_abstains",
        "budget_pressure_economizes_lower_utility_first",
        "no_silent_fallback",
    ):
        _require(decision.get(key) is True, f"Adaptive decision policy disabled: {key}")

    shadow = contract.get("shadow_policy") or {}
    for key in (
        "supported",
        "recommended_action_recorded",
        "effective_item_action_remains_keep",
        "effective_session_action_remains_keep",
        "side_effect_claims_forbidden",
    ):
        _require(shadow.get(key) is True, f"Adaptive shadow policy disabled: {key}")

    receipt = contract.get("receipt_policy") or {}
    for key in (
        "content_addressed",
        "timestamps_excluded_from_identity",
        "config_recorded",
        "state_recorded",
        "reference_only_signals_recorded",
        "recommended_and_effective_actions_recorded",
        "reason_codes_recorded",
        "token_metrics_recorded",
        "budget_fit_recorded",
    ):
        _require(receipt.get(key) is True, f"Adaptive receipt policy disabled: {key}")

    completeness = certify_completeness(repo)
    _require(completeness.get("ok") is True, "capability completeness is not valid")
    _require(
        completeness.get("current_milestone") == "adaptive_context_policy_v1",
        "registry has not advanced to adaptive_context_policy_v1",
    )
    _require(completeness.get("python_complete_ready") is False, "Python COMPLETE unexpectedly true")
    _require(completeness.get("rust_resume_allowed") is False, "Rust resume unexpectedly true")

    registry = _read_json(repo / REGISTRY_RELATIVE)
    by_id = {
        row["id"]: row
        for row in registry.get("capabilities", [])
        if isinstance(row, dict) and isinstance(row.get("id"), str)
    }
    _require(
        (by_id.get("multi_graph_retrieval_v1") or {}).get("state") == "certified",
        "Multi-Graph Retrieval must be certified before Adaptive Context Policy admission",
    )
    _require(
        (by_id.get("adaptive_context_policy_v1") or {}).get("state") in {"implemented", "verified"},
        "Adaptive Context Policy registry state must be pre-certification implemented/verified",
    )

    rust_freeze = certify_rust_freeze(repo)
    _require(rust_freeze.get("ok") is True, "Rust feature freeze is not certified")
    _require((rust_freeze.get("rust") or {}).get("production_promoted") == 174, "Rust production authority drift")
    _require((rust_freeze.get("rust") or {}).get("remaining_parity_promotion") == 71, "Rust remaining parity/promotion drift")

    source = (repo / RUNTIME_RELATIVE).read_text(encoding="utf-8")
    _require("sqlite" not in source.casefold(), "Adaptive policy introduced SQLite persistence")
    _require("TaskContextPack" in source, "Adaptive policy stopped reusing TaskContextPack")
    _require("OptimizationMode" in source, "Adaptive policy stopped reusing OptimizationMode")
    _require("receipt_hash" in source, "Adaptive policy lost receipt identity")

    runtime = _runtime_smoke()
    enforcement = _validate_enforcement(repo)
    exact_head = _head(repo)
    _require(bool(exact_head), "unable to resolve exact Adaptive Context Policy head")
    return {
        "schema_version": 1,
        "family": "adaptive-context-policy",
        "claim": "ADAPTIVE_CONTEXT_POLICY_V1",
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
    parser = argparse.ArgumentParser(description="Certify Syntavra Adaptive Context Policy v1")
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
            "family": "adaptive-context-policy",
            "claim": "ADAPTIVE_CONTEXT_POLICY_V1",
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
