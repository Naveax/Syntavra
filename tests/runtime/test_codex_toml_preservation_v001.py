from __future__ import annotations

import tempfile
import tomllib
import unittest
from pathlib import Path

from syntavra_runtime.codex_integration import mcp_entry, render_config
from syntavra_runtime.installer import HostInstaller


class CodexTomlPreservationV001Tests(unittest.TestCase):
    def test_central_renderer_preserves_array_table_after_legacy_syntavra_tables(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp) / "project"
            project.mkdir()
            existing = (
                '[mcp_servers.syntavra]\n'
                'command = "old"\n'
                'args = ["--project", "wrong", "mcp", "serve"]\n\n'
                '[mcp_servers.syntavra.env]\n'
                'SYNTAVRA_PROJECT = "wrong"\n\n'
                '[[profiles]]\n'
                'name = "keep-me"\n'
                'enabled = true\n'
            )
            rendered = render_config(existing, mcp_entry(("syntavra",), project=project, scope="user"))
            parsed = tomllib.loads(rendered)
        self.assertEqual(parsed["profiles"][0]["name"], "keep-me")
        self.assertTrue(parsed["profiles"][0]["enabled"])
        self.assertNotIn("SYNTAVRA_PROJECT", parsed["mcp_servers"]["syntavra"].get("env", {}))

    def test_classic_installer_renderer_preserves_array_table(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project = root / "project"
            project.mkdir()
            skill = root / "skill"
            skill.mkdir()
            (skill / "SKILL.md").write_text("# Syntavra\n", encoding="utf-8")
            installer = HostInstaller(project=project, skill_root=skill, home=root / "home")
            existing = (
                '[mcp_servers.syntavra]\n'
                'command = "old"\n\n'
                '[mcp_servers.syntavra.env]\n'
                'SYNTAVRA_PROJECT = "wrong"\n\n'
                '[[profiles]]\n'
                'name = "keep-me-too"\n'
            )
            rendered = installer._render_codex_toml(existing, scope="user")
            parsed = tomllib.loads(rendered)
        self.assertEqual(parsed["profiles"][0]["name"], "keep-me-too")
        self.assertNotIn("SYNTAVRA_PROJECT", parsed["mcp_servers"]["syntavra"].get("env", {}))


if __name__ == "__main__":
    unittest.main()
