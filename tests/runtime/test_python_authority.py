from __future__ import annotations

import inspect
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from tools import validate as repository_validate
from tools.certify_python_authority import _assert_no_route_identity_copy, certify

ROOT = Path(__file__).resolve().parents[2]
CONTRACT = ROOT / "contracts/python/python-authority-v1.json"
EXPECTED_ENFORCEMENT = {
    "repository_validator": "tools/validate.py",
    "exact_head_workflow": ".github/workflows/python-authority.yml",
    "release_main_gate": ".github/workflows/release-main-merge-gate.yml",
    "immutable_action_pin_policy": "tests/runtime/test_release_action_pins.py",
}


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
        self.assertEqual(contract["enforcement"], EXPECTED_ENFORCEMENT)
        self.assertTrue(contract["rust_freeze"]["active"])
        self.assertFalse(contract["rust_freeze"]["feature_development_allowed"])
        self.assertFalse(contract["rust_freeze"]["production_promotion_allowed"])
        self.assertFalse(contract["rust_freeze"]["native_counter_change_allowed"])
        self.assertEqual(contract["rust_freeze"]["resume_requires"], "contracts/python/python-completion-certificate-v1.json")
        self.assertTrue(all(contract["rust_freeze"]["resume_transition"].values()))
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

    def test_certifier_rejects_foreign_checkout(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(AssertionError, "must run against its own checkout"):
                certify(Path(directory))

    def test_certifier_reports_split_between_implementation_and_promotion(self) -> None:
        report = certify(ROOT)
        self.assertTrue(report["ok"])
        self.assertEqual(report["exact_head"], _git_head(ROOT))
        self.assertEqual(report["enforcement"], EXPECTED_ENFORCEMENT)
        self.assertTrue(report["python"]["feature_development_authority"])
        self.assertEqual(report["python"]["public_route_count"], 245)
        self.assertFalse(report["rust"]["feature_development_frozen"])
        self.assertEqual(report["rust"]["implemented_native_routes"], 245)
        self.assertEqual(report["rust"]["implementation_missing_routes"], 0)
        self.assertEqual(report["rust"]["production_promoted_routes"], 174)
        self.assertEqual(report["rust"]["remaining_routes"], 71)
        self.assertEqual(report["rust"]["remaining_owned_routes"], 71)
        self.assertEqual(report["rust"]["unowned_routes"], 0)
        self.assertTrue(report["rust"]["resume_allowed"])
        self.assertTrue(report["python_complete_ready"])

    def test_repository_validator_enforces_python_authority(self) -> None:
        required = {path.relative_to(ROOT).as_posix() for path in repository_validate.REQUIRED}
        self.assertTrue(
            {
                ".github/workflows/python-authority.yml",
                "contracts/python/python-authority-v1.json",
                "tests/runtime/test_python_authority.py",
                "tools/certify_python_authority.py",
            }.issubset(required)
        )
        ok, detail = repository_validate._python_authority_check()
        self.assertTrue(ok, detail)
        observed = json.loads(detail)
        self.assertEqual(observed["claim"], "PYTHON_FEATURE_DEVELOPMENT_AUTHORITY")
        self.assertEqual(observed["python_public_routes"], 245)
        self.assertEqual(observed["rust_implemented_native_routes"], 245)
        self.assertEqual(observed["rust_promoted_native_routes"], 174)
        self.assertEqual(observed["rust_remaining_routes"], 71)
        self.assertTrue(observed["rust_resume_allowed"])
        self.assertIn('checks.append(("python_authority", authority_ok, authority_detail))', inspect.getsource(repository_validate.main))

    def test_enforcement_surfaces_are_bound(self) -> None:
        contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
        for relative in contract["enforcement"].values():
            self.assertTrue((ROOT / relative).is_file(), relative)

        workflow = (ROOT / EXPECTED_ENFORCEMENT["exact_head_workflow"]).read_text(encoding="utf-8")
        self.assertIn("python-authority-${{ github.event.pull_request.number || github.ref }}", workflow)
        self.assertIn("tests.runtime.test_python_authority", workflow)
        self.assertIn("tools/certify_python_authority.py", workflow)
        self.assertIn("rm -rf syntavra_runtime.egg-info", workflow)
        self.assertIn("git diff --check", workflow)
        self.assertIn('test -z "$status"', workflow)

        release_gate = (ROOT / EXPECTED_ENFORCEMENT["release_main_gate"]).read_text(encoding="utf-8")
        self.assertIn("tests.runtime.test_python_authority", release_gate)
        self.assertIn("tools/certify_python_authority.py", release_gate)

        pin_policy = (ROOT / EXPECTED_ENFORCEMENT["immutable_action_pin_policy"]).read_text(encoding="utf-8")
        self.assertIn('".github/workflows/python-authority.yml"', pin_policy)


if __name__ == "__main__":
    unittest.main()
