from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from signalcore_runtime.benchmark_harness import TIER_CONFIGS, validate_config
from signalcore_runtime.claim_governance import decide_claim, verify_claim, write_claim
from signalcore_runtime.difficulty import evaluate_difficulty

CONTROLS = {name: True for name in ("same_prompt", "same_model", "same_reasoning", "same_repository", "same_verifier", "same_permissions", "same_timeout", "balanced_cache", "no_artificial_sleep", "no_meaningless_duplication")}

class DifficultyClaimTests(unittest.TestCase):
    def test_tiers_qualify_and_gaming_fails(self):
        for tier in ("20X", "30X", "100X"):
            result = validate_config({"tier": tier, "axes": TIER_CONFIGS[tier], "controls": CONTROLS}); self.assertTrue(result["ok"], (tier, result))
        bad = validate_config({"tier": "20X", "axes": {key: 1000 for key in TIER_CONFIGS["20X"]}, "controls": {**CONTROLS, "no_artificial_sleep": False}}); self.assertFalse(bad["ok"])
    def test_claim_refuses_without_quota(self):
        difficulty=evaluate_difficulty("20X",TIER_CONFIGS["20X"],integrity=CONTROLS); self.assertEqual(decide_claim(tier="20X",baseline_costs=[10]*7,signalcore_costs=[1]*7,difficulty=difficulty,actual_quota_available=False).claim,"5X_NOT_PROVEN")
    def test_claim_passes_only_with_strong_ci(self):
        difficulty=evaluate_difficulty("20X",TIER_CONFIGS["20X"],integrity=CONTROLS); self.assertEqual(decide_claim(tier="20X",baseline_costs=[10]*7,signalcore_costs=[1]*7,difficulty=difficulty).claim,"5X_20X_QUALIFIED")
    def test_receipt_tamper_detection(self):
        difficulty=evaluate_difficulty("20X",TIER_CONFIGS["20X"],integrity=CONTROLS); decision=decide_claim(tier="20X",baseline_costs=[10]*7,signalcore_costs=[1]*7,difficulty=difficulty)
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/"receipt.json"; write_claim(path,decision); self.assertTrue(verify_claim(path)["ok"]); value=json.loads(path.read_text()); value["median_ratio"]=999; path.write_text(json.dumps(value)); self.assertFalse(verify_claim(path)["ok"])

if __name__ == "__main__": unittest.main()
