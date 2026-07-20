from __future__ import annotations

import io
import json
import sys
import tempfile
import tracemalloc
import unittest
from pathlib import Path

from signalcore_runtime.context_governor import pack_context
from signalcore_runtime.evidence import EvidenceStore
from signalcore_runtime.history import ImmutableHistory
from signalcore_runtime.hooks import HookEngine
from signalcore_runtime.mcp_server import MCPServer
from signalcore_runtime.models import ContextItem
from signalcore_runtime.output_firewall import summarize
from signalcore_runtime.process_broker import ProcessBroker
from signalcore_runtime.structural import StructuralIndex


class RuntimeV02CoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_streaming_firewall_bounds_memory_and_preserves_final_summary(self):
        out = self.root / "out.log"
        err = self.root / "err.log"
        with out.open("w", encoding="utf-8") as handle:
            for index in range(500_000):
                handle.write(f"test_case_{index} ... ok\n")
            handle.write("500000 passed in 123.4s\n")
        err.write_text("", encoding="utf-8")
        store = EvidenceStore(self.root / "evidence", project_id="p")
        tracemalloc.start()
        result = summarize(
            ("python", "-m", "unittest"),
            stdout_path=out,
            stderr_path=err,
            exit_code=0,
            duration_seconds=123.4,
            evidence=store,
        )
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        self.assertIn("500000 passed", result.summary)
        self.assertLessEqual(result.visible_bytes, 4300)
        self.assertLess(peak, 8 * 1024 * 1024)
        self.assertGreater(result.dropped_lines, 490_000)
        self.assertEqual(store.describe(result.evidence_handle)["bytes"], out.stat().st_size + 1)

    def test_completion_cursor_and_background_scope(self):
        store = EvidenceStore(self.root / "evidence", project_id="project-scope")
        broker = ProcessBroker(self.root / "broker", store, heartbeat_interval=0.05)
        result = broker.run((sys.executable, "-c", "print('done')"), cwd=self.root, timeout=5)
        first = broker.drain_completions(after=0)
        self.assertEqual(len(first["events"]), 1)
        self.assertEqual(first["events"][0].job_id, result.job_id)
        self.assertTrue(store.verify(first["events"][0].evidence_handle))
        second = broker.drain_completions(after=first["cursor"])
        self.assertEqual(second["events"], [])

    def test_context_pack_mandatory_dependency_and_stable_prefix(self):
        items = [
            ContextItem("task", "task", "repair auth", 20, 10, mandatory=True, stable=True),
            ContextItem("definition", "evidence", "def auth", 30, 9, stable=True),
            ContextItem("caller", "impact", "caller auth", 20, 8, dependencies=("definition",)),
            ContextItem("noise", "log", "success noise", 100, 1),
        ]
        pack = pack_context(items, budget=80, mandatory_roles=("task", "impact"))
        self.assertTrue(pack.mandatory_satisfied)
        self.assertEqual(set(pack.selected_ids), {"task", "definition", "caller"})
        self.assertNotIn("noise", pack.selected_ids)
        self.assertTrue(pack.stable_prefix_hash)

    def test_history_compacts_to_single_exact_root(self):
        history = ImmutableHistory(self.root / "history.sqlite3", session_id="s")
        for index in range(256):
            history.append("tool", {"index": index})
        root = history.compact(leaf_size=8, fanout=4)
        self.assertIsNotNone(root)
        expanded = history.expand_summary(root)
        self.assertEqual(expanded["coverage"], 256)
        self.assertEqual(expanded["events"][0]["payload"], {"index": 0})
        self.assertEqual(expanded["events"][-1]["payload"], {"index": 255})

    def test_hook_blocks_destructive_and_rewrites_long_command(self):
        engine = HookEngine(project_root=self.root)
        blocked = engine.pre_tool({"tool": "shell", "command": ["git", "reset", "--hard"], "cwd": str(self.root)})
        self.assertFalse(blocked.allowed)
        rewritten = engine.pre_tool({"tool": "shell", "command": ["pytest", "-q"], "cwd": str(self.root)})
        self.assertTrue(rewritten.allowed)
        self.assertEqual(rewritten.mode, "replace")
        self.assertIn("--background", rewritten.replacement["argv"])

    def test_mcp_initialize_and_tool_list(self):
        skill = self.root / "skills" / "signal-core"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text("name: signal-core\n")
        server = MCPServer(
            project=self.root,
            state_root=self.root / ".state",
            skill_root=skill,
            codex_home=self.root / ".codex",
            host="codex",
        )
        init = server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        self.assertEqual(init["result"]["serverInfo"]["version"], "0.3.0")
        tools = server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        names = {row["name"] for row in tools["result"]["tools"]}
        self.assertIn("signalcore.process.submit", names)
        self.assertIn("signalcore.inspect.impact", names)


if __name__ == "__main__":
    unittest.main()
