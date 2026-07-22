from __future__ import annotations

import unittest

from syntavra_runtime.long_context_quality import (
    LONG_CONTEXT_TIERS,
    LongContextQualityGate,
    LongContextReceipt,
    manifest,
)


class LongContextQualityV001Tests(unittest.TestCase):
    @staticmethod
    def _receipt(index: int, arm: str) -> LongContextReceipt:
        families = (
            "needle-retrieval",
            "temporal-supersession",
            "multi-hop-evidence",
            "repository-history",
            "cross-session-continuity",
            "recursive-map-reduce",
        )
        tiers = (32_000, 128_000, 1_000_000)
        baseline = arm == "baseline"
        family = families[index % len(families)]
        return LongContextReceipt(
            receipt_id=f"lc-{index}-{arm}",
            case_id=f"case-{index % 10}",
            task_family=family,
            tier_tokens=tiers[index % len(tiers)],
            arm=arm,
            repetition=index + 1,
            repository_hash=f"repository-{index % 5}-0123456789abcdef",
            provider="openai",
            model="test-model",
            answer_quality=0.90 if baseline else 0.91,
            required_fact_recall=0.96 if baseline else 0.995,
            stale_fact_rejection=0.95 if baseline else 0.995,
            evidence_precision=0.94 if baseline else 0.98,
            exact_recovery=not baseline,
            forced_restart=False,
            continuity_restored=(family != "cross-session-continuity" or not baseline),
            wall_time_ms=1000 if baseline else 900,
            input_tokens=1000 if baseline else 650,
            output_tokens=200,
            synthetic=False,
        )

    def test_manifest_is_explicitly_not_proof(self) -> None:
        value = manifest()
        self.assertEqual(value["tiers"], list(LONG_CONTEXT_TIERS))
        self.assertIn("never proves", value["claim_boundary"])

    def test_real_paired_receipts_open_quality_gate(self) -> None:
        rows = []
        for index in range(30):
            rows.extend((self._receipt(index, "baseline"), self._receipt(index, "syntavra")))
        result = LongContextQualityGate.evaluate(rows)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["claim"], "LONG_CONTEXT_QUALITY_VERIFIED")
        self.assertLess(result["metrics"]["mean_token_ratio"], 1.0)
        self.assertLess(result["metrics"]["mean_wall_time_ratio"], 1.0)
        self.assertGreaterEqual(result["metrics"]["mean_required_fact_recall"], 0.98)

    def test_synthetic_or_unpaired_runs_fail_closed(self) -> None:
        row = self._receipt(0, "syntavra")
        synthetic = LongContextReceipt(**{**row.__dict__, "synthetic": True})
        result = LongContextQualityGate.evaluate([synthetic])
        self.assertFalse(result["ok"])
        self.assertEqual(result["claim"], "LONG_CONTEXT_QUALITY_NOT_PROVEN")


if __name__ == "__main__":
    unittest.main()
