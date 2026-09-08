from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from syntavra_runtime.util import atomic_write_json
from tools import certify_release_readiness as readiness


class ReleaseReadinessTests(unittest.TestCase):
    def test_complete_snapshot_without_priority_markers_is_blocker_free(self) -> None:
        snapshot = {
            "complete": True,
            "repository": "Naveax/Syntavra",
            "exact_head": "a" * 40,
            "issues": [
                {"number": 1, "state": "open", "title": "ordinary bug", "labels": [{"name": "bug"}]},
                {
                    "number": 2,
                    "state": "open",
                    "title": "[P0] pull request title must not count",
                    "labels": [],
                    "pull_request": {"url": "https://example.invalid/pr/2"},
                },
            ],
        }
        result = readiness.certify_known_p0_p1_blockers(snapshot)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["claim"], "ZERO_KNOWN_P0_P1_BLOCKERS_PROVEN")
        self.assertEqual(result["blocker_count"], 0)

    def test_explicit_p0_or_p1_marker_blocks_release(self) -> None:
        snapshot = {
            "complete": True,
            "repository": "Naveax/Syntavra",
            "issues": [
                {"number": 7, "state": "open", "title": "correctness regression", "labels": [{"name": "priority:p1"}]},
                {"number": 8, "state": "open", "title": "[P0] data loss", "labels": []},
            ],
        }
        result = readiness.certify_known_p0_p1_blockers(snapshot)
        self.assertFalse(result["ok"], result)
        self.assertEqual(result["blocker_count"], 2)

    def test_incomplete_issue_snapshot_fails_closed(self) -> None:
        result = readiness.certify_known_p0_p1_blockers(
            {"complete": False, "repository": "Naveax/Syntavra", "issues": []}
        )
        self.assertFalse(result["ok"], result)
        self.assertIn("issue-snapshot-not-complete", result["failures"])

    def test_release_readiness_contract_preserves_10_promotion_boundary(self) -> None:
        contract_path = Path(__file__).resolve().parents[2] / "contracts/python/release-readiness-v1.json"
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
        self.assertEqual(contract["family"], "syntavra-release-readiness-v1")
        self.assertEqual(float(contract["target"]), 10.0)
        self.assertTrue(contract["dogfood"]["readiness_receipt_required_per_candidate_run"])
        self.assertEqual(contract["dogfood"]["unexplained_provider_overflow_must_equal"], 0)
        self.assertEqual(contract["known_blockers"]["known_p0_p1_blocker_count_must_equal"], 0)

    @staticmethod
    def _contract() -> dict:
        return {"workloads": [{"id": f"B{index}-task"} for index in range(10)]}

    @staticmethod
    def _e5() -> dict:
        return {
            "provider_evidence_complete": True,
            "score": 9.6,
            "scope": {"candidate_version": "candidate-v1"},
        }

    def _row(self, root: Path, index: int) -> dict:
        run_id = f"run-B{index}"
        artifact_dir = root / run_id
        artifact_dir.mkdir(parents=True)
        return {
            "run_id": run_id,
            "task_id": f"B{index}-task",
            "arm_id": "candidate",
            "repetition": 1,
            "cache_mode": "cold",
            "success": True,
            "verifier_success": True,
            "security_regressions": 0,
            "verifier_skips": 0,
            "provider_response_hash": f"{index + 1:064x}",
            "usage_receipt_hash": f"{index + 101:064x}",
            "artifact_dir": str(artifact_dir),
        }

    def _write_receipt(
        self,
        row: dict,
        *,
        overflow: int = 0,
        explained: int = 0,
        rollback: bool = False,
        failure_receipt: bool = False,
    ) -> None:
        receipt = {
            "schema_version": 1,
            "run_id": row["run_id"],
            "task_id": row["task_id"],
            "arm_id": row["arm_id"],
            "repetition": row["repetition"],
            "cache_mode": row["cache_mode"],
            "provider_response_hash": row["provider_response_hash"],
            "usage_receipt_hash": row["usage_receipt_hash"],
            "provider_overflow_count": overflow,
            "explained_provider_overflow_count": explained,
            "overflow_reason_hashes": [f"{500 + index:064x}" for index in range(explained)],
            "rollback_verified": rollback,
            "rollback_receipt_hash": "b" * 64 if rollback else "",
            "failure_receipt_verified": failure_receipt,
            "failure_receipt_hash": "c" * 64 if failure_receipt else "",
        }
        receipt["receipt_sha256"] = readiness._receipt_hash(receipt)
        atomic_write_json(
            Path(row["artifact_dir"]) / "arm-result.json",
            {"success": True, "readiness_receipt": receipt},
            mode=0o600,
        )

    def _replay(self, root: Path) -> dict:
        rows = [self._row(root, index) for index in range(10)]
        for index, row in enumerate(rows):
            self._write_receipt(
                row,
                rollback=index == 5,
                failure_receipt=index == 5,
            )
        return {"candidate_arm": "candidate", "results": rows}

    def test_live_dogfood_requires_bound_self_hashed_readiness_receipts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.object(
            readiness, "certify_provider_e5", return_value=self._e5()
        ):
            replay = self._replay(Path(tmp))
            result = readiness.certify_live_dogfood(replay, self._contract())
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["claim"], "LIVE_DOGFOOD_READINESS_PROVEN")
        self.assertEqual(result["readiness_receipt_count"], 10)
        self.assertEqual(result["unexplained_provider_overflow_count"], 0)
        self.assertGreaterEqual(result["rollback_receipt_count"], 1)
        self.assertGreaterEqual(result["failure_receipt_count"], 1)

    def test_unexplained_provider_overflow_blocks_dogfood(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.object(
            readiness, "certify_provider_e5", return_value=self._e5()
        ):
            replay = self._replay(Path(tmp))
            row = replay["results"][0]
            self._write_receipt(row, overflow=1, explained=0)
            result = readiness.certify_live_dogfood(replay, self._contract())
        self.assertFalse(result["ok"], result)
        self.assertIn("unexplained-provider-overflow:1", result["failures"])

    def test_missing_b5_rollback_and_failure_proof_blocks_dogfood(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.object(
            readiness, "certify_provider_e5", return_value=self._e5()
        ):
            replay = self._replay(Path(tmp))
            self._write_receipt(replay["results"][5])
            result = readiness.certify_live_dogfood(replay, self._contract())
        self.assertFalse(result["ok"], result)
        self.assertIn("rollback-receipt-not-verified", result["failures"])
        self.assertIn("failure-receipt-not-verified", result["failures"])

    def test_receipt_identity_mismatch_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.object(
            readiness, "certify_provider_e5", return_value=self._e5()
        ):
            replay = self._replay(Path(tmp))
            row = replay["results"][0]
            path = Path(row["artifact_dir"]) / "arm-result.json"
            arm_result = json.loads(path.read_text(encoding="utf-8"))
            arm_result["readiness_receipt"]["run_id"] = "different-run"
            arm_result["readiness_receipt"]["receipt_sha256"] = readiness._receipt_hash(
                arm_result["readiness_receipt"]
            )
            atomic_write_json(path, arm_result, mode=0o600)
            result = readiness.certify_live_dogfood(replay, self._contract())
        self.assertFalse(result["ok"], result)
        self.assertTrue(
            any("readiness-receipt-binding:run_id" in value for value in result["receipt_failures"]),
            result,
        )


if __name__ == "__main__":
    unittest.main()
