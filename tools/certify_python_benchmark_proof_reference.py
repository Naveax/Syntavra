#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import traceback
from dataclasses import asdict
from pathlib import Path
from typing import Any

from syntavra_runtime.benchmark_harness import ArmResult, compare_results, generate_synthetic_repository, validate_config, write_config
from syntavra_runtime.external_benchmarks import ExternalBenchmarkGate, ExternalSuiteRegistry
from syntavra_runtime.live_certification import LiveCertificationGate
from syntavra_runtime.paired_benchmark import CodingCorpusPlanner, SuperiorityGate
from syntavra_runtime.product_surface import MeasuredBenchmarkGate, PROOF_WORKLOADS, ReceiptValidator
from syntavra_runtime.signalbench import RunResult, SignalBenchRunner
from tools import report_missing_native_public_routes as public_surface
from tools import report_python_public_execution_contract as execution_contract

FIXTURE_RELATIVE = Path("contracts/python/benchmark-proof-reference-v1.json")
HEX64 = "a" * 64
HEX40 = "b" * 40
DIGEST = "sha256:" + "c" * 64


def _head(repo: Path) -> str:
    value = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    return value.stdout.strip() if value.returncode == 0 else ""


def _run(repo: Path, project: Path, state: Path, argv: list[str]) -> dict[str, Any]:
    env = os.environ.copy()
    env.update({"PYTHONPATH": str(repo), "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"})
    env.pop("SYNTAVRA_BULK_PARITY_PROBE", None)
    result = subprocess.run(
        [sys.executable, "-m", "syntavra_runtime.engine_entry", "--engine", "python", "--project", str(project), "--state-root", str(state), *argv],
        cwd=repo, env=env, text=True, encoding="utf-8", errors="replace", stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=90, check=False,
    )
    try:
        parsed = json.loads(result.stdout) if result.stdout.strip() else None
    except json.JSONDecodeError:
        parsed = None
    return {"exit": result.returncode, "stdout": result.stdout, "stderr": result.stderr, "value": parsed}


def _json(label: str, result: dict[str, Any], exit_code: int) -> dict[str, Any]:
    if result["exit"] != exit_code or result["stderr"]:
        raise AssertionError(f"{label}: expected clean exit {exit_code}, got {result}")
    if not isinstance(result["value"], dict):
        raise AssertionError(f"{label}: expected JSON object stdout, got {result}")
    return result["value"]


def _routes(fixture: dict[str, Any]) -> dict[str, Any]:
    all_routes = public_surface.python_public_route_sources()
    routes = sorted(route for route in all_routes if route in set(fixture["public_routes"]))
    if routes != fixture["public_routes"]:
        raise AssertionError(f"benchmark/proof route inventory drift: {routes}")
    execution = {row["route"]: row for row in execution_contract.route_execution_manifest()}
    owners: dict[str, str] = {}
    for route in routes:
        row = execution[route]
        if len(row["entrypoints"]) != 1 or row["unknown_sources"]:
            raise AssertionError(f"benchmark/proof ownership drift: {row}")
        owners[route] = row["entrypoint"]
    return {"routes": routes, "route_count": len(routes), "route_sha256": public_surface._digest(routes), "ownership": owners}


def _thresholds(fixture: dict[str, Any]) -> dict[str, Any]:
    expected = fixture["thresholds"]
    observed = {
        "signalbench": {
            "required_tasks": SuperiorityGate.required_tasks,
            "required_repetitions": SuperiorityGate.required_repetitions,
            "minimum_success": SuperiorityGate.minimum_success,
            "maximum_token_ratio": SuperiorityGate.maximum_token_ratio,
            "maximum_wall_ratio": SuperiorityGate.maximum_wall_ratio,
            "maximum_security_regressions": SuperiorityGate.maximum_security_regressions,
        },
        "measured_benchmark": {
            "minimum_pairs": MeasuredBenchmarkGate.minimum_pairs,
            "minimum_repositories": MeasuredBenchmarkGate.minimum_repositories,
            "minimum_tasks": MeasuredBenchmarkGate.minimum_tasks,
            "minimum_workload_families": MeasuredBenchmarkGate.minimum_workload_families,
            "quality_non_inferiority_margin": MeasuredBenchmarkGate.quality_non_inferiority_margin,
            "success_non_inferiority_margin": MeasuredBenchmarkGate.success_non_inferiority_margin,
        },
        "external_suite": {
            "minimum_pairs": ExternalBenchmarkGate.minimum_pairs,
            "quality_non_inferiority_margin": ExternalBenchmarkGate.quality_non_inferiority_margin,
            "success_non_inferiority_margin": ExternalBenchmarkGate.success_non_inferiority_margin,
        },
        "live_integration": {
            "minimum_receipts_per_integration": LiveCertificationGate.minimum_receipts_per_integration,
            "minimum_operating_systems": LiveCertificationGate.minimum_operating_systems,
        },
        "provider_billed": {
            "minimum_provider_observed_runs": 10,
            "minimum_cost_ratio_lcb": 1.0,
        },
    }
    if observed != expected:
        raise AssertionError(f"benchmark/proof threshold drift: {observed}")
    return observed


