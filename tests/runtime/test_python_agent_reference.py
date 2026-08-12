from __future__ import annotations

import unittest

from tools.validate_python_agent_reference import validate


class PythonAgentReferenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.report = validate()

    def test_reference_gate_is_clean(self) -> None:
        self.assertTrue(self.report["ok"], self.report)
        self.assertEqual(self.report["failed_checks"], [])

    def test_public_surface_is_agent_run_and_replay(self) -> None:
        self.assertEqual(self.report["surface"], ["agent run", "agent replay"])
        self.assertEqual(self.report["network_boundary"], "localhost-only deterministic OpenAI-compatible fixture")

    def test_run_receipt_and_request_contract(self) -> None:
        run = self.report["run"]
        request = self.report["request"]
        self.assertTrue(run["ok"])
        self.assertEqual(run["run_state"], "completed")
        self.assertEqual(run["stop_reason"], "verifier passed")
        self.assertEqual(run["changed_files"], ["module.py"])
        self.assertEqual(run["attempt_count"], 1)
        self.assertEqual(run["tool_actions"], ["edit"])
        self.assertEqual(request["path"], "/chat/completions")
        self.assertEqual(request["method"], "POST")
        self.assertEqual(request["model"], "fixture-model")
        self.assertEqual(request["roles"][:2], ["system", "user"])
        self.assertFalse(request["authorization_present"])

    def test_event_and_durable_receipt_contract(self) -> None:
        self.assertTrue(self.report["events"]["contiguous"])
        self.assertEqual(
            self.report["events"]["types"],
            ["agent-started", "verification-plan", "patch-proposed", "primary-run-finished", "delivery-finished"],
        )
        self.assertEqual(self.report["durable_receipt"]["count"], 1)
        self.assertEqual(self.report["durable_receipt"]["state"], "completed")
        self.assertEqual(self.report["durable_receipt"]["changed_files"], ["module.py"])

    def test_replay_contract(self) -> None:
        replay = self.report["replay"]
        self.assertEqual(replay["exit"], 0)
        self.assertTrue(replay["ok"])
        self.assertEqual(replay["surface"], "agent-replay")
        self.assertEqual(replay["state"], "completed")
        self.assertEqual(replay["stop_reason"], "verifier passed")
        self.assertEqual(replay["changed_files"], ["module.py"])

    def test_negative_contracts(self) -> None:
        negative = self.report["negative"]
        self.assertEqual(negative["malformed_replay"], "agent replay requires a patch list and non-empty verifier argv")
        self.assertEqual(negative["malformed_model"], "model response is not a JSON action")
        self.assertEqual(negative["missing_verifier"], "agent cannot run safely because no project verifier was discovered")
        self.assertIn("model endpoint returned HTTP 500", negative["http_error"])


if __name__ == "__main__":
    unittest.main()
