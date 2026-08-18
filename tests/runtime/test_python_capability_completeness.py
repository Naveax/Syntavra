from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from tools.certify_python_capability_completeness import (
    EXPECTED_CLASSIFICATIONS,
    EXPECTED_MILESTONE_PREFIX,
    EXPECTED_STATES,
    _validate_capabilities,
    certify,
)
from tools.certify_python_authority import _assert_no_route_identity_copy

ROOT = Path(__file__).resolve().parents[2]
CONTRACT = ROOT / "contracts/python/capability-completeness-registry-v1.json"


class PythonCapabilityCompletenessTests(unittest.TestCase):
    def _contract(self) -> dict:
        return json.loads(CONTRACT.read_text(encoding="utf-8"))

    def test_registry_vocabulary_and_order_are_canonical(self) -> None:
        contract = self._contract()
        self.assertEqual(contract["claim"], "PYTHON_CAPABILITY_COMPLETENESS_TRACKED")
        self.assertTrue(contract["strict"])
        self.assertEqual(contract["state_vocabulary"], EXPECTED_STATES)
        self.assertEqual(contract["classification_vocabulary"], EXPECTED_CLASSIFICATIONS)
        self.assertEqual(contract["milestone_order"][:10], EXPECTED_MILESTONE_PREFIX)
        self.assertEqual(len(contract["milestone_order"]), len(set(contract["milestone_order"])))

    def test_registry_does_not_duplicate_route_identity_lists(self) -> None:
        contract = self._contract()
        _assert_no_route_identity_copy(contract)
        with self.assertRaisesRegex(AssertionError, "245-route identity list"):
            _assert_no_route_identity_copy([f"route-{index}" for index in range(245)])
        with self.assertRaisesRegex(AssertionError, "71-route identity list"):
            _assert_no_route_identity_copy([f"route-{index}" for index in range(71)])

    def test_capability_ids_are_unique_and_evidence_backed(self) -> None:
        contract = self._contract()
        capabilities, state_counts, classification_counts = _validate_capabilities(ROOT, contract)
        ids = [item["id"] for item in capabilities]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertGreater(state_counts.get("partial", 0), 0)
        self.assertGreater(classification_counts.get("HARDEN", 0), 0)
        self.assertGreater(classification_counts.get("UNIFY", 0), 0)
        self.assertIn("external_superiority_adoption_evidence", ids)

    def test_advanced_state_without_evidence_fails_closed(self) -> None:
        contract = self._contract()
        mutated = copy.deepcopy(contract)
        row = next(item for item in mutated["capabilities"] if item["id"] == "evidence_store_v2")
        row["implementation_evidence"] = []
        with self.assertRaisesRegex(AssertionError, "advanced state requires implementation evidence"):
            _validate_capabilities(ROOT, mutated)

    def test_certified_state_without_certification_evidence_fails_closed(self) -> None:
        contract = self._contract()
        mutated = copy.deepcopy(contract)
        row = next(item for item in mutated["capabilities"] if item["id"] == "python_authority_v1")
        row["certification_evidence"] = []
        with self.assertRaisesRegex(AssertionError, "certified state requires certification evidence"):
            _validate_capabilities(ROOT, mutated)

    def test_external_proof_cannot_masquerade_as_internal_implementation(self) -> None:
        contract = self._contract()
        mutated = copy.deepcopy(contract)
        row = next(item for item in mutated["capabilities"] if item["id"] == "external_superiority_adoption_evidence")
        row["required_for_python_complete"] = True
        row["implementation_evidence"] = ["README.md"]
        with self.assertRaisesRegex(AssertionError, "external proof cannot block Python COMPLETE"):
            _validate_capabilities(ROOT, mutated)

    def test_registry_self_state_is_not_preemptively_certified(self) -> None:
        contract = self._contract()
        row = next(item for item in contract["capabilities"] if item["id"] == "capability_completeness_registry_v1")
        self.assertIn(row["state"], {"implemented", "verified"})
        self.assertEqual(row["certification_evidence"], [])

    def test_exact_head_report_keeps_python_complete_and_rust_resume_closed(self) -> None:
        report = certify(ROOT)
        self.assertTrue(report["ok"])
        self.assertEqual(report["claim"], "PYTHON_CAPABILITY_COMPLETENESS_TRACKED")
        self.assertEqual(report["current_milestone"], "capability_completeness_registry_v1")
        self.assertTrue(report["registry_admission_ready"])
        self.assertFalse(report["python_complete_ready"])
        self.assertFalse(report["rust_resume_allowed"])
        self.assertEqual(report["rust"]["implementation_coverage"], 245)
        self.assertEqual(report["rust"]["production_promoted"], 174)
        self.assertEqual(report["rust"]["remaining_parity_promotion"], 71)
        self.assertTrue(report["rust"]["feature_development_frozen"])
        self.assertGreater(report["uncertified_required_count"], 0)
        self.assertIn("capability_completeness_registry_v1", report["uncertified_required"])
        self.assertIn("rust_feature_freeze_guard_v1", report["uncertified_required"])

    def test_certifier_rejects_foreign_checkout(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(AssertionError, "must run against its own checkout"):
                certify(Path(directory))


if __name__ == "__main__":
    unittest.main()
