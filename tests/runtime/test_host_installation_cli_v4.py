from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from syntavra_runtime.cli import main


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
        self.assertTrue((self.project / ".codex" / "mcp.json").is_file())

        code, verified = self.run_cli("fabric", "verify-install", "codex", "--home", str(self.home))
        self.assertEqual(code, 0)
        self.assertTrue(verified["ok"])

        code, rows = self.run_cli("fabric", "installations", "--host-name", "codex", "--home", str(self.home))
        self.assertEqual(code, 0)
        self.assertEqual(rows[0]["transaction_id"], transaction_id)

        code, rolled = self.run_cli("fabric", "rollback-install", transaction_id, "--home", str(self.home))
        self.assertEqual(code, 0)
        self.assertEqual(rolled["status"], "rolled-back")
        self.assertFalse((self.project / ".codex" / "mcp.json").exists())

    def test_install_dry_run_writes_nothing(self):
        code, result = self.run_cli("fabric", "install", "claude-code", "--dry-run")
        self.assertEqual(code, 0)
        self.assertEqual(result["status"], "dry-run")
        self.assertFalse((self.project / ".claude").exists())


if __name__ == "__main__":
    unittest.main()
