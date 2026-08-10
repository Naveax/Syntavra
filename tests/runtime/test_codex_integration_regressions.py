from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from syntavra_runtime.engine_entry import _context
from syntavra_runtime.installer import HostInstaller
from syntavra_runtime.mcp_policy import MCPToolPolicy
from syntavra_runtime.optimization_modes import SavingsLedger, render_statusline
from syntavra_runtime.runtime_paths import default_state_root, discover_project_root
from syntavra_runtime.tool_registry import MINIMAL_TOOLS


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

    def test_user_scope_codex_mcp_is_dynamic_and_does_not_pin_syntavra_repo(self) -> None:
        with patch.dict(os.environ, {"SYNTAVRA_STATE_HOME": str(self.state_home)}, clear=False):
            installer = HostInstaller(
                project=self.project,
                skill_root=self.skill,
                home=self.home,
            )
            result = installer.install(["codex"], scope="user", dry_run=False)
        self.assertTrue(result["changes"])
        config = json.loads((self.home / ".codex" / "mcp.json").read_text(encoding="utf-8"))
        entry = config["mcpServers"]["syntavra"]
        self.assertNotIn("cwd", entry)
        self.assertNotIn("SYNTAVRA_PROJECT", entry.get("env", {}))
        self.assertNotIn(str(self.project), entry.get("args", []))
        self.assertEqual(config["syntavra"]["project"], "auto")
        self.assertFalse((self.project / ".syntavra").exists())

    def test_project_scope_codex_mcp_may_bind_only_to_that_project(self) -> None:
        with patch.dict(os.environ, {"SYNTAVRA_STATE_HOME": str(self.state_home)}, clear=False):
            installer = HostInstaller(
                project=self.project,
                skill_root=self.skill,
                home=self.home,
            )
            entry = installer._mcp_entry(scope="project")
        self.assertEqual(entry["cwd"], str(self.project.resolve()))
        self.assertEqual(entry["env"]["SYNTAVRA_PROJECT"], str(self.project.resolve()))
        self.assertIn(str(self.project.resolve()), entry["args"])

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
