from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CONTRACT = ROOT / "contracts/python/token-economy-competitive-gap-v1.json"
PLAN = ROOT / "docs/plans/SYNTAVRA_TOKEN_ECONOMY_COMPETITIVE_GAP_V1.md"


class TokenEconomyCompetitiveGapTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
        cls.plan = PLAN.read_text(encoding="utf-8")

    def test_authority_and_claim_boundary(self) -> None:
        value = self.contract
        self.assertEqual(value["schema_version"], 1)
        self.assertEqual(value["family"], "syntavra-token-economy-competitive-gap")
        self.assertTrue(value["no_new_capability_namespace"])
        self.assertIn("do_not_create", value["priority_order"])
        self.assertIn("compact_irreducible_residual", value["priority_order"])
        self.assertIn("do not prove current Syntavra", value["claim_boundary"])

    def test_targets_remain_aggressive_but_quality_gated(self) -> None:
        targets = self.contract["targets"]
        self.assertLessEqual(targets["eligible_easy_provider_total_tokens_max"], 3000)
        self.assertEqual(targets["eligible_easy_inference_skip_tokens"], 0)
        self.assertLessEqual(targets["normal_coding_provider_total_tokens_max"], 8000)
        self.assertGreaterEqual(targets["avoidable_input_reduction_floor"], 0.85)
        self.assertGreaterEqual(targets["avoidable_output_reduction_floor"], 0.85)
        self.assertGreaterEqual(targets["avoidable_input_reduction_stretch"], 0.95)
        self.assertGreaterEqual(targets["avoidable_output_reduction_stretch"], 0.95)
        self.assertEqual(targets["unexplained_envelope_overflow"], 0)
        self.assertTrue(targets["quality_non_inferior"])

    def test_p0_registry_has_unique_ids_and_existing_owner_direction(self) -> None:
        rows = self.contract["p0"]
        ids = [row["id"] for row in rows]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertGreaterEqual(len(rows), 14)
        by_name = {row["name"]: row for row in rows}
        for required in (
            "Pre-Model Ingestion Fold Gate",
            "Stable Tool Schema Canonicalization",
            "Tool Result Query Pushdown",
            "Verifier-Gated Inference Skip Cache",
            "Workflow Skill Macro Compiler",
            "Provider-Native Context Editing Adapter",
            "OpenAI Prompt Cache Modernization",
        ):
            self.assertIn(required, by_name)
            self.assertTrue(by_name[required]["owners"])

    def test_safety_invariants_reject_fake_savings(self) -> None:
        invariants = set(self.contract["hard_invariants"])
        self.assertIn("PROVIDER_RECEIPT_REQUIRED_FOR_PROVIDER_SAVINGS_CLAIM", invariants)
        self.assertIn("COMPONENT_PERCENTAGES_ARE_NON_ADDITIVE", invariants)
        self.assertIn("EXACT_RECOVERY_REQUIRED_BEFORE_PAYLOAD_EVICTION", invariants)
        self.assertIn("SEMANTIC_CACHE_FAILS_CLOSED", invariants)
        self.assertIn("PROMPT_CACHE_IS_NOT_CONTEXT_CAPACITY_REDUCTION", invariants)
        self.assertIn("OUTPUT_SAVINGS_MUST_HAPPEN_BEFORE_GENERATION", invariants)
        self.assertIn("QUALITY_AND_VERIFIER_NON_INFERIORITY_REQUIRED", invariants)

    def test_plan_contains_runtime_first_order(self) -> None:
        for marker in (
            "TE-1: Stop payloads at ingestion",
            "TE-2: Constant-context tool loop",
            "TE-3: Make prompt cache stability explicit",
            "TE-4: Push computation below the provider boundary",
            "TE-5: Turn repeated work into zero/near-zero inference",
            "Component percentages may overlap",
        ):
            self.assertIn(marker, self.plan)


if __name__ == "__main__":
    unittest.main()
