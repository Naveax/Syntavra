from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "syntavra" / "scripts"
sys.path.insert(0, str(SCRIPTS))

spec = importlib.util.spec_from_file_location("syntavra_platforms", SCRIPTS / "platforms.py")
platforms = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(platforms)


class PlatformTests(unittest.TestCase):
    def test_registry_has_unique_ids(self):
        rows = platforms.registry()["platforms"]
        ids = [row["id"] for row in rows]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertGreaterEqual(len(ids), 20)

    def test_verified_native_targets(self):
        expected = {"codex", "claude-code", "gemini-cli", "antigravity", "antigravity-cli", "windsurf", "opencode", "vscode-copilot"}
        rows = platforms.platform_map()
        self.assertTrue(expected.issubset(rows))
        for key in expected:
            self.assertEqual(rows[key]["support"], "native")
            self.assertTrue(rows[key]["verified"])

    def test_install_native_project(self):
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp) / "project"
            home = Path(temp) / "home"
            project.mkdir(); home.mkdir()
            result = platforms.install("codex", scope="project", project=project, home=home)
            target = project / ".codex" / "skills" / "syntavra"
            self.assertTrue(result["changed"])
            self.assertTrue((target / "SKILL.md").is_file())
            self.assertTrue((target / "scripts" / "platforms.py").is_file())

    def test_rule_bridge_preserves_existing_agents_md(self):
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp) / "project"
            home = Path(temp) / "home"
            project.mkdir(); home.mkdir()
            target = project / "AGENTS.md"
            target.write_text("# Existing\n\nKeep this.\n", encoding="utf-8")
            platforms.install("junie", scope="project", project=project, home=home)
            text = target.read_text(encoding="utf-8")
            self.assertIn("Keep this.", text)
            self.assertIn(platforms.BEGIN, text)
            self.assertIn(platforms.END, text)

    def test_uninstall_removes_only_managed_block(self):
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp) / "project"
            home = Path(temp) / "home"
            project.mkdir(); home.mkdir()
            target = project / "AGENTS.md"
            target.write_text("# Existing\n", encoding="utf-8")
            platforms.install("generic-agents-md", scope="project", project=project, home=home)
            platforms.uninstall("generic-agents-md", scope="project", project=project, home=home)
            self.assertEqual(target.read_text(encoding="utf-8").strip(), "# Existing")

    def test_cursor_rule_format(self):
        text = platforms.cursor_adapter()
        self.assertTrue(text.startswith("---\n"))
        self.assertIn("alwaysApply: false", text)
        self.assertIn(platforms.BEGIN, text)

    def test_all_native_expansion(self):
        selected = platforms._expand_selection("all-native")
        self.assertIn("codex", selected)
        self.assertIn("windsurf", selected)
        self.assertNotIn("cursor", selected)

    def test_platform_json_valid(self):
        data = json.loads((ROOT / "skills" / "syntavra" / "data" / "platforms.json").read_text(encoding="utf-8"))
        self.assertEqual(data["schema_version"], 1)


if __name__ == "__main__":
    unittest.main()
