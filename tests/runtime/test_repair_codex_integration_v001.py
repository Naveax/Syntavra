from __future__ import annotations

import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


TOOL_PATH = Path(__file__).resolve().parents[2] / "tools" / "repair_codex_integration.py"
SPEC = importlib.util.spec_from_file_location("repair_codex_integration", TOOL_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class RepairCodexIntegrationV001Tests(unittest.TestCase):
    def test_config_repair_preserves_other_servers_and_removes_only_syntavra_pins(self) -> None:
        original = {
            "mcpServers": {
                "other": {"command": "other", "args": ["serve"]},
                "syntavra": {
                    "command": "python",
                    "args": [
                        "-m", "syntavra_runtime",
                        "--project", "C:/repo/Syntavra",
                        "--state-root=C:/repo/Syntavra/.syntavra/runtime-v3",
                        "mcp", "serve",
                    ],
                    "cwd": "C:/repo/Syntavra",
                    "env": {
                        "SYNTAVRA_PROJECT": "C:/repo/Syntavra",
                        "SYNTAVRA_STATE_ROOT": "C:/repo/Syntavra/.syntavra/runtime-v3",
                        "KEEP": "yes",
                    },
                },
            },
            "unrelated": {"keep": True},
        }
        repaired, changes = MODULE.repair_user_mcp_config(original)
        self.assertTrue(changes)
        self.assertEqual(repaired["mcpServers"]["other"], original["mcpServers"]["other"])
        self.assertEqual(repaired["unrelated"], original["unrelated"])
        entry = repaired["mcpServers"]["syntavra"]
        self.assertNotIn("--project", entry["args"])
        self.assertFalse(any(item.startswith("--state-root") for item in entry["args"]))
        self.assertNotIn("cwd", entry)
        self.assertNotIn("SYNTAVRA_PROJECT", entry["env"])
        self.assertNotIn("SYNTAVRA_STATE_ROOT", entry["env"])
        self.assertEqual(entry["env"]["KEEP"], "yes")
        self.assertEqual(original["mcpServers"]["syntavra"]["cwd"], "C:/repo/Syntavra")

    def test_apply_is_backup_first_and_migrates_only_known_runtime_directories(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project = root / "project"
            project.mkdir()
            (project / ".git").mkdir()
            legacy = project / ".syntavra"
            (legacy / "pre-release").mkdir(parents=True)
            (legacy / "pre-release" / "state.db").write_text("legacy", encoding="utf-8")
            (legacy / "engine.json").write_text('{"engine":"python"}', encoding="utf-8")
            codex_home = root / "home" / ".codex"
            codex_home.mkdir(parents=True)
            config_path = codex_home / "mcp.json"
            config_path.write_text(json.dumps({
                "mcpServers": {
                    "syntavra": {
                        "command": "python",
                        "args": ["-m", "syntavra_runtime", "--project", str(project), "mcp", "serve"],
                        "cwd": str(project),
                        "env": {"SYNTAVRA_PROJECT": str(project)},
                    }
                }
            }), encoding="utf-8")
            state_home = root / "state"
            with patch.dict(os.environ, {"SYNTAVRA_STATE_HOME": str(state_home)}, clear=False):
                dry = MODULE.repair(project=project, codex_home=codex_home, apply=False)
                self.assertTrue(dry["changed"])
                self.assertTrue((legacy / "pre-release").is_dir())
                self.assertFalse(list(codex_home.glob("mcp.json.syntavra-backup-*")))
                applied = MODULE.repair(project=project, codex_home=codex_home, apply=True)

            self.assertTrue(applied["changed"])
            backups = list(codex_home.glob("mcp.json.syntavra-backup-*"))
            self.assertEqual(len(backups), 1)
            self.assertTrue((legacy / "engine.json").is_file())
            self.assertFalse((legacy / "pre-release").exists())
            moved = applied["legacy_state"]
            self.assertEqual(len(moved), 1)
            self.assertTrue(Path(moved[0]["destination"]).is_dir())
            repaired = json.loads(config_path.read_text(encoding="utf-8"))
            entry = repaired["mcpServers"]["syntavra"]
            self.assertNotIn("cwd", entry)
            self.assertNotIn("SYNTAVRA_PROJECT", entry.get("env", {}))
            self.assertNotIn("--project", entry.get("args", []))


if __name__ == "__main__":
    unittest.main()
