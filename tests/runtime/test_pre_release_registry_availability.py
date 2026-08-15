from __future__ import annotations

import unittest

from tools.check_pre_release_registry_availability import _parse_vsce_show, build_report


class PreReleaseRegistryAvailabilityTests(unittest.TestCase):
    def test_all_current_targets_available(self) -> None:
        def http_probe(url: str):
            return {"status": "available", "http_status": 404, "error": None, "url": url}

        def vsce_probe(extension_id: str, version: str):
            return {
                "status": "available",
                "extension_exists": False,
                "version_exists": False,
                "observed_versions": [],
                "error": None,
                "extension_id": extension_id,
            }

        value = build_report(http_probe=http_probe, vsce_probe=vsce_probe)
        self.assertTrue(value["production_available"])
        self.assertTrue(value["legacy_available"])
        self.assertTrue(value["all_observed"])
        self.assertEqual(value["claim"], "REGISTRY_VERSION_PREFLIGHT_AVAILABLE")
        self.assertFalse(value["publication_performed"])
        self.assertEqual(
            value["targets"]["native"]["publish_order"],
            ["syntavra-contracts", "syntavra-core", "syntavra-cli"],
        )

    def test_occupied_required_target_blocks_production(self) -> None:
        def http_probe(url: str):
            occupied = "%40syntavra%2Finstall/0.0.1" in url
            return {
                "status": "occupied" if occupied else "available",
                "http_status": 200 if occupied else 404,
                "error": None,
                "url": url,
            }

        value = build_report(
            http_probe=http_probe,
            vsce_probe=lambda extension_id, version: {
                "status": "available",
                "extension_exists": False,
                "version_exists": False,
                "observed_versions": [],
                "error": None,
                "extension_id": extension_id,
            },
        )
        self.assertFalse(value["production_available"])
        self.assertEqual(value["targets"]["npm"]["status"], "occupied")
        self.assertEqual(value["claim"], "REGISTRY_VERSION_PREFLIGHT_BLOCKED")

    def test_unreachable_required_target_fails_closed(self) -> None:
        def http_probe(url: str):
            if "syntavra-core" in url:
                return {"status": "unreachable", "http_status": 503, "error": None, "url": url}
            return {"status": "available", "http_status": 404, "error": None, "url": url}

        value = build_report(
            http_probe=http_probe,
            vsce_probe=lambda extension_id, version: {
                "status": "available",
                "extension_exists": False,
                "version_exists": False,
                "observed_versions": [],
                "error": None,
                "extension_id": extension_id,
            },
        )
        self.assertFalse(value["production_available"])
        self.assertFalse(value["all_observed"])
        self.assertEqual(value["targets"]["native"]["status"], "unreachable")

    def test_legacy_collision_does_not_block_production(self) -> None:
        def http_probe(url: str):
            occupied = "syntavra-native/0.0.1" in url
            return {
                "status": "occupied" if occupied else "available",
                "http_status": 200 if occupied else 404,
                "error": None,
                "url": url,
            }

        value = build_report(
            http_probe=http_probe,
            vsce_probe=lambda extension_id, version: {
                "status": "available",
                "extension_exists": False,
                "version_exists": False,
                "observed_versions": [],
                "error": None,
                "extension_id": extension_id,
            },
        )
        self.assertTrue(value["production_available"])
        self.assertFalse(value["legacy_available"])
        self.assertEqual(value["targets"]["legacy_native_companion"]["status"], "occupied")

    def test_vsce_undefined_means_extension_is_available(self) -> None:
        value = _parse_vsce_show(returncode=0, stdout="undefined\n", stderr="", version="0.0.1")
        self.assertEqual(value["status"], "available")
        self.assertFalse(value["extension_exists"])
        self.assertFalse(value["version_exists"])

    def test_vsce_existing_extension_checks_exact_version(self) -> None:
        available = _parse_vsce_show(
            returncode=0,
            stdout='{"versions":[{"version":"0.0.2"},{"version":"0.0.3"}]}',
            stderr="",
            version="0.0.1",
        )
        occupied = _parse_vsce_show(
            returncode=0,
            stdout='{"versions":[{"version":"0.0.1"},{"version":"0.0.2"}]}',
            stderr="",
            version="0.0.1",
        )
        self.assertEqual(available["status"], "available")
        self.assertTrue(available["extension_exists"])
        self.assertFalse(available["version_exists"])
        self.assertEqual(occupied["status"], "occupied")
        self.assertTrue(occupied["version_exists"])

    def test_vsce_failure_is_unreachable_not_available(self) -> None:
        value = _parse_vsce_show(returncode=1, stdout="", stderr="network failed", version="0.0.1")
        self.assertEqual(value["status"], "unreachable")
        self.assertIsNone(value["extension_exists"])
        self.assertIn("network failed", value["error"])


if __name__ == "__main__":
    unittest.main()
