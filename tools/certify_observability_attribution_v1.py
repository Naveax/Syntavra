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

from tools.python_phase_state import validate_python_complete_state

from syntavra_runtime.observability_attribution import (
    AttributionPolicy, ObservabilityAttribution, PerformanceBudget, PerformanceSample,
    QualitySLO, QualitySample, RecoveryBudget, RecoverySample,
)
from syntavra_runtime.runtime_evidence import RuntimeEvidenceGraph
from syntavra_runtime.usage_receipt_ledger import UsageReceiptLedger

CONTRACT = Path("contracts/python/observability-attribution-v1.json")
REGISTRY = Path("contracts/python/capability-completeness-registry-v1.json")
WORKFLOW = Path(".github/workflows/observability-attribution.yml")
RELEASE_GATE = Path(".github/workflows/release-main-merge-gate.yml")
PIN_POLICY = Path("tests/runtime/test_release_action_pins.py")


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    _require(isinstance(value, dict), f"expected JSON object: {path}")
    return value


def _head(repo: Path) -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()


def certify(repo: Path) -> dict[str, Any]:
    repo = repo.resolve()
    _require(repo == ROOT, "observability attribution certifier must run against its own checkout")
    contract = _read_json(repo / CONTRACT)
    _require(contract.get("schema_version") == 1, "observability attribution schema drift")
    _require(contract.get("family") == "observability-attribution", "observability attribution family drift")
    _require(contract.get("phase") == "python-first", "observability attribution phase drift")
    _require(contract.get("claim") == "OBSERVABILITY_ATTRIBUTION_V1", "observability attribution claim drift")
    _require(contract.get("strict") is True, "observability attribution contract must remain strict")

    ownership = contract.get("ownership_policy") or {}
    _require(ownership.get("parallel_persistent_store_forbidden") is True, "parallel store policy disabled")
    _require(ownership.get("provider_usage_store_duplication_forbidden") is True, "provider usage duplication policy disabled")
    _require(ownership.get("token_attribution_store_duplication_forbidden") is True, "token attribution duplication policy disabled")
    _require(ownership.get("runtime_evidence_graph_reused") is True, "RuntimeEvidenceGraph reuse disabled")
    _require(ownership.get("existing_budget_authority_reused") is True, "budget authority reuse disabled")
    _require(ownership.get("existing_quality_authority_reused") is True, "quality authority reuse disabled")
    _require(ownership.get("public_cli_route_added") is False, "observability milestone must not add public CLI route")

    status = ObservabilityAttribution.status()
    _require(status["parallel_persistent_store"] is False, "runtime introduced parallel persistence")
    _require(status["provider_usage_store_duplicated"] is False, "runtime duplicated provider usage store")
    _require(status["token_attribution_store_duplicated"] is False, "runtime duplicated token attribution store")
    _require(status["public_cli_route"] is False, "runtime introduced public CLI route")

    with tempfile.TemporaryDirectory(prefix="syntavra-observability-attribution-") as directory:
        root = Path(directory)
        graph = RuntimeEvidenceGraph(root / "runtime-evidence.sqlite3")
        usage_ledger = UsageReceiptLedger(root / "usage.sqlite3")
        usage = usage_ledger.record(
            task_id="certify-task", arm_id="candidate", repetition=1, cache_mode="warm",
            provider="openai", request_id="certify-request",
            provider_response={"usage": {"input_tokens": 24, "input_tokens_details": {"cached_tokens": 8}, "output_tokens": 4}},
            quota_cost=1.0, hardware_hash="a" * 64,
        ).receipt
        token = usage_ledger.record_attribution(
            task_id="certify-task", arm_id="candidate", repetition=1, session_id="certify-session",
            provider="openai", model="fixture-model", request_id_hash=usage.request_id_hash,
            provider_receipt_hash=usage.receipt_hash,
            sources={"user_prompt": 8, "repository_context": 8, "assistant_output": 4},
            confidence={"user_prompt": "LOCALLY_TOKENIZED", "repository_context": "LOCALLY_TOKENIZED", "assistant_output": "PROVIDER_OBSERVED"},
            baseline_tokens=20, baseline_confidence="LOCALLY_TOKENIZED",
        )
        policy = AttributionPolicy(
            PerformanceBudget(500.0, 250.0, 64 * 1024 * 1024, 8 * 1024 * 1024, 128),
            RecoveryBudget(2.0, True), QualitySLO(1.0, 1.0, 1.0, 0),
        )
        gate = ObservabilityAttribution.evaluate(
            policy=policy, performance=PerformanceSample(100.0, 50.0, 1024, 512, 16),
            recovery=RecoverySample(100, 125, True), quality_samples=[QualitySample(True, True, True, 0)],
        )
        _require(gate.ok is True, "passing observability gate failed")
        runtime = ObservabilityAttribution(graph)
        for kind, action, subject in (("context", "include", "repository-context"), ("tool", "select", "read-file"), ("policy", "allow", "safe-action")):
            runtime.record_decision(
                task_id="certify-task", session_id="certify-session", decision_kind=kind,
                action=action, subject=subject, policy=policy, evidence_hashes=["b" * 64],
                usage_receipt=usage, token_receipt=token, gate=gate, repository_commit="c" * 40,
            )
        relations = {row["relation"]: row["count"] for row in graph.stats()["relations"]}
        _require(relations.get("ATTRIBUTED_DECISION") == 3, "context/tool/policy decisions were not attributed")
        _require(relations.get("LINKED_PROVIDER_USAGE") == 3, "provider usage receipt linkage failed")
        _require(relations.get("LINKED_TOKEN_ATTRIBUTION") == 3, "token attribution receipt linkage failed")
        _require(relations.get("EVALUATED_BY") == 3, "gate linkage failed")
        failed = ObservabilityAttribution.evaluate(
            policy=policy, performance=PerformanceSample(900.0, 400.0, 128 * 1024 * 1024, 16 * 1024 * 1024, 256),
            recovery=RecoverySample(100, 300, False), quality_samples=[QualitySample(False, False, False, 1)],
        )
        _require(failed.ok is False, "violating observability gate did not fail closed")

    for relative in (WORKFLOW, RELEASE_GATE, PIN_POLICY):
        _require((repo / relative).is_file(), f"missing observability enforcement surface: {relative}")
    workflow = (repo / WORKFLOW).read_text(encoding="utf-8")
    _require("tests.runtime.test_observability_attribution_v1" in workflow, "observability workflow lost regression suite")
    _require("tools/certify_observability_attribution_v1.py" in workflow, "observability workflow lost certifier")
    for pin in (
        "actions/checkout@11d5960a326750d5838078e36cf38b85af677262",
        "actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065",
        "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02",
    ):
        _require(pin in workflow, f"observability workflow pin drift: {pin}")
    release = (repo / RELEASE_GATE).read_text(encoding="utf-8")
    _require("tests.runtime.test_observability_attribution_v1" in release, "Release Main lost observability regression")
    _require("tools/certify_observability_attribution_v1.py" in release, "Release Main lost observability certifier")
    pins = (repo / PIN_POLICY).read_text(encoding="utf-8")
    _require('".github/workflows/observability-attribution.yml"' in pins, "immutable pin policy lost observability workflow")

    registry = _read_json(repo / REGISTRY)
    by_id = {item["id"]: item for item in registry["capabilities"]}
    _require(by_id["host_adapter_conformance_v1"]["state"] == "certified", "Host Adapter must be certified first")
    lifecycle_state = by_id["observability_attribution_v1"]["state"]
    _require(lifecycle_state in {"partial", "implemented", "verified", "certified"}, "invalid observability lifecycle state")
    validate_python_complete_state(registry)

    return {
        "ok": True, "schema_version": 1, "claim": "OBSERVABILITY_ATTRIBUTION_V1", "exact_head": _head(repo),
        "runtime_ready": True, "lifecycle_state": lifecycle_state,
        "admission_ready": lifecycle_state in {"implemented", "verified", "certified"},
        "decision_kinds": ["context", "tool", "policy"], "provider_usage_receipt_linkage": True,
        "token_attribution_receipt_linkage": True, "performance_budget_gate": True,
        "recovery_amplification_gate": True, "context_quality_slo_gate": True,
        "parallel_persistent_store": False, "public_cli_route": False,
        "python_complete_ready": True, "rust_resume_allowed": False,
        "rust": {"production_promoted": 174, "remaining_parity_promotion": 71, "feature_development_frozen": True},
        "claim_boundary": contract["claim_boundary"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Certify Syntavra Observability Attribution v1")
    parser.add_argument("--repo", default=".")
    parser.add_argument("--out")
    args = parser.parse_args()
    try:
        report = certify(Path(args.repo))
    except Exception as exc:
        report = {"ok": False, "schema_version": 1, "claim": "OBSERVABILITY_ATTRIBUTION_V1", "error": f"{type(exc).__name__}: {exc}", "traceback": traceback.format_exc()}
    payload = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.out:
        output = Path(args.out)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(payload, encoding="utf-8")
    print(payload, end="")
    return 0 if report.get("ok") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
