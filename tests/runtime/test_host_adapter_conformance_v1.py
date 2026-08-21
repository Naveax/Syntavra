from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from syntavra_runtime.adapter_platform import ADAPTERS, AdapterRegistry
from syntavra_runtime.adapter_runtime import AdapterMaturity, AdapterPlatformRuntime
from syntavra_runtime.host_adapters import KNOWN_HOSTS, host_spec, negotiate
from syntavra_runtime.integration_matrix import IntegrationMatrix
from syntavra_runtime.product_surface import PlatformAdapterRegistry
from syntavra_runtime.zero_friction import ZeroFrictionManager


ROOT = Path(__file__).resolve().parents[2]
CONTRACT = ROOT / "contracts/python/host-adapter-conformance-v1.json"


def _contract() -> dict[str, object]:
    value = json.loads(CONTRACT.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _canonical_hosts() -> tuple[str, ...]:
    return tuple(row["integration_id"] for row in IntegrationMatrix.records("host"))


class HostAdapterConformanceV1Tests(unittest.TestCase):
    def test_canonical_runtime_product_registry_is_exact(self) -> None:
        matrix = IntegrationMatrix.validate()
        product = PlatformAdapterRegistry.validate()
        hosts = _canonical_hosts()
        self.assertTrue(matrix["ok"], matrix)
        self.assertEqual(matrix["hosts"], 18)
        self.assertEqual(len(hosts), 18)
        self.assertEqual(len(hosts), len(set(hosts)))
        self.assertTrue(product["ok"], product)
        self.assertEqual(product["adapters"], 18)
        self.assertEqual(product["missing_matrix_hosts"], [])
        self.assertEqual(product["extra_adapters"], [])

    def test_canonical_alias_map_resolves_legacy_contracts_one_to_one(self) -> None:
        aliases = _contract()["canonical_aliases"]
        self.assertIsInstance(aliases, dict)
        canonical = set(_canonical_hosts())
        legacy = {item.adapter_id for item in ADAPTERS}
        self.assertEqual(set(aliases), canonical)
        self.assertEqual(len(set(aliases.values())), len(canonical))
        for host, adapter_id in aliases.items():
            with self.subTest(host=host, adapter_id=adapter_id):
                self.assertIn(host, KNOWN_HOSTS)
                self.assertIn(adapter_id, legacy)

    def test_capability_claims_and_negotiation_are_consistent(self) -> None:
        for row in IntegrationMatrix.records("host"):
            host = row["integration_id"]
            claims = set(row["capabilities"])
            spec = host_spec(host)
            decision = negotiate(host, runtime_available=True, installed=None)
            with self.subTest(host=host):
                if "mcp" in claims:
                    self.assertTrue(spec.supports_mcp)
                if "pre-tool" in claims:
                    self.assertTrue(spec.supports_pre_tool_hook)
                if "post-tool" in claims:
                    self.assertTrue(spec.supports_post_tool_hook)
                self.assertNotEqual(decision["mode"], "UNSUPPORTED")
        self.assertEqual(negotiate("aider")["mode"], "INSTRUCTION_ONLY")

    def test_fresh_repo_all_host_dry_run_is_zero_code_and_complete(self) -> None:
        canonical = set(_canonical_hosts())
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "project"
            state = root / "state"
            project.mkdir()
            manager = ZeroFrictionManager(project, state_root=state)
            plan = manager.install_plan(all_hosts=True, profile="minimal")
            self.assertEqual(set(plan.installable_hosts), canonical)
            self.assertEqual(plan.contract_only_hosts, ())
            result = manager.install(all_hosts=True, dry_run=True, profile="minimal")
            self.assertTrue(result["ok"], result)
            self.assertEqual(len(result["host_results"]), 18)
            self.assertTrue(all(row["status"] == "dry-run" for row in result["host_results"]))
            self.assertTrue(all(row["verification"]["ok"] for row in result["host_results"]))

    def test_empty_fresh_repo_install_and_doctor_do_not_fabricate_hosts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "project"
            state = root / "state"
            project.mkdir()
            manager = ZeroFrictionManager(project, state_root=state)
            before = manager.doctor()
            self.assertTrue(before["ok"], before)
            self.assertTrue(before["ready_to_install"])
            self.assertFalse(before["installed"])
            result = manager.install(dry_run=False, profile="minimal")
            self.assertTrue(result["ok"], result)
            self.assertEqual(result["host_results"], [])
            after = manager.doctor()
            self.assertTrue(after["ok"], after)
            self.assertTrue(after["installed"])
            self.assertEqual(after["configured_hosts"], [])

    def test_external_live_certification_boundary_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "project"
            state = root / "state"
            project.mkdir()
            runtime = AdapterPlatformRuntime(project, state)
            denied = runtime.certify("codex-cli", {})
            self.assertFalse(denied.ok)
            self.assertEqual(denied.maturity, AdapterMaturity.ENFORCED)
            valid = {
                "host": "codex",
                "host_version": "external-fixture",
                "clean_install": True,
                "tool_interception": True,
                "context_interception": True,
                "security_denial": True,
                "session_restore": True,
                "artifact_hash": "sha256:" + "0" * 64,
            }
            admitted = runtime.certify("codex-cli", valid)
            self.assertTrue(admitted.ok)
            self.assertEqual(admitted.maturity, AdapterMaturity.CERTIFIED)
            self.assertIn("external execution receipt", admitted.claim_boundary)

    def test_internal_legacy_registry_never_claims_live_certification(self) -> None:
        report = AdapterRegistry.validate()
        self.assertTrue(report["ok"], report)
        self.assertTrue(report["inventory_gate"], report)
        self.assertGreaterEqual(report["adapters"], 20)
        self.assertEqual(report["live_certified"], 0)
        self.assertIn("external execution receipts", report["live_boundary"])

    def test_contract_keeps_python_first_and_rust_freeze_boundaries(self) -> None:
        contract = _contract()
        self.assertEqual(contract["claim"], "HOST_ADAPTER_CONFORMANCE_V1")
        policy = contract["policy"]
        self.assertTrue(policy["no_parallel_adapter_runtime"])
        self.assertTrue(policy["no_new_persistent_store"])
        self.assertTrue(policy["no_external_network_required"])
        admission = contract["admission"]
        self.assertEqual(admission["rust_production_promoted"], 174)
        self.assertEqual(admission["rust_remaining_parity_promotion"], 71)
        self.assertTrue(admission["python_complete_must_remain_false"])
        self.assertTrue(admission["rust_resume_must_remain_false"])


if __name__ == "__main__":
    unittest.main()
