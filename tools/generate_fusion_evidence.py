#!/usr/bin/env python3
from __future__ import annotations

import argparse
import io
import json
import platform
import shutil
import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from signalcore_runtime.benchmark_harness import TIER_CONFIGS, observed_difficulty, validate_config
from signalcore_runtime.claim_governance import decide_claim, write_claim
from signalcore_runtime.claims import ClaimReceipt, write_receipt
from signalcore_runtime.difficulty import evaluate_difficulty
from tools.verify_claims import source_tree_hash


def generate(source_commit: str) -> dict[str, object]:
    tree_hash = source_tree_hash()
    result_dir = ROOT / "benchmarks" / "results" / "5x"
    result_dir.mkdir(parents=True, exist_ok=True)
    claim_dir = ROOT / "artifacts" / "claims"
    claim_dir.mkdir(parents=True, exist_ok=True)

    started = time.perf_counter()
    suite = unittest.TestLoader().discover(str(ROOT / "tests" / "roblox_profile"))
    stream = io.StringIO()
    result = unittest.TextTestRunner(stream=stream, verbosity=0).run(suite)
    if not result.wasSuccessful():
        raise RuntimeError(stream.getvalue())
    profile_artifact = {
        "duration_ms": (time.perf_counter() - started) * 1000.0,
        "errors": len(result.errors),
        "failures": len(result.failures),
        "maturity": "INTERNALLY_VERIFIED",
        "skipped": len(result.skipped),
        "source_commit": source_commit,
        "status": "PASS",
        "tests_run": result.testsRun,
    }
    profile_path = ROOT / "benchmarks" / "results" / "roblox-profile" / "profile-tests.json"
    profile_path.write_text(json.dumps(profile_artifact, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")

    registry_path = ROOT / "docs" / "claims" / "claims.json"
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    registry["source_commit"] = source_commit
    registry["source_tree_hash"] = tree_hash
    registry_path.write_text(json.dumps(registry, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")

    environment = {
        "schema_version": 1,
        "source_commit": source_commit,
        "source_tree_hash": tree_hash,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "codex_executable": shutil.which("codex"),
        "live_quota_interface": False,
        "raw_rollout_corpus": False,
        "paired_external_arms": [],
        "claim_ceiling": "5X_NOT_PROVEN",
    }
    (result_dir / "environment.json").write_text(json.dumps(environment, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    blockers = {
        "schema_version": 1,
        "source_commit": source_commit,
        "claim": "5X_NOT_PROVEN",
        "blockers": [
            "no live provider quota telemetry",
            "no identical-model paired plain-host repetitions",
            "no strongest-rival paired repetitions",
            "no raw long-session rollout corpus",
        ],
        "local_engineering_gates": "validated separately",
    }
    (result_dir / "blockers.json").write_text(json.dumps(blockers, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    config_reports = {name: validate_config(config) for name, config in TIER_CONFIGS.items()}
    (result_dir / "difficulty-configs.json").write_text(json.dumps(config_reports, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    config = TIER_CONFIGS["20X"]
    difficulty = evaluate_difficulty(
        observed_difficulty(config),
        configuration={
            "prompts_equal": True,
            "models_equal": True,
            "verifiers_equal": True,
            "repository_state_equal": True,
            "acceptance_equal": True,
            "meaningless_duplication_ratio": 0.0,
        },
    )
    decision = decide_claim(
        baseline_name="plain_host",
        baseline=[],
        signalcore=[],
        difficulty=difficulty,
        tier="20X",
    )
    claim_path = result_dir / "claim-not-proven.json"
    write_claim(
        claim_path,
        decision,
        metadata={
            "source_commit": source_commit,
            "source_tree_hash": tree_hash,
            "benchmark_config_hash": config.identity,
            "public_claim_allowed": False,
        },
    )
    write_receipt(
        claim_dir / "5x-qualification.json",
        ClaimReceipt(
            schema_version=1,
            claim_id="signalcore.5x.qualification",
            requested_claim="5X_20X_QUALIFIED",
            decision="REFUSED",
            maximum_allowed_claim="Internal runtime, benchmark machinery, and integrity gates verified; 5X_NOT_PROVEN",
            source_commit=source_commit,
            suite="signalcore-fusion-runtime-local-engineering",
            artifact="benchmarks/results/5x/claim-not-proven.json",
            covered_distribution=(
                "local deterministic runtime tests",
                "simulated benchmark generation",
                "claim-gate refusal path",
            ),
            not_covered=(
                "live provider quota",
                "identical-model paired coding runs",
                "strongest-rival comparison",
                "independent reproduction",
            ),
            adversarial_checks=(
                "benchmark anti-gaming",
                "tamper-evident receipt",
                "required-verifier protection",
            ),
            demotion_conditions=(
                "source commit changes",
                "artifact hash mismatch",
                "validator failure",
                "new live evidence contradicts receipt",
            ),
            verifier="python tools/validate_release.py --profile 5x",
        ),
    )
    return {
        "source_commit": source_commit,
        "source_tree_hash": tree_hash,
        "claim": "5X_NOT_PROVEN",
        "profile_tests": result.testsRun,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-commit", required=True)
    args = parser.parse_args()
    print(json.dumps(generate(args.source_commit), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
