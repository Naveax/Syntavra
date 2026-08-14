from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from syntavra_runtime.codex_mcp_bridge import CodexWorkspaceMCPBridge


class CodexMCPBridgeV001Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.project = self.root / "target"
        self.project.mkdir()
        (self.project / ".git").mkdir()
        self.nested = self.project / "src" / "nested"
        self.nested.mkdir(parents=True)
        (self.project / "README.md").write_text("target repository\n", encoding="utf-8")
        self.skill = self.root / "skill"
        self.skill.mkdir()
        (self.skill / "SKILL.md").write_text("# Syntavra\n", encoding="utf-8")
        self.codex_home = self.root / ".codex"
        self.state_home = self.root / "state"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _bridge(self) -> CodexWorkspaceMCPBridge:
        return CodexWorkspaceMCPBridge(skill_root=self.skill, codex_home=self.codex_home)

    @staticmethod
    def _call(name: str, arguments: dict | None = None, request_id: int = 1) -> dict:
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments or {}},
        }

    def test_user_scope_starts_unbound_and_blocks_repository_tools(self) -> None:
        with patch.dict(os.environ, {"SYNTAVRA_STATE_HOME": str(self.state_home)}, clear=False):
            os.environ.pop("SYNTAVRA_PROJECT", None)
            os.environ.pop("SYNTAVRA_STATE_ROOT", None)
            bridge = self._bridge()
            listed = bridge.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
            denied = bridge.handle(self._call("syntavra.inspect.map", {"query": "README"}, request_id=2))

        self.assertIsNotNone(listed)
        names = {row["name"] for row in listed["result"]["tools"]}
        self.assertIn("syntavra.project.bind", names)
        self.assertIn("syntavra.inspect.map", names)
        binding = listed["result"]["_meta"]["syntavra"]["workspace_binding"]
        self.assertTrue(binding["required"])
        self.assertFalse(binding["bound"])
        self.assertEqual(denied["error"]["code"], -32002)
        self.assertFalse(denied["error"]["data"]["bound"])
        self.assertIsNone(bridge.project)

    def test_bind_nested_path_resolves_canonical_git_root_and_external_state(self) -> None:
        with patch.dict(os.environ, {"SYNTAVRA_STATE_HOME": str(self.state_home)}, clear=False):
            os.environ.pop("SYNTAVRA_PROJECT", None)
            os.environ.pop("SYNTAVRA_STATE_ROOT", None)
            bridge = self._bridge()
            bound = bridge.handle(self._call(
                "syntavra.project.bind",
                {"project_root": str(self.nested)},
                request_id=3,
            ))
            listed = bridge.handle({"jsonrpc": "2.0", "id": 4, "method": "tools/list", "params": {}})

        self.assertEqual(bound["result"]["project_root"], str(self.project.resolve()))
        self.assertTrue(bound["result"]["state_outside_project"])
        self.assertEqual(bridge.project, self.project.resolve())
        self.assertIsNotNone(bridge.state_root)
        self.assertNotIn(self.project.resolve(), bridge.state_root.resolve().parents)
        binding = listed["result"]["_meta"]["syntavra"]["workspace_binding"]
        self.assertFalse(binding["required"])
        self.assertTrue(binding["bound"])
        self.assertEqual(binding["project_root"], str(self.project.resolve()))

    def test_invalid_non_git_workspace_is_rejected_without_fallback(self) -> None:
        outside = self.root / "not-a-repo"
        outside.mkdir()
        with patch.dict(os.environ, {"SYNTAVRA_STATE_HOME": str(self.state_home)}, clear=False):
            os.environ.pop("SYNTAVRA_PROJECT", None)
            bridge = self._bridge()
            response = bridge.handle(self._call(
                "syntavra.project.bind",
                {"project_root": str(outside)},
                request_id=5,
            ))
        self.assertEqual(response["error"]["code"], -32003)
        self.assertFalse(response["error"]["data"]["bound"])
        self.assertIsNone(bridge.project)

    def test_project_scope_environment_auto_binds_exact_project(self) -> None:
        with patch.dict(
            os.environ,
            {
                "SYNTAVRA_PROJECT": str(self.project),
                "SYNTAVRA_STATE_HOME": str(self.state_home),
            },
            clear=False,
        ):
            os.environ.pop("SYNTAVRA_STATE_ROOT", None)
            bridge = self._bridge()
            listed = bridge.handle({"jsonrpc": "2.0", "id": 6, "method": "tools/list", "params": {}})
        self.assertEqual(bridge.project, self.project.resolve())
        binding = listed["result"]["_meta"]["syntavra"]["workspace_binding"]
        self.assertTrue(binding["bound"])
        self.assertFalse(binding["required"])

    def test_bootstrap_server_is_reused_until_binding(self) -> None:
        with patch.dict(os.environ, {"SYNTAVRA_STATE_HOME": str(self.state_home)}, clear=False):
            os.environ.pop("SYNTAVRA_PROJECT", None)
            bridge = self._bridge()
            first = bridge._bootstrap_server()
            second = bridge._bootstrap_server()
            self.assertIs(first, second)
            bridge.bind(self.project)
            self.assertIsNone(bridge._bootstrap)
            self.assertIs(bridge._bootstrap_server(), bridge.server)


if __name__ == "__main__":
    unittest.main()