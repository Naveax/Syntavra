from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from syntavra_runtime.unified_cli import main


class ExternalBenchmarkCLIV001Tests(unittest.TestCase):
    def _run(self, argv: list[str]) -> tuple[int, dict]:
        output = io.StringIO()
        with redirect_stdout(output):
            code = main(argv)
        return code, json.loads(output.getvalue())

    def test_suite_manifest_is_available_under_prove(self) -> None:
        code, value = self._run(["prove", "suites"])
        self.assertEqual(code, 0)
        self.assertEqual(value["version"], "0.0.1")
        self.assertEqual(value["channel"], "pre-release")
        self.assertEqual(value["suite_count"], 5)
        self.assertIn("not external benchmark results", value["claim_boundary"])

    def test_empty_external_receipts_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "receipts.json"
            path.write_text('{"receipts":[]}', encoding="utf-8")
            code, value = self._run(["prove", "external-suite", str(path), "--suite", "swe-bench"])
        self.assertEqual(code, 4)
        self.assertFalse(value["ok"])
        self.assertEqual(value["claim"], "EXTERNAL_SUITE_EVIDENCE_NOT_PROVEN")
        self.assertEqual(value["public_superiority"], "EXTERNAL_SUPERIORITY_NOT_PROVEN")


if __name__ == "__main__":
    unittest.main()
