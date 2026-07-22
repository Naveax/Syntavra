from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from syntavra_runtime.runtime_evidence import RuntimeEvidenceGraph
from syntavra_runtime.semantic_intelligence import IncrementalCodeIntelligenceGraph
from syntavra_runtime.semantic_services import (
    DEFAULT_LANGUAGE_SERVICES,
    LSPClient,
    LanguageServiceRegistry,
    SemanticIndexImporter,
)


class SemanticServiceCompatibilityTests(unittest.TestCase):
    def test_closed_default_whitelist_is_removed(self) -> None:
        self.assertEqual(DEFAULT_LANGUAGE_SERVICES, ())
        status = LanguageServiceRegistry().status()
        self.assertFalse(status["fixed_language_whitelist"])
        self.assertTrue(status["universal_text_fallback"])
        self.assertGreater(status["declared"], 50)

    def test_unknown_path_uses_universal_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "module.language-from-2035"
            source.write_text("construct Future\nFuture Future\n", encoding="utf-8")
            row = LanguageServiceRegistry().for_path(source)
            self.assertIsNotNone(row)
            assert row is not None
            self.assertTrue(row.language.startswith("unknown:"))
            self.assertEqual(row.evidence_level, "lexical")
            self.assertFalse(row.available)

    def test_direct_unpinned_lsp_execution_is_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(ValueError, "hash-pinned"):
                LSPClient(("future-language-server", "--stdio"), Path(temporary))

    def test_semantic_importer_rejects_runtime_evidence_only_target(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            evidence = RuntimeEvidenceGraph(Path(temporary) / "evidence.sqlite3")
            with self.assertRaisesRegex(TypeError, "IncrementalCodeIntelligenceGraph"):
                SemanticIndexImporter(evidence)

    def test_semantic_importer_accepts_unified_graph(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            graph = IncrementalCodeIntelligenceGraph(Path(temporary) / "graph.sqlite3")
            importer = SemanticIndexImporter(graph)
            self.assertIs(importer.graph, graph)


if __name__ == "__main__":
    unittest.main()
