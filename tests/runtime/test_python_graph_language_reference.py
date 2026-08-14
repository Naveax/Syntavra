from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

from syntavra_runtime.language_parsers import TreeSitterLanguageBackend
from tools.certify_python_graph_language_reference import certify


class PythonGraphLanguageReferenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.report = certify(Path(__file__).resolve().parents[2])

    def test_nine_public_routes_are_frozen(self) -> None:
        self.assertTrue(self.report["ok"], self.report)
        self.assertEqual(self.report["engine"], "python")
        self.assertEqual(self.report["family"], "graph-language-semantic")
        self.assertEqual(len(self.report["routes"]), 9)
        self.assertEqual(
            self.report["routes"],
            [
                "run graph-index",
                "run graph-query",
                "run graph-impact",
                "run language detect",
                "run language inventory",
                "run language index",
                "run language query",
                "run language doctor",
                "run semantic-services",
            ],
        )

    def test_manifest_detection_and_unknown_fallback_are_distinct(self) -> None:
        fixture = self.report["cases"]["fixture_detect"]["detection"]
        self.assertEqual(fixture["language_id"], "fixturelang")
        self.assertTrue(fixture["descriptor_source"].startswith("manifest:"))
        unknown = self.report["cases"]["unknown_detect"]["detection"]
        self.assertEqual(unknown["language_id"], "unknown:futurelang")
        self.assertEqual(unknown["capability_level"], "lexical")
        self.assertEqual(unknown["descriptor_source"], "fallback")

    def test_language_alias_and_adapter_capability_contract(self) -> None:
        registry = self.report["registry_contract"]
        self.assertTrue(registry["fixture_alias_identity"])
        self.assertFalse(registry["entry_point_plugins_authorized"])
        tree = registry["tree_sitter"]
        self.assertEqual(tree["capabilities"], ["definitions", "references", "syntax"])
        self.assertIn("c_sharp", tree["language_ids"])
        self.assertIn("csharp", tree["language_ids"])
        self.assertEqual(self.report["cases"]["csharp_detect"]["detection"]["language_id"], "csharp")

    def test_packaged_csharp_parser_does_not_require_language_pack_fetch(self) -> None:
        backend = TreeSitterLanguageBackend()
        self.assertIsNotNone(backend._csharp_binding, "packaged tree-sitter-c-sharp binding is required")
        backend._get_parser = mock.Mock(side_effect=AssertionError("language-pack path must not be used for C#"))
        source = "public class Program { public static void Main() { Helper(); } static void Helper() {} }"
        for language in ("c_sharp", "csharp"):
            with self.subTest(language=language):
                declarations = backend.parse(source, language)
                self.assertIsNotNone(declarations)
                self.assertTrue(any(item.name == "Program" for item in declarations or ()))
        backend._get_parser.assert_not_called()

    def test_graph_materialization_query_and_impact(self) -> None:
        index = self.report["cases"]["graph_index"]
        self.assertTrue(index["canonical_graph"])
        self.assertGreater(index["nodes"], 0)
        self.assertGreater(index["edges"], 0)
        self.assertEqual(index["unknown_language_files"], 1)
        query = self.report["cases"]["graph_query"]
        self.assertTrue(query["results"])
        self.assertEqual(query["results"][0]["query_backend"], "sqlite-fts5")
        impact = self.report["cases"]["graph_impact"]
        self.assertTrue(impact["exact_evidence"])
        self.assertIsInstance(impact["impacted"], list)

    def test_inventory_doctor_and_semantic_services_share_one_status(self) -> None:
        status = self.report["cases"]["language_status"]
        registry = status["language_registry"]
        self.assertIn("fixturelang", registry["languages"])
        self.assertIn("c_sharp", registry["adapters"])
        self.assertIn("csharp", registry["adapters"])
        self.assertFalse(registry["entry_point_plugins_authorized"])
        self.assertTrue(any("broken.json" in item for item in registry["diagnostics"]))

    def test_query_empty_state_and_no_match_are_successful_empty_reads(self) -> None:
        self.assertEqual(
            self.report["cases"]["empty_graph_query"],
            {"ok": True, "query": "alpha", "results": []},
        )
        self.assertEqual(self.report["cases"]["language_query_no_match"]["results"], [])

    def test_negative_cli_semantics_are_explicit(self) -> None:
        self.assertEqual(
            self.report["exit_policy"],
            {"success": 0, "application_error": 4, "argument_parser_error": 2},
        )
        for name in ("missing_detect_error", "escaped_detect_error"):
            with self.subTest(name=name):
                case = self.report["cases"][name]
                self.assertEqual(case["exit"], 4, case)
                self.assertEqual(case["error_code"], "PYTHON_PUBLIC_COMMAND_FAILED", case)
                self.assertTrue(case["stderr_empty"], case)
        self.assertEqual(self.report["cases"]["invalid_limit_error"]["exit"], 2)
        self.assertEqual(self.report["cases"]["missing_detect_argument"]["exit"], 2)

    def test_sqlite_graph_side_effect_is_durable(self) -> None:
        sqlite = self.report["cases"]["sqlite"]
        self.assertTrue(sqlite["tables"])
        self.assertTrue(any(count > 0 for count in sqlite["row_counts"].values()))


if __name__ == "__main__":
    unittest.main()