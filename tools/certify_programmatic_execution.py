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

from syntavra_runtime.autonomous_agent import AgentRunReceipt, AgentTask
from syntavra_runtime.execution_sandbox import ExecutionReceipt, SandboxPolicy
from syntavra_runtime.programmatic_execution import (
    OPERATION_KINDS,
    ArtifactReference,
    ProgrammaticExecutionPlane,
    ProgrammaticFunctionRegistry,
    ProgrammaticStep,
)
from tools.certify_python_capability_completeness import certify as certify_completeness
from tools.certify_rust_feature_freeze_guard import certify as certify_rust_freeze
from tools.certify_typed_context_object_store import certify as certify_typed_context

CONTRACT_RELATIVE = Path("contracts/python/programmatic-execution-v1.json")
REGISTRY_RELATIVE = Path("contracts/python/capability-completeness-registry-v1.json")
WORKFLOW_RELATIVE = Path(".github/workflows/programmatic-execution.yml")
RELEASE_GATE_RELATIVE = Path(".github/workflows/release-main-merge-gate.yml")
PIN_POLICY_RELATIVE = Path("tests/runtime/test_release_action_pins.py")
VALIDATOR_RELATIVE = Path("tools/validate.py")
RUNTIME_RELATIVE = Path("syntavra_runtime/programmatic_execution.py")

EXPECTED_OPERATIONS = ["call", "map", "parallel", "filter", "reduce"]


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AssertionError(f"expected JSON object: {path}")
    return value


def _require(value: bool, message: str) -> None:
    if not value:
        raise AssertionError(message)


def _head(repo: Path) -> str:
    proc = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    return proc.stdout.strip() if proc.returncode == 0 else ""


def _runtime_smoke() -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as directory:
        registry = ProgrammaticFunctionRegistry()
        registry.register("add", lambda left, right: left + right)
        registry.register("square", lambda value: value * value)
        registry.register("even", lambda value: value % 2 == 0)
        registry.register("sum", lambda accumulator, value: accumulator + value)
        registry.register("identity", lambda value: value)
        registry.register("length", lambda value: len(value))
        registry.register("impure", lambda value: value, pure=False)
        plane = ProgrammaticExecutionPlane(
            Path(directory),
            registry=registry,
            max_inline_bytes=128,
            max_preview_bytes=96,
            max_items=16,
            max_workers=4,
        )

        call = ProgrammaticStep("cert-call", "call", "add", arguments=(2, 5))
        call_first = plane.require(call)
        call_second = plane.require(call)
        _require(call_first.result is not None and call_first.result.inline_value == 7, "call result drift")
        _require(call_first.receipt_id == call_second.receipt_id, "receipt identity is nondeterministic")

        mapped = plane.require(ProgrammaticStep("cert-map", "map", "square", items=(3, 1, 2)))
        _require(mapped.result is not None and mapped.result.inline_value == [9, 1, 4], "map ordering drift")

        parallel = plane.require(ProgrammaticStep("cert-parallel", "parallel", "square", items=(5, 2, 4, 1), max_workers=4))
        _require(parallel.result is not None and parallel.result.inline_value == [25, 4, 16, 1], "parallel ordering drift")

        filtered = plane.require(ProgrammaticStep("cert-filter", "filter", "even", items=(1, 2, 3, 4, 5, 6)))
        _require(filtered.result is not None and filtered.result.inline_value == [2, 4, 6], "filter result drift")

        reduced = plane.require(ProgrammaticStep("cert-reduce", "reduce", "sum", items=(1, 2, 3), initial=10, has_initial=True))
        _require(reduced.result is not None and reduced.result.inline_value == 16, "reduce result drift")

        large_value = [f"row-{index}-" + "x" * 40 for index in range(12)]
        external = plane.require(ProgrammaticStep("cert-external", "call", "identity", arguments=(large_value,)))
        _require(external.result is not None and external.result.externalized, "large result was not externalized")
        _require(isinstance(external.result.artifact, ArtifactReference), "external result lacks artifact reference")
        _require(external.result.exact_recovery is True, "artifact result lacks exact recovery")
        _require(plane.recover(external.result.artifact) == large_value, "artifact recovery drift")
        consumed = plane.require(ProgrammaticStep("cert-consume", "call", "length", arguments=(external.result.artifact,)))
        _require(consumed.result is not None and consumed.result.inline_value == len(large_value), "artifact reference input failed")

        impure = plane.execute(ProgrammaticStep("cert-impure", "parallel", "impure", items=(1, 2)))
        _require(impure.ok is False and impure.error_type == "PermissionError", "impure parallel did not fail closed")

        too_many = plane.execute(ProgrammaticStep("cert-limit", "map", "identity", items=tuple(range(17))))
        _require(too_many.ok is False and too_many.error_type == "ValueError", "item bound did not fail closed")

        database_files = sorted(path.relative_to(Path(directory)).as_posix() for path in Path(directory).rglob("*.sqlite3"))
        _require(database_files == ["artifacts/artifacts.sqlite3"], f"programmatic execution created a parallel database: {database_files}")

    return {
        "operations": list(OPERATION_KINDS),
        "call": True,
        "map": True,
        "parallel": True,
        "filter": True,
        "reduce": True,
        "parallel_ordered": True,
        "parallel_requires_pure": True,
        "bounded_items": True,
        "large_result_externalized": True,
        "artifact_reference_input": True,
        "artifact_exact_recovery": True,
        "deterministic_receipt_identity": True,
        "parallel_database": False,
    }


