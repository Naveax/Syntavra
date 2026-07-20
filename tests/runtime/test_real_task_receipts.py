from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from signalcore_runtime.real_task_receipts import (
    load_verified_real_tasks,
    verify_real_task_receipt,
)


class RealTaskReceiptTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def write_receipt(
        self, name: str = "task", *, baseline_failed: int = 1
    ) -> Path:
        task = self.root / name
        evidence = task / "evidence"
        evidence.mkdir(parents=True)
        patch = task / "fix.patch"
        patch.write_text("diff --git a/a.py b/a.py\n", encoding="utf-8")
        raw = b"1 failed, 1 passed\n"
        digest = hashlib.sha256(raw).hexdigest()
        evidence_path = evidence / f"{digest}.txt"
        evidence_path.write_bytes(raw)
        receipt = {
            "schema_version": 1,
            "receipt_type": "signalcore-real-repository-task",
            "task": {
                "identity": f"owner/repo#{name}@main:blob",
                "repository": "owner/repo",
                "issue_number": 7,
                "issue_state_at_selection": "open",
                "baseline_matches_source": True,
            },
            "result": {
                "status": "FIXED_LOCALLY_VERIFIED",
                "working_tree_clean": True,
                "baseline_tests": {"failed": baseline_failed, "passed": 1},
                "patched_tests": {"failed": 0, "passed": 2},
            },
            "artifacts": {
                "patch": {
                    "path": "fix.patch",
                    "bytes": patch.stat().st_size,
                    "sha256": hashlib.sha256(
                        patch.read_bytes()
                    ).hexdigest(),
                },
                "evidence": [
                    {
                        "path": f"evidence/{digest}.txt",
                        "bytes": len(raw),
                        "sha256": digest,
                        "handle": f"sc://sha256/{digest}",
                    }
                ],
            },
            "execution": {"all_evidence_verified": True},
            "claim_boundary": {
                "counts_as_real_repository_task": True,
                "counts_as_competitor_arm": False,
                "public_superiority_proven": False,
            },
        }
        path = task / "receipt.json"
        path.write_text(json.dumps(receipt), encoding="utf-8")
        return path

    def test_valid_receipt_is_counted(self):
        path = self.write_receipt()
        task, reasons = verify_real_task_receipt(path)
        self.assertEqual([], reasons)
        self.assertIsNotNone(task)
        result = load_verified_real_tasks(self.root)
        self.assertEqual(1, result["verified_count"])
        self.assertEqual(0, result["rejected_count"])

    def test_tampered_patch_is_rejected(self):
        path = self.write_receipt()
        (path.parent / "fix.patch").write_text("tampered", encoding="utf-8")
        task, reasons = verify_real_task_receipt(path)
        self.assertIsNone(task)
        self.assertIn("patch-hash", reasons)

    def test_baseline_must_demonstrate_the_bug(self):
        path = self.write_receipt(baseline_failed=0)
        task, reasons = verify_real_task_receipt(path)
        self.assertIsNone(task)
        self.assertIn("baseline-did-not-fail", reasons)

    def test_duplicate_identity_is_not_double_counted(self):
        first = self.write_receipt("one")
        second = self.write_receipt("two")
        first_payload = json.loads(first.read_text(encoding="utf-8"))
        second_payload = json.loads(second.read_text(encoding="utf-8"))
        second_payload["task"]["identity"] = first_payload["task"]["identity"]
        second.write_text(json.dumps(second_payload), encoding="utf-8")
        result = load_verified_real_tasks(self.root)
        self.assertEqual(1, result["verified_count"])
        self.assertEqual(1, result["rejected_count"])
        self.assertEqual(
            ["duplicate-task-identity"],
            result["rejected"][0]["reasons"],
        )


if __name__ == "__main__":
    unittest.main()
