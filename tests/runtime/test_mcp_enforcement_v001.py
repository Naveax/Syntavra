from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from syntavra_runtime.mcp_server import MCPServer


class MCPEnforcementV001Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.skill = self.root / "skill"
        self.skill.mkdir()
        (self.skill / "SKILL.md").write_text("name: syntavra\n", encoding="utf-8")
        self.server = MCPServer(
            project=self.root,
            state_root=self.root / "state",
            skill_root=self.skill,
            codex_home=self.root / ".codex",
            host="codex",
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    @staticmethod
    def _call(name: str, arguments: dict | None = None, request_id: int = 1) -> dict:
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments or {}},
        }

    def test_product_profile_counts_are_deterministic(self) -> None:
        with patch.dict(os.environ, {"SYNTAVRA_MCP_PROFILE": "minimal"}, clear=False):
            minimal = self.server.exposed_tools()
        with patch.dict(os.environ, {"SYNTAVRA_MCP_PROFILE": "balanced"}, clear=False):
            balanced = self.server.exposed_tools()
        with patch.dict(os.environ, {"SYNTAVRA_MCP_PROFILE": "audit"}, clear=False):
            audit = self.server.exposed_tools()
        self.assertEqual(len(minimal), 8)
        self.assertEqual(len(balanced), 36)
        self.assertEqual(len(audit), len(self.server.tools()))
        self.assertLess(len(minimal), len(balanced))
        self.assertLess(len(balanced), len(audit))

    def test_minimal_profile_alias_is_valid_and_hidden_tool_cannot_be_called(self) -> None:
        with patch.dict(os.environ, {"SYNTAVRA_MCP_PROFILE": "minimal"}, clear=False):
            listed = {row["name"] for row in self.server.exposed_tools()}
            self.assertIn("syntavra.status", listed)
            self.assertNotIn("syntavra.evidence.rotate_key", listed)
            response = self.server.handle(self._call("syntavra.evidence.rotate_key"))
        self.assertEqual(response["error"]["code"], -32001)
        self.assertEqual(response["error"]["data"]["reason"], "tool-not-exposed-by-active-profile")
        self.assertFalse(response["error"]["data"]["allowed"])

    def test_safe_call_returns_route_receipt(self) -> None:
        with patch.dict(os.environ, {"SYNTAVRA_MCP_PROFILE": "minimal"}, clear=False):
            response = self.server.handle(self._call("syntavra.status"))
        self.assertIn("result", response)
        metadata = response["result"]["_meta"]
        self.assertEqual(metadata["syntavra_profile"], "minimal")
        self.assertEqual(metadata["syntavra_risk"], "read-or-plan")
        self.assertGreaterEqual(len(metadata["syntavra_route_receipt"]), 32)

    def test_audit_profile_still_requires_explicit_authorization_for_destructive_tool(self) -> None:
        with patch.dict(os.environ, {"SYNTAVRA_MCP_PROFILE": "audit"}, clear=False):
            listed = {row["name"] for row in self.server.exposed_tools()}
            self.assertIn("syntavra.evidence.rotate_key", listed)
            response = self.server.handle(self._call("syntavra.evidence.rotate_key", {"reencrypt": True}))
        self.assertEqual(response["error"]["code"], -32001)
        self.assertEqual(response["error"]["data"]["reason"], "explicit-user-authorization-required")
        self.assertEqual(response["error"]["data"]["risk"], "destructive")

    def test_safe_state_write_requires_exact_evidence(self) -> None:
        with patch.dict(os.environ, {"SYNTAVRA_MCP_PROFILE": "audit"}, clear=False):
            response = self.server.handle(self._call(
                "syntavra.session.open",
                {"session_id": "denied", "_syntavra_authorization": {"exact_evidence": False}},
            ))
        self.assertEqual(response["error"]["data"]["reason"], "exact-evidence-required")

    def test_unsandboxed_process_stays_disabled_even_when_authorized(self) -> None:
        authorization = {
            "_syntavra_authorization": {
                "user_authorized": True,
                "exact_evidence": True,
                "sandboxed": False,
            },
            "argv": ["python", "-c", "print('must not execute')"],
        }
        with patch.dict(os.environ, {"SYNTAVRA_MCP_PROFILE": "balanced"}, clear=False):
            os.environ.pop("SYNTAVRA_ALLOW_UNSANDBOXED_PROCESS", None)
            response = self.server.handle(self._call("syntavra.process.submit", authorization))
        self.assertEqual(response["error"]["data"]["reason"], "unsandboxed-process-disabled")

    def test_installed_profile_file_is_runtime_default(self) -> None:
        state = self.root / "balanced-state"
        state.mkdir()
        (state / "mcp-profile.json").write_text(
            '{"name":"balanced","max_active_tools":36}', encoding="utf-8"
        )
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SYNTAVRA_MCP_PROFILE", None)
            server = MCPServer(
                project=self.root,
                state_root=state,
                skill_root=self.skill,
                codex_home=self.root / ".codex-balanced",
                host="codex",
            )
            listed = {row["name"] for row in server.exposed_tools()}
        self.assertEqual(server.product_mcp_policy.profile, "balanced")
        self.assertEqual(len(listed), 36)
        self.assertIn("syntavra.process.submit", listed)
        self.assertIn("syntavra.fabric.route", listed)


if __name__ == "__main__":
    unittest.main()