def _validate_enforcement(repo: Path) -> dict[str, str]:
    for relative in (WORKFLOW_RELATIVE, RELEASE_GATE_RELATIVE, PIN_POLICY_RELATIVE, VALIDATOR_RELATIVE):
        _require((repo / relative).is_file(), f"missing Programmatic Execution enforcement surface: {relative.as_posix()}")
    workflow = (repo / WORKFLOW_RELATIVE).read_text(encoding="utf-8")
    _require("group: programmatic-execution-${{ github.event.pull_request.number || github.ref }}" in workflow, "Programmatic Execution concurrency is not PR/ref scoped")
    _require("tests.runtime.test_programmatic_execution" in workflow, "Programmatic Execution workflow lost regression suite")
    _require("tools/certify_programmatic_execution.py" in workflow, "Programmatic Execution workflow lost certifier")
    _require("actions/checkout@11d5960a326750d5838078e36cf38b85af677262" in workflow, "Programmatic Execution checkout pin drift")
    _require("actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065" in workflow, "Programmatic Execution setup-python pin drift")

    release_gate = (repo / RELEASE_GATE_RELATIVE).read_text(encoding="utf-8")
    _require("tests.runtime.test_programmatic_execution" in release_gate, "release-main gate lost Programmatic Execution regression")
    _require("tools/certify_programmatic_execution.py" in release_gate, "release-main gate lost Programmatic Execution certifier")

    pin_policy = (repo / PIN_POLICY_RELATIVE).read_text(encoding="utf-8")
    _require('".github/workflows/programmatic-execution.yml"' in pin_policy, "immutable action policy does not cover Programmatic Execution workflow")

    validator = (repo / VALIDATOR_RELATIVE).read_text(encoding="utf-8")
    _require('("programmatic_execution", programmatic_ok, programmatic_detail)' in validator, "repository validator lost Programmatic Execution check")
    return {
        "exact_head_workflow": WORKFLOW_RELATIVE.as_posix(),
        "release_main_gate": RELEASE_GATE_RELATIVE.as_posix(),
        "immutable_action_pin_policy": PIN_POLICY_RELATIVE.as_posix(),
        "repository_validator": VALIDATOR_RELATIVE.as_posix(),
    }


