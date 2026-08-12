#!/usr/bin/env python3
from __future__ import annotations

import argparse, hashlib, json, os, subprocess, sys, tempfile, traceback
from collections import Counter
from dataclasses import asdict
from pathlib import Path
from typing import Any

from syntavra_runtime.benchmark_harness import ArmResult, compare_results, generate_synthetic_repository, validate_config, write_config
from syntavra_runtime.external_benchmarks import ExternalBenchmarkGate, ExternalSuiteRegistry
from syntavra_runtime.live_certification import LiveCertificationGate
from syntavra_runtime.paired_benchmark import CATEGORY_COUNTS, DEFAULT_ARMS, CodingCorpusPlanner, SuperiorityGate
from syntavra_runtime.product_surface import MeasuredBenchmarkGate, PROOF_WORKLOADS
from syntavra_runtime.signalbench import RunResult
from tools import report_missing_native_public_routes as public_surface
from tools import report_python_public_execution_contract as execution_contract

FIXTURE_RELATIVE = Path("contracts/python/benchmark-proof-reference-v1.json")
HEX40 = "b" * 40
DIGEST = "sha256:" + "c" * 64


def _head(repo: Path) -> str:
    p = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    return p.stdout.strip() if p.returncode == 0 else ""


def _run(repo: Path, project: Path, state: Path, argv: list[str]) -> dict[str, Any]:
    env = os.environ.copy()
    env.update({"PYTHONPATH": str(repo), "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"})
    env.pop("SYNTAVRA_BULK_PARITY_PROBE", None)
    p = subprocess.run(
        [sys.executable, "-m", "syntavra_runtime.engine_entry", "--engine", "python", "--project", str(project), "--state-root", str(state), *argv],
        cwd=repo, env=env, text=True, encoding="utf-8", errors="replace",
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=90, check=False,
    )
    try:
        value = json.loads(p.stdout) if p.stdout.strip() else None
    except json.JSONDecodeError:
        value = None
    return {"exit": p.returncode, "stdout": p.stdout, "stderr": p.stderr, "value": value}


def _json(label: str, result: dict[str, Any], code: int) -> dict[str, Any]:
    if result["exit"] != code or result["stderr"]:
        raise AssertionError(f"{label}: expected clean exit {code}, got {result}")
    if not isinstance(result["value"], dict):
        raise AssertionError(f"{label}: expected JSON object stdout, got {result}")
    return result["value"]


def _routes(fixture: dict[str, Any]) -> dict[str, Any]:
    routes = sorted(route for route in public_surface.python_public_route_sources() if route in set(fixture["public_routes"]))
    if routes != fixture["public_routes"]:
        raise AssertionError(f"benchmark/proof route inventory drift: {routes}")
    manifest = {row["route"]: row for row in execution_contract.route_execution_manifest()}
    owners = {}
    for route in routes:
        row = manifest[route]
        if len(row["entrypoints"]) != 1 or row["unknown_sources"]:
            raise AssertionError(f"benchmark/proof ownership drift: {row}")
        owners[route] = row["entrypoint"]
    return {"routes": routes, "route_count": len(routes), "route_sha256": public_surface._digest(routes), "ownership": owners}


def _thresholds(fixture: dict[str, Any]) -> dict[str, Any]:
    value = {
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
        "provider_billed": {"minimum_provider_observed_runs": 10, "minimum_cost_ratio_lcb": 1.0},
    }
    if value != fixture["thresholds"]:
        raise AssertionError(f"benchmark/proof threshold drift: {value}")
    return value


