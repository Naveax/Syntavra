from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from syntavra_runtime.cli import build_parser, main


class CompetitiveCLIV4Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.state = self.root / ".state"

    def tearDown(self):
        self.temp.cleanup()

    def run_cli(self, *values: str) -> tuple[int, dict]:
        stream = io.StringIO()
        argv = ["--project", str(self.root), "--state-root", str(self.state), *values]
        with redirect_stdout(stream):
            code = main(argv)
        return code, json.loads(stream.getvalue())

    def test_parser_exposes_fabric_and_provider_commands(self):
        parser = build_parser()
        help_text = parser.format_help()
        self.assertIn("fabric", help_text)
        self.assertIn("provider", help_text)

    def test_fabric_route_and_cache_align(self):
        code, routed = self.run_cli("fabric", "route", "--", "pytest", "-q")
        self.assertEqual(code, 0)
        self.assertEqual(routed["mode"], "background-replace")
        payload = json.dumps({
            "messages": [
                {"role": "system", "content": "stable", "request_id": "volatile"},
                {"role": "user", "content": "tail"},
            ]
        })
        code, aligned = self.run_cli("fabric", "cache-align", "--payload", payload)
        self.assertEqual(code, 0)
        self.assertEqual(aligned["stable_message_count"], 1)
        self.assertIn("request_id", aligned["volatile_fields"])

    def test_provider_prepare_capture_replay_and_verify(self):
        request_path = self.root / "request.json"
        plan_path = self.root / "plan.json"
        response_path = self.root / "response.json"
        capture_path = self.root / "capture.json"
        request_path.write_text(json.dumps({
            "model": "gpt-test",
            "messages": [
                {"role": "system", "content": "stable"},
                {"role": "user", "content": "question"},
            ],
            "temperature": 0,
        }), encoding="utf-8")
        response_path.write_text(json.dumps({
            "id": "resp-cli",
            "output_text": "answer",
            "usage": {"input_tokens": 10, "output_tokens": 2},
        }), encoding="utf-8")

        code, written = self.run_cli(
            "provider", "prepare", "openai", "--input", str(request_path), "--output", str(plan_path)
        )
        self.assertEqual(code, 0)
        self.assertTrue(written["ok"])
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        self.assertIn("prompt_cache_key", plan["prepared_request"])

        code, written = self.run_cli(
            "provider", "capture", "--plan", str(plan_path), "--response", str(response_path),
            "--output", str(capture_path),
        )
        self.assertEqual(code, 0)
        self.assertTrue(written["ok"])
        capture = json.loads(capture_path.read_text(encoding="utf-8"))
        self.assertTrue(capture["replay_stored"])

        code, replay = self.run_cli("provider", "replay", "--cache-key", plan["cache_key"])
        self.assertEqual(code, 0)
        self.assertEqual(replay["output_text"], "answer")
        code, verified = self.run_cli("provider", "verify")
        self.assertEqual(code, 0)
        self.assertTrue(verified["ok"])


if __name__ == "__main__":
    unittest.main()