def _benchmark_component(root: Path) -> dict[str, Any]:
    config_path = root / "benchmark-config.json"
    config = write_config(config_path, "1X")
    validation = validate_config(config)
    if validation.get("ok") is not True:
        raise AssertionError(f"generated benchmark config no longer validates: {validation}")
    repo_result = generate_synthetic_repository(root / "synthetic-repository", files=8, depth=3, fanout=2, faults=2)
    if repo_result != {
        "files": 11,
        "depth": 3,
        "fanout": 2,
        "faults": 2,
        "ground_truth_hash": repo_result["ground_truth_hash"],
        "observed_axes": repo_result["observed_axes"],
    }:
        raise AssertionError(f"synthetic repository result shape drift: {repo_result}")
    if len(str(repo_result["ground_truth_hash"])) != 64:
        raise AssertionError("synthetic repository ground-truth hash drift")

    observed_axes = dict(config["observed_baseline"])
    baseline: list[ArmResult] = []
    candidate: list[ArmResult] = []
    for repetition in range(1, 11):
        common = dict(
            repetition=repetition,
            success=True,
            verified_work=1.0,
            model_turns=1,
            wait_calls=0,
            verifier_skips=0,
            security_regressions=0,
            repository_tree="repo-tree",
            model="fixture-model",
            reasoning="fixture-reasoning",
            prompt_hash="prompt-hash",
            verifier_hash="verifier-hash",
            cache_mode="cold",
            permissions_hash="permissions-hash",
            timeout_seconds=60.0,
            workload_hash="workload-hash",
            observed_axes=observed_axes,
        )
        baseline.append(ArmResult(arm="baseline", quota_cost=2.0, fresh_input_tokens=1000, cached_input_tokens=0, output_tokens=100, reasoning_tokens=0, wall_seconds=10.0, order_index=repetition, **common))
        candidate.append(ArmResult(arm="syntavra", quota_cost=1.0, fresh_input_tokens=100, cached_input_tokens=0, output_tokens=10, reasoning_tokens=0, wall_seconds=1.0, order_index=repetition, **common))
    comparison = compare_results(baseline, candidate, tier="1X", config=config)
    if comparison.get("valid_pairs") != 10 or comparison.get("invalid_runs") != []:
        raise AssertionError(f"benchmark compare pair semantics drift: {comparison}")
    if comparison.get("diagnostics", {}).get("token_ratios") != [10.0] * 10:
        raise AssertionError(f"benchmark token-ratio calculation drift: {comparison}")
    if comparison.get("diagnostics", {}).get("quota_available") is not True:
        raise AssertionError(f"benchmark quota-availability drift: {comparison}")

    return {
        "config_schema_version": config["schema_version"],
        "config_tier": config["tier"],
        "config_validation_keys": sorted(validation),
        "generated_repository": {"files": repo_result["files"], "depth": repo_result["depth"], "fanout": repo_result["fanout"], "faults": repo_result["faults"], "ground_truth_hash_shape": True},
        "comparison": comparison,
    }


def _signalbench_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for task_index in range(SuperiorityGate.required_tasks):
        task_id = f"task-{task_index:03d}"
        for repetition in range(1, SuperiorityGate.required_repetitions + 1):
            pair_key = f"{task_id}:{repetition}"
            rows.append({"task_id": task_id, "repetition": repetition, "arm_id": "plain-baseline", "source_kind": "live-external-arm", "synthetic": False, "success": True, "active_tokens": 1000, "wall_seconds": 10.0, "security_regressions": 0, "pair_key": pair_key})
            rows.append({"task_id": task_id, "repetition": repetition, "arm_id": "syntavra", "source_kind": "live-external-arm", "synthetic": False, "success": True, "active_tokens": 100, "wall_seconds": 1.0, "security_regressions": 0, "pair_key": pair_key})
    return rows