def _benchmark(root: Path) -> dict[str, Any]:
    config = write_config(root / "benchmark-config.json", "1X")
    validation = validate_config(config)
    if validation.get("ok") is not True:
        raise AssertionError(f"generated benchmark config no longer validates: {validation}")
    generated = generate_synthetic_repository(root / "synthetic-repository", files=8, depth=3, fanout=2, faults=2)
    if [generated.get(k) for k in ("files", "depth", "fanout", "faults")] != [11, 3, 2, 2] or len(str(generated.get("ground_truth_hash", ""))) != 64:
        raise AssertionError(f"synthetic repository result drift: {generated}")
    common = dict(
        success=True, verified_work=1.0, model_turns=1, wait_calls=0, verifier_skips=0,
        security_regressions=0, repository_tree="repo-tree", model="fixture-model",
        reasoning="fixture-reasoning", prompt_hash="prompt-hash", verifier_hash="verifier-hash",
        cache_mode="cold", permissions_hash="permissions-hash", timeout_seconds=60.0,
        workload_hash="workload-hash", observed_axes=dict(config["observed_baseline"]),
    )
    base, cand = [], []
    for rep in range(1, 11):
        base.append(ArmResult(arm="baseline", repetition=rep, quota_cost=2.0, fresh_input_tokens=1000, cached_input_tokens=0, output_tokens=100, reasoning_tokens=0, wall_seconds=10.0, order_index=rep, **common))
        cand.append(ArmResult(arm="syntavra", repetition=rep, quota_cost=1.0, fresh_input_tokens=100, cached_input_tokens=0, output_tokens=10, reasoning_tokens=0, wall_seconds=1.0, order_index=rep, **common))
    comp = compare_results(base, cand, tier="1X", config=config)
    if comp.get("valid_pairs") != 10 or comp.get("invalid_runs") != [] or comp.get("diagnostics", {}).get("token_ratios") != [10.0] * 10 or comp.get("diagnostics", {}).get("quota_available") is not True:
        raise AssertionError(f"benchmark compare drift: {comp}")
    return {
        "config_schema_version": config["schema_version"], "config_tier": config["tier"],
        "config_validation_keys": sorted(validation),
        "generated_repository": {"files": generated["files"], "depth": generated["depth"], "fanout": generated["fanout"], "faults": generated["faults"], "ground_truth_hash_shape": True},
        "comparison": comp,
    }


def _signalbench_rows() -> list[dict[str, Any]]:
    rows = []
    for i in range(SuperiorityGate.required_tasks):
        task = f"task-{i:03d}"
        for rep in range(1, SuperiorityGate.required_repetitions + 1):
            pair = f"{task}:{rep}"
            common = {"task_id": task, "repetition": rep, "source_kind": "live-external-arm", "synthetic": False, "success": True, "security_regressions": 0, "pair_key": pair}
            rows += [
                {**common, "arm_id": "plain-baseline", "active_tokens": 1000, "wall_seconds": 10.0},
                {**common, "arm_id": "syntavra", "active_tokens": 100, "wall_seconds": 1.0},
            ]
    return rows


