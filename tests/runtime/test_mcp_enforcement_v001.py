from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from signalcore_runtime.mcp_server import MCPServer


class MCPEnforcementV001Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.skill = self.root / "skill"
        self.skill.mkdir()
        (self.skill / "SKILL.md").write_text("name: signal-core\n", encoding="utf-8")
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

    def test_minimal_profile_alias_is_valid_and_hidden_tool_cannot_be_called(self) -> None:
        with patch.dict(os.environ, {"SIGNALCORE_MCP_PROFILE": "minimal"}, clear=False):
            listed = {row["name"] for row in self.server.exposed_tools()}
            self.assertIn("signalcore.status", listed)
            self.assertNotIn("signalcore.evidence.rotate_key", listed)
            response = self.server.handle(self._call("signalcore.evidence.rotate_key"))
        self.assertEqual(response["error"]["code"], -32001)
        self.assertEqual(response["error"]["data"]["reason"], "tool-not-exposed-by-active-profile")
        self.assertFalse(response["error"]["data"]["allowed"])

    def test_safe_call_returns_route_receipt(self) -> None:
        with patch.dict(os.environ, {"SIGNALCORE_MCP_PROFILE": "minimal"}, clear=False):
            response = self.server.handle(self._call("signalcore.status"))
        self.assertIn("result", response)
        metadata = response["result"]["_meta"]
        self.assertEqual(metadata["signalcore_profile"], "minimal")
        self.assertEqual(metadata["signalcore_risk"], "read-or-plan")
        self.assertGreaterEqual(len(metadata["signalcore_route_receipt"]), 32)

    def test_audit_profile_still_requires_explicit_authorization_for_destructive_tool(self) -> None:
        with patch.dict(os.environ, {"SIGNALCORE_MCP_PROFILE": "audit"}, clear=False):
            listed = {row["name"] for row in self.server.exposed_tools()}
            self.assertIn("signalcore.evidence.rotate_key", listed)
            response = self.server.handle(self._call("signalcore.evidence.rotate_key", {"reencrypt": True}))
        self.assertEqual(response["error"]["code"], -32001)
        self.assertEqual(response["error"]["data"]["reason"], "explicit-user-authorization-required")
        self.assertEqual(response["error"]["data"]["risk"], "destructive")

    def test_safe_state_write_requires_exact_evidence(self) -> None:
        with patch.dict(os.environ, {"SIGNALCORE_MCP_PROFILE": "audit"}, clear=False):
            response = self.server.handle(self._call(
                "signalcore.session.open",
                {"session_id": "denied", "_signalcore_authorization": {"exact_evidence": False}},
            ))
        self.assertEqual(response["error"]["data"]["reason"], "exact-evidence-required")

    def test_unsandboxed_process_stays_disabled_even_when_authorized(self) -> None:
        authorization = {
            "_signalcore_authorization": {
                "user_authorized": True,
                "exact_evidence": True,
                "sandboxed": False,
            },
            "argv": ["python", "-c", "print('must not execute')"],
        }
        with patch.dict(os.environ, {"SIGNALCORE_MCP_PROFILE": "balanced"}, clear=False):
            os.environ.pop("SIGNALCORE_ALLOW_UNSANDBOXED_PROCESS", None)
            response = self.server.handle(self._call("signalcore.process.submit", authorization))
        self.assertEqual(response["error"]["data"]["reason"], "unsandboxed-process-disabled")

    def test_installed_profile_file_is_runtime_default(self) -> None:
        state = self.root / "balanced-state"
        state.mkdir()
        (state / "mcp-profile.json").write_text(
            '{"name":"balanced","max_active_tools":8}', encoding="utf-8"
        )
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SIGNALCORE_MCP_PROFILE", None)
            server = MCPServer(
                project=self.root,
                state_root=state,
                skill_root=self.skill,
                codex_home=self.root / ".codex-balanced",
                host="codex",
            )
            listed = {row["name"] for row in server.exposed_tools()}
        self.assertEqual(server.product_mcp_policy.profile, "balanced")
        self.assertIn("signalcore.process.submit", listed)
        self.assertIn("signalcore.fabric.route", listed)


if __name__ == "__main__":
    unittest.main()