def _signalbench_contract(repo: Path, project: Path, state: Path, root: Path) -> dict[str, Any]:
    plan = _json("signalbench plan", _run(repo, project, state, ["signalbench", "plan", "--repetitions", "30"]), 0)
    plan2 = _json("signalbench2 plan", _run(repo, project, state, ["signalbench2", "plan", "--repetitions", "30"]), 0)
    if plan != plan2:
        raise AssertionError("signalbench and signalbench2 plan compatibility drift")
    if plan.get("tasks") != 150 or plan.get("required_pairs") != 4500 or plan.get("repetitions") != 30:
        raise AssertionError(f"signalbench plan arithmetic drift: {plan}")
    if len(plan.get("families") or {}) != 12:
        raise AssertionError(f"signalbench family inventory drift: {plan}")

    rows = _signalbench_rows()
    gate_path = root / "signalbench-gate.json"
    gate_path.write_text(json.dumps({"results": rows}, separators=(",", ":")), encoding="utf-8")
    gate = _json("signalbench gate", _run(repo, project, state, ["signalbench", "gate", str(gate_path)]), 0)
    gate2 = _json("signalbench2 gate", _run(repo, project, state, ["signalbench2", "gate", str(gate_path)]), 0)
    if gate != gate2 or gate.get("ok") is not True or gate.get("claim") != "INTERNAL_SUPERIORITY_MEASURED":
        raise AssertionError(f"signalbench gate compatibility drift: {gate} / {gate2}")
    metrics = gate.get("metrics") or {}
    if metrics.get("pairs") != 4500 or metrics.get("tasks") != 150 or metrics.get("candidate_success") != 1.0 or metrics.get("token_ratio") != 0.1 or metrics.get("wall_ratio") != 0.1:
        raise AssertionError(f"signalbench metric calculation drift: {gate}")
    empty = SuperiorityGate.evaluate([])
    if empty.get("ok") is not False or "empty-results" not in (empty.get("reasons") or []):
        raise AssertionError(f"signalbench empty-state drift: {empty}")
    return {"plan": plan, "gate": gate, "empty": empty, "fixture_rows": len(rows)}


def _provider_receipts() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    workloads = list(PROOF_WORKLOADS[:3])
    observed_at = "2026-08-12T00:00:00+00:00"
    for index in range(30):
        common = dict(
            provider="openai",
            model="fixture-model",
            request_id=f"request-{index}",
            session_id=f"session-{index}",
            repository_hash=hashlib.sha256(f"repo-{index % 5}".encode()).hexdigest(),
            integration_id="openai",
            observed_at=observed_at,
            quality_score=0.9,
            success=True,
            synthetic=False,
            raw_usage_hash=hashlib.sha256(f"usage-{index}".encode()).hexdigest(),
            workload=workloads[index % len(workloads)],
            task_id=f"task-{index % 10}",
            repetition=index + 1,
            metadata={},
        )
        rows.append({"receipt_id": f"baseline-{index}", "wall_time_ms": 1000.0, "input_tokens": 1000, "cached_input_tokens": 0, "output_tokens": 100, "cost_usd": 1.0, "arm": "baseline", **common})
        rows.append({"receipt_id": f"syntavra-{index}", "wall_time_ms": 100.0, "input_tokens": 100, "cached_input_tokens": 0, "output_tokens": 10, "cost_usd": 0.1, "arm": "syntavra", **common})
    return rows


def _signalbench_provider_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index in range(10):
        common = dict(
            task_id=f"provider-task-{index}",
            repetition=index + 1,
            success=True,
            verifier_success=True,
            verified_work=1.0,
            wall_seconds=1.0,
            exit_code=0,
            fresh_input_tokens=100,
            cached_input_tokens=0,
            output_tokens=10,
            reasoning_tokens=0,
            model_turns=1,
            tool_calls=1,
            wait_calls=0,
            compactions=0,
            security_regressions=0,
            verifier_skips=0,
            repository_tree="provider-repo-tree",
            prompt_hash="provider-prompt",
            verifier_hash="provider-verifier",
            permissions_hash="provider-permissions",
            cache_mode="cold",
            artifact_dir=f"artifact-{index}",
            error="",
            provider_observed=True,
            provider="openai",
            model="fixture-model",
            request_id_hash=hashlib.sha256(f"req-{index}".encode()).hexdigest(),
            provider_receipt_hash=hashlib.sha256(f"provider-{index}".encode()).hexdigest(),
        )
        rows.append(asdict(RunResult(run_id=f"baseline-{index}", arm_id="plain-host", quota_cost=2.0, **common)))
        rows.append(asdict(RunResult(run_id=f"candidate-{index}", arm_id="syntavra-minimal", quota_cost=1.0, **common)))
    return rows