def _signalbench(repo: Path, project: Path, state: Path, root: Path) -> dict[str, Any]:
    plan = _json("signalbench plan", _run(repo, project, state, ["signalbench", "plan", "--repetitions", "30"]), 0)
    plan2 = _json("signalbench2 plan", _run(repo, project, state, ["signalbench2", "plan", "--repetitions", "30"]), 0)
    if plan != plan2:
        raise AssertionError("signalbench and signalbench2 plan compatibility drift")
    manifest = plan.get("manifest")
    tasks = manifest.get("tasks") if isinstance(manifest, dict) else None
    arms = manifest.get("arms") if isinstance(manifest, dict) else None
    if not isinstance(tasks, list) or not isinstance(arms, list):
        raise AssertionError(f"signalbench manifest shape drift: {plan}")
    runs = CodingCorpusPlanner.task_target * SuperiorityGate.required_repetitions * len(DEFAULT_ARMS)
    categories = Counter(str(row.get("category")) for row in tasks if isinstance(row, dict))
    if plan.get("corpus") != {"tasks": 150, "live": False} or plan.get("schedule") != {"runs": runs, "repetitions": 30}:
        raise AssertionError(f"signalbench plan arithmetic drift: {plan}")
    if manifest.get("schema_version") != 2 or manifest.get("repetitions") != 30 or manifest.get("run_count") != runs or len(tasks) != 150:
        raise AssertionError(f"signalbench manifest arithmetic drift: {manifest}")
    if [row.get("arm_id") for row in arms] != list(DEFAULT_ARMS) or dict(categories) != CATEGORY_COUNTS:
        raise AssertionError(f"signalbench inventory drift: arms={arms}, categories={dict(categories)}")
    rows = _signalbench_rows()
    path = root / "signalbench-gate.json"
    path.write_text(json.dumps(rows, separators=(",", ":")), encoding="utf-8")
    gate = _json("signalbench gate", _run(repo, project, state, ["signalbench", "gate", str(path)]), 0)
    gate2 = _json("signalbench2 gate", _run(repo, project, state, ["signalbench2", "gate", str(path)]), 0)
    expected_metrics = {"tasks": 150, "repetitions": 30, "success": 1.0, "token_ratio": 0.1, "wall_ratio": 0.1, "security_regressions": 0, "candidate_wall_p95": 1.0}
    if gate != gate2 or gate.get("ok") is not True or gate.get("claim") != "SUPERIORITY_PROVEN" or gate.get("metrics") != expected_metrics:
        raise AssertionError(f"signalbench gate drift: {gate} / {gate2}")
    empty = SuperiorityGate.evaluate([])
    empty_reasons = ["insufficient-tasks", "insufficient-repetitions", "missing-required-arm", "success-floor-missed", "token-target-missed", "wall-target-missed", "paired-coverage-incomplete"]
    if empty.get("ok") is not False or empty.get("claim") != "EXTERNAL_SUPERIORITY_NOT_PROVEN" or empty.get("reasons") != empty_reasons:
        raise AssertionError(f"signalbench empty-state drift: {empty}")
    return {
        "plan": {
            "corpus": plan["corpus"], "schedule": plan["schedule"],
            "manifest": {"schema_version": manifest["schema_version"], "repetitions": manifest["repetitions"], "run_count": manifest["run_count"], "task_count": len(tasks), "arm_ids": [row["arm_id"] for row in arms], "category_counts": dict(categories), "manifest_hash": manifest["manifest_hash"]},
        },
        "gate": gate, "empty": empty, "fixture_rows": len(rows),
        "pair_count": SuperiorityGate.required_tasks * SuperiorityGate.required_repetitions,
    }


def _provider_receipts() -> list[dict[str, Any]]:
    rows, workloads = [], list(PROOF_WORKLOADS[:3])
    for i in range(30):
        common = {
            "provider": "openai", "model": "fixture-model", "request_id": f"request-{i}", "session_id": f"session-{i}",
            "repository_hash": hashlib.sha256(f"repo-{i % 5}".encode()).hexdigest(), "integration_id": "openai",
            "observed_at": "2026-08-12T00:00:00+00:00", "quality_score": 0.9, "success": True,
            "synthetic": False, "raw_usage_hash": hashlib.sha256(f"usage-{i}".encode()).hexdigest(),
            "workload": workloads[i % len(workloads)], "task_id": f"task-{i % 10}", "repetition": i + 1, "metadata": {},
        }
        rows += [
            {"receipt_id": f"baseline-{i}", "wall_time_ms": 1000.0, "input_tokens": 1000, "cached_input_tokens": 0, "output_tokens": 100, "cost_usd": 1.0, "arm": "baseline", **common},
            {"receipt_id": f"syntavra-{i}", "wall_time_ms": 100.0, "input_tokens": 100, "cached_input_tokens": 0, "output_tokens": 10, "cost_usd": 0.1, "arm": "syntavra", **common},
        ]
    return rows


