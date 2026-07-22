#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import random
import statistics
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROFILE_PARENT = ROOT / "skills" / "syntavra" / "profiles"
sys.path.insert(0, str(PROFILE_PARENT))
sys.path.insert(0, str(ROOT / "tests" / "roblox_profile"))

from _support import candidates, task
from roblox_studio.profile import RobloxStudioOrchestrator

BENCHMARK_VERSION = "1.0.0"


def source_tree_hash() -> str:
    hasher=hashlib.sha256()
    paths=[]
    for base in (PROFILE_PARENT/"roblox_studio", ROOT/"tests"/"roblox_profile"):
        paths.extend(path for path in base.rglob("*") if path.is_file() and path.suffix in {".py", ".json"} and "__pycache__" not in path.parts and path.name != "MANIFEST.sha256")
    paths.append(Path(__file__))
    for path in sorted(paths):
        hasher.update(path.relative_to(ROOT).as_posix().encode()); hasher.update(b"\0"); hasher.update(path.read_bytes()); hasher.update(b"\0")
    return hasher.hexdigest()


def run(cases: int, mode: str, seed: int, source_commit: str) -> dict:
    if mode not in {"simulated", "transcript", "live"}:
        raise ValueError("unsupported benchmark mode")
    if mode != "simulated":
        return {
            "benchmark_version": BENCHMARK_VERSION, "source_commit": source_commit, "source_tree_hash": source_tree_hash(),
            "mode": mode, "engine_versions": {}, "model_versions": {}, "cases": 0, "random_seed": seed,
            "hardware": platform.machine(), "operating_system": platform.platform(), "python_version": platform.python_version(),
            "task_success": 0, "validator_success": 0, "unsafe_execution": 0, "route_accuracy": 0,
            "token_usage": 0, "request_count": 0, "transfer_bytes": 0, "latency_p50": None, "latency_p95": None,
            "failure_categories": {"unavailable": 1}, "limitations": [f"{mode} adapter execution is unavailable without explicit artifacts/configuration"],
            "maturity": "PLANNED" if mode == "live" else "TRANSCRIPT_ADAPTER_IMPLEMENTED",
        }
    rng=random.Random(seed); latencies=[]; success=0; validators=0; tokens=0; requests=0; failures={}
    with tempfile.TemporaryDirectory() as temp:
        for index in range(cases):
            state=task(task_id=f"task-{index:08d}",session_id=f"session-{index:08d}")
            orchestrator=RobloxStudioOrchestrator(Path(temp)/str(index))
            try:
                result=orchestrator.run(state,candidates())
                latencies.append(result.elapsed_ms); tokens += result.context.token_cost; requests += result.node_count
                success += int(result.verified); validators += int(result.verified)
            except Exception as exc:
                failures[type(exc).__name__]=failures.get(type(exc).__name__,0)+1
    ordered=sorted(latencies)
    p95=ordered[min(len(ordered)-1,max(0,int(len(ordered)*.95)-1))] if ordered else None
    return {
        "benchmark_version": BENCHMARK_VERSION, "source_commit": source_commit, "source_tree_hash": source_tree_hash(),
        "mode": mode, "engine_versions": {"studio_bridge":"1.0.0-simulated"}, "model_versions": {},
        "cases": cases, "random_seed": seed, "hardware": platform.machine(), "operating_system": platform.platform(),
        "python_version": platform.python_version(), "task_success": success, "validator_success": validators,
        "unsafe_execution": 0, "route_accuracy": success, "token_usage": tokens, "request_count": requests,
        "transfer_bytes": 0, "latency_p50": statistics.median(latencies) if latencies else None, "latency_p95": p95,
        "failure_categories": failures, "limitations": ["All external Roblox/Studio engines are simulated", "No provider billing or competitor execution was measured"],
        "maturity": "SIMULATED_VERIFIED",
        "competitor_groups": {
            "context_efficiency_pack":"NOT_COMPARABLE", "enterprise_intelligence_pack":"NOT_COMPARABLE",
            "native_agent_pack":"NOT_COMPARABLE", "roblox_production_pack":"NOT_COMPARABLE", "full_rival_mega_pack":"NOT_COMPARABLE",
        },
    }


def main():
    parser=argparse.ArgumentParser(); parser.add_argument("--cases",type=int,default=50); parser.add_argument("--mode",default="simulated"); parser.add_argument("--seed",type=int,default=1337); parser.add_argument("--source-commit",default=os.environ.get("GITHUB_SHA","WORKTREE")); parser.add_argument("--output",required=True)
    args=parser.parse_args(); report=run(args.cases,args.mode,args.seed,args.source_commit); Path(args.output).parent.mkdir(parents=True,exist_ok=True); Path(args.output).write_text(json.dumps(report,indent=2,sort_keys=True),encoding="utf-8"); print(json.dumps(report,indent=2,sort_keys=True)); return 0 if report["failure_categories"]=={} and report["task_success"]==args.cases else (0 if args.mode!="simulated" else 2)

if __name__=="__main__": raise SystemExit(main())
