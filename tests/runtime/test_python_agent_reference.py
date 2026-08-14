from __future__ import annotations

import unittest
from pathlib import Path

from tools.certify_python_agent_reference import certify


class PythonAgentReferenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.report = certify(Path(__file__).resolve().parents[2])

    def test_reference_gate_is_clean(self) -> None:
        self.assertTrue(self.report["ok"], self.report)
        self.assertEqual(self.report["engine"], "python")
        self.assertEqual(self.report["routes"], ["agent run", "agent replay"])

    def test_exit_policy_is_explicit(self) -> None:
        self.assertEqual(
            self.report["exit_policy"],
            {
                "success": 0,
                "application_or_gateway_error": 4,
                "argument_parser_error": 2,
            },
        )
        self.assertEqual(self.report["cases"]["replay_missing_required_arguments"]["exit"], 2)

    def test_live_request_and_json_action_protocol(self) -> None:
        run = self.report["cases"]["agent_run_happy"]
        self.assertEqual(run["exit"], 0)
        self.assertEqual(run["request_rounds"], 2)
        self.assertEqual(run["tool_trace_actions"], ["search", "patch"])
        self.assertEqual(run["first_message_roles"], ["system", "user"])
        self.assertEqual(run["second_message_roles"], ["system", "user", "assistant", "user"])
        self.assertFalse(run["tools_present"])
        self.assertFalse(run["tool_choice_present"])
        self.assertFalse(run["authorization_present"])
        self.assertEqual(self.report["transport_protocol"], "openai-compatible-chat-json-action")

    def test_event_and_durable_receipt_schema(self) -> None:
        run = self.report["cases"]["agent_run_happy"]
        self.assertGreater(run["event_count"], 0)
        self.assertEqual(run["event_keys"], ["created_at", "event_type", "payload", "sequence"])
        self.assertGreaterEqual(run["durable_receipt_count"], 1)

    def test_replay_contract(self) -> None:
        replay = self.report["cases"]["agent_replay_happy"]
        self.assertEqual(replay["exit"], 0)
        self.assertTrue(replay["stderr_empty"])
        self.assertEqual(replay["surface"], "agent-replay")

    def test_negative_public_error_contracts(self) -> None:
        for name in (
            "replay_malformed_json",
            "model_response_not_json_action",
            "model_unknown_action",
            "model_malformed_action_arguments",
            "model_empty_chat_output",
            "model_http_500",
            "model_invalid_http_json",
            "model_endpoint_unavailable",
            "model_endpoint_timeout",
        ):
            with self.subTest(name=name):
                case = self.report["cases"][name]
                self.assertEqual(case["exit"], 4, case)
                self.assertEqual(case["error_code"], "PYTHON_PUBLIC_COMMAND_FAILED", case)
                self.assertTrue(case["stderr_empty"], case)


if __name__ == "__main__":
    unittest.main()
