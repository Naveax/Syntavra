from __future__ import annotations

import io
import json
import tempfile
import tomllib
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from syntavra_runtime.cli import main
from syntavra_runtime.engine_entry import CODEX_BRIDGE_COMMAND


class HostInstallationCLIV4Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.project = self.root / "project"
        self.state = self.root / "state"
        self.skill = self.root / "skill"
        self.home = self.root / "home"
        self.project.mkdir()
        self.skill.mkdir()
        self.home.mkdir()
        (self.skill / "SKILL.md").write_text("# Syntavra\n", encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def run_cli(self, *values: str) -> tuple[int, dict]:
        stream = io.StringIO()
        with redirect_stdout(stream):
            code = main([
                "--project", str(self.project),
                "--state-root", str(self.state),
                "--skill-root", str(self.skill),
                *values,
            ])
        return code, json.loads(stream.getvalue())

    def test_install_verify_list_and_rollback(self):
        code, installed = self.run_cli("fabric", "install", "codex", "--home", str(self.home))
        self.assertEqual(code, 0)
        transaction_id = installed["transaction_id"]
        config_path = self.project / ".codex" / "config.toml"
        skill_path = self.project / ".agents" / "skills" / "syntavra" / "SKILL.md"
        self.assertTrue(config_path.is_file())
        self.assertTrue(skill_path.is_file())
        config = tomllib.loads(config_path.read_text(encoding="utf-8"))
        entry = config["mcp_servers"]["syntavra"]
        self.assertEqual(entry["command"], "syntavra")
        self.assertEqual(entry["args"], [CODEX_BRIDGE_COMMAND])
        self.assertEqual(entry["cwd"], str(self.project.resolve()))
        self.assertEqual(entry["env"]["SYNTAVRA_PROJECT"], str(self.project.resolve()))

        code, verified = self.run_cli("fabric", "verify-install", "codex", "--home", str(self.home))
        self.assertEqual(code, 0)
        self.assertTrue(verified["ok"])

        code, rows = self.run_cli("fabric", "installations", "--host-name", "codex", "--home", str(self.home))
        self.assertEqual(code, 0)
        self.assertEqual(rows[0]["transaction_id"], transaction_id)

        code, rolled = self.run_cli("fabric", "rollback-install", transaction_id, "--home", str(self.home))
        self.assertEqual(code, 0)
        self.assertEqual(rolled["status"], "rolled-back")
        self.assertFalse(config_path.exists())
        self.assertFalse(skill_path.exists())

    def test_install_dry_run_writes_nothing(self):
        code, result = self.run_cli("fabric", "install", "claude-code", "--dry-run")
        self.assertEqual(code, 0)
        self.assertEqual(result["status"], "dry-run")
        self.assertFalse((self.project / ".claude").exists())


if __name__ == "__main__":
    unittest.main()
