from __future__ import annotations

import json
import os
import shutil
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest import mock

from syntavra_runtime.engine_entry import CODEX_BRIDGE_COMMAND
from syntavra_runtime.host_installation import HostInstallationManager, HostInstallationRollbackError


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

    def test_codex_apply_preserves_toml_verifies_and_rolls_back(self):
        config = self.project / ".codex" / "config.toml"
        config.parent.mkdir(parents=True)
        original = 'model = "test-model"\n\n[mcp_servers.existing]\ncommand = "existing"\n'
        config.write_text(original, encoding="utf-8")

        result = self.manager.apply("codex")
        self.assertEqual(result.status, "applied")
        merged = tomllib.loads(config.read_text(encoding="utf-8"))
        self.assertEqual(merged["model"], "test-model")
        self.assertEqual(merged["mcp_servers"]["existing"]["command"], "existing")
        syntavra = merged["mcp_servers"]["syntavra"]
        self.assertEqual(syntavra["command"], "syntavra")
        self.assertEqual(syntavra["args"], [CODEX_BRIDGE_COMMAND])
        self.assertEqual(syntavra["cwd"], str(self.project.resolve()))
        self.assertEqual(syntavra["env"]["SYNTAVRA_PROJECT"], str(self.project.resolve()))
        installed_skill = self.project / ".agents" / "skills" / "syntavra"
        self.assertTrue((installed_skill / "SKILL.md").is_file())
        self.assertTrue((installed_skill / "REFERENCE.md").is_file())
        self.assertTrue(self.manager.verify("codex")["ok"])

        rolled = self.manager.rollback(result.transaction_id)
        self.assertEqual(rolled.status, "rolled-back")
        self.assertEqual(config.read_text(encoding="utf-8"), original)
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
        self.assertEqual(text.count("SYNTAVRA:BEGIN"), 1)
        second = self.manager.apply("aider")
        text = agents.read_text(encoding="utf-8")
        self.assertEqual(text.count("SYNTAVRA:BEGIN"), 1)
        self.manager.rollback(second.transaction_id)
        self.assertEqual(agents.read_text(encoding="utf-8").count("SYNTAVRA:BEGIN"), 1)
        self.manager.rollback(first.transaction_id)
        self.assertEqual(agents.read_text(encoding="utf-8"), "# User instructions\n\nKeep this text.\n")

    def test_user_scope_is_dynamic_and_dry_run_does_not_write(self):
        result = self.manager.apply("codex", scope="user", dry_run=True)
        self.assertEqual(result.status, "dry-run")
        self.assertTrue(result.verification["dry_run"])
        self.assertFalse((self.home / ".codex" / "config.toml").exists())
        applied = self.manager.apply("codex", scope="user")
        config = self.home / ".codex" / "config.toml"
        self.assertTrue(config.is_file())
        entry = tomllib.loads(config.read_text(encoding="utf-8"))["mcp_servers"]["syntavra"]
        self.assertEqual(entry["command"], "syntavra")
        self.assertEqual(entry["args"], [CODEX_BRIDGE_COMMAND])
        self.assertNotIn("cwd", entry)
        self.assertNotIn("SYNTAVRA_PROJECT", entry.get("env", {}))
        self.assertNotIn(str(self.project.resolve()), entry.get("args", []))
        self.assertTrue((self.home / ".agents" / "skills" / "syntavra" / "SKILL.md").is_file())
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

    def test_explicit_rollback_staging_failure_keeps_live_target_and_backup(self):
        config = self.project / ".codex" / "config.toml"
        config.parent.mkdir(parents=True)
        original = 'model = "before"\n'
        config.write_text(original, encoding="utf-8")
        result = self.manager.apply("codex")
        live_bytes = config.read_bytes()
        config_change = next(change for change in result.changes if change.path == ".codex/config.toml")
        backup = Path(config_change.backup_path)
        backup_bytes = backup.read_bytes()
        real_copy2 = shutil.copy2

        def fail_backup_stage(source, destination, *args, **kwargs):
            if Path(source).resolve(strict=False) == backup.resolve(strict=False):
                raise OSError("forced backup staging failure")
            return real_copy2(source, destination, *args, **kwargs)

        with mock.patch("syntavra_runtime.host_installation.shutil.copy2", side_effect=fail_backup_stage):
            with self.assertRaises(HostInstallationRollbackError) as caught:
                self.manager.rollback(result.transaction_id)

        self.assertIsNone(caught.exception.apply_error)
        self.assertIn("forced backup staging failure", str(caught.exception.rollback_error))
        self.assertEqual(config.read_bytes(), live_bytes)
        self.assertEqual(backup.read_bytes(), backup_bytes)
        self.assertEqual(self.manager.transactions(host="codex")[0]["status"], "applied")

        rolled = self.manager.rollback(result.transaction_id)
        self.assertEqual(rolled.status, "rolled-back")
        self.assertEqual(config.read_text(encoding="utf-8"), original)

    def test_apply_rollback_failure_reports_both_errors_and_preserves_backup(self):
        config = self.project / ".codex" / "config.toml"
        config.parent.mkdir(parents=True)
        original = 'model = "before"\n'
        config.write_text(original, encoding="utf-8")
        real_copy2 = shutil.copy2

        def fail_backup_stage(source, destination, *args, **kwargs):
            source_path = Path(source)
            if "host-installations" in source_path.parts and "backup" in source_path.parts:
                raise OSError("forced automatic rollback staging failure")
            return real_copy2(source, destination, *args, **kwargs)

        with mock.patch.object(self.manager, "verify", return_value={"ok": False, "reasons": ["forced verify failure"]}):
            with mock.patch("syntavra_runtime.host_installation.shutil.copy2", side_effect=fail_backup_stage):
                with self.assertRaises(HostInstallationRollbackError) as caught:
                    self.manager.apply("codex")

        error = caught.exception
        self.assertIsInstance(error.apply_error, RuntimeError)
        self.assertIn("forced verify failure", str(error.apply_error))
        self.assertIn("forced automatic rollback staging failure", str(error.rollback_error))
        self.assertTrue(config.is_file())
        self.assertNotEqual(config.read_text(encoding="utf-8"), original)
        transaction = self.manager.storage / error.transaction_id
        backups = list((transaction / "backup").rglob("config.toml"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(encoding="utf-8"), original)

    def test_directory_install_failure_restores_preexisting_live_directory(self):
        installed_skill = self.project / ".agents" / "skills" / "syntavra"
        installed_skill.mkdir(parents=True)
        marker = installed_skill / "USER.txt"
        marker.write_text("keep me\n", encoding="utf-8")
        real_replace = os.replace

        def fail_staged_directory_install(source, destination):
            source_path = Path(source)
            destination_path = Path(destination)
            if (
                destination_path == installed_skill
                and source_path.name.startswith(".syntavra.syntavra-stage-")
            ):
                raise OSError("forced staged directory install failure")
            return real_replace(source, destination)

        with mock.patch("syntavra_runtime.host_installation.os.replace", side_effect=fail_staged_directory_install):
            with self.assertRaises(OSError):
                self.manager.apply("codex")

        self.assertTrue(marker.is_file())
        self.assertEqual(marker.read_text(encoding="utf-8"), "keep me\n")
        self.assertFalse((self.project / ".codex" / "config.toml").exists())
        self.assertFalse(list(installed_skill.parent.glob(".syntavra.syntavra-safety-*")))


if __name__ == "__main__":
    unittest.main()
