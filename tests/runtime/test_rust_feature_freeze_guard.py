from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.check_rust_feature_freeze import _classify, check, verify_baseline
from tools.certify_rust_feature_freeze_guard import certify

ROOT = Path(__file__).resolve().parents[2]
CONTRACT = ROOT / "contracts/python/rust-feature-freeze-guard-v1.json"


class RustFeatureFreezeGuardTests(unittest.TestCase):
    def _contract(self) -> dict:
        return json.loads(CONTRACT.read_text(encoding="utf-8"))

    def test_contract_keeps_python_first_rust_boundaries_closed(self) -> None:
        contract = self._contract()
        self.assertEqual(contract["claim"], "RUST_FEATURE_FREEZE_ENFORCED")
        self.assertTrue(contract["strict"])
        self.assertFalse(contract["policy"]["rust_feature_development_allowed"])
        self.assertFalse(contract["policy"]["rust_production_promotion_allowed"])
        self.assertFalse(contract["policy"]["native_counter_change_allowed"])
        self.assertFalse(contract["policy"]["remaining71_parity_work_allowed"])
        self.assertFalse(contract["policy"]["promotion_authority_change_allowed"])
        self.assertEqual(contract["expected"]["rust_implementation_coverage"], 245)
        self.assertEqual(contract["expected"]["rust_production_promoted"], 174)
        self.assertEqual(contract["expected"]["remaining_parity_promotion"], 71)
        self.assertFalse(contract["expected"]["python_complete"])
        self.assertFalse(contract["expected"]["rust_resume_allowed"])

    def test_protected_path_classes_are_explicit(self) -> None:
        contract = self._contract()
        self.assertEqual(_classify("native/syntavra-native/src/main.rs", contract), "native")
        self.assertEqual(_classify(".github/workflows/remaining71-agent-differential.yml", contract), "remaining71")
        self.assertEqual(_classify("tools/validate_remaining71_agent_differential.py", contract), "remaining71")
        self.assertEqual(_classify("contracts/engine/phase2-rust-migration-matrix-v1.json", contract), "promotion-authority")
        self.assertEqual(_classify("contracts/engine/dual-engine-public-surface-v2.json", contract), "promotion-authority")
        self.assertIsNone(_classify("syntavra_runtime/context_pack.py", contract))

    def test_baseline_is_174_promoted_71_remaining_and_resume_closed(self) -> None:
        baseline = verify_baseline(ROOT, self._contract())
        self.assertEqual(baseline["implementation_coverage"], 245)
        self.assertEqual(baseline["production_promoted"], 174)
        self.assertEqual(baseline["remaining"], 71)
        self.assertFalse(baseline["python_complete"])
        self.assertFalse(baseline["rust_resume_allowed"])

    def test_ordinary_ci_denies_native_feature_change(self) -> None:
        changed = [{"status": "M", "path": "native/syntavra-native/src/main.rs", "role": "path"}]
        with patch("tools.check_rust_feature_freeze._changed_paths", return_value=changed):
            report = check(ROOT, base="base", head="head")
        self.assertFalse(report["ok"])
        self.assertEqual(report["denied_change_count"], 1)
        self.assertEqual(report["denied_changes"][0]["class"], "native")

    def test_explicit_security_exception_may_admit_native_only_repair(self) -> None:
        changed = [{"status": "M", "path": "native/syntavra-native/src/main.rs", "role": "path"}]
        with patch("tools.check_rust_feature_freeze._changed_paths", return_value=changed):
            report = check(
                ROOT,
                base="base",
                head="head",
                maintenance_exception="security",
                maintenance_reason="repair a reviewed native security defect without changing promotion authority",
            )
        self.assertTrue(report["ok"])
        self.assertEqual(report["protected_change_count"], 1)
        self.assertEqual(report["denied_change_count"], 0)

    def test_python_surface_metadata_sync_is_narrowly_allowed(self) -> None:
        path = "contracts/engine/dual-engine-public-surface-v2.json"
        changed = [{"status": "M", "path": path, "role": "path"}]
        before = {
            "claim": "FULL_DUAL_ENGINE_PARITY_PROVEN",
            "python_surface": {
                "module_count": 198,
                "public_command_count": 245,
                "command_paths_sha256": "a" * 64,
                "digest_encoding": "canonical-json-array-utf8",
            },
            "rust_surface": {"native_public_command_count": 245},
        }
        after = {
            **before,
            "python_surface": {
                **before["python_surface"],
                "module_count": 206,
            },
        }
        with (
            patch("tools.check_rust_feature_freeze._changed_paths", return_value=changed),
            patch(
                "tools.check_rust_feature_freeze._read_revision_json",
                side_effect=[before, after],
            ),
        ):
            report = check(ROOT, base="base", head="head")
        self.assertTrue(report["ok"])
        self.assertEqual(report["protected_change_count"], 1)
        self.assertEqual(report["allowed_python_surface_metadata_change_count"], 1)
        self.assertEqual(report["denied_change_count"], 0)
        self.assertEqual(
            report["allowed_python_surface_metadata_changes"][0]["allowance"],
            "python-surface-metadata-sync",
        )

    def test_dual_engine_rust_or_promotion_change_remains_denied(self) -> None:
        path = "contracts/engine/dual-engine-public-surface-v2.json"
        changed = [{"status": "M", "path": path, "role": "path"}]
        before = {
            "claim": "FULL_DUAL_ENGINE_PARITY_PROVEN",
            "python_surface": {
                "module_count": 198,
                "public_command_count": 245,
                "command_paths_sha256": "a" * 64,
                "digest_encoding": "canonical-json-array-utf8",
            },
            "rust_surface": {"native_public_command_count": 245},
        }
        after = {
            **before,
            "rust_surface": {"native_public_command_count": 244},
        }
        with (
            patch("tools.check_rust_feature_freeze._changed_paths", return_value=changed),
            patch(
                "tools.check_rust_feature_freeze._read_revision_json",
                side_effect=[before, after],
            ),
        ):
            report = check(ROOT, base="base", head="head")
        self.assertFalse(report["ok"])
        self.assertEqual(report["allowed_python_surface_metadata_change_count"], 0)
        self.assertEqual(report["denied_change_count"], 1)
        self.assertEqual(report["denied_changes"][0]["class"], "promotion-authority")

    def test_maintenance_exception_never_admits_promotion_authority_change(self) -> None:
        changed = [{"status": "M", "path": "contracts/engine/phase2-rust-migration-matrix-v1.json", "role": "path"}]
        with patch("tools.check_rust_feature_freeze._changed_paths", return_value=changed):
            report = check(
                ROOT,
                base="base",
                head="head",
                maintenance_exception="contract-blocker",
                maintenance_reason="test-only exception path",
            )
        self.assertFalse(report["ok"])
        self.assertEqual(report["denied_changes"][0]["class"], "promotion-authority")

    def test_maintenance_exception_never_admits_remaining71_parity_change(self) -> None:
        changed = [{"status": "M", "path": "tools/validate_remaining71_agent_differential.py", "role": "path"}]
        with patch("tools.check_rust_feature_freeze._changed_paths", return_value=changed):
            report = check(
                ROOT,
                base="base",
                head="head",
                maintenance_exception="build-blocker",
                maintenance_reason="test-only exception path",
            )
        self.assertFalse(report["ok"])
        self.assertEqual(report["denied_changes"][0]["class"], "remaining71")

    def test_exception_requires_known_type_and_explicit_reason(self) -> None:
        with patch("tools.check_rust_feature_freeze._changed_paths", return_value=[]):
            with self.assertRaisesRegex(AssertionError, "unknown maintenance exception"):
                check(ROOT, base="base", head="head", maintenance_exception="feature-work", maintenance_reason="no")
            with self.assertRaisesRegex(AssertionError, "requires explicit reason"):
                check(ROOT, base="base", head="head", maintenance_exception="security", maintenance_reason="")

    def test_certifier_reports_guard_admission_without_resuming_rust(self) -> None:
        report = certify(ROOT)
        self.assertTrue(report["ok"])
        self.assertTrue(report["guard_admission_ready"])
        self.assertFalse(report["python_complete_ready"])
        self.assertFalse(report["rust_resume_allowed"])
        self.assertEqual(report["rust"]["implementation_coverage"], 245)
        self.assertEqual(report["rust"]["production_promoted"], 174)
        self.assertEqual(report["rust"]["remaining_parity_promotion"], 71)
        self.assertTrue(report["rust"]["feature_development_frozen"])

    def test_certifier_rejects_foreign_checkout(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(AssertionError, "must run against its own checkout"):
                certify(Path(directory))


if __name__ == "__main__":
    unittest.main()
