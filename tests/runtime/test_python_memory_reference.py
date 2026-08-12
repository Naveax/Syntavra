from __future__ import annotations

import unittest
from pathlib import Path

from tools.certify_python_memory_reference import certify


class PythonMemoryReferenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.report = certify(Path(__file__).resolve().parents[2])

    def test_all_memory_routes_are_frozen(self) -> None:
        self.assertTrue(self.report["ok"], self.report)
        self.assertEqual(self.report["engine"], "python")
        self.assertEqual(self.report["family"], "memory-intelligence")
        self.assertEqual(len(self.report["routes"]), 15)
        self.assertEqual(
            self.report["exit_policy"],
            {
                "success": 0,
                "integrity_failure": 3,
                "application_error": 4,
                "argument_parser_error": 2,
            },
        )

    def test_session_chain_checkpoint_fork_merge_and_restore(self) -> None:
        session = self.report["session_memory"]
        self.assertTrue(session["empty_verify"]["ok"])
        self.assertEqual(session["verified_before"]["events"], 3)
        self.assertTrue(session["checkpoint_idempotent"])
        self.assertTrue(session["restore"]["exact_recovery"])
        self.assertEqual(len(session["restore"]["events"]), 3)
        self.assertEqual(session["verified_after"]["events"], 4)
        self.assertTrue(session["child_verify"]["ok"])
        self.assertTrue(session["merged_verify"]["ok"])

    def test_repository_summary_and_retrieval_ranking_are_explicit(self) -> None:
        session = self.report["session_memory"]
        summaries = session["compact"]["summaries"]
        repository = next(row for row in summaries if row["view"] == "repository")
        self.assertIn("agent/reference", repository["summary"])
        self.assertIn("memory", repository["summary"])
        scores = [float(row["score"]) for row in session["retrieve"]["results"]]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_missing_sessions_fail_closed_and_hash_tampering_is_reported(self) -> None:
        session = self.report["session_memory"]
        for name in (
            "missing_verify",
            "missing_retrieve",
            "missing_checkpoint",
            "unsupported_view",
            "duplicate_merge_parent",
            "malformed_payload",
        ):
            with self.subTest(name=name):
                case = session["missing_and_malformed"][name]
                self.assertEqual(case["exit"], 4, case)
                self.assertEqual(case["error_code"], "PYTHON_PUBLIC_COMMAND_FAILED", case)
                self.assertTrue(case["stderr_empty"], case)
        self.assertEqual(session["missing_and_malformed"]["missing_append_argument"]["exit"], 2)
        self.assertFalse(session["tampered_verify"]["ok"])
        self.assertEqual(session["tampered_verify"]["failures"], ["hash:1"])

    def test_memory_intelligence_empty_state_upsert_and_unicode_search(self) -> None:
        memory = self.report["memory_intelligence"]
        self.assertEqual(memory["empty_status"]["stats"]["observations"], 0)
        self.assertEqual(memory["empty_search"], {"results": []})
        self.assertTrue(memory["empty_export"]["zero_bytes"])
        self.assertTrue(memory["duplicate_upsert"]["same_observation_id"])
        self.assertEqual(memory["duplicate_upsert"]["importance"], 0.8)
        self.assertEqual(memory["duplicate_upsert"]["confidence"], 0.9)
        search = memory["unicode_search"]["results"]
        self.assertTrue(search)
        self.assertEqual(search[0]["observation"]["observation_id"], memory["unicode_add"]["observation_id"])

    def test_memory_intelligence_ranking_backfill_export_and_notification(self) -> None:
        memory = self.report["memory_intelligence"]
        self.assertEqual(memory["backfill"], {"embedded": 1, "remaining": 0})
        status = memory["status"]
        self.assertEqual(status["stats"], {"observations": 8, "valid": 8, "missing_embeddings": 0})
        rois = [float(row["roi"]) for row in status["ranked"]]
        self.assertEqual(rois, sorted(rois, reverse=True))
        self.assertEqual(status["ranked"][0]["observation_id"], memory["critical_add"]["observation_id"])
        self.assertEqual(len(memory["notifications"]), 1)
        self.assertEqual(memory["notifications"][0]["severity"], "critical")
        self.assertTrue(memory["notifications"][0]["event_hash_shape"])
        self.assertTrue(memory["export"]["sha256_matches_file"])
        self.assertTrue(memory["export"]["rank_order_matches_status"])

    def test_memory_intelligence_negative_and_malformed_state_contract(self) -> None:
        memory = self.report["memory_intelligence"]
        for name in ("empty_text", "invalid_extractor_config"):
            with self.subTest(name=name):
                case = memory["negative"][name]
                self.assertEqual(case["exit"], 4, case)
                self.assertEqual(case["error_code"], "PYTHON_PUBLIC_COMMAND_FAILED", case)
        self.assertEqual(memory["negative"]["invalid_search_limit"]["exit"], 2)
        self.assertEqual(memory["negative"]["missing_search_query"]["exit"], 2)
        self.assertEqual(memory["malformed_state"]["exit"], 4)
        self.assertIn("JSONDecodeError", memory["malformed_state"]["detail"])

    def test_durable_sqlite_schemas_are_frozen(self) -> None:
        session = self.report["session_memory"]["sqlite"]
        self.assertEqual(session["tables"], ["checkpoints", "events", "sessions", "summaries"])
        self.assertEqual(session["indexes"], ["idx_summary_session_view"])
        intelligence = self.report["memory_intelligence"]["sqlite"]
        self.assertEqual(intelligence["tables"], ["observations"])
        self.assertEqual(intelligence["indexes"], ["observations_kind_idx"])
        self.assertEqual(intelligence["row_counts"]["observations"], 8)


if __name__ == "__main__":
    unittest.main()
