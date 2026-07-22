from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from syntavra_runtime.host_installation import HostInstallationManager


class HostInstallationV4Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.project = self.root / "project"
        self.home = self.root / "home"
        self.skill = self.root / "skill"
        self.project.mkdir()
        self.home.mkdir()
        self.skill.mkdir()
        (self.skill / "SKILL.md").write_text("# Syntavra Skill\n\nUse exact evidence.\n", encoding="utf-8")
        (self.skill / "REFERENCE.md").write_text("reference\n", encoding="utf-8")
        self.manager = HostInstallationManager(
            self.root / "install.sqlite3",
            project=self.project,
            skill_root=self.skill,
            home=self.home,
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_codex_apply_preserves_config_verifies_and_rolls_back(self):
        config = self.project / ".codex" / "mcp.json"
        config.parent.mkdir(parents=True)
        original = {"mcpServers": {"existing": {"command": "existing"}}, "userSetting": 7}
        config.write_text(json.dumps(original), encoding="utf-8")

        result = self.manager.apply("codex")
        self.assertEqual(result.status, "applied")
        merged = json.loads(config.read_text(encoding="utf-8"))
        self.assertEqual(merged["userSetting"], 7)
        self.assertEqual(merged["mcpServers"]["existing"]["command"], "existing")
        self.assertEqual(merged["mcpServers"]["syntavra"]["command"], "syntavra")
        installed_skill = self.project / ".codex" / "skills" / "syntavra"
        self.assertTrue((installed_skill / "SKILL.md").is_file())
        self.assertTrue((installed_skill / "REFERENCE.md").is_file())
        self.assertTrue(self.manager.verify("codex")["ok"])

        rolled = self.manager.rollback(result.transaction_id)
        self.assertEqual(rolled.status, "rolled-back")
        self.assertEqual(json.loads(config.read_text(encoding="utf-8")), original)
        self.assertFalse(installed_skill.exists())

    def test_claude_install_adds_hooks_without_deleting_user_settings(self):
        config = self.project / ".claude" / "settings.json"
        config.parent.mkdir(parents=True)
        config.write_text(json.dumps({"permissions": {"allow": ["Read"]}}), encoding="utf-8")
        result = self.manager.apply("claude-code")
        self.assertEqual(result.status, "applied")
        merged = json.loads(config.read_text(encoding="utf-8"))
        self.assertEqual(merged["permissions"]["allow"], ["Read"])
        self.assertIn("PreToolUse", merged["hooks"])
        self.assertIn("PostToolUse", merged["hooks"])
        self.assertTrue(self.manager.verify("claude-code")["ok"])

    def test_managed_text_host_is_idempotent_and_rollback_restores_file(self):
        agents = self.project / "AGENTS.md"
        agents.write_text("# User instructions\n\nKeep this text.\n", encoding="utf-8")
        first = self.manager.apply("aider")
        text = agents.read_text(encoding="utf-8")
        self.assertIn("Keep this text.", text)
        self.assertEqual(text.count("SIGNALCORE:BEGIN"), 1)
        second = self.manager.apply("aider")
        text = agents.read_text(encoding="utf-8")
        self.assertEqual(text.count("SIGNALCORE:BEGIN"), 1)
        self.manager.rollback(second.transaction_id)
        self.assertEqual(agents.read_text(encoding="utf-8").count("SIGNALCORE:BEGIN"), 1)
        self.manager.rollback(first.transaction_id)
        self.assertEqual(agents.read_text(encoding="utf-8"), "# User instructions\n\nKeep this text.\n")

    def test_user_scope_and_dry_run_do_not_write(self):
        result = self.manager.apply("codex", scope="user", dry_run=True)
        self.assertEqual(result.status, "dry-run")
        self.assertTrue(result.verification["dry_run"])
        self.assertFalse((self.home / ".codex" / "mcp.json").exists())
        applied = self.manager.apply("codex", scope="user")
        self.assertTrue((self.home / ".codex" / "mcp.json").is_file())
        self.assertEqual(applied.scope, "user")

    def test_symlink_escape_is_rejected_when_supported(self):
        outside = self.root / "outside"
        outside.mkdir()
        link = self.project / ".codex"
        try:
            link.symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("symlink creation unavailable")
        with self.assertRaises(PermissionError):
            self.manager.apply("codex")

    def test_transactions_are_auditable(self):
        result = self.manager.apply("cursor")
        rows = self.manager.transactions(host="cursor")
        self.assertEqual(rows[0]["transaction_id"], result.transaction_id)
        self.assertEqual(rows[0]["status"], "applied")


if __name__ == "__main__":
    unittest.main()
