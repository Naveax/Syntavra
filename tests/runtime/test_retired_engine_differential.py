from __future__ import annotations

import unittest

from tools.certify_retired_engine_differential import certify


class RetiredEngineDifferentialTests(unittest.TestCase):
    @staticmethod
    def report(*mismatches):
        return {"differential": {"ok": not mismatches, "mismatches": list(mismatches)}}

    def test_agent_allows_only_named_post_retirement_telemetry(self) -> None:
        result = certify(self.report(
            {"path": "live_run.provider_observation", "python": {"usage": 1}, "rust": None},
            {"path": "successful_execute.context.retry_economics.provider_calls_avoided", "python": 1, "rust": None},
        ), "agent")
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["accepted_retirement_divergence_count"], 2)

    def test_agent_unknown_public_drift_blocks(self) -> None:
        result = certify(self.report(
            {"path": "successful_execute.state", "python": "completed", "rust": "failed"}
        ), "agent")
        self.assertFalse(result["ok"], result)
        self.assertEqual(result["blocking_mismatch_count"], 1)

    def test_agent_provider_prompt_exception_is_narrow(self) -> None:
        result = certify(self.report(
            {"path": "live_requests[0].system_prompt", "python": "new prompt", "rust": "old prompt"}
        ), "agent")
        self.assertTrue(result["ok"], result)
        empty = certify(self.report(
            {"path": "live_requests[0].system_prompt", "python": "", "rust": "old prompt"}
        ), "agent")
        self.assertFalse(empty["ok"], empty)

    def test_headless_detail_with_same_semantic_error_is_non_blocking(self) -> None:
        result = certify(self.report(
            {
                "path": "resume_queued_error.reason",
                "python": "job cannot be resumed from queued",
                "rust": "cannot resume queued job",
            }
        ), "headless")
        self.assertTrue(result["ok"], result)

    def test_headless_exit_or_state_drift_stays_blocking(self) -> None:
        result = certify(self.report(
            {"path": "resume_queued_error.exit", "python": 2, "rust": 1}
        ), "headless")
        self.assertFalse(result["ok"], result)

    def test_headless_unknown_detail_is_blocking(self) -> None:
        result = certify(self.report(
            {
                "path": "resume_queued_error.reason",
                "python": "permission denied",
                "rust": "cannot resume queued job",
            }
        ), "headless")
        self.assertFalse(result["ok"], result)


if __name__ == "__main__":
    unittest.main()
