from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from tools.check_rust_feature_freeze import check

ROOT = Path(__file__).resolve().parents[2]
AGENT = ".github/workflows/remaining71-agent-differential.yml"
HEADLESS = ".github/workflows/remaining71-headless-differential.yml"
AGENT_SHA = "217bb35a857f2a8dc5e3af6e6e2dcdddcc71476b"
HEADLESS_SHA = "c01889e5460d7ec6c62a9e5175e322f7e595bc41"


def baseline() -> dict:
    return {
        "python_complete": True,
        "rust_resume_allowed": False,
        "implementation_coverage": 245,
        "production_promoted": 174,
        "remaining": 71,
    }


class RustFeatureFreezeEvidenceWrapperTests(unittest.TestCase):
    def _check(self, changed, blob_sha):
        with (
            patch("tools.check_rust_feature_freeze.verify_baseline", return_value=baseline()),
            patch("tools.check_rust_feature_freeze._changed_paths", return_value=changed),
            patch("tools.check_rust_feature_freeze._revision_blob_sha", return_value=blob_sha),
        ):
            return check(ROOT, base="base", head="head")

    def test_exact_agent_evidence_wrapper_is_allowed_without_resuming_rust(self) -> None:
        report = self._check(
            [{"status": "M", "path": AGENT, "role": "path"}],
            AGENT_SHA,
        )
        self.assertTrue(report["ok"], report)
        self.assertEqual(report["allowed_retired_evidence_workflow_change_count"], 1)
        self.assertEqual(report["denied_change_count"], 0)
        self.assertFalse(report["baseline"]["rust_resume_allowed"])

    def test_exact_headless_evidence_wrapper_is_allowed_without_resuming_rust(self) -> None:
        report = self._check(
            [{"status": "M", "path": HEADLESS, "role": "path"}],
            HEADLESS_SHA,
        )
        self.assertTrue(report["ok"], report)
        self.assertEqual(report["allowed_retired_evidence_workflow_change_count"], 1)
        self.assertEqual(report["denied_change_count"], 0)

    def test_one_byte_workflow_drift_is_blocked(self) -> None:
        report = self._check(
            [{"status": "M", "path": AGENT, "role": "path"}],
            "0" * 40,
        )
        self.assertFalse(report["ok"], report)
        self.assertEqual(report["allowed_retired_evidence_workflow_change_count"], 0)
        self.assertEqual(report["denied_change_count"], 1)

    def test_other_remaining71_workflow_is_still_blocked(self) -> None:
        report = self._check(
            [{
                "status": "M",
                "path": ".github/workflows/remaining71-provider-proxy-differential.yml",
                "role": "path",
            }],
            AGENT_SHA,
        )
        self.assertFalse(report["ok"], report)
        self.assertEqual(report["denied_change_count"], 1)

    def test_remaining71_validator_change_is_still_blocked(self) -> None:
        report = self._check(
            [{"status": "M", "path": "tools/validate_remaining71_agent_differential.py", "role": "path"}],
            AGENT_SHA,
        )
        self.assertFalse(report["ok"], report)
        self.assertEqual(report["denied_change_count"], 1)

    def test_rename_or_delete_of_approved_workflow_is_blocked(self) -> None:
        for status, role in (("D", "path"), ("R100", "rename-to")):
            with self.subTest(status=status, role=role):
                report = self._check(
                    [{"status": status, "path": AGENT, "role": role}],
                    AGENT_SHA,
                )
                self.assertFalse(report["ok"], report)
                self.assertEqual(report["denied_change_count"], 1)


if __name__ == "__main__":
    unittest.main()
