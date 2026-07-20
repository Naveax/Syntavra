from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from signalcore_runtime.hooks import HookEngine


class CompetitiveHookV4Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.engine = HookEngine(
            project_root=self.root,
            state_root=self.root / ".state",
            auto_externalize=False,
            host="codex",
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_test_command_is_automatically_brokered(self):
        decision = self.engine.pre_tool({"tool": "shell", "argv": ["pytest", "-q"]})
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.mode, "replace")
        self.assertEqual(decision.replacement["argv"][:3], ["signalcore", "run", "--background"])
        self.assertIn("long-running-command", decision.reasons)

    def test_untrusted_network_command_is_automatically_sandboxed(self):
        decision = self.engine.pre_tool({
            "tool": "bash",
            "argv": ["curl", "https://example.com"],
            "network_untrusted": True,
        })
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.mode, "replace")
        self.assertEqual(decision.replacement["argv"][:3], ["signalcore", "sandbox", "execute"])
        self.assertIn("untrusted-network-command", decision.reasons)

    def test_destructive_command_remains_fail_closed(self):
        decision = self.engine.pre_tool({"tool": "terminal", "command": "git reset --hard"})
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.mode, "blocked")
        self.assertIn("destructive-command", decision.reasons)

    def test_repeated_read_is_marked_for_evidence_reuse(self):
        decision = self.engine.pre_tool({"tool": "shell", "argv": ["cat", "README.md"], "repeated": True})
        self.assertTrue(decision.allowed)
        self.assertIn("repeat-read-elision-eligible", decision.reasons)


if __name__ == "__main__":
    unittest.main()
