from __future__ import annotations

import hashlib
import json
import unittest
from pathlib import Path

from syntavra_runtime.provider_e5 import certify_external_superiority, certify_provider_e5
from syntavra_runtime.util import canonical_json

ROOT = Path(__file__).resolve().parents[2]


class ProviderE5Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.contract = json.loads(
            (ROOT / "contracts/python/token-economy-frozen-workloads-v1.json").read_text(encoding="utf-8")
        )

    @staticmethod
    def _hash(label: str) -> str:
        return hashlib.sha256(label.encode()).hexdigest()

    def _replay(self, *, superiority: bool = False, ratio: float = 2.0) -> dict[str, object]:
        repetitions = 3
        baseline = "plain-provider"
        candidate = "syntavra-balanced"
        rows: list[dict[str, object]] = []
        pair_count = 0
        for workload in self.contract["workloads"]:
            task_id = workload["id"]
            for cache_mode in workload["variants"]:
                for repetition in range(1, repetitions + 1):
                    pair_count += 1
                    shared = {
                        "task_id": task_id,
                        "repetition": repetition,
                        "cache_mode": cache_mode,
                        "success": True,
                        "verifier_success": True,
                        "verified_work": 1.0,
                        "fresh_input_tokens": 100,
                        "cached_input_tokens": 10,
                        "output_tokens": 20,
                        "reasoning_tokens": 5,
                        "model_turns": 1,
                        "tool_calls": 2,
                        "wait_calls": 0,
                        "compactions": 0,
                        "security_regressions": 0,
                        "verifier_skips": 0,
                        "repository_tree": "a" * 40,
                        "repository_commit": "b" * 40,
                        "prompt_hash": self._hash(task_id + ":prompt"),
                        "verifier_hash": self._hash(task_id + ":verifier"),
                        "permissions_hash": self._hash(task_id + ":permissions"),
                        "provider_observed": True,
                        "provider": "test-provider",
                        "model": "same-model",
                        "reasoning": "same-effort",
                        "context_window": 32768,
                        "hardware_hash": self._hash("hardware"),
                        "task_hash": self._hash(task_id + ":task"),
                        "timeout_seconds": 120.0,
                    }
                    for arm, quota in ((baseline, 2.0), (candidate, 2.0 / ratio)):
                        key = f"{task_id}:{cache_mode}:{repetition}:{arm}"
                        rows.append({
                            **shared,
                            "arm_id": arm,
                            "arm_version": "baseline-v1" if arm == baseline else "candidate-v1",
                            "quota_cost": quota,
                            "request_id_hash": self._hash(key + ":request"),
                            "provider_response_hash": self._hash(key + ":response"),
                            "provider_receipt_hash": self._hash(key + ":provider-receipt"),
                            "usage_receipt_hash": self._hash(key + ":usage-receipt"),
                        })
        ci = [ratio * 0.9, ratio * 1.1]
        comparison = {
            "comparison_authority": "HardenedSignalBench.compare",
            "matched_pairs": pair_count,
            "successful_equal_work_pairs": pair_count,
            "provider_observed_pairs": pair_count,
            "identity_mismatches": [],
            "receipt_errors": [],
            "invalid": [],
            "claimable_superiority": superiority,
            "median_success_pair_ratio": ratio,
            "failure_inclusive_efficiency_ratio": ratio,
            "confidence_interval_95": ci,
            "pass_rates": {baseline: 1.0, candidate: 1.0},
        }
        replay: dict[str, object] = {
            "schema_version": 1,
            "family": "syntavra-token-economy-provider-replay",
            "mode": "execute",
            "baseline_arm": baseline,
            "candidate_arm": candidate,
            "repetitions": repetitions,
            "pair_issues": [],
            "pair_identity_ok": True,
            "comparison": comparison,
            "results": rows,
        }
        replay["result_sha256"] = hashlib.sha256(canonical_json(replay)).hexdigest()
        return replay

    def test_e5_validity_does_not_depend_on_winning(self) -> None:
        replay = self._replay(superiority=False)
        e5 = certify_provider_e5(replay, self.contract)
        superiority = certify_external_superiority(e5, replay)
        self.assertTrue(e5["provider_evidence_complete"])
        self.assertEqual(e5["evidence_level"], "E5_PAIRED_PROVIDER_RECEIPTS")
        self.assertGreaterEqual(e5["score"], 9.0)
        self.assertFalse(superiority["superiority_proven"])
        self.assertEqual(superiority["claim"], "E5_VALID_SUPERIORITY_NOT_PROVEN")

    def test_superiority_requires_e5_and_hardened_comparator(self) -> None:
        replay = self._replay(superiority=True, ratio=2.0)
        e5 = certify_provider_e5(replay, self.contract)
        report = certify_external_superiority(e5, replay)
        self.assertTrue(report["superiority_proven"])
        self.assertFalse(report["five_x_proven"])
        self.assertGreaterEqual(report["score"], 9.0)

    def test_five_x_claim_requires_failure_inclusive_and_ci_floor(self) -> None:
        replay = self._replay(superiority=True, ratio=6.0)
        e5 = certify_provider_e5(replay, self.contract)
        report = certify_external_superiority(e5, replay)
        self.assertTrue(report["five_x_proven"])
        self.assertEqual(report["claim"], "5X_PAIRED_PROVIDER_SUPERIORITY_PROVEN")

    def test_corrupt_or_missing_receipt_fails_e5_closed(self) -> None:
        replay = self._replay(superiority=True)
        replay["results"][0]["usage_receipt_hash"] = "bad"  # type: ignore[index]
        replay["result_sha256"] = hashlib.sha256(
            canonical_json({key: value for key, value in replay.items() if key != "result_sha256"})
        ).hexdigest()
        e5 = certify_provider_e5(replay, self.contract)
        report = certify_external_superiority(e5, replay)
        self.assertFalse(e5["provider_evidence_complete"])
        self.assertTrue(any("usage_receipt_hash" in reason for reason in e5["failures"]))
        self.assertFalse(report["superiority_proven"])

    def test_pair_identity_drift_invalidates_e5(self) -> None:
        replay = self._replay()
        replay["results"][1]["model"] = "different-model"  # type: ignore[index]
        replay["result_sha256"] = hashlib.sha256(
            canonical_json({key: value for key, value in replay.items() if key != "result_sha256"})
        ).hexdigest()
        e5 = certify_provider_e5(replay, self.contract)
        self.assertFalse(e5["provider_evidence_complete"])
        self.assertTrue(any(reason.startswith("pair-identity-mismatch") for reason in e5["failures"]))


if __name__ == "__main__":
    unittest.main()
