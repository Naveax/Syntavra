from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from syntavra_runtime.zero_friction import ZeroFrictionManager


class ZeroFrictionHostSetupV001Tests(unittest.TestCase):
    def test_detected_codex_host_is_installed_and_verified(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            (project / ".codex").mkdir()
            manager = ZeroFrictionManager(project, project / ".syntavra" / "pre-release")
            plan = manager.install_plan(profile="minimal")
            self.assertIn("codex", plan.detected_hosts)
            self.assertIn("codex", plan.installable_hosts)

            result = manager.install(dry_run=False, profile="minimal")
            self.assertTrue(result["ok"], result)
            self.assertEqual(len(result["host_results"]), 1)
            self.assertTrue(result["host_results"][0]["verification"]["ok"])
            self.assertTrue((project / ".codex" / "mcp.json").is_file())
            self.assertTrue((project / ".codex" / "skills" / "syntavra" / "SKILL.md").is_file())

            config = json.loads((project / ".codex" / "mcp.json").read_text(encoding="utf-8"))
            self.assertEqual(config["mcpServers"]["syntavra"]["command"], "syntavra")
            doctor = manager.doctor()
            self.assertTrue(doctor["ok"], doctor)
            self.assertEqual(doctor["configured_hosts"], ["codex"])
            self.assertTrue(doctor["host_verification"][0]["ok"])

    def test_invalid_existing_host_config_fails_without_false_install_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            (project / ".codex").mkdir()
            (project / ".codex" / "mcp.json").write_text("not-json", encoding="utf-8")
            state = project / ".syntavra" / "pre-release"
            manager = ZeroFrictionManager(project, state)
            result = manager.install(dry_run=False)
            self.assertFalse(result["ok"])
            self.assertIn("host config is not valid JSON", result["error"])
            self.assertFalse((state / "install-receipt.json").exists())

    def test_empty_project_does_not_claim_host_installation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            manager = ZeroFrictionManager(project, project / "state")
            result = manager.install(dry_run=False)
            self.assertTrue(result["ok"], result)
            self.assertEqual(result["host_results"], [])
            stats = manager.stats()
            self.assertEqual(stats["onboarding"]["host_installations"], 0)


if __name__ == "__main__":
    unittest.main()
