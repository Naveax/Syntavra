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

from syntavra_runtime.artifacts import ArtifactStore
from syntavra_runtime.output_intelligence import OutputIntelligenceEngine

CONTRACT = Path("contracts/python/output-intelligence-v1.json")
WORKFLOW = Path(".github/workflows/output-intelligence.yml")
RELEASE_GATE = Path(".github/workflows/release-main-merge-gate.yml")
PIN_POLICY = Path("tests/runtime/test_release_action_pins.py")
REGISTRY = Path("contracts/python/capability-completeness-registry-v1.json")


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    _require(isinstance(value, dict), f"expected JSON object: {path}")
    return value


def _head(repo: Path) -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()


def certify(repo: Path) -> dict[str, Any]:
    repo = repo.resolve()
    _require(repo == ROOT, "output intelligence certifier must run against its own checkout")

    contract = _json(repo / CONTRACT)
    _require(contract.get("schema_version") == 1, "output intelligence contract schema drift")
    _require(contract.get("family") == "output-intelligence", "output intelligence family drift")
    _require(contract.get("claim") == "OUTPUT_INTELLIGENCE_V1", "output intelligence claim drift")
    _require(contract.get("phase") == "python-first", "output intelligence phase drift")
    _require(contract.get("strict") is True, "output intelligence contract must remain strict")

    ownership = contract.get("ownership_policy") or {}
    for key in (
        "parallel_persistent_store_forbidden",
        "existing_artifact_store_reused",
        "existing_terminal_engine_reused",
        "existing_compactor_registry_reused",
        "existing_rewrite_engine_reused",
    ):
        _require(ownership.get(key) is True, f"ownership policy disabled: {key}")
    _require(ownership.get("public_cli_route_added") is False, "output intelligence may not add public CLI")

    policy = contract.get("output_policy") or {}
    for key in (
        "exact_recovery_required",
        "bounded_visible_output",
        "semantic_preservation_verifier",
        "no_worse_guard",
        "fail_closed_on_preservation_failure",
        "content_addressed_decision_receipt",
        "no_silent_fallback",
    ):
        _require(policy.get(key) is True, f"output policy disabled: {key}")
    _require(
        policy.get("compression_safety_classes")
        == ["EXACT_ONLY", "STRUCTURAL_SAFE", "SEMANTIC_SAFE", "LOSSY_ALLOWED"],
        "compression safety vocabulary drift",
    )

    for relative in (WORKFLOW, RELEASE_GATE, PIN_POLICY, REGISTRY):
        _require((repo / relative).is_file(), f"missing output intelligence enforcement surface: {relative}")

    workflow = (repo / WORKFLOW).read_text(encoding="utf-8")
    _require("tests.runtime.test_output_intelligence_v1" in workflow, "workflow lost output intelligence regressions")
    _require("tools/certify_output_intelligence_v1.py" in workflow, "workflow lost output intelligence certifier")
    for pin in (
        "actions/checkout@11d5960a326750d5838078e36cf38b85af677262",
        "actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065",
        "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02",
    ):
        _require(pin in workflow, f"workflow action pin drift: {pin}")

    release = (repo / RELEASE_GATE).read_text(encoding="utf-8")
    _require("tests.runtime.test_output_intelligence_v1" in release, "release gate lost output intelligence tests")
    _require("tools/certify_output_intelligence_v1.py" in release, "release gate lost output intelligence certifier")
    pins = (repo / PIN_POLICY).read_text(encoding="utf-8")
    _require('".github/workflows/output-intelligence.yml"' in pins, "pin policy lost output intelligence workflow")

    registry = _json(repo / REGISTRY)
    by_id = {item["id"]: item for item in registry["capabilities"]}
    _require(by_id["cache_provider_budget_v1"]["state"] == "certified", "Cache Provider Budget must be certified first")
    lifecycle_state = by_id["output_intelligence_v1"]["state"]
    _require(
        lifecycle_state in {"partial", "implemented", "verified", "certified"},
        "output intelligence registry state is invalid",
    )
    validate_python_complete_state(registry)

    with tempfile.TemporaryDirectory(prefix="syntavra-output-intelligence-cert-") as td:
        store = ArtifactStore(Path(td) / "artifacts")
        engine = OutputIntelligenceEngine(store)
        output = (
            "\n".join(f"progress {index}" for index in range(300))
            + "\nFAILED tests/test_core.py:42 AssertionError: expected 7 got 9\n"
            + "1 failed, 9 passed in 2.50s\n"
        )
        decision = engine.process("pytest", output, exit_code=1, budget_bytes=1024)
        _require(decision.exact_recovery is True, "exact recovery smoke failed")
        _require(decision.semantic_preservation is True, "semantic preservation smoke failed")
        _require(decision.no_worse is True, "no-worse guard smoke failed")
        _require(decision.visible_bytes < decision.original_bytes, "output compaction smoke did not save bytes")
        _require(store.read(decision.artifact_id) == output.encode("utf-8"), "exact output artifact drift")
        unsafe = engine.plan_command_rewrite("pytest | tee out.txt")
        _require(unsafe.changed is False and unsafe.safe is False, "unsafe shell rewrite did not fail closed")

    status = OutputIntelligenceEngine.status()
    for key in (
        "exact_output_store_reused",
        "terminal_output_engine_reused",
        "command_compactor_registry_reused",
        "command_rewriter_reused",
        "semantic_preservation_verifier",
        "compression_safety_classes",
        "no_worse_guard",
        "bounded_visible_output",
        "fail_closed_on_verification_failure",
        "content_addressed_receipt",
    ):
        _require(status.get(key) is True, f"runtime surface disabled: {key}")
    _require(status.get("parallel_persistent_store") is False, "output intelligence introduced a store")
    _require(status.get("public_cli_route") is False, "output intelligence claims public CLI")

    admission = contract.get("admission") or {}
    _require(admission.get("rust_production_promoted") == 174, "Rust promotion baseline drift")
    _require(admission.get("rust_remaining_parity_promotion") == 71, "Rust remaining baseline drift")
    exact_head = _head(repo)
    _require(len(exact_head) == 40, "unable to resolve exact head")

    return {
        "ok": True,
        "schema_version": 1,
        "claim": "OUTPUT_INTELLIGENCE_V1",
        "exact_head": exact_head,
        "runtime_ready": True,
        "lifecycle_state": lifecycle_state,
        "admission_ready": lifecycle_state in {"implemented", "verified", "certified"},
        "python_complete_ready": False,
        "rust_resume_allowed": False,
        "runtime": status,
        "rust": {
            "production_promoted": 174,
            "remaining_parity_promotion": 71,
            "feature_development_frozen": True,
        },
        "claim_boundary": contract["claim_boundary"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Certify Syntavra Output Intelligence v1")
    parser.add_argument("--repo", default=".")
    parser.add_argument("--out")
    args = parser.parse_args()
    try:
        report = certify(Path(args.repo))
    except Exception as exc:  # pragma: no cover
        report = {
            "ok": False,
            "schema_version": 1,
            "claim": "OUTPUT_INTELLIGENCE_V1",
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
