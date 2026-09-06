from __future__ import annotations

import tempfile
import tomllib
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from syntavra_runtime.engine_entry import CODEX_BRIDGE_COMMAND
from syntavra_runtime.host_installation import InstallationResult
from syntavra_runtime.zero_friction import ZeroFrictionManager


class ZeroFrictionHostSetupV001Tests(unittest.TestCase):
    def test_detected_codex_host_is_installed_and_verified(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            (project / ".codex").mkdir()
            manager = ZeroFrictionManager(project)
            plan = manager.install_plan(profile="minimal")
            self.assertIn("codex", plan.detected_hosts)
            self.assertIn("codex", plan.installable_hosts)

            result = manager.install(dry_run=False, profile="minimal")
            self.assertTrue(result["ok"], result)
            self.assertEqual(len(result["host_results"]), 1)
            self.assertTrue(result["host_results"][0]["verification"]["ok"])

            config_path = project / ".codex" / "config.toml"
            skill_path = project / ".agents" / "skills" / "syntavra" / "SKILL.md"
            self.assertTrue(config_path.is_file())
            self.assertTrue(skill_path.is_file())
            self.assertFalse((project / ".codex" / "mcp.json").exists())
            self.assertFalse((project / ".codex" / "skills" / "syntavra").exists())

            config = tomllib.loads(config_path.read_text(encoding="utf-8"))
            entry = config["mcp_servers"]["syntavra"]
            self.assertEqual(entry["command"], "syntavra")
            self.assertEqual(entry["args"], [CODEX_BRIDGE_COMMAND])
            self.assertEqual(entry["cwd"], str(project.resolve()))
            self.assertEqual(entry["env"]["SYNTAVRA_PROJECT"], str(project.resolve()))

            self.assertNotIn(project.resolve(), manager.state_root.resolve().parents)
            doctor = manager.doctor()
            self.assertTrue(doctor["ok"], doctor)
            self.assertEqual(doctor["configured_hosts"], ["codex"])
            self.assertTrue(doctor["host_verification"][0]["ok"])

    def test_invalid_existing_codex_toml_fails_without_false_install_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            codex = project / ".codex"
            codex.mkdir()
            (codex / "config.toml").write_text('[mcp_servers.syntavra\ncommand = "broken"\n', encoding="utf-8")
            manager = ZeroFrictionManager(project)
            result = manager.install(dry_run=False)
            self.assertFalse(result["ok"])
            self.assertIn("TOML", result["error"])
            self.assertFalse((manager.state_root / "install-receipt.json").exists())

    def test_empty_project_does_not_claim_host_installation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            manager = ZeroFrictionManager(project)
            result = manager.install(dry_run=False)
            self.assertTrue(result["ok"], result)
            self.assertEqual(result["host_results"], [])
            self.assertNotIn(project.resolve(), manager.state_root.resolve().parents)
            stats = manager.stats()
            self.assertEqual(stats["onboarding"]["host_installations"], 0)

    def test_post_host_failure_reports_only_successful_rollbacks(self) -> None:
        class RollbackManager:
            def rollback(self, transaction_id: str) -> SimpleNamespace:
                if transaction_id == "tx-fail":
                    raise OSError("rollback blocked")
                return SimpleNamespace(status="rolled-back")

        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            manager = ZeroFrictionManager(project)
            host_results = [{"host": "codex", "verification": {"ok": True}}]
            with (
                patch.object(
                    manager,
                    "_host_installations",
                    return_value=(host_results, ["tx-ok", "tx-fail"]),
                ),
                patch.object(manager, "_host_manager", return_value=RollbackManager()),
                patch(
                    "syntavra_runtime.zero_friction.atomic_write_json",
                    side_effect=RuntimeError("config write failed"),
                ),
            ):
                result = manager.install(dry_run=False)

            self.assertFalse(result["ok"])
            self.assertIn("config write failed", result["error"])
            self.assertEqual(
                result["rollback_attempted_transactions"],
                ["tx-fail", "tx-ok"],
            )
            self.assertEqual(result["rolled_back_transactions"], ["tx-ok"])
            self.assertFalse(result["rollback_ok"])
            self.assertEqual(
                result["rollback_failures"],
                [
                    {
                        "transaction_id": "tx-fail",
                        "error": "OSError: rollback blocked",
                    }
                ],
            )

    def test_partial_host_apply_surfaces_rollback_failure(self) -> None:
        class PartialApplyManager:
            def apply(self, host: str, *, scope: str, dry_run: bool) -> InstallationResult:
                if host == "codex":
                    return InstallationResult(
                        transaction_id="tx-codex",
                        host=host,
                        scope=scope,
                        root="/tmp/project",
                        status="applied",
                        changes=(),
                        verification={"ok": True},
                        created_at=0.0,
                    )
                raise RuntimeError("second host apply failed")

            def rollback(self, transaction_id: str) -> SimpleNamespace:
                raise OSError(f"rollback blocked for {transaction_id}")

        with tempfile.TemporaryDirectory() as directory:
            manager = ZeroFrictionManager(Path(directory))
            with patch.object(manager, "_host_manager", return_value=PartialApplyManager()):
                with self.assertRaisesRegex(RuntimeError, "rollback incomplete") as caught:
                    manager._host_installations(("codex", "claude-code"), dry_run=False)

            message = str(caught.exception)
            self.assertIn("second host apply failed", message)
            self.assertIn("tx-codex", message)
            self.assertIn("rollback blocked for tx-codex", message)


if __name__ == "__main__":
    unittest.main()
