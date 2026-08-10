from __future__ import annotations

import os
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest.mock import patch

from syntavra_runtime.bootstrap import runtime_health
from syntavra_runtime.engine_entry import CODEX_BRIDGE_COMMAND, _context, main as engine_entry_main
from syntavra_runtime.host_adapters import host_spec
from syntavra_runtime.installer import HostInstaller
from syntavra_runtime.mcp_policy import MCPToolPolicy
from syntavra_runtime.optimization_modes import SavingsLedger, render_statusline
from syntavra_runtime.runtime_paths import default_state_root, discover_project_root
from syntavra_runtime.tool_registry import MINIMAL_TOOLS
from syntavra_runtime.zero_friction import ZeroFrictionManager


PYTHON_BRIDGE_ARGS = ["-m", "syntavra_runtime", CODEX_BRIDGE_COMMAND]


class CodexIntegrationRegressionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.project = self.root / "target-project"
        self.project.mkdir()
        (self.project / ".git").mkdir()
        self.skill = self.root / "skill"
        self.skill.mkdir()
        (self.skill / "SKILL.md").write_text("# Syntavra\n", encoding="utf-8")
        self.home = self.root / "home"
        self.home.mkdir()
        self.state_home = self.root / "state-home"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_auto_project_discovery_uses_active_worktree_not_installation_repo(self) -> None:
        nested = self.project / "src" / "nested"
        nested.mkdir(parents=True)
        resolved = discover_project_root("auto", cwd=nested)
        self.assertEqual(resolved, self.project.resolve())

    def test_default_state_root_is_outside_target_repository(self) -> None:
        with patch.dict(os.environ, {"SYNTAVRA_STATE_HOME": str(self.state_home)}, clear=False):
            state = default_state_root(self.project, namespace="pre-release")
        self.assertNotEqual(state, self.project)
        self.assertNotIn(self.project.resolve(), state.resolve().parents)
        self.assertFalse((self.project / ".syntavra").exists())

    def test_zero_friction_direct_constructor_uses_external_state(self) -> None:
        with patch.dict(os.environ, {"SYNTAVRA_STATE_HOME": str(self.state_home)}, clear=False):
            manager = ZeroFrictionManager(self.project)
        self.assertNotIn(self.project.resolve(), manager.state_root.resolve().parents)
        self.assertFalse((self.project / ".syntavra").exists())
        self.assertTrue(manager.state_root.is_dir())

    def test_engine_entry_injects_one_canonical_project_and_external_state(self) -> None:
        with patch.dict(os.environ, {"SYNTAVRA_STATE_HOME": str(self.state_home)}, clear=False):
            project, state, rest, forwarded = _context([
                "--project",
                str(self.project),
                "status",
                "--doctor",
            ])
        self.assertEqual(project, self.project.resolve())
        self.assertEqual(rest, ["status", "--doctor"])
        self.assertEqual(forwarded.count("--project"), 1)
        self.assertEqual(forwarded.count("--state-root"), 1)
        self.assertNotIn(self.project.resolve(), state.resolve().parents)

    def test_codex_bridge_bypasses_project_canonicalization_before_binding(self) -> None:
        with patch("syntavra_runtime.codex_mcp_bridge.main", return_value=0) as bridge_main:
            with patch("syntavra_runtime.engine_entry._context") as context:
                result = engine_entry_main([CODEX_BRIDGE_COMMAND])
        self.assertEqual(result, 0)
        bridge_main.assert_called_once_with()
        context.assert_not_called()

    def test_runtime_status_exposes_exact_project_root_for_fail_closed_workspace_check(self) -> None:
        state = self.state_home / "health"
        health = runtime_health(
            project=self.project,
            skill_root=self.skill,
            state_root=state,
            codex_home=self.root / ".codex",
            host="codex",
        )
        self.assertEqual(health.details["project_root"], str(self.project.resolve()))
        self.assertEqual(health.details["project_identity"]["project_name"], self.project.name)
        self.assertTrue(health.details["state_outside_project"])

    def test_codex_adapter_metadata_uses_current_official_paths(self) -> None:
        spec = host_spec("codex")
        self.assertEqual(spec.config_path, ".codex/config.toml")
        self.assertEqual(spec.skill_path, ".agents/skills/syntavra")
        self.assertIn(".agents", spec.project_markers)
        self.assertIn(".agents", spec.user_markers)

    def test_user_scope_codex_mcp_is_dynamic_and_uses_workspace_bridge(self) -> None:
        codex_dir = self.home / ".codex"
        codex_dir.mkdir(parents=True)
        (codex_dir / "config.toml").write_text(
            'model = "test-model"\n\n[mcp_servers.other]\ncommand = "other"\n',
            encoding="utf-8",
        )
        with patch.dict(os.environ, {"SYNTAVRA_STATE_HOME": str(self.state_home)}, clear=False):
            installer = HostInstaller(project=self.project, skill_root=self.skill, home=self.home)
            result = installer.install(["codex"], scope="user", dry_run=False)
        self.assertTrue(result["changes"])
        config_path = self.home / ".codex" / "config.toml"
        config = tomllib.loads(config_path.read_text(encoding="utf-8"))
        self.assertEqual(config["model"], "test-model")
        self.assertEqual(config["mcp_servers"]["other"]["command"], "other")
        entry = config["mcp_servers"]["syntavra"]
        self.assertEqual(entry["command"], sys.executable)
        self.assertEqual(entry["args"], PYTHON_BRIDGE_ARGS)
        self.assertNotIn("cwd", entry)
        self.assertNotIn("SYNTAVRA_PROJECT", entry.get("env", {}))
        self.assertTrue((self.home / ".agents" / "skills" / "syntavra" / "SKILL.md").is_file())
        self.assertFalse((self.home / ".codex" / "skills" / "syntavra").exists())
        self.assertFalse((self.project / ".syntavra").exists())

    def test_codex_toml_replaces_existing_syntavra_table_without_duplicate_tables(self) -> None:
        installer = HostInstaller(project=self.project, skill_root=self.skill, home=self.home)
        existing = (
            'model = "test-model"\n\n'
            '[mcp_servers.syntavra]\n'
            'command = "old"\n'
            'args = ["--project", "wrong"]\n\n'
            '[mcp_servers.syntavra.env]\n'
            'SYNTAVRA_PROJECT = "wrong"\n\n'
            '[mcp_servers.other]\n'
            'command = "other"\n'
        )
        rendered = installer._render_codex_toml(existing, scope="user")
        parsed = tomllib.loads(rendered)
        self.assertEqual(parsed["mcp_servers"]["other"]["command"], "other")
        entry = parsed["mcp_servers"]["syntavra"]
        self.assertEqual(entry["args"], PYTHON_BRIDGE_ARGS)
        self.assertNotEqual(entry["command"], "old")
        self.assertNotIn("SYNTAVRA_PROJECT", entry["env"])
        self.assertEqual(rendered.count("[mcp_servers.syntavra]"), 1)

    def test_project_scope_codex_mcp_auto_binds_only_to_that_project(self) -> None:
        with patch.dict(os.environ, {"SYNTAVRA_STATE_HOME": str(self.state_home)}, clear=False):
            installer = HostInstaller(project=self.project, skill_root=self.skill, home=self.home)
            installer.install(["codex"], scope="project", dry_run=False)
        config = tomllib.loads((self.project / ".codex" / "config.toml").read_text(encoding="utf-8"))
        entry = config["mcp_servers"]["syntavra"]
        self.assertEqual(entry["command"], sys.executable)
        self.assertEqual(entry["args"], PYTHON_BRIDGE_ARGS)
        self.assertEqual(entry["cwd"], str(self.project.resolve()))
        self.assertEqual(entry["env"]["SYNTAVRA_PROJECT"], str(self.project.resolve()))
        self.assertTrue((self.project / ".agents" / "skills" / "syntavra" / "SKILL.md").is_file())

    def test_minimal_codex_surface_contains_zero_poll_broker_tools(self) -> None:
        self.assertIn("syntavra.process.submit", MINIMAL_TOOLS)
        self.assertIn("syntavra.process.completions", MINIMAL_TOOLS)

    def test_explicitly_authorized_broker_call_no_longer_needs_process_env_switch(self) -> None:
        policy = MCPToolPolicy("minimal")
        with patch.dict(os.environ, {"SYNTAVRA_ALLOW_UNSANDBOXED_PROCESS": ""}, clear=False):
            decision = policy.authorize(
                "syntavra.process.submit",
                {
                    "argv": ["pytest", "-q"],
                    "_syntavra_authorization": {
                        "user_authorized": True,
                        "exact_evidence": True,
                    },
                },
                exposed_tools=MINIMAL_TOOLS,
            )
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.reason, "authorized-risky-operation")

    def test_local_savings_ledger_cannot_claim_provider_session_savings(self) -> None:
        state = self.state_home / "ledger"
        ledger = SavingsLedger(state)
        ledger.record(source="tool-output", original_tokens=1000, visible_tokens=250)
        summary = ledger.summary()
        self.assertEqual(summary["saved_tokens"], 750)
        self.assertEqual(summary["measurement_basis"], "LOCAL_MODEL_VISIBLE_ESTIMATE")
        self.assertFalse(summary["net_provider_savings_proven"])
        self.assertIn("paired provider-observed receipts", summary["claim_boundary"])
        self.assertNotIn("$", render_statusline(state))


if __name__ == "__main__":
    unittest.main()