def _external_receipts() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index in range(30):
        common = dict(
            suite_id="longbench-v2",
            task_id=f"external-task-{index}",
            repetition=index + 1,
            dataset_version="fixture-v1",
            harness_commit=HEX40,
            verifier_commit="d" * 40,
            environment_image_digest=DIGEST,
            repository_commit="",
            provider="openai",
            model="fixture-model",
            model_config_hash="e" * 64,
            quality_score=0.9,
            success=True,
            cached_input_tokens=0,
            recursive_calls=0,
            synthetic=False,
            metadata={},
        )
        rows.append({"receipt_id": f"ext-base-{index}", "arm": "baseline", "result_artifact_hash": hashlib.sha256(f"base-art-{index}".encode()).hexdigest(), "raw_provider_receipt_hash": hashlib.sha256(f"base-provider-{index}".encode()).hexdigest(), "input_tokens": 1000, "output_tokens": 100, "cost_usd": 1.0, "wall_time_ms": 1000.0, **common})
        rows.append({"receipt_id": f"ext-syn-{index}", "arm": "syntavra", "result_artifact_hash": hashlib.sha256(f"syn-art-{index}".encode()).hexdigest(), "raw_provider_receipt_hash": hashlib.sha256(f"syn-provider-{index}".encode()).hexdigest(), "input_tokens": 100, "output_tokens": 10, "cost_usd": 0.1, "wall_time_ms": 100.0, **common})
    return rows


def _integration_receipts() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, operating_system in enumerate(("linux", "windows", "linux"), start=1):
        rows.append({
            "receipt_id": f"integration-{index}",
            "integration_id": "claude-code",
            "product_version": "0.0.1",
            "source_tree_hash": hashlib.sha256(f"tree-{index}".encode()).hexdigest(),
            "operating_system": operating_system,
            "live": True,
            "detected": True,
            "command_config_verified": True,
            "artifact_hash": hashlib.sha256(f"artifact-{index}".encode()).hexdigest(),
            "result_hash": hashlib.sha256(f"result-{index}".encode()).hexdigest(),
            "apply_attempted": True,
            "restoration_verified": True,
            "rollback_hash": hashlib.sha256(f"rollback-{index}".encode()).hexdigest(),
            "real_repository": True,
            "provider_billed": False,
            "provider": "",
            "model": "",
            "request_id_hash": "",
            "raw_provider_receipt_hash": "",
            "metadata": {},
        })
    return rows


