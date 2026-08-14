from __future__ import annotations

import unittest
from pathlib import Path

from tools.certify_python_context_compaction_reference import certify


class PythonContextCompactionReferenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.report = certify(Path(__file__).resolve().parents[2])

    def test_public_inventory_and_ownership_are_frozen(self) -> None:
        self.assertTrue(self.report["ok"], self.report)
        self.assertEqual(self.report["routes"]["route_count"], 27)
        self.assertEqual(len(self.report["routes"]["ownership"]), 27)
        self.assertEqual(len(self.report["routes"]["route_sha256"]), 64)

    def test_context_thresholds_pack_ordering_and_malformed_inputs_are_frozen(self) -> None:
        context = self.report["context"]
        self.assertTrue(context["implicit_alias_equal"])
        self.assertEqual(context["evaluate_50_percent"]["level"], 1)
        self.assertEqual(context["pressure_all_thresholds"]["level"], 6)
        self.assertTrue(context["pressure_all_thresholds"]["mandatory_split"])
        self.assertEqual(context["pack"]["used"], 75)
        self.assertEqual(context["pack"]["selected_ids"], ["dep", "sys", "task"])
        self.assertEqual(context["pack"]["dropped_ids"], ["noise"])
        self.assertTrue(context["pack"]["mandatory_satisfied"])
        self.assertEqual(context["malformed_item"]["exit"], 4)
        self.assertEqual(context["malformed_item"]["error_type"], "TypeError")
        self.assertEqual(context["invalid_window"]["exit"], 4)
        self.assertEqual(context["argparse_error"], {"exit": 2, "stderr_nonempty": True})

    def test_legacy_session_context_compaction_and_exact_state_are_frozen(self) -> None:
        session = self.report["legacy_session"]
        self.assertEqual(session["append_sequence"], 1)
        self.assertEqual(session["context"]["exact_history_events"], 30)
        self.assertEqual(session["context"]["recent_event_count"], 4)
        self.assertTrue(session["context"]["root_summary_present"])
        self.assertTrue(session["compact_idempotent"])
        self.assertEqual(session["checkpoint"]["through_sequence"], 30)
        self.assertEqual(session["fork_parent_ids"], ["legacy-a"])
        self.assertEqual(session["merge_parent_ids"], ["legacy-a", "legacy-b"])
        self.assertEqual(session["verify"], {"ok": True, "events": 30, "reasons": []})
        self.assertTrue(session["export"]["hash_shape"])
        self.assertEqual(session["imported_from"], "legacy-a")
        self.assertEqual(session["closed_state"], "CLOSED")
        self.assertTrue(session["recover_ok"])
        self.assertEqual(session["missing_context"]["exit"], 4)
        self.assertEqual(session["malformed_append"]["exit"], 4)
        self.assertGreaterEqual(session["sqlite_counts"]["sessions"], 5)
        self.assertGreaterEqual(session["sqlite_counts"]["events"], 62)

    def test_product_continuity_reads_the_same_exact_session_state(self) -> None:
        continuity = self.report["continuity"]
        self.assertTrue(continuity["resume"]["continuity_restored"])
        self.assertEqual(continuity["resume"]["events"], 30)
        self.assertEqual(continuity["append_sequence"], 31)
        self.assertTrue(continuity["compact"]["ok"])
        self.assertEqual(continuity["compact"]["exact_history_events"], 31)
        self.assertTrue(continuity["continuity"]["exact_recovery"])
        self.assertFalse(continuity["continuity"]["forced_restart"])
        self.assertEqual(continuity["continuity"]["claim"], "SESSION_CONTINUITY_INTERNALLY_VERIFIED")
        self.assertEqual(continuity["malformed_metadata"]["exit"], 4)
        self.assertEqual(continuity["malformed_payload"]["exit"], 4)

    def test_rewrite_is_deterministic_and_fail_closed(self) -> None:
        rewrite = self.report["rewrite"]
        self.assertTrue(rewrite["manifest"]["fail_closed"])
        self.assertFalse(rewrite["manifest"]["shell_composition_rewritten"])
        self.assertTrue(rewrite["manifest"]["coverage_gate"])
        self.assertTrue(rewrite["git_status"]["changed"])
        self.assertEqual(rewrite["git_status"]["rule"], "git-status")
        self.assertFalse(rewrite["explicit_format_preserved"]["changed"])
        self.assertFalse(rewrite["unsafe_shell"]["safe"])
        self.assertEqual(rewrite["missing_command"]["exit"], 4)

    def test_reversible_compression_and_integrity_failure_are_frozen(self) -> None:
        compression = self.report["compression"]
        self.assertTrue(compression["put"]["reversible"])
        self.assertTrue(compression["put"]["secret_redacted"])
        self.assertTrue(compression["restore_exact"])
        self.assertTrue(compression["verify_ok"])
        self.assertEqual(compression["tampered_verify"], {"exit": 3, "ok": False})
        self.assertEqual(compression["invalid_chunk"]["exit"], 4)
        self.assertEqual(compression["invalid_chunk"]["error_type"], "IndexError")
        self.assertEqual(compression["missing_id"]["exit"], 4)
        self.assertEqual(compression["malformed_json"]["exit"], 4)

    def test_fabric_compaction_accounting_security_and_side_effect_are_frozen(self) -> None:
        fabric = self.report["fabric"]
        self.assertEqual(fabric["family"], "test")
        self.assertTrue(fabric["deterministic"])
        self.assertTrue(fabric["exact_required"])
        self.assertLessEqual(fabric["visible_bytes"], 512)
        self.assertTrue(fabric["secret_types"])
        self.assertEqual(fabric["invalid_budget"]["exit"], 4)
        self.assertEqual(fabric["insight_compact_events"], 2)

    def test_certification_is_offline_and_nondeterminism_is_explicit(self) -> None:
        self.assertEqual(
            self.report["network_boundary"],
            "offline deterministic local filesystem/SQLite fixtures only; no external service",
        )
        self.assertGreaterEqual(len(self.report["nondeterministic_fields"]), 6)
        self.assertEqual(self.report["exit_policy"]["success"], 0)
        self.assertEqual(self.report["exit_policy"]["argparse_error"], 2)
        self.assertEqual(self.report["exit_policy"]["integrity_failure"], 3)
        self.assertEqual(self.report["exit_policy"]["application_or_malformed_input"], 4)


if __name__ == "__main__":
    unittest.main()