def _provider_runs() -> list[dict[str, Any]]:
    rows = []
    for i in range(10):
        common = dict(
            task_id=f"provider-task-{i}", repetition=i + 1, success=True, verifier_success=True,
            verified_work=1.0, wall_seconds=1.0, exit_code=0, fresh_input_tokens=100,
            cached_input_tokens=0, output_tokens=10, reasoning_tokens=0, model_turns=1, tool_calls=1,
            wait_calls=0, compactions=0, security_regressions=0, verifier_skips=0,
            repository_tree="provider-repo-tree", prompt_hash="provider-prompt", verifier_hash="provider-verifier",
            permissions_hash="provider-permissions", cache_mode="cold", artifact_dir=f"artifact-{i}", error="",
            provider_observed=True, provider="openai", model="fixture-model",
            request_id_hash=hashlib.sha256(f"req-{i}".encode()).hexdigest(),
            provider_receipt_hash=hashlib.sha256(f"provider-{i}".encode()).hexdigest(),
        )
        rows += [
            asdict(RunResult(run_id=f"baseline-{i}", arm_id="plain-host", quota_cost=2.0, **common)),
            asdict(RunResult(run_id=f"candidate-{i}", arm_id="syntavra-minimal", quota_cost=1.0, **common)),
        ]
    return rows


def _external_receipts() -> list[dict[str, Any]]:
    rows = []
    for i in range(30):
        common = dict(
            suite_id="longbench-v2", task_id=f"external-task-{i}", repetition=i + 1, dataset_version="fixture-v1",
            harness_commit=HEX40, verifier_commit="d" * 40, environment_image_digest=DIGEST, repository_commit="",
            provider="openai", model="fixture-model", model_config_hash="e" * 64, quality_score=0.9, success=True,
            cached_input_tokens=0, recursive_calls=0, synthetic=False, metadata={},
        )
        rows += [
            {"receipt_id": f"ext-base-{i}", "arm": "baseline", "result_artifact_hash": hashlib.sha256(f"base-art-{i}".encode()).hexdigest(), "raw_provider_receipt_hash": hashlib.sha256(f"base-provider-{i}".encode()).hexdigest(), "input_tokens": 1000, "output_tokens": 100, "cost_usd": 1.0, "wall_time_ms": 1000.0, **common},
            {"receipt_id": f"ext-syn-{i}", "arm": "syntavra", "result_artifact_hash": hashlib.sha256(f"syn-art-{i}".encode()).hexdigest(), "raw_provider_receipt_hash": hashlib.sha256(f"syn-provider-{i}".encode()).hexdigest(), "input_tokens": 100, "output_tokens": 10, "cost_usd": 0.1, "wall_time_ms": 100.0, **common},
        ]
    return rows


def _integration_receipts() -> list[dict[str, Any]]:
    rows = []
    for i, os_name in enumerate(("linux", "windows", "linux"), 1):
        rows.append({
            "receipt_id": f"integration-{i}", "integration_id": "claude-code", "product_version": "0.0.1",
            "source_tree_hash": hashlib.sha256(f"tree-{i}".encode()).hexdigest(), "operating_system": os_name,
            "live": True, "detected": True, "command_config_verified": True,
            "artifact_hash": hashlib.sha256(f"artifact-{i}".encode()).hexdigest(),
            "result_hash": hashlib.sha256(f"result-{i}".encode()).hexdigest(), "apply_attempted": True,
            "restoration_verified": True, "rollback_hash": hashlib.sha256(f"rollback-{i}".encode()).hexdigest(),
            "real_repository": True, "provider_billed": False, "provider": "", "model": "",
            "request_id_hash": "", "raw_provider_receipt_hash": "", "metadata": {},
        })
    return rows