def _proof_contract(repo: Path, project: Path, state: Path, root: Path) -> dict[str, Any]:
    proof_status = _json("proof status", _run(repo, project, state, ["proof", "status"]), 0)
    if proof_status.get("claim") != "NOT_PROVEN_WITHOUT_LIVE_EVIDENCE":
        raise AssertionError(f"proof status claim boundary drift: {proof_status}")
    prove_plan = _json("prove plan", _run(repo, project, state, ["prove", "plan"]), 0)
    if prove_plan.get("claim") != "EXTERNAL_SUPERIORITY_NOT_PROVEN":
        raise AssertionError(f"prove plan claim boundary drift: {prove_plan}")

    provider_receipts = _provider_receipts()
    receipts_path = root / "provider-receipts.json"
    receipts_path.write_text(json.dumps({"receipts": provider_receipts}, separators=(",", ":")), encoding="utf-8")
    receipts = _json("prove receipts", _run(repo, project, state, ["prove", "receipts", str(receipts_path)]), 0)
    benchmark = _json("prove benchmark", _run(repo, project, state, ["prove", "benchmark", str(receipts_path)]), 0)
    if receipts.get("ok") is not True or receipts.get("total") != 60 or receipts.get("live") != 60:
        raise AssertionError(f"provider receipt validation drift: {receipts}")
    if benchmark.get("ok") is not True or benchmark.get("metrics", {}).get("pairs") != 30 or benchmark.get("metrics", {}).get("repositories") != 5 or benchmark.get("metrics", {}).get("tasks") != 10 or benchmark.get("metrics", {}).get("workloads") != 3:
        raise AssertionError(f"measured benchmark gate drift: {benchmark}")
    if benchmark.get("metrics", {}).get("mean_token_ratio") != 0.1 or benchmark.get("metrics", {}).get("mean_cost_ratio") != 0.1 or benchmark.get("metrics", {}).get("mean_wall_time_ratio") != 0.1:
        raise AssertionError(f"measured benchmark ratio drift: {benchmark}")

    provider_rows = _signalbench_provider_rows()
    provider_path = root / "provider-billed-results.json"
    provider_path.write_text(json.dumps({"results": provider_rows}, separators=(",", ":")), encoding="utf-8")
    provider_billed = _json("prove provider-billed", _run(repo, project, state, ["prove", "provider-billed", str(provider_path), "--baseline", "plain-host", "--candidate", "syntavra-minimal"]), 0)
    if provider_billed.get("claimable_superiority") is not True or provider_billed.get("valid_pairs") != 10 or provider_billed.get("provider_observed_runs") != 20:
        raise AssertionError(f"provider-billed compare drift: {provider_billed}")
    if provider_billed.get("median_efficiency_ratio") != 2.0 or provider_billed.get("confidence_interval_95") != [2.0, 2.0]:
        raise AssertionError(f"provider-billed statistics drift: {provider_billed}")

    external_rows = _external_receipts()
    external_path = root / "external-receipts.json"
    external_path.write_text(json.dumps({"receipts": external_rows}, separators=(",", ":")), encoding="utf-8")
    external_suite = _json("prove external-suite", _run(repo, project, state, ["prove", "external-suite", str(external_path), "--suite", "longbench-v2"]), 0)
    if external_suite.get("ok") is not True or external_suite.get("metrics", {}).get("pairs") != 30 or external_suite.get("suites") != ["longbench-v2"]:
        raise AssertionError(f"external-suite gate drift: {external_suite}")
    if external_suite.get("metrics", {}).get("mean_token_ratio") != 0.1 or external_suite.get("metrics", {}).get("mean_cost_ratio") != 0.1 or external_suite.get("metrics", {}).get("mean_wall_time_ratio") != 0.1:
        raise AssertionError(f"external-suite ratio drift: {external_suite}")

    integration_rows = _integration_receipts()
    integration_path = root / "integration-receipts.json"
    integration_path.write_text(json.dumps({"receipts": integration_rows}, separators=(",", ":")), encoding="utf-8")
    integrations = _json("prove integrations", _run(repo, project, state, ["prove", "integrations", str(integration_path), "--integration", "claude-code"]), 0)
    if integrations.get("ok") is not True or integrations.get("metrics", {}).get("certified_integrations") != 1 or integrations.get("metrics", {}).get("receipts") != 3:
        raise AssertionError(f"live integration gate drift: {integrations}")

    long_context = _json("prove long-context manifest", _run(repo, project, state, ["prove", "long-context"]), 0)
    if not isinstance(long_context.get("required_workloads"), list) or not long_context.get("required_workloads"):
        raise AssertionError(f"long-context proof manifest drift: {long_context}")

    empty_path = root / "empty.json"
    empty_path.write_text("[]\n", encoding="utf-8")
    maturity = _json("prove maturity empty", _run(repo, project, state, ["prove", "maturity", str(empty_path)]), 4)
    if maturity.get("ok") is not False:
        raise AssertionError(f"maturity empty-state fail-closed drift: {maturity}")
    readiness = _json("prove readiness empty", _run(repo, project, state, ["prove", "readiness"]), 4)
    if readiness.get("ok") is not False:
        raise AssertionError(f"readiness empty-state fail-closed drift: {readiness}")

    schema_path = root / "provider-usage-schema.json"
    schema = _json("prove schema", _run(repo, project, state, ["prove", "schema", "--output", str(schema_path)]), 0)
    if schema.get("ok") is not True or not schema_path.is_file() or schema.get("schema") != json.loads(schema_path.read_text(encoding="utf-8")):
        raise AssertionError(f"proof schema write drift: {schema}")
    suites = _json("prove suites", _run(repo, project, state, ["prove", "suites"]), 0)
    if suites.get("suite_count") != 5 or [row["suite_id"] for row in suites.get("suites") or []] != [row["suite_id"] for row in ExternalSuiteRegistry.manifest()["suites"]]:
        raise AssertionError(f"external suite manifest ordering drift: {suites}")

    malformed_provider_path = root / "malformed-provider-results.json"
    malformed_provider_path.write_text('{"results":[{"run_id":"broken"}]}\n', encoding="utf-8")
    malformed_provider = _run(repo, project, state, ["prove", "provider-billed", str(malformed_provider_path)])

    malformed_external_path = root / "malformed-external.json"
    malformed_external_path.write_text('{"receipts":[{"receipt_id":"broken","repetition":null}]}\n', encoding="utf-8")
    malformed_external = _run(repo, project, state, ["prove", "external-suite", str(malformed_external_path)])

    return {
        "proof_status": proof_status,
        "plan_keys": sorted(prove_plan),
        "receipts": receipts,
        "measured_benchmark": benchmark,
        "provider_billed": provider_billed,
        "external_suite": external_suite,
        "integrations": integrations,
        "long_context_manifest_keys": sorted(long_context),
        "maturity_empty": {"exit": 4, "reasons": maturity.get("reasons")},
        "readiness_empty": {"exit": 4, "failures": readiness.get("failures")},
        "schema_keys": sorted(schema.get("schema") or {}),
        "suites": {"suite_count": suites["suite_count"], "suite_ids": [row["suite_id"] for row in suites["suites"]]},
        "malformed_provider_raw": {"exit": malformed_provider["exit"], "stderr_nonempty": bool(malformed_provider["stderr"]), "json_object": isinstance(malformed_provider["value"], dict)},
        "malformed_external_raw": {"exit": malformed_external["exit"], "stderr_nonempty": bool(malformed_external["stderr"]), "json_object": isinstance(malformed_external["value"], dict)},
    }


