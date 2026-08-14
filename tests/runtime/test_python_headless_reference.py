from __future__ import annotations

import unittest
from pathlib import Path

from tools.certify_python_headless_reference import certify


class PythonHeadlessReferenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.report = certify(Path(__file__).resolve().parents[2])

    def test_all_eight_public_routes_are_frozen(self) -> None:
        self.assertTrue(self.report["ok"], self.report)
        self.assertEqual(self.report["family"], "headless")
        self.assertEqual(self.report["engine"], "python")
        self.assertEqual(
            self.report["routes"],
            [
                "run headless-submit",
                "run headless-run",
                "run headless-status",
                "run headless-events",
                "run headless-cancel",
                "run headless-resume",
                "run headless-export",
                "run headless-import",
            ],
        )

    def test_exit_policy_and_negative_cases(self) -> None:
        self.assertEqual(
            self.report["exit_policy"],
            {"success": 0, "application_error": 4, "argument_parser_error": 2},
        )
        for name in (
            "resume_queued_error",
            "unknown_status_error",
            "unknown_events_error",
            "malformed_submit_error",
            "malformed_policy_error",
            "tampered_bundle_error",
        ):
            with self.subTest(name=name):
                case = self.report["cases"][name]
                self.assertEqual(case["exit"], 4, case)
                self.assertEqual(case["error_code"], "PYTHON_PUBLIC_COMMAND_FAILED", case)
                self.assertTrue(case["stderr_empty"], case)
        self.assertEqual(self.report["cases"]["missing_events_argument"]["exit"], 2)

    def test_execution_and_event_order_are_frozen(self) -> None:
        run = self.report["cases"]["run_completed"]
        self.assertEqual(run["state"], "completed")
        self.assertEqual(run["attempts"], 1)
        self.assertEqual(run["claimed_by"], "python-reference-worker")
        self.assertEqual(run["event_types"], ["submitted", "claimed", "running", "completed"])
        self.assertIn("receipt_id", run["execution_keys"])
        self.assertIn("backend", run["execution_keys"])
        self.assertEqual(
            run["backend_keys"],
            ["available", "command_prefix", "detail", "enforced", "name", "platform", "unsupported"],
        )

    def test_cancel_resume_and_final_cancel_idempotency(self) -> None:
        lifecycle = self.report["cases"]["cancel_resume"]
        self.assertEqual(lifecycle["cancelled_state"], "cancelled")
        self.assertEqual(lifecycle["resumed_state"], "queued")
        self.assertEqual(lifecycle["event_types"], ["submitted", "cancelled", "resumed"])
        self.assertTrue(lifecycle["completed_cancel_idempotent"])

    def test_export_import_and_sqlite_are_frozen(self) -> None:
        transfer = self.report["cases"]["export_import"]
        self.assertEqual(transfer["bundle_schema"], "syntavra-headless-job")
        self.assertEqual(transfer["bundle_payload_keys"], ["events", "job", "schema"])
        sqlite = self.report["cases"]["sqlite"]
        self.assertEqual(sqlite["tables"], ["events", "jobs"])
        self.assertEqual(sqlite["indexes"], ["idx_events_job", "idx_jobs_state"])
        self.assertEqual(sqlite["job_count"], 3)
        self.assertEqual(sqlite["states"], {"completed": 1, "queued": 2})

    def test_every_allowed_and_forbidden_state_edge_is_exercised(self) -> None:
        machine = self.report["sqlite_state_machine"]
        self.assertEqual(machine["allowed_case_count"], 18)
        self.assertEqual(machine["forbidden_case_count"], 46)
        self.assertEqual(
            machine["allowed"],
            {
                "blocked": ["cancelled", "queued"],
                "cancelled": ["queued"],
                "claimed": ["cancelled", "queued", "running"],
                "completed": [],
                "failed": ["queued"],
                "queued": ["cancelled", "claimed"],
                "running": ["blocked", "cancelled", "completed", "failed", "verifying"],
                "verifying": ["blocked", "cancelled", "completed", "failed"],
            },
        )

    def test_nondeterminism_is_explicit(self) -> None:
        fields = self.report["nondeterministic_fields"]
        self.assertIn("time-derived sha256 job_id", fields)
        self.assertIn("sandbox receipt_id", fields)
        self.assertIn("temporary project/state paths", fields)


if __name__ == "__main__":
    unittest.main()
