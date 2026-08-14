from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest.mock import patch

from syntavra_runtime.engine_entry import CODEX_BRIDGE_COMMAND


TOOL_PATH = Path(__file__).resolve().parents[2] / "tools" / "repair_codex_integration.py"
SPEC = importlib.util.spec_from_file_location("repair_codex_integration", TOOL_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
PYTHON_BRIDGE_ARGS = ["-m", "syntavra_runtime", CODEX_BRIDGE_COMMAND]


class RepairCodexIntegrationV001Tests(unittest.TestCase):
    def test_legacy_json_repair_preserves_other_servers_and_removes_obsolete_syntavra_entry(self) -> None:
        original = {
            "mcpServers": {
                "other": {"command": "other", "args": ["serve"]},
                "syntavra": {
                    "command": "python",
                    "args": ["-m", "syntavra_runtime", "--project", "C:/repo/Syntavra", "mcp", "serve"],
                    "cwd": "C:/repo/Syntavra",
                },
            },
            "unrelated": {"keep": True},
        }
        repaired, changes = MODULE.repair_user_mcp_config(original)
        self.assertEqual(changes, ["removed-obsolete-json-syntavra-mcp-entry"])
        self.assertEqual(repaired["mcpServers"]["other"], original["mcpServers"]["other"])
        self.assertNotIn("syntavra", repaired["mcpServers"])
        self.assertEqual(repaired["unrelated"], original["unrelated"])
        self.assertIn("syntavra", original["mcpServers"])

    def test_apply_is_backup_first_migrates_known_legacy_paths_and_installs_current_codex_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project = root / "project"
            project.mkdir()
            (project / ".git").mkdir()

            legacy = project / ".syntavra"
            (legacy / "pre-release").mkdir(parents=True)
            (legacy / "pre-release" / "state.db").write_text("legacy", encoding="utf-8")
            (legacy / "engine.json").write_text('{"engine":"python"}', encoding="utf-8")

            old_project_skill = project / ".codex" / "skills" / "syntavra"
            old_project_skill.mkdir(parents=True)
            (old_project_skill / "SKILL.md").write_text("old project skill", encoding="utf-8")

            home = root / "home"
            codex_home = home / ".codex"
            codex_home.mkdir(parents=True)
            (codex_home / "config.toml").write_text(
                'model = "test-model"\n\n[mcp_servers.other]\ncommand = "other"\n',
                encoding="utf-8",
            )
            legacy_json_path = codex_home / "mcp.json"
            legacy_json_path.write_text(json.dumps({
                "mcpServers": {
                    "other": {"command": "other-json"},
                    "syntavra": {
                        "command": "python",
                        "args": ["-m", "syntavra_runtime", "--project", str(project), "mcp", "serve"],
                        "cwd": str(project),
                        "env": {"SYNTAVRA_PROJECT": str(project)},
                    },
                }
            }), encoding="utf-8")

            old_user_skill = codex_home / "skills" / "syntavra"
            old_user_skill.mkdir(parents=True)
            (old_user_skill / "SKILL.md").write_text("old user skill", encoding="utf-8")

            state_home = root / "state"
            with patch.dict(os.environ, {"SYNTAVRA_STATE_HOME": str(state_home)}, clear=False):
                dry = MODULE.repair(project=project, codex_home=codex_home, apply=False)
                self.assertTrue(dry["changed"])
                self.assertTrue(dry["current_install_required"])
                self.assertTrue((legacy / "pre-release").is_dir())
                self.assertTrue(old_user_skill.is_dir())
                self.assertTrue(old_project_skill.is_dir())
                self.assertFalse(list(codex_home.glob("mcp.json.syntavra-backup-*")))
                applied = MODULE.repair(project=project, codex_home=codex_home, apply=True)

            self.assertTrue(applied["ok"])
            self.assertTrue(applied["changed"])
            self.assertEqual(applied["next"], "restart Codex after the applied MCP migration")

            backups = list(codex_home.glob("mcp.json.syntavra-backup-*"))
            self.assertEqual(len(backups), 1)
            repaired_legacy = json.loads(legacy_json_path.read_text(encoding="utf-8"))
            self.assertEqual(repaired_legacy["mcpServers"]["other"]["command"], "other-json")
            self.assertNotIn("syntavra", repaired_legacy["mcpServers"])

            self.assertTrue((legacy / "engine.json").is_file())
            self.assertFalse((legacy / "pre-release").exists())
            self.assertFalse(old_user_skill.exists())
            self.assertFalse(old_project_skill.exists())
            moved = applied["legacy_paths"]
            self.assertEqual(len(moved), 3)
            self.assertTrue(all(Path(row["destination"]).exists() for row in moved))

            current = tomllib.loads((codex_home / "config.toml").read_text(encoding="utf-8"))
            self.assertEqual(current["model"], "test-model")
            self.assertEqual(current["mcp_servers"]["other"]["command"], "other")
            entry = current["mcp_servers"]["syntavra"]
            self.assertEqual(entry["command"], sys.executable)
            self.assertEqual(entry["args"], PYTHON_BRIDGE_ARGS)
            self.assertNotIn("cwd", entry)
            self.assertNotIn("SYNTAVRA_PROJECT", entry.get("env", {}))
            self.assertNotIn(str(project), entry.get("args", []))
            self.assertTrue((home / ".agents" / "skills" / "syntavra" / "SKILL.md").is_file())

    def test_current_static_user_binding_is_repaired_even_without_legacy_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project = root / "project"
            project.mkdir()
            (project / ".git").mkdir()
            home = root / "home"
            codex_home = home / ".codex"
            codex_home.mkdir(parents=True)
            (codex_home / "config.toml").write_text(
                '[mcp_servers.syntavra]\n'
                'command = "syntavra"\n'
                f'args = ["--project", {json.dumps(str(project))}, "mcp", "serve"]\n'
                f'cwd = {json.dumps(str(project))}\n\n'
                '[mcp_servers.syntavra.env]\n'
                f'SYNTAVRA_PROJECT = {json.dumps(str(project))}\n',
                encoding="utf-8",
            )
            state_home = root / "state"
            with patch.dict(os.environ, {"SYNTAVRA_STATE_HOME": str(state_home)}, clear=False):
                dry = MODULE.repair(project=project, codex_home=codex_home, apply=False)
                self.assertTrue(dry["current_install_required"])
                self.assertTrue(dry["current_codex_config"]["reasons"])
                applied = MODULE.repair(project=project, codex_home=codex_home, apply=True)
            self.assertTrue(applied["ok"])
            self.assertFalse(applied["current_codex_config"]["reasons"])


if __name__ == "__main__":
    unittest.main()
