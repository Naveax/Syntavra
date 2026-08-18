from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path

from tools.certify_python_authority import _assert_no_route_identity_copy, certify

ROOT = Path(__file__).resolve().parents[2]
CONTRACT = ROOT / "contracts/python/python-authority-v1.json"


def _git_head(repo: Path) -> str:
    proc = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )
    return proc.stdout.strip()


class PythonAuthorityTests(unittest.TestCase):
    def test_contract_is_python_first_and_rust_frozen(self) -> None:
        contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
        self.assertEqual(contract["claim"], "PYTHON_FEATURE_DEVELOPMENT_AUTHORITY")
        self.assertTrue(contract["strict"])
        self.assertEqual(contract["authority"]["feature_development_engine"], "python")
        self.assertTrue(contract["rust_freeze"]["active"])
        self.assertFalse(contract["rust_freeze"]["feature_development_allowed"])
        self.assertFalse(contract["rust_freeze"]["production_promotion_allowed"])
        self.assertFalse(contract["rust_freeze"]["native_counter_change_allowed"])
        self.assertEqual(contract["expected"]["rust_implemented_native_routes"], 245)
        self.assertEqual(contract["expected"]["rust_implementation_missing_routes"], 0)
        self.assertEqual(contract["expected"]["rust_promoted_native_routes"], 174)
        self.assertEqual(contract["expected"]["remaining_routes"], 71)
        self.assertEqual(contract["expected"]["atomic_promotion_target"], 245)

    def test_contract_does_not_duplicate_route_identity_lists(self) -> None:
        contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
        _assert_no_route_identity_copy(contract)
        with self.assertRaisesRegex(AssertionError, "245-route identity list"):
            _assert_no_route_identity_copy([f"route-{index}" for index in range(245)])
        with self.assertRaisesRegex(AssertionError, "71-route identity list"):
            _assert_no_route_identity_copy([f"route-{index}" for index in range(71)])

    def test_certifier_reports_split_between_implementation_and_promotion(self) -> None:
        report = certify(ROOT)
        self.assertTrue(report["ok"])
        self.assertEqual(report["exact_head"], _git_head(ROOT))
        self.assertTrue(report["python"]["feature_development_authority"])
        self.assertEqual(report["python"]["public_route_count"], 245)
        self.assertTrue(report["rust"]["feature_development_frozen"])
        self.assertEqual(report["rust"]["implemented_native_routes"], 245)
        self.assertEqual(report["rust"]["implementation_missing_routes"], 0)
        self.assertEqual(report["rust"]["production_promoted_routes"], 174)
        self.assertEqual(report["rust"]["remaining_routes"], 71)
        self.assertEqual(report["rust"]["remaining_owned_routes"], 71)
        self.assertEqual(report["rust"]["unowned_routes"], 0)
        self.assertFalse(report["rust"]["resume_allowed"])


if __name__ == "__main__":
    unittest.main()
