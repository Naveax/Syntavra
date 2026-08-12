from __future__ import annotations

import unittest
from pathlib import Path

from tools.certify_python_platform_helper_evidence_reference import certify


class PythonPlatformHelperEvidenceReferenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.report = certify(Path(__file__).resolve().parents[2])

    def test_public_route_inventory_and_ownership_are_frozen(self) -> None:
        self.assertTrue(self.report["ok"], self.report)
        routes = self.report["routes"]
        self.assertEqual(routes["route_count"], 19)
        self.assertEqual(len(routes["routes"]), 19)
        self.assertEqual(len(routes["ownership"]), 19)
        self.assertEqual(len(routes["route_sha256"]), 64)

    def test_platform_compatibility_identity_and_manifest_are_frozen(self) -> None:
        platform = self.report["platform"]
        self.assertTrue(platform["compatibility_stable_projection_exact"])
        self.assertEqual(
            platform["compatibility_normalization"],
            [
                "c_sharp/csharp tree-sitter alias canonicalization delegated to F",
                "sandbox backend detail/command-prefix/probe-cache delegated to J",
            ],
        )
        self.assertEqual(platform["product"], "Syntavra")
        self.assertEqual(platform["version"], "0.0.1")
        self.assertEqual(platform["channel"], "pre-release")
        self.assertEqual(platform["manifest_external_claims"], "NOT_PROVEN_WITHOUT_EXTERNAL_RECEIPTS")
        self.assertGreater(platform["capability_count"], 20)
        self.assertGreater(platform["manifest_component_count"], 20)

    def test_output_artifact_exact_recovery_and_durable_state_are_frozen(self) -> None:
        artifacts = self.report["artifacts"]
        self.assertEqual(artifacts["capture_kind"], "terminal")
        self.assertTrue(artifacts["capture_exact_recovery"])
        self.assertEqual(artifacts["errors_matched"], 1)
        self.assertEqual(artifacts["artifact_stats"]["artifacts"], 2)
        self.assertEqual(artifacts["verify_all"], {"ok": True, "checked": 2, "failures": []})
        self.assertEqual(artifacts["durable"], {"sqlite": True, "object_files": 2})

    def test_runtime_evidence_import_stats_and_neighbors_are_frozen(self) -> None:
        runtime = self.report["runtime_evidence"]
        self.assertEqual(runtime["import"], {"ok": True, "spans": 1})
        self.assertEqual(runtime["stats"]["nodes"], 2)
        self.assertEqual(runtime["stats"]["edges"], 1)
        self.assertEqual(runtime["stats"]["relations"], [{"relation": "RUNTIME_CALL", "count": 1}])
        self.assertTrue(runtime["durable_sqlite"])
        self.assertEqual(len(runtime["source_node_id"]), 64)

    def test_encrypted_evidence_rotation_gc_and_malformed_handle_are_frozen(self) -> None:
        evidence = self.report["encrypted_evidence"]
        self.assertTrue(evidence["handle_shape"])
        self.assertTrue(evidence["ciphertext_excludes_plaintext"])
        self.assertTrue(evidence["exact_recovery"])
        self.assertEqual(evidence["encryption"]["algorithm"], "AES-256-GCM")
        self.assertEqual(evidence["encryption"]["mode"], "encrypted")
        self.assertEqual(evidence["rotation"]["previous_key_version"], 1)
        self.assertEqual(evidence["rotation"]["active_key_version"], 2)
        self.assertTrue(evidence["exact_recovery_after_rotation"])
        self.assertEqual(evidence["gc_dry_run"]["deleted"], 0)
        self.assertEqual(evidence["gc_apply"]["deleted"], 1)
        self.assertEqual(evidence["stats_after"]["objects"], 0)
        self.assertEqual(evidence["malformed_handle"]["exit"], 4)
        self.assertEqual(evidence["malformed_handle"]["error_type"], "EvidenceError")
        self.assertTrue(evidence["durable"]["sqlite"])
        self.assertTrue(evidence["durable"]["active_marker"])

    def test_fd3_and_os_boundaries_are_explicit_without_overclaiming(self) -> None:
        self.assertFalse(self.report["fd3"]["applicable"])
        self.assertEqual(self.report["fd3"]["dedicated_channel_hits"], [])
        os_variants = self.report["os_variants"]
        self.assertEqual(os_variants["k_owned_os_branch_hits"], [])
        self.assertEqual(
            os_variants["k_owned_evidence_artifact_output"],
            "schema-and-semantics-invariant-across-windows-linux-macos",
        )
        self.assertEqual(os_variants["nested_sandbox_backend"], "host-dependent-delegated-to-J")
        self.assertEqual(os_variants["nested_language_adapter_availability"], "host-dependent-delegated-to-F")

    def test_offline_boundary_and_exit_policy_are_explicit(self) -> None:
        self.assertEqual(self.report["network_boundary"], "offline; no live external service or remote network required")
        self.assertEqual(
            self.report["exit_policy"],
            {
                "success": 0,
                "application_error": 4,
                "integrity_or_result_failure": 3,
                "argparse_error": 2,
            },
        )


if __name__ == "__main__":
    unittest.main()
