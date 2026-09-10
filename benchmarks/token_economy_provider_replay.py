#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any, Iterable, Mapping

from benchmarks.token_economy_frozen_corpus import materialize
from syntavra_runtime.signalbench import ArmSpec, RunResult, SignalBenchError, SignalBenchRunner, TaskSpec
from syntavra_runtime.util import atomic_write_json, canonical_json, sha256_bytes


ROOT = Path(__file__).resolve().parents[1]
WORKLOAD_CONTRACT = ROOT / "contracts/python/token-economy-frozen-workloads-v1.json"
_SECRET_MARKERS = ("API_KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL", "AUTH")
_REPETITION_POLICY = "minimum three repetitions"


def _load_arms(path: Path) -> list[ArmSpec]:
    value = json.loads(path.read_text(encoding="utf-8"))
    rows = value if isinstance(value, list) else value.get("arms", [])
    if not isinstance(rows, list):
        raise ValueError("arms document must contain a list")
    arms = [
        ArmSpec(
            **{
                **row,
                "command": tuple(row["command"]),
                "inherit_environment": tuple(row.get("inherit_environment", ())),
            }
        )
        for row in rows
    ]
    if len(arms) != 2:
        raise ValueError("provider replay requires exactly two arms: baseline and candidate")
    if len({arm.arm_id for arm in arms}) != 2:
        raise ValueError("provider replay arm IDs must be unique")
    for arm in arms:
        for key in arm.environment:
            upper = key.upper()
            if any(marker in upper for marker in _SECRET_MARKERS):
                raise ValueError(
                    f"explicit credential-like environment value forbidden in arms file: {arm.arm_id}:{key}; "
                    "use inherit_environment"
                )
    model_identity = {(arm.model, arm.reasoning, arm.context_window) for arm in arms}
    if len(model_identity) != 1:
        raise ValueError("baseline/candidate must use the same model, reasoning and context window")
    return arms


def _tasks(manifest: Mapping[str, Any]) -> list[TaskSpec]:
    return [
        TaskSpec(
            **{
                **row,
                "verifier": tuple(row["verifier"]),
                "permissions": tuple(row.get("permissions", ("read", "write", "execute"))),
            }
        )
        for row in manifest.get("tasks", [])
    ]


def _variant_map() -> dict[str, tuple[str, ...]]:
    contract = json.loads(WORKLOAD_CONTRACT.read_text(encoding="utf-8"))
    rows = contract.get("workloads", [])
    result: dict[str, tuple[str, ...]] = {}
    for row in rows:
        variants = tuple(str(value) for value in row.get("variants", ()))
        if not variants or any(value not in {"cold", "warm"} for value in variants):
            raise ValueError(f"invalid workload variants for {row.get('id')}: {variants}")
        result[str(row["id"])] = variants
    return result


def build_schedule(tasks: Iterable[TaskSpec], arms: Iterable[ArmSpec], repetitions: int) -> list[dict[str, Any]]:
    if repetitions < 3:
        raise ValueError("provider proof requires at least three repetitions per arm/workload variant")
    tasks = list(tasks)
    arms = list(arms)
    variants = _variant_map()
    if {task.task_id for task in tasks} != set(variants):
        raise ValueError("materialized task IDs do not exactly match frozen B0-B9 contract")
    rows: list[dict[str, Any]] = []
    for task in tasks:
        frozen_identity = str(task.metadata.get("frozen_workload_identity") or "")
        if not frozen_identity.startswith("sha256:"):
            raise ValueError(f"missing frozen workload identity: {task.task_id}")
        for cache_mode in variants[task.task_id]:
            for repetition in range(1, repetitions + 1):
                for arm in arms:
                    rows.append(
                        {
                            "task_id": task.task_id,
                            "arm_id": arm.arm_id,
                            "repetition": repetition,
                            "cache_mode": cache_mode,
                            "repository_tree": task.repository_tree,
                            "repository_commit": task.repository_commit,
                            "frozen_workload_identity": frozen_identity,
                        }
                    )
    return rows


def _pair_issues(results: Iterable[RunResult], baseline: str, candidate: str) -> list[str]:
    rows = list(results)
    keyed = {(row.task_id, row.repetition, row.cache_mode, row.arm_id): row for row in rows}
    pair_keys = sorted({(row.task_id, row.repetition, row.cache_mode) for row in rows})
    issues: list[str] = []
    for task_id, repetition, cache_mode in pair_keys:
        left = keyed.get((task_id, repetition, cache_mode, baseline))
        right = keyed.get((task_id, repetition, cache_mode, candidate))
        label = f"{task_id}:{cache_mode}:r{repetition}"
        if left is None or right is None:
            issues.append(f"{label}:missing-arm")
            continue
        for name in (
            "task_hash",
            "repository_tree",
            "repository_commit",
            "prompt_hash",
            "verifier_hash",
            "permissions_hash",
            "model",
            "reasoning",
            "context_window",
            "hardware_hash",
        ):
            if getattr(left, name) != getattr(right, name):
                issues.append(f"{label}:pair-identity-mismatch:{name}")
        if not left.provider_observed or not right.provider_observed:
            issues.append(f"{label}:provider-receipt-missing")
        elif left.provider != right.provider:
            issues.append(f"{label}:provider-mismatch")
    return issues


