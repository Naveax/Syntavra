#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from benchmarks.syntavra_component_benchmark import run as run_internal_measurement
from syntavra_runtime.benchmark_harness import TIER_CONFIGS, validate_config
from syntavra_runtime.claim_governance import decide_claim
from syntavra_runtime.difficulty import evaluate_configured
from syntavra_runtime.util import atomic_write_json

CONTROLS = {name: True for name in (
    "same_prompt", "same_model", "same_reasoning", "same_repository", "same_verifier",
    "same_permissions", "same_timeout", "balanced_cache", "no_artificial_sleep", "no_meaningless_duplication",
)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", default="5x")
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--output")
    args = parser.parse_args(argv)

    tiers = {tier: validate_config({"tier": tier, "axes": axes, "controls": CONTROLS}) for tier, axes in TIER_CONFIGS.items()}
    configured = evaluate_configured("20X", TIER_CONFIGS["20X"], integrity=CONTROLS)
    claim = decide_claim(
        tier="20X",
        baseline_costs=[],
        syntavra_costs=[],
        difficulty=configured,
        actual_quota_available=False,
    )
    internal = run_internal_measurement(scale=1 if args.smoke else 4)
    result = {
        "ok": all(value["ok"] for key, value in tiers.items() if key != "1X")
              and claim.claim == "5X_NOT_PROVEN"
              and internal["claim"] == "INTERNAL_FUNCTIONAL_MEASUREMENT_ONLY"
              and internal["external_superiority"] is False
              and internal["memory"]["exact_recovery"] is True,
        "product": "Syntavra",
        "version": "0.0.1",
        "channel": "pre-release",
        "profile": args.profile,
        "difficulty_shapes": tiers,
        "claim_ceiling": asdict(claim),
        "internal_measurement": internal,
        "note": "Canonical Syntavra components are internally verified. Live paired provider, long-context, integration and public maturity claims remain evidence-gated.",
    }
    if args.output:
        atomic_write_json(Path(args.output), result, mode=0o644)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
