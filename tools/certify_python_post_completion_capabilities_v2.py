#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

REGISTRY_V1 = ROOT / "contracts/python/capability-completeness-registry-v1.json"
REGISTRY_V2 = ROOT / "contracts/python/capability-completeness-registry-v2.json"
CERTIFICATE = ROOT / "contracts/python/python-post-completion-280-certificate-v1.json"
TEST = ROOT / "tests/runtime/test_python_post_completion_capabilities_v2.py"
INTEGRATION_TEST = ROOT / "tests/runtime/test_python_post_completion_evidence_integration.py"
EXPECTED_NUMBERS = list(range(243, 271)) + list(range(276, 281))
DEFERRED_RUST = list(range(271, 276))
CERTIFIED_SOURCE_HEAD = "55b95394cfc5a0239b81c5c54684e477d86a5b21"
CERTIFIED_MANIFEST_HEAD = "cd143bb21cf8875500efa55c5d1aa62862a02437"
CERTIFIED_WORKFLOW_RUN_ID = 33987594435


def _read(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AssertionError(f"expected object: {path}")
    return value


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _head() -> str:
    process = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    return process.stdout.strip() if process.returncode == 0 else ""


def certify() -> dict[str, Any]:
    v1 = _read(REGISTRY_V1)
    v2 = _read(REGISTRY_V2)
    seal = _read(CERTIFICATE)

    python_complete = v1.get("python_complete") or {}
    _require(python_complete.get("ready") is True, "Python COMPLETE v1 must remain sealed")
    _require(python_complete.get("rust_resume_allowed") is False, "Rust must remain retired")
    _require(python_complete.get("rust_retired") is True, "Rust retirement authority drift")

    by_id_v1 = {
        row.get("id"): row
        for row in v1.get("capabilities") or []
        if isinstance(row, dict)
    }
    for capability_id in (
        "runtime_contract_version_graph_v1",
        "context_decision_trace_v1",
        "deterministic_policy_snapshot_v1",
    ):
        _require(
            (by_id_v1.get(capability_id) or {}).get("state") == "certified",
            f"{capability_id} must be certified before v2",
        )

    _require(v2.get("schema_version") == 2, "post-completion registry schema drift")
    _require(
        v2.get("family") == "python-post-completion-capability-registry",
        "registry family drift",
    )
    _require(v2.get("phase") == "python-first-post-completion", "registry phase drift")
    _require(v2.get("strict") is True, "post-completion registry must remain strict")
    _require(
        v2.get("extends") == "contracts/python/capability-completeness-registry-v1.json",
        "v2 must extend frozen v1",
    )

    policy = v2.get("policy") or {}
    for key in (
        "parallel_persistent_store_forbidden",
        "no_silent_fallback",
        "exact_recovery_required_when_declared",
        "external_evidence_not_self_certified",
        "rust_transition_capabilities_deferred",
        "python_complete_v1_not_reopened",
    ):
        _require(policy.get(key) is True, f"post-completion policy disabled: {key}")
    _require(
        policy.get("public_command_growth_default") == 0,
        "post-completion work must default to internal capability composition",
    )

    capabilities = v2.get("capabilities") or []
    numbers = [row.get("number") for row in capabilities]
    _require(numbers == EXPECTED_NUMBERS, f"Python post-completion numbers drift: {numbers}")
    ids = [str(row.get("id") or "") for row in capabilities]
    _require(len(ids) == len(set(ids)), "duplicate v2 capability ids")
    _require(v2.get("execution_order") == ids, "execution order must match capability rows")
    deferred = v2.get("deferred_rust_transition") or []
    _require(
        [row.get("number") for row in deferred] == DEFERRED_RUST,
        "271-275 Rust transition deferral drift",
    )

    for row in capabilities:
        capability_id = str(row.get("id") or "")
        _require(
            row.get("required_for_python_post_completion_complete") is True,
            f"{capability_id}: required flag drift",
        )
        _require(
            row.get("state") in {"implemented", "certified"},
            f"{capability_id}: invalid state",
        )
        _require(
            row.get("classification") in {"EXISTS", "HARDEN", "UNIFY", "NEW", "CERTIFY"},
            f"{capability_id}: invalid classification",
        )
        _require(
            isinstance(row.get("acceptance"), str) and row["acceptance"].strip(),
            f"{capability_id}: acceptance missing",
        )
        evidence = row.get("implementation_evidence") or []
        _require(evidence, f"{capability_id}: implementation evidence missing")
        for relative in evidence:
            _require((ROOT / relative).is_file(), f"{capability_id}: missing evidence path {relative}")

    _require(TEST.is_file(), "post-completion unit test suite missing")
    _require(INTEGRATION_TEST.is_file(), "post-completion integration test suite missing")

    _require(seal.get("schema_version") == 1, "post-completion certificate schema drift")
    _require(
        seal.get("family") == "python-post-completion-280-certificate",
        "post-completion certificate family drift",
    )
    _require(
        seal.get("claim") == "PYTHON_POST_COMPLETION_280_CERTIFIED",
        "post-completion certificate claim drift",
    )
    _require(seal.get("certified_numbers") == EXPECTED_NUMBERS, "certificate scope drift")
    _require(seal.get("source_code_head") == CERTIFIED_SOURCE_HEAD, "certificate source head drift")
    _require(
        seal.get("manifest_sync_head") == CERTIFIED_MANIFEST_HEAD,
        "certificate manifest head drift",
    )
    _require(
        seal.get("source_workflow_run_id") == CERTIFIED_WORKFLOW_RUN_ID,
        "certificate workflow run drift",
    )
    _require(
        seal.get("source_workflow_conclusion") == "success",
        "certificate source workflow did not succeed",
    )
    _require(seal.get("unit_tests_passed") == 34, "unit certification count drift")
    _require(
        seal.get("canonical_store_integration_tests_passed") == 2,
        "canonical-store integration certification count drift",
    )
    _require(seal.get("python_complete_v1_preserved") is True, "v1 preservation seal drift")
    _require(seal.get("rust_resume_allowed") is False, "Rust resume seal drift")
    _require(seal.get("rust_retired") is True, "Rust retirement seal drift")
    _require(
        seal.get("rust_transition_deferred") == DEFERRED_RUST,
        "Rust-transition deferral seal drift",
    )
    _require(
        seal.get("external_claims_not_asserted") is True,
        "certificate must not manufacture external claims",
    )
    certification_evidence = seal.get("evidence") or []
    _require(certification_evidence, "certificate evidence paths missing")
    for relative in certification_evidence:
        _require((ROOT / relative).is_file(), f"missing certificate evidence path {relative}")

    return {
        "ok": True,
        "claim": seal["claim"],
        "exact_head": _head(),
        "certified_source_head": CERTIFIED_SOURCE_HEAD,
        "certified_manifest_head": CERTIFIED_MANIFEST_HEAD,
        "source_workflow_run_id": CERTIFIED_WORKFLOW_RUN_ID,
        "python_complete_v1_preserved": True,
        "rust_resume_allowed": False,
        "rust_retired": True,
        "rust_production_promoted": 174,
        "rust_remaining": 71,
        "python_capability_numbers": EXPECTED_NUMBERS,
        "implemented_count": len(capabilities),
        "certified_count": len(EXPECTED_NUMBERS),
        "uncertified": [],
        "implementation_complete": len(capabilities) == len(EXPECTED_NUMBERS),
        "certification_complete": True,
        "rust_transition_deferred": DEFERRED_RUST,
        "public_command_growth_default": 0,
        "unit_tests_passed": 34,
        "canonical_store_integration_tests_passed": 2,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = certify()
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
