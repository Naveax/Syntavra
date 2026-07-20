from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from signalcore_runtime.mcp_server import MCPServer


class ProviderMCPV4Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.skill = self.root / "skills" / "signal-core"
        self.skill.mkdir(parents=True)
        (self.skill / "SKILL.md").write_text("name: signal-core\n", encoding="utf-8")
        self.server = MCPServer(
            project=self.root,
            state_root=self.root / ".state",
            skill_root=self.skill,
            codex_home=self.root / ".codex",
            host="codex",
        )

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def request() -> dict:
        return {
            "model": "gpt-test",
            "messages": [
                {"role": "system", "content": "stable"},
                {"role": "user", "content": "question"},
            ],
            "temperature": 0,
        }

    def test_provider_tools_are_visible_in_optimized_profile(self):
        with patch.dict(os.environ, {"SIGNALCORE_MCP_PROFILE": "optimized"}, clear=False):
            names = {row["name"] for row in self.server.exposed_tools()}
        self.assertIn("signalcore.provider.prepare", names)
        self.assertIn("signalcore.provider.capture", names)
        self.assertIn("signalcore.provider.replay", names)
        self.assertIn("signalcore.provider.verify", names)

    def test_provider_prepare_capture_and_replay(self):
        plan = self.server.call_tool(
            "signalcore.provider.prepare",
            {"provider": "openai", "request": self.request()},
        )
        self.assertIn("prompt_cache_key", plan["prepared_request"])
        capture = self.server.call_tool(
            "signalcore.provider.capture",
            {
                "plan": plan,
                "response": {
                    "id": "resp-mcp-provider",
                    "output_text": "answer",
                    "usage": {"input_tokens": 10, "output_tokens": 2},
                },
            },
        )
        self.assertTrue(capture["replay_stored"])
        repeated = self.server.call_tool(
            "signalcore.provider.prepare",
            {"provider": "openai", "request": self.request()},
        )
        self.assertTrue(repeated["replay_hit"])
        replay = self.server.call_tool(
            "signalcore.provider.replay", {"cache_key": plan["cache_key"]}
        )
        self.assertEqual(replay["output_text"], "answer")
        self.assertTrue(self.server.call_tool("signalcore.provider.verify", {})["ok"])
        self.assertGreaterEqual(self.server.call_tool("signalcore.provider.stats", {})["requests"], 2)


if __name__ == "__main__":
    unittest.main()