def _proof(repo: Path, project: Path, state: Path, root: Path) -> dict[str, Any]:
    status = _json("proof status", _run(repo, project, state, ["proof", "status"]), 0)
    plan = _json("prove plan", _run(repo, project, state, ["prove", "plan"]), 0)
    if status.get("claim") != "NOT_PROVEN_WITHOUT_LIVE_EVIDENCE" or plan.get("claim") != "EXTERNAL_SUPERIORITY_NOT_PROVEN":
        raise AssertionError(f"proof claim boundary drift: {status} / {plan}")

    receipts_path = root / "provider-receipts.json"
    receipts_path.write_text(json.dumps({"receipts": _provider_receipts()}, separators=(",", ":")), encoding="utf-8")
    receipts = _json("prove receipts", _run(repo, project, state, ["prove", "receipts", str(receipts_path)]), 0)
    bench = _json("prove benchmark", _run(repo, project, state, ["prove", "benchmark", str(receipts_path)]), 0)
    bm = bench.get("metrics", {})
    if receipts.get("ok") is not True or receipts.get("total") != 60 or receipts.get("live") != 60:
        raise AssertionError(f"provider receipt validation drift: {receipts}")
    if bench.get("ok") is not True or [bm.get(k) for k in ("pairs", "repositories", "tasks", "workloads")] != [30, 5, 10, 3] or [bm.get(k) for k in ("mean_token_ratio", "mean_cost_ratio", "mean_wall_time_ratio")] != [0.1, 0.1, 0.1]:
        raise AssertionError(f"measured benchmark drift: {bench}")

    provider_path = root / "provider-billed.json"
    provider_path.write_text(json.dumps({"results": _provider_runs()}, separators=(",", ":")), encoding="utf-8")
    provider = _json("prove provider-billed", _run(repo, project, state, ["prove", "provider-billed", str(provider_path), "--baseline", "plain-host", "--candidate", "syntavra-minimal"]), 0)
    if provider.get("claimable_superiority") is not True or provider.get("valid_pairs") != 10 or provider.get("provider_observed_runs") != 20 or provider.get("median_efficiency_ratio") != 2.0 or provider.get("confidence_interval_95") != [2.0, 2.0]:
        raise AssertionError(f"provider-billed drift: {provider}")

    external_path = root / "external.json"
    external_path.write_text(json.dumps({"receipts": _external_receipts()}, separators=(",", ":")), encoding="utf-8")
    external = _json("prove external-suite", _run(repo, project, state, ["prove", "external-suite", str(external_path), "--suite", "longbench-v2"]), 0)
    em = external.get("metrics", {})
    if external.get("ok") is not True or em.get("pairs") != 30 or external.get("suites") != ["longbench-v2"] or [em.get(k) for k in ("mean_token_ratio", "mean_cost_ratio", "mean_wall_time_ratio")] != [0.1, 0.1, 0.1]:
        raise AssertionError(f"external-suite drift: {external}")

    integration_path = root / "integrations.json"
    integration_path.write_text(json.dumps({"receipts": _integration_receipts()}, separators=(",", ":")), encoding="utf-8")
    integrations = _json("prove integrations", _run(repo, project, state, ["prove", "integrations", str(integration_path), "--integration", "claude-code"]), 0)
    if integrations.get("ok") is not True or integrations.get("metrics", {}).get("certified_integrations") != 1 or integrations.get("metrics", {}).get("receipts") != 3:
        raise AssertionError(f"integration drift: {integrations}")

    long_context = _json("prove long-context", _run(repo, project, state, ["prove", "long-context"]), 0)
    if not isinstance(long_context.get("required_workloads"), list) or not long_context["required_workloads"]:
        raise AssertionError(f"long-context drift: {long_context}")
    empty_path = root / "empty.json"; empty_path.write_text("[]\n", encoding="utf-8")
    maturity = _json("prove maturity empty", _run(repo, project, state, ["prove", "maturity", str(empty_path)]), 4)
    readiness = _json("prove readiness empty", _run(repo, project, state, ["prove", "readiness"]), 4)
    if maturity.get("ok") is not False or readiness.get("ok") is not False:
        raise AssertionError(f"empty proof fail-closed drift: {maturity} / {readiness}")

    schema_path = root / "provider-usage-schema.json"
    schema = _json("prove schema", _run(repo, project, state, ["prove", "schema", "--output", str(schema_path)]), 0)
    if schema.get("ok") is not True or not schema_path.is_file() or schema.get("schema") != json.loads(schema_path.read_text(encoding="utf-8")):
        raise AssertionError(f"proof schema drift: {schema}")
    suites = _json("prove suites", _run(repo, project, state, ["prove", "suites"]), 0)
    suite_ids = [row["suite_id"] for row in ExternalSuiteRegistry.manifest()["suites"]]
    if suites.get("suite_count") != 5 or [row["suite_id"] for row in suites.get("suites") or []] != suite_ids:
        raise AssertionError(f"suite ordering drift: {suites}")

    bad_provider = root / "bad-provider.json"; bad_provider.write_text('{"results":[{"run_id":"broken"}]}\n', encoding="utf-8")
    bad_external = root / "bad-external.json"; bad_external.write_text('{"receipts":[{"receipt_id":"broken","repetition":null}]}\n', encoding="utf-8")
    bp = _run(repo, project, state, ["prove", "provider-billed", str(bad_provider)])
    be = _run(repo, project, state, ["prove", "external-suite", str(bad_external)])
    raw = lambda r: {"exit": r["exit"], "stderr_nonempty": bool(r["stderr"]), "json_object": isinstance(r["value"], dict)}

    return {
        "proof_status": status, "plan_keys": sorted(plan), "receipts": receipts, "measured_benchmark": bench,
        "provider_billed": provider, "external_suite": external, "integrations": integrations,
        "long_context_manifest_keys": sorted(long_context),
        "maturity_empty": {"exit": 4, "reasons": maturity.get("reasons")},
        "readiness_empty": {"exit": 4, "failures": readiness.get("failures")},
        "schema_keys": sorted(schema.get("schema") or {}),
        "suites": {"suite_count": suites["suite_count"], "suite_ids": [row["suite_id"] for row in suites["suites"]]},
        "malformed_provider_raw": raw(bp), "malformed_external_raw": raw(be),
    }