def plan(corpus_root: Path, arms_path: Path, repetitions: int) -> dict[str, Any]:
    manifest = materialize(corpus_root)
    tasks = _tasks(manifest)
    arms = _load_arms(arms_path)
    runner = SignalBenchRunner(corpus_root.parent / "validation")
    validation = runner.validate_product(tasks, arms)
    if not validation["ok"]:
        raise SignalBenchError("; ".join(validation["reasons"]))
    schedule = build_schedule(tasks, arms, repetitions)
    pair_count = len(schedule) // 2
    value = {
        "schema_version": 1,
        "family": "syntavra-token-economy-provider-replay",
        "mode": "plan",
        "claim_boundary": "REPLAY_PLAN_ONLY_NOT_PROVIDER_PROOF",
        "workload_count": len(tasks),
        "arm_count": len(arms),
        "repetitions": repetitions,
        "scheduled_runs": len(schedule),
        "scheduled_pairs": pair_count,
        "portable_corpus_identity": manifest["portable_identity_sha256"],
        "arms": [
            {
                "arm_id": arm.arm_id,
                "category": arm.category,
                "version": arm.version,
                "model": arm.model,
                "reasoning": arm.reasoning,
                "context_window": arm.context_window,
                "inherit_environment": list(arm.inherit_environment),
                "explicit_environment_keys": sorted(arm.environment),
            }
            for arm in arms
        ],
        "schedule": schedule,
    }
    value["plan_sha256"] = sha256_bytes(canonical_json(value))
    return value


def execute(corpus_root: Path, run_root: Path, arms_path: Path, repetitions: int, seed: int) -> dict[str, Any]:
    manifest = materialize(corpus_root)
    tasks = _tasks(manifest)
    task_by_id = {task.task_id: task for task in tasks}
    arms = _load_arms(arms_path)
    arm_by_id = {arm.arm_id: arm for arm in arms}
    runner = SignalBenchRunner(run_root, seed=seed)
    validation = runner.validate_product(tasks, arms)
    if not validation["ok"]:
        raise SignalBenchError("; ".join(validation["reasons"]))
    schedule = build_schedule(tasks, arms, repetitions)
    results: list[RunResult] = []
    for row in schedule:
        results.append(
            runner.run_one(
                task_by_id[row["task_id"]],
                arm_by_id[row["arm_id"]],
                repetition=int(row["repetition"]),
                cache_mode=str(row["cache_mode"]),
            )
        )
    baseline, candidate = (arm.arm_id for arm in arms)
    issues = _pair_issues(results, baseline, candidate)
    comparison = SignalBenchRunner.compare(results, baseline_arm=baseline, candidate_arm=candidate)
    receipt_complete = all(
        row.provider_observed
        and bool(row.usage_receipt_hash)
        and bool(row.provider_receipt_hash)
        and row.quota_cost is not None
        and float(row.quota_cost) > 0
        for row in results
    )
    comparator_integrity = bool(
        comparison.get("comparison_authority") == "HardenedSignalBench.compare"
        and not comparison.get("identity_mismatches")
        and not comparison.get("receipt_errors")
        and not comparison.get("invalid")
        and int(comparison.get("provider_observed_pairs") or 0) == len(schedule) // 2
    )
    provider_evidence_complete = bool(not issues and receipt_complete and comparator_integrity)
    superiority_proven = bool(provider_evidence_complete and comparison.get("claimable_superiority"))
    value = {
        "schema_version": 1,
        "family": "syntavra-token-economy-provider-replay",
        "mode": "execute",
        "claim_boundary": "E5_PROVIDER_EVIDENCE_AND_SUPERIORITY_ARE_SEPARATE_GATES",
        "portable_corpus_identity": manifest["portable_identity_sha256"],
        "repetitions": repetitions,
        "seed": seed,
        "baseline_arm": baseline,
        "candidate_arm": candidate,
        "pair_issues": issues,
        "pair_identity_ok": not issues,
        "comparison": comparison,
        "results": [asdict(row) for row in results],
        "provider_evidence_complete": provider_evidence_complete,
        "provider_proof_complete": provider_evidence_complete,
        "superiority_proven": superiority_proven,
    }
    value["result_sha256"] = sha256_bytes(canonical_json(value))
    atomic_write_json(run_root / "provider-replay.json", value, mode=0o600)
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description="Plan or execute exact B0-B9 paired provider replay without inventing receipts.")
    parser.add_argument("--corpus-root", type=Path, required=True)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--arms", type=Path, required=True)
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--seed", type=int, default=1337)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    args.run_root.mkdir(parents=True, exist_ok=True)
    value = (
        execute(args.corpus_root, args.run_root, args.arms, args.repetitions, args.seed)
        if args.execute
        else plan(args.corpus_root, args.arms, args.repetitions)
    )
    if args.output:
        atomic_write_json(args.output, value, mode=0o600)
    print(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    if args.execute and not value["provider_evidence_complete"]:
        return 4
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
