from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from syntavra_runtime.mcp_server import MCPServer


class CompetitiveMCPV4Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.skill = self.root / "skills" / "syntavra"
        self.skill.mkdir(parents=True)
        (self.skill / "SKILL.md").write_text("name: syntavra\n", encoding="utf-8")
        (self.root / "sample.py").write_text(
            "class Demo:\n    def run(self):\n        return 7\n", encoding="utf-8"
        )
        self.server = MCPServer(
            project=self.root,
            state_root=self.root / ".state",
            skill_root=self.skill,
            codex_home=self.root / ".codex",
            host="codex",
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_full_profile_exposes_combined_competitive_surface(self):
        with patch.dict(os.environ, {"SYNTAVRA_MCP_PROFILE": "full"}, clear=False):
            names = {row["name"] for row in self.server.exposed_tools()}
        required = {
            "syntavra.inspect.source",
            "syntavra.sandbox.batch",
            "syntavra.session.compact",
            "syntavra.session.verify",
            "syntavra.fabric.profile",
            "syntavra.fabric.route",
            "syntavra.fabric.compact",
            "syntavra.fabric.cache_align",
            "syntavra.fabric.platform_plan",
            "syntavra.fabric.insights",
        }
        self.assertTrue(required.issubset(names))

    def test_optimized_profile_reduces_manifest_but_keeps_core(self):
        with patch.dict(os.environ, {"SYNTAVRA_MCP_PROFILE": "optimized"}, clear=False):
            selected = self.server.exposed_tools()
        self.assertLess(len(selected), len(self.server.tools()))
        names = {row["name"] for row in selected}
        self.assertIn("syntavra.inspect.impact", names)
        self.assertIn("syntavra.process.submit", names)
        self.assertIn("syntavra.fabric.route", names)

    def test_fabric_calls_route_compact_cache_and_platform(self):
        route = self.server.call_tool("syntavra.fabric.route", {"command": ["pytest", "-q"]})
        self.assertEqual(route["mode"], "background-replace")
        compact = self.server.call_tool(
            "syntavra.fabric.compact",
            {"command": ["pytest", "-q"], "stdout": "1 passed in 0.1s\n", "budget_bytes": 512},
        )
        self.assertEqual(compact["family"], "test")
        aligned = self.server.call_tool(
            "syntavra.fabric.cache_align",
            {"messages": [{"role": "system", "content": "stable"}, {"role": "user", "content": "tail"}]},
        )
        self.assertEqual(aligned["stable_message_count"], 1)
        plan = self.server.call_tool("syntavra.fabric.platform_plan", {"host": "claude-code"})
        self.assertTrue(plan["enforced"])

    def test_exact_range_and_session_dag_tools(self):
        ranged = self.server.call_tool(
            "syntavra.inspect.range", {"path": "sample.py", "start_line": 1, "end_line": 3}
        )
        self.assertIn("class Demo", ranged["text"])
        session = self.server.call_tool("syntavra.session.open", {"session_id": "mcp-v4"})
        for index in range(8):
            self.server.call_tool(
                "syntavra.session.append",
                {"session_id": session["session_id"], "event_type": "tool", "payload": {"index": index}},
            )
        compact = self.server.call_tool(
            "syntavra.session.compact", {"session_id": session["session_id"], "leaf_size": 2, "fanout": 2}
        )
        expanded = self.server.call_tool("syntavra.session.expand", {"summary_id": compact["summary_id"]})
        self.assertEqual(expanded["coverage"], 8)
        verified = self.server.call_tool("syntavra.session.verify", {"session_id": session["session_id"]})
        self.assertTrue(verified["ok"])


if __name__ == "__main__":
    unittest.main()
