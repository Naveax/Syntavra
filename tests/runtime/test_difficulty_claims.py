from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from syntavra_runtime.benchmark_harness import OBSERVED_BASELINE, TIER_CONFIGS, validate_config
from syntavra_runtime.claim_governance import decide_claim, verify_claim, write_claim
from syntavra_runtime.difficulty import evaluate_configured, evaluate_observed

CONTROLS = {name: True for name in (
    "same_prompt", "same_model", "same_reasoning", "same_repository", "same_verifier",
    "same_permissions", "same_timeout", "balanced_cache", "no_artificial_sleep", "no_meaningless_duplication",
)}


class DifficultyClaimTests(unittest.TestCase):
    def observed(self):
        raw = {axis: OBSERVED_BASELINE[axis] * TIER_CONFIGS["20X"][axis] for axis in OBSERVED_BASELINE}
        return evaluate_observed("20X", raw, OBSERVED_BASELINE, integrity=CONTROLS)

    def test_tiers_configure_and_gaming_fails(self):
        for tier in ("20X", "30X", "100X"):
            result = validate_config({"tier": tier, "axes": TIER_CONFIGS[tier], "controls": CONTROLS})
            self.assertTrue(result["ok"], (tier, result))
            self.assertFalse(result["claim_eligible"])
        bad = validate_config({
            "tier": "20X",
            "axes": {key: 1000 for key in TIER_CONFIGS["20X"]},
            "controls": {**CONTROLS, "no_artificial_sleep": False},
        })
        self.assertFalse(bad["ok"])

    def test_configured_difficulty_cannot_claim(self):
        difficulty = evaluate_configured("20X", TIER_CONFIGS["20X"], integrity=CONTROLS)
        decision = decide_claim(
            tier="20X",
            baseline_costs=[10] * 10,
            syntavra_costs=[1] * 10,
            difficulty=difficulty,
        )
        self.assertEqual(decision.claim, "5X_NOT_PROVEN")
        self.assertIn("difficulty-not-observed", decision.reasons)

    def test_claim_refuses_without_quota(self):
        decision = decide_claim(
            tier="20X",
            baseline_costs=[10] * 10,
            syntavra_costs=[1] * 10,
            difficulty=self.observed(),
            actual_quota_available=False,
        )
        self.assertEqual(decision.claim, "5X_NOT_PROVEN")

    def test_claim_requires_ten_pairs(self):
        decision = decide_claim(
            tier="20X",
            baseline_costs=[10] * 7,
            syntavra_costs=[1] * 7,
            difficulty=self.observed(),
        )
        self.assertEqual(decision.claim, "5X_NOT_PROVEN")
        self.assertTrue(any(reason.startswith("insufficient-valid-pairs") for reason in decision.reasons))

    def test_claim_passes_only_with_observed_strong_ci(self):
        decision = decide_claim(
            tier="20X",
            baseline_costs=[10] * 12,
            syntavra_costs=[1] * 12,
            difficulty=self.observed(),
        )
        self.assertEqual(decision.claim, "5X_20X_QUALIFIED")

    def test_receipt_tamper_detection(self):
        decision = decide_claim(
            tier="20X",
            baseline_costs=[10] * 12,
            syntavra_costs=[1] * 12,
            difficulty=self.observed(),
        )
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "receipt.json"
            write_claim(path, decision)
            self.assertTrue(verify_claim(path)["ok"])
            value = json.loads(path.read_text())
            value["median_ratio"] = 999
            path.write_text(json.dumps(value))
            self.assertFalse(verify_claim(path)["ok"])


if __name__ == "__main__":
    unittest.main()