def _component_empty_and_ordering() -> dict[str, Any]:
    measured_empty = MeasuredBenchmarkGate.evaluate([])
    external_empty = ExternalBenchmarkGate.evaluate([])
    integration_empty = LiveCertificationGate.evaluate([])
    if measured_empty.get("ok") is not False or measured_empty.get("reasons") != sorted(measured_empty.get("reasons") or []):
        raise AssertionError(f"measured benchmark empty/order drift: {measured_empty}")
    if external_empty.get("ok") is not False or external_empty.get("reasons") != sorted(external_empty.get("reasons") or []):
        raise AssertionError(f"external benchmark empty/order drift: {external_empty}")
    if integration_empty.get("ok") is not False or integration_empty.get("reasons") != sorted(integration_empty.get("reasons") or []):
        raise AssertionError(f"integration empty/order drift: {integration_empty}")
    return {"measured": measured_empty, "external": external_empty, "integration": integration_empty}


def certify(repo: Path) -> dict[str, Any]:
    fixture_path = repo / FIXTURE_RELATIVE
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory(prefix="syntavra-python-benchmark-proof-") as directory:
        root = Path(directory)
        project, state = root / "project", root / "state"
        project.mkdir(); state.mkdir(); (project / ".git").mkdir()
        routes = _routes(fixture)
        thresholds = _thresholds(fixture)
        benchmark = _benchmark_component(root)
        signalbench = _signalbench_contract(repo, project, state, root)
        proof = _proof_contract(repo, project, state, root)
        empty = _component_empty_and_ordering()
    return {
        "ok": True,
        "schema_version": 1,
        "family": "benchmark-proof",
        "engine": "python",
        "exact_head": _head(repo),
        "fixture": str(FIXTURE_RELATIVE).replace("\\", "/"),
        "fixture_sha256": hashlib.sha256(fixture_path.read_bytes()).hexdigest(),
        "routes": routes,
        "thresholds": thresholds,
        "benchmark": benchmark,
        "signalbench": signalbench,
        "proof": proof,
        "empty_state": empty,
        "exit_policy": fixture["exit_policy"],
        "ordering": fixture["ordering"],
        "nondeterministic_fields": ["bootstrap resampling internals where ratios are not constant", "generated proof schema output path", "temporary fixture paths"],
        "claim_boundary": "offline deterministic certification fixtures validate Python gate semantics only; they are not real external superiority evidence",
        "network_boundary": fixture["network_boundary"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Certify Python benchmark/proof reference behavior")
    parser.add_argument("--repo", default=str(Path(__file__).resolve().parents[1])); parser.add_argument("--output")
    args = parser.parse_args(); repo = Path(args.repo).resolve(strict=True)
    try:
        result = certify(repo)
    except Exception as exc:
        result = {"ok": False, "schema_version": 1, "family": "benchmark-proof", "engine": "python", "exact_head": _head(repo), "error": f"{type(exc).__name__}: {exc}", "traceback": traceback.format_exc()}
    rendered = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True, default=str)
    if args.output: Path(args.output).write_text(rendered + "\n", encoding="utf-8")
    print(rendered); return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
