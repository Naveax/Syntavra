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
            {
                "path": "successful_execute.context.retry_economics.provider_calls_avoided",
                "python": 1,
                "rust": None,
            },
        ), "agent")
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["accepted_retirement_divergence_count"], 2)

    def test_agent_allows_only_four_exact_event_telemetry_paths(self) -> None:
        result = certify(self.report(
            {
                "path": "live_run.events[2].payload.task_id",
                "python": "task-1",
                "rust": None,
            },
            {
                "path": "live_run.events[3].payload.context_bytes",
                "python": 123,
                "rust": None,
            },
            {
                "path": "live_run.events[3].payload.counting_method",
                "python": "utf8-bytes",
                "rust": None,
            },
            {
                "path": "live_run.events[3].payload.previews_dropped",
                "python": 2,
                "rust": None,
            },
        ), "agent")
        self.assertTrue(result["ok"], result)
        nearby = certify(self.report(
            {
                "path": "live_run.events[4].payload.task_id",
                "python": "task-1",
                "rust": None,
            }
        ), "agent")
        self.assertFalse(nearby["ok"], nearby)

    def test_agent_unknown_public_drift_blocks(self) -> None:
        result = certify(self.report(
            {"path": "successful_execute.state", "python": "completed", "rust": "failed"}
        ), "agent")
        self.assertFalse(result["ok"], result)
        self.assertEqual(result["blocking_mismatch_count"], 1)

    def test_agent_provider_prompt_exception_requires_v2_and_legacy_markers(self) -> None:
        result = certify(self.report(
            {
                "path": "live_requests[0].system_prompt",
                "python": (
                    "Use search_inspect, compact receipts, and exact recovery handles. "
                    "Fail closed on ambiguous retrieval."
                ),
                "rust": "Use search, inspect, diff, verifier, and edit actions.",
            }
        ), "agent")
        self.assertTrue(result["ok"], result)

        weak_python = certify(self.report(
            {
                "path": "live_requests[0].system_prompt",
                "python": "new prompt",
                "rust": "Use search, inspect, and edit actions.",
            }
        ), "agent")
        self.assertFalse(weak_python["ok"], weak_python)

        weak_rust = certify(self.report(
            {
                "path": "live_requests[0].system_prompt",
                "python": "Use search_inspect with compact receipts and exact recovery.",
                "rust": "old prompt",
            }
        ), "agent")
        self.assertFalse(weak_rust["ok"], weak_rust)

    def test_sandbox_detail_with_same_semantic_probe_error_is_non_blocking(self) -> None:
        result = certify(self.report(
            {
                "path": "successful_execute.attempts[0].verifier.backend.detail",
                "python": (
                    "native capability probe rejected with exit 1: "
                    "unshare: unshare failed: Operation not permitted"
                ),
                "rust": (
                    "unshare probe failed: "
                    "unshare: unshare failed: Operation not permitted"
                ),
            }
        ), "agent")
        self.assertTrue(result["ok"], result)

        headless = certify(self.report(
            {
                "path": "run_once.execution.backend.detail",
                "python": (
                    "native capability probe rejected with exit 1: "
                    "unshare: unshare failed: Operation not permitted"
                ),
                "rust": (
                    "unshare probe failed: "
                    "unshare: unshare failed: Operation not permitted"
                ),
            }
        ), "headless")
        self.assertTrue(headless["ok"], headless)

    def test_sandbox_detail_other_semantics_stay_blocking(self) -> None:
        result = certify(self.report(
            {
                "path": "run_once.execution.backend.detail",
                "python": "unshare probe failed: Permission denied",
                "rust": "unshare probe failed: Operation not permitted",
            }
        ), "headless")
        self.assertFalse(result["ok"], result)

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
