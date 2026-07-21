from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from signalcore_runtime.prerelease_cli import main


class PreReleaseCLIProductV001Tests(unittest.TestCase):
    def _run(self, args: list[str]) -> tuple[int, dict]:
        output = io.StringIO()
        with redirect_stdout(output):
            code = main(args)
        return code, json.loads(output.getvalue())

    def test_setup_status_run_prove_workflow(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            common = ["--project", str(root), "--state-root", str(root / "state")]
            code, setup = self._run([*common, "setup", "--apply", "--mcp-profile", "minimal"])
            self.assertEqual(code, 0)
            self.assertTrue(setup["ok"])
            self.assertFalse(setup["dry_run"])

            code, status = self._run([*common, "status"])
            self.assertEqual(code, 0)
            self.assertEqual(status["primary_workflow"], ["setup", "status", "run", "prove"])

            code, route = self._run([*common, "run", "route", "repo.search"])
            self.assertEqual(code, 0)
            self.assertTrue(route["allowed"])

            code, proxy = self._run([*common, "run", "proxy-plan", "openai"])
            self.assertEqual(code, 0)
            self.assertTrue(proxy["ok"])

            code, opened = self._run([*common, "run", "session-open", "--session-id", "cli-session"])
            self.assertEqual(code, 0)
            self.assertTrue(opened["ok"])
            code, _ = self._run([*common, "run", "session-append", "cli-session", "decision", '{"decision":"test"}'])
            self.assertEqual(code, 0)
            code, continuity = self._run([*common, "run", "session-continuity", "cli-session", "--token-budget", "4096"])
            self.assertEqual(code, 0)
            self.assertTrue(continuity["continuity_restored"])

            code, plan = self._run([*common, "prove", "plan"])
            self.assertEqual(code, 0)
            self.assertEqual(plan["version"], "0.0.1")
            self.assertIn("maturity", plan)

    def test_maturity_and_benchmark_commands_fail_closed_without_receipts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            maturity = root / "maturity.json"
            maturity.write_text('{"onboarding":[],"distributions":[],"releases":[]}', encoding="utf-8")
            code, result = self._run(["--project", str(root), "prove", "maturity", str(maturity)])
            self.assertEqual(code, 4)
            self.assertFalse(result["ok"])
            self.assertEqual(result["claim"], "PUBLIC_PRODUCT_MATURITY_NOT_PROVEN")

            code, long_context = self._run(["--project", str(root), "prove", "long-context"])
            self.assertEqual(code, 0)
            self.assertIn("OOLONG-like", long_context["style"])


if __name__ == "__main__":
    unittest.main()
