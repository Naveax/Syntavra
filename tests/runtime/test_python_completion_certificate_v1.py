from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tools.certify_python_completion_certificate_v1 import (
    CONTRACT,
    PLATFORM_CLAIM,
    REGISTRY,
    _head,
    _validate_enforcement,
    derive_contract_freeze,
    validate_platform_evidence,
)

ROOT = Path(__file__).resolve().parents[2]


class PythonCompletionCertificateV1Tests(unittest.TestCase):
    def _contract(self) -> dict:
        return json.loads((ROOT / CONTRACT).read_text(encoding="utf-8"))

    def _registry(self) -> dict:
        return json.loads((ROOT / REGISTRY).read_text(encoding="utf-8"))

    def test_completion_contract_is_strict_and_phase_exit_is_admitted(self) -> None:
        contract = self._contract()
        self.assertEqual(contract["claim"], "PYTHON_COMPLETION_CERTIFICATE_V1")
        self.assertTrue(contract["strict"])
        self.assertFalse(contract["required_gates"]["external_superiority_required"])
        registry = self._registry()
        by_id = {item["id"]: item for item in registry["capabilities"]}
        self.assertEqual(by_id["signalbench_python_product_v1"]["state"], "certified")
        self.assertEqual(by_id["python_completion_certificate_v1"]["state"], "certified")
        self.assertTrue(by_id["python_completion_certificate_v1"]["certification_evidence"])
        self.assertTrue(registry["python_complete"]["ready"])
        self.assertFalse(registry["python_complete"]["rust_resume_allowed"])
        self.assertTrue(registry["python_complete"]["rust_retired"])

    def test_registry_derived_contract_freeze_matches_pinned_digest(self) -> None:
        contract = self._contract()
        freeze = derive_contract_freeze(ROOT, self._registry())
        self.assertGreater(freeze["contract_count"], 0)
        self.assertEqual(freeze["contract_count"], contract["contract_freeze"]["expected_contract_count"])
        self.assertEqual(freeze["sha256"], contract["contract_freeze"]["expected_sha256"])
        self.assertEqual(len(freeze["sha256"]), 64)

    def test_platform_receipts_require_both_exact_head_operating_systems(self) -> None:
        contract = self._contract()
        head = _head(ROOT)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = []
            for platform in ("linux", "windows"):
                path = root / f"{platform}.json"
                path.write_text(
                    json.dumps(
                        {
                            "ok": True,
                            "schema_version": 1,
                            "claim": PLATFORM_CLAIM,
                            "exact_head": head,
                            "platform": platform,
                            "installed_module_path": f"/installed/{platform}/syntavra_runtime/__init__.py",
                            "clean_install": True,
                            "source_import_isolation": True,
                            "fresh_repository_smoke": True,
                            "basic_runtime": True,
                        }
                    ),
                    encoding="utf-8",
                )
                paths.append(path)
            report = validate_platform_evidence(ROOT, exact_head=head, contract=contract, evidence_paths=paths)
            self.assertTrue(report["ready"])
            self.assertEqual(report["present_platforms"], ["linux", "windows"])

    def test_platform_receipt_head_drift_fails_closed(self) -> None:
        contract = self._contract()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "linux.json"
            path.write_text(
                json.dumps(
                    {
                        "ok": True,
                        "schema_version": 1,
                        "claim": PLATFORM_CLAIM,
                        "exact_head": "0" * 40,
                        "platform": "linux",
                        "installed_module_path": "/installed/syntavra_runtime/__init__.py",
                        "clean_install": True,
                        "source_import_isolation": True,
                        "fresh_repository_smoke": True,
                        "basic_runtime": True,
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(AssertionError, "exact-head drift"):
                validate_platform_evidence(ROOT, exact_head=_head(ROOT), contract=contract, evidence_paths=[path])

    def test_completion_workflow_release_gate_and_pin_policy_are_bound(self) -> None:
        report = _validate_enforcement(ROOT)
        self.assertTrue(report["windows_linux_matrix_bound"])
        self.assertTrue(report["aggregate_validation_bound"])


if __name__ == "__main__":
    unittest.main()
