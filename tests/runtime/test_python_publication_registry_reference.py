from __future__ import annotations

import unittest
from pathlib import Path

from tools.certify_python_publication_registry_reference import certify


class PythonPublicationRegistryReferenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.report = certify(Path(__file__).resolve().parents[2])

    def test_publication_does_not_invent_a_public_cli_route(self) -> None:
        self.assertTrue(self.report["ok"], self.report)
        public_cli = self.report["public_cli"]
        self.assertEqual(public_cli["canonical_route_count"], 245)
        self.assertEqual(public_cli["owned_routes"], [])
        self.assertEqual(public_cli["matching_routes"], [])
        self.assertEqual(len(public_cli["canonical_route_sha256"]), 64)

    def test_release_identity_and_unpublished_claim_boundary_are_frozen(self) -> None:
        release = self.report["release"]
        self.assertEqual(release["identity"]["version"], "0.0.1")
        self.assertEqual(release["identity"]["channel"], "pre-release")
        self.assertEqual(release["identity"]["stability"], "pre-alpha")
        self.assertTrue(release["identity"]["version_locked"])
        self.assertTrue(release["repository_identity"]["ok"])
        self.assertEqual(
            release["pre_release"]["claim_boundaries"]["registry_publication"],
            "REGISTRY_PUBLICATION_NOT_PERFORMED",
        )
        for target in ("python", "npm", "npm_sdk", "vscode", "native", "legacy_native_companion"):
            self.assertFalse(release["publish_readiness"][target]["published"])
        npm_sdk = release["publish_readiness"]["npm_sdk"]
        self.assertEqual(npm_sdk["package"], "@syntavra/sdk")
        self.assertEqual(npm_sdk["tag"], "next")
        self.assertFalse(npm_sdk["published"])
        native = release["publish_readiness"]["native"]
        self.assertEqual(native["package"], "syntavra-cli")
        self.assertEqual(native["binary"], "syntavra")
        self.assertEqual(native["publish_order"], ["syntavra-contracts", "syntavra-core", "syntavra-cli"])
        legacy_native = release["publish_readiness"]["legacy_native_companion"]
        self.assertEqual(legacy_native["package"], "syntavra-native")
        self.assertFalse(legacy_native["workspace_member"])
        self.assertFalse(legacy_native["production_selector"])
        self.assertEqual(len(release["snapshot_sha256"]), 64)

    def test_package_publication_metadata_is_exact(self) -> None:
        package = self.report["release"]["package_metadata"]
        self.assertEqual(package["python_name"], "syntavra-runtime")
        self.assertEqual(package["npm_installer_name"], "@syntavra/install")
        self.assertEqual(package["typescript_sdk_name"], "@syntavra/sdk")
        self.assertEqual(package["vscode_name"], "syntavra-vscode")
        self.assertEqual(package["npm_tag"], "next")
        self.assertTrue(package["npm_provenance"])
        self.assertEqual(package["typescript_tag"], "next")
        self.assertTrue(package["typescript_provenance"])
        self.assertEqual(set(self.report["release"]["package_versions"].values()), {"0.0.1"})

    def test_generated_prerelease_manifest_is_deterministic_and_configured_only(self) -> None:
        release = self.report["release"]
        self.assertEqual(len(release["generated_manifest_sha256"]), 64)
        self.assertEqual(len(release["generated_manifest_hash"]), 64)
        self.assertGreaterEqual(len(release["distributions"]), 4)
        self.assertTrue(all(row["status"] == "configured" for row in release["distributions"]))

    def test_schema_registry_read_write_migrate_and_catalog_are_frozen(self) -> None:
        registry = self.report["schema_registry"]
        self.assertEqual(
            registry["catalog"],
            {"publication.metadata": {"latest": 2, "versions": [1, 2]}},
        )
        self.assertEqual(
            registry["validated"],
            {
                "schema_version": 2,
                "package": "syntavra-runtime",
                "version": "0.0.1",
                "channel": "pre-release",
            },
        )
        self.assertEqual(registry["migrated"], registry["validated"])
        self.assertEqual(
            registry["source_after_migration"],
            {"schema_version": 1, "package": "syntavra-runtime", "version": "0.0.1"},
        )
        self.assertEqual(len(registry["snapshot_sha256"]), 64)

    def test_schema_registry_conflict_and_failure_envelopes_are_exact(self) -> None:
        errors = self.report["schema_registry"]["errors"]
        self.assertEqual(errors["invalid_identity"], "invalid schema identity")
        self.assertEqual(errors["duplicate_schema"], "schema version already registered")
        self.assertEqual(errors["duplicate_migration"], "schema migration already registered")
        self.assertEqual(
            errors["missing_migration_endpoints"],
            "both migration endpoint schemas must be registered",
        )
        self.assertEqual(errors["missing_required"], "missing required properties: package")
        self.assertEqual(errors["unknown_property"], "unknown properties: extra")
        self.assertEqual(errors["invalid_type"], "property package has invalid type")
        self.assertEqual(errors["unknown_schema"], "unknown schema: publication.metadata@3")
        self.assertEqual(errors["downgrade_forbidden"], "automatic schema downgrade is forbidden")
        self.assertEqual(errors["missing_migration"], "missing migration: publication.metadata@1->2")

    def test_certification_is_offline_and_has_no_nondeterministic_fields(self) -> None:
        self.assertEqual(
            self.report["network_boundary"],
            "offline repository metadata and temporary local registry fixtures only; no PyPI, npm, VS Code Marketplace, package registry, signing service, or credential use",
        )
        self.assertEqual(self.report["claim_boundary"], "REGISTRY_PUBLICATION_NOT_PERFORMED")
        self.assertEqual(self.report["nondeterministic_fields"], [])


if __name__ == "__main__":
    unittest.main()
