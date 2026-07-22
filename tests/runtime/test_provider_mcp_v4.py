from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from syntavra_runtime.mcp_server import MCPServer


class ProviderMCPV4Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.skill = self.root / "skills" / "syntavra"
        self.skill.mkdir(parents=True)
        (self.skill / "SKILL.md").write_text("name: syntavra\n", encoding="utf-8")
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
        with patch.dict(os.environ, {"SYNTAVRA_MCP_PROFILE": "optimized"}, clear=False):
            names = {row["name"] for row in self.server.exposed_tools()}
        self.assertIn("syntavra.provider.prepare", names)
        self.assertIn("syntavra.provider.capture", names)
        self.assertIn("syntavra.provider.replay", names)
        self.assertIn("syntavra.provider.verify", names)

    def test_provider_prepare_capture_and_replay(self):
        plan = self.server.call_tool(
            "syntavra.provider.prepare",
            {"provider": "openai", "request": self.request()},
        )
        self.assertIn("prompt_cache_key", plan["prepared_request"])
        capture = self.server.call_tool(
            "syntavra.provider.capture",
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
            "syntavra.provider.prepare",
            {"provider": "openai", "request": self.request()},
        )
        self.assertTrue(repeated["replay_hit"])
        replay = self.server.call_tool(
            "syntavra.provider.replay", {"cache_key": plan["cache_key"]}
        )
        self.assertEqual(replay["output_text"], "answer")
        self.assertTrue(self.server.call_tool("syntavra.provider.verify", {})["ok"])
        self.assertGreaterEqual(self.server.call_tool("syntavra.provider.stats", {})["requests"], 2)


if __name__ == "__main__":
    unittest.main()