def _empty() -> dict[str, Any]:
    values = {"measured": MeasuredBenchmarkGate.evaluate([]), "external": ExternalBenchmarkGate.evaluate([]), "integration": LiveCertificationGate.evaluate([])}
    for label, value in values.items():
        if value.get("ok") is not False or value.get("reasons") != sorted(value.get("reasons") or []):
            raise AssertionError(f"{label} empty/order drift: {value}")
    return values


def certify(repo: Path) -> dict[str, Any]:
    fixture_path = repo / FIXTURE_RELATIVE
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory(prefix="syntavra-python-benchmark-proof-") as d:
        root = Path(d); project = root / "project"; state = root / "state"
        project.mkdir(); state.mkdir(); (project / ".git").mkdir()
        result = {
            "routes": _routes(fixture), "thresholds": _thresholds(fixture), "benchmark": _benchmark(root),
            "signalbench": _signalbench(repo, project, state, root), "proof": _proof(repo, project, state, root),
            "empty_state": _empty(),
        }
    return {
        "ok": True, "schema_version": 1, "family": "benchmark-proof", "engine": "python",
        "exact_head": _head(repo), "fixture": str(FIXTURE_RELATIVE).replace("\\", "/"),
        "fixture_sha256": hashlib.sha256(fixture_path.read_bytes()).hexdigest(), **result,
        "exit_policy": fixture["exit_policy"], "ordering": fixture["ordering"],
        "nondeterministic_fields": ["bootstrap resampling internals where ratios are not constant", "generated proof schema output path", "temporary fixture paths"],
        "claim_boundary": "offline deterministic certification fixtures validate Python gate semantics only; they are not real external superiority evidence",
        "network_boundary": fixture["network_boundary"],
    }


def main() -> int:
    p = argparse.ArgumentParser(description="Certify Python benchmark/proof reference behavior")
    p.add_argument("--repo", default=str(Path(__file__).resolve().parents[1])); p.add_argument("--output")
    args = p.parse_args(); repo = Path(args.repo).resolve(strict=True)
    try:
        result = certify(repo)
    except Exception as exc:
        result = {"ok": False, "schema_version": 1, "family": "benchmark-proof", "engine": "python", "exact_head": _head(repo), "error": f"{type(exc).__name__}: {exc}", "traceback": traceback.format_exc()}
    rendered = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True, default=str)
    if args.output:
        Path(args.output).write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