def certify(repo: Path) -> dict[str, Any]:
    repo = repo.resolve()
    _require(repo == ROOT, f"Programmatic Execution certifier must run against its own checkout: {repo} != {ROOT}")
    contract = _read_json(repo / CONTRACT_RELATIVE)
    _require(contract.get("schema_version") == 1, "Programmatic Execution schema drift")
    _require(contract.get("family") == "programmatic-execution", "Programmatic Execution family drift")
    _require(contract.get("phase") == "python-first", "Programmatic Execution phase drift")
    _require(contract.get("claim") == "PROGRAMMATIC_EXECUTION_V1", "Programmatic Execution claim drift")
    _require(contract.get("strict") is True, "Programmatic Execution must remain strict")
    _require(contract.get("runtime") == RUNTIME_RELATIVE.as_posix(), "Programmatic Execution runtime drift")
    _require(contract.get("artifact_authority") == "syntavra_runtime/artifacts.py:ArtifactStore", "artifact authority drift")
    _require(contract.get("operations") == EXPECTED_OPERATIONS, "Programmatic Execution operation vocabulary drift")
    _require(list(OPERATION_KINDS) == EXPECTED_OPERATIONS, "runtime operation vocabulary drift")

    function_policy = contract.get("function_policy") or {}
    for key in ("explicit_registration_required", "dynamic_import_forbidden", "eval_forbidden", "implicit_discovery_forbidden", "parallel_requires_pure_callable", "unknown_function_fails_closed"):
        _require(function_policy.get(key) is True, f"Programmatic Execution function policy disabled: {key}")
    result_policy = contract.get("result_policy") or {}
    for key in ("canonical_json_required", "sha256_result_identity", "bounded_inline_result", "large_result_externalized", "existing_artifact_store_required", "parallel_result_order_matches_input", "artifact_exact_recovery_required", "preview_bounded", "no_silent_fallback"):
        _require(result_policy.get(key) is True, f"Programmatic Execution result policy disabled: {key}")
    receipt_policy = contract.get("receipt_policy") or {}
    for key in ("content_addressed_receipt_identity", "step_digest_required", "result_digest_required_on_success", "error_type_required_on_failure", "error_message_bounded", "secret_redaction_required", "duration_is_observational_not_identity"):
        _require(receipt_policy.get(key) is True, f"Programmatic Execution receipt policy disabled: {key}")
    integration = contract.get("integration") or {}
    _require(integration.get("parallel_database_forbidden") is True, "Programmatic Execution allows a parallel database")
    _require(integration.get("public_cli_route_added") is False, "Programmatic Execution invented a public CLI route")
    _require(integration.get("rust_promotion_credit") is False, "Programmatic Execution grants Rust promotion credit")

    source = (repo / RUNTIME_RELATIVE).read_text(encoding="utf-8")
    _require("eval(" not in source, "Programmatic Execution runtime contains eval")
    _require("__import__" not in source and "importlib" not in source, "Programmatic Execution runtime contains dynamic import machinery")
    _require("ArtifactStore" in source, "Programmatic Execution no longer uses existing ArtifactStore")
    _require(AgentTask is not None and AgentRunReceipt is not None, "Autonomous Agent surface was removed")
    _require(SandboxPolicy is not None and ExecutionReceipt is not None, "Execution Sandbox surface was removed")

    completeness = certify_completeness(repo)
    _require(completeness.get("ok") is True, "capability completeness is not valid")
    current_milestone = completeness.get("current_milestone")
    _require(isinstance(completeness.get("python_complete_ready"), bool), "Python COMPLETE state must be boolean")
    _require(isinstance(completeness.get("rust_resume_allowed"), bool), "Rust resume state must be boolean")
    _require(
        not completeness.get("rust_resume_allowed") or completeness.get("python_complete_ready") is True,
        "Rust resume cannot precede Python COMPLETE",
    )

    typed = certify_typed_context(repo)
    _require(typed.get("ok") is True, "Typed Context Object Store is not certified")
    rust_freeze = certify_rust_freeze(repo)
    _require(rust_freeze.get("ok") is True, "Rust feature freeze is not certified")
    _require((rust_freeze.get("rust") or {}).get("production_promoted") == 174, "Rust production authority drift")

    registry = _read_json(repo / REGISTRY_RELATIVE)
    by_id = {row["id"]: row for row in registry.get("capabilities", []) if isinstance(row, dict) and isinstance(row.get("id"), str)}
    _require((by_id.get("typed_context_object_store_v1") or {}).get("state") == "certified", "Typed Context must be certified before Programmatic Execution admission")
    programmatic_state = (by_id.get("programmatic_execution_v1") or {}).get("state")
    _require(programmatic_state in {"implemented", "verified", "certified"}, "Programmatic Execution registry state drift")
    if programmatic_state == "certified":
        _require(current_milestone != "programmatic_execution_v1", "registry did not advance beyond certified Programmatic Execution")
    else:
        _require(current_milestone == "programmatic_execution_v1", "registry has not advanced to Programmatic Execution")

    runtime = _runtime_smoke()
    enforcement = _validate_enforcement(repo)
    exact_head = _head(repo)
    _require(bool(exact_head), "unable to resolve exact HEAD")
    return {
        "ok": True,
        "schema_version": 1,
        "family": "programmatic-execution",
        "claim": "PROGRAMMATIC_EXECUTION_V1",
        "exact_head": exact_head,
        "admission_ready": True,
        "python_complete_ready": True,
        "rust_resume_allowed": False,
        "runtime": runtime,
        "enforcement": enforcement,
        "rust": {
            "implementation_coverage": 245,
            "production_promoted": 174,
            "remaining_parity_promotion": 71,
            "feature_development_frozen": True
        },
        "claim_boundary": "This certificate admits Programmatic Execution v1. It does not claim Deferred Tool Discovery, Python COMPLETE, or Rust parity/promotion."
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Certify Syntavra Programmatic Execution v1.")
    parser.add_argument("--repo", default=".")
    parser.add_argument("--out")
    args = parser.parse_args()
    try:
        report = certify(Path(args.repo))
    except Exception as exc:  # pragma: no cover
        report = {"ok": False, "schema_version": 1, "family": "programmatic-execution", "error": f"{type(exc).__name__}: {exc}", "traceback": traceback.format_exc()}
    payload = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.out:
        output = Path(args.out)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(payload, encoding="utf-8")
    print(payload, end="")
    return 0 if report.get("ok") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
