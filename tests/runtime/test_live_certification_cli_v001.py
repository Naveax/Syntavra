from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from syntavra_runtime.unified_cli import main


class LiveCertificationCLIV001Tests(unittest.TestCase):
    def _run(self, argv: list[str]) -> tuple[int, dict]:
        output = io.StringIO()
        with redirect_stdout(output):
            code = main(argv)
        return code, json.loads(output.getvalue())

    @staticmethod
    def _receipt(index: int) -> dict:
        return {
            "receipt_id": f"codex-live-{index}",
            "integration_id": "codex",
            "family": "host",
            "observed_at": f"2026-07-{index + 1:02d}T12:00:00+00:00",
            "syntavra_version": "0.0.1",
            "syntavra_channel": "pre-release",
            "adapter_version": "adapter-v1",
            "operating_system": ("linux", "windows", "macos")[index % 3],
            "runtime_version": "python-3.13",
            "environment_hash": f"{index + 10:064x}"[-64:],
            "config_hash": f"{index + 20:064x}"[-64:],
            "harness_commit": "a" * 40,
            "artifact_hash": f"{index + 30:064x}"[-64:],
            "install_succeeded": True,
            "doctor_passed": True,
            "request_succeeded": True,
            "response_succeeded": True,
            "streaming_verified": True,
            "provider_usage_captured": True,
            "tool_routing_verified": True,
            "session_continuity_verified": True,
            "rollback_verified": True,
            "external": True,
            "synthetic": False,
            "metadata": {},
        }

    def test_valid_external_codex_receipts_certify(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "live.json"
            path.write_text(json.dumps({"receipts": [self._receipt(index) for index in range(3)]}), encoding="utf-8")
            code, value = self._run(["prove", "integrations", str(path), "--integration", "codex"])
        self.assertEqual(code, 0)
        self.assertTrue(value["ok"], value)
        self.assertEqual(value["claim"], "LIVE_INTEGRATION_CERTIFIED")
        self.assertEqual(value["certified_integrations"], ["codex"])

    def test_empty_or_internal_receipts_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            empty = root / "empty.json"
            empty.write_text('{"receipts":[]}', encoding="utf-8")
            code, value = self._run(["prove", "integrations", str(empty), "--integration", "codex"])
            self.assertEqual(code, 4)
            self.assertFalse(value["ok"])
            self.assertEqual(value["claim"], "LIVE_INTEGRATION_CERTIFICATION_NOT_PROVEN")

            row = self._receipt(0)
            row["external"] = False
            row["synthetic"] = True
            internal = root / "internal.json"
            internal.write_text(json.dumps({"receipts": [row]}), encoding="utf-8")
            code, value = self._run(["prove", "integrations", str(internal), "--integration", "codex"])
            self.assertEqual(code, 4)
            self.assertFalse(value["ok"])


if __name__ == "__main__":
    unittest.main()
