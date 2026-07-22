from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from syntavra_runtime.language_platform import (
    LanguageDescriptor,
    LanguageParseResult,
    LanguageRegistry,
)
from syntavra_runtime.semantic_intelligence import IncrementalCodeIntelligenceGraph


class FutureLanguageAdapter:
    language_ids = ("novalang",)
    capabilities = ("syntax", "semantic")

    def parse(self, *, path: str, text: str, evidence_ref: str) -> LanguageParseResult:
        name = "ignite"
        node_id = f"adapter:{path}:{name}"
        return LanguageParseResult(
            nodes=(
                {
                    "node_id": node_id,
                    "kind": "function",
                    "name": name,
                    "qualified_name": f"{path}::{name}",
                    "start_line": 1,
                    "end_line": 1,
                    "metadata": {"adapter_verified": True},
                },
            ),
            edges=(),
            capability_level="semantic",
            evidence_source="test-novalang-adapter",
        )


class LanguagePlatformTests(unittest.TestCase):
    def test_unknown_future_language_is_indexed_without_exact_claims(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "repo"
            root.mkdir()
            source = root / "engine.never-seen-before"
            source.write_text(
                "construct HyperDrive\nignite HyperDrive\nignite HyperDrive\n",
                encoding="utf-8",
            )
            graph = IncrementalCodeIntelligenceGraph(Path(temporary) / "graph.sqlite3")
            result = graph.index_repository(root)

            self.assertTrue(result["ok"])
            self.assertEqual(result["files"], 1)
            self.assertEqual(result["unknown_language_files"], 1)
            self.assertTrue(result["universal_text_fallback"])
            self.assertTrue(graph.query("HyperDrive"))
            self.assertTrue(all(item["semantic_status"] == "candidate" for item in graph.query("HyperDrive")))

    def test_extensionless_shebang_language_is_detected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "repo"
            root.mkdir()
            script = root / "deploy"
            script.write_text("#!/usr/bin/env bash\nfunction deploy() { echo ok; }\n", encoding="utf-8")
            graph = IncrementalCodeIntelligenceGraph(Path(temporary) / "graph.sqlite3")
            result = graph.index_repository(root)

            self.assertTrue(result["ok"])
            self.assertEqual(result["languages"], [{"language": "shell", "files": 1}])
            self.assertTrue(graph.query("deploy"))

    def test_repository_manifest_adds_language_without_runtime_release(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "repo"
            manifest_dir = root / ".syntavra" / "languages"
            manifest_dir.mkdir(parents=True)
            (manifest_dir / "novalang.json").write_text(
                json.dumps(
                    {
                        "id": "novalang",
                        "suffixes": [".nova"],
                        "aliases": ["nova"],
                        "capabilities": ["lexical"],
                    }
                ),
                encoding="utf-8",
            )
            (root / "main.nova").write_text("function ignite\nignite\n", encoding="utf-8")
            graph = IncrementalCodeIntelligenceGraph(Path(temporary) / "graph.sqlite3")
            result = graph.index_repository(root)

            self.assertTrue(result["ok"])
            self.assertEqual(result["unknown_language_files"], 0)
            self.assertEqual(result["languages"], [{"language": "novalang", "files": 1}])
            self.assertTrue(graph.query("ignite"))

    def test_adapter_arrival_reindexes_unchanged_source_and_upgrades_evidence(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "repo"
            root.mkdir()
            source = root / "main.nova"
            source.write_text("function ignite\n", encoding="utf-8")

            registry = LanguageRegistry(discover_entry_points=False)
            registry.register_descriptor(
                LanguageDescriptor(
                    "novalang",
                    suffixes=(".nova",),
                    capabilities=frozenset({"lexical"}),
                    source="test",
                )
            )
            graph = IncrementalCodeIntelligenceGraph(
                Path(temporary) / "graph.sqlite3",
                language_registry=registry,
            )
            first = graph.index_repository(root)
            self.assertEqual(first["changed_files"], 1)
            self.assertEqual(graph.query("ignite")[0]["semantic_status"], "candidate")

            registry.register_adapter(FutureLanguageAdapter())
            second = graph.index_repository(root)
            self.assertEqual(second["changed_files"], 1)
            result = graph.query("ignite")[0]
            self.assertEqual(result["semantic_status"], "exact")
            self.assertTrue(result["metadata"]["adapter_verified"])

    def test_binary_input_is_not_misrepresented_as_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "repo"
            root.mkdir()
            (root / "payload.future").write_bytes(b"\x00\x01\x02\xff" * 100)
            graph = IncrementalCodeIntelligenceGraph(Path(temporary) / "graph.sqlite3")
            result = graph.index_repository(root)

            self.assertTrue(result["ok"])
            self.assertEqual(result["files"], 0)
            self.assertEqual(result["binary_skipped"], 1)
            self.assertEqual(result["nodes"], 0)

    def test_python_exact_nodes_are_preserved(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "repo"
            root.mkdir()
            (root / "app.py").write_text("def exact_symbol():\n    return 1\n", encoding="utf-8")
            graph = IncrementalCodeIntelligenceGraph(Path(temporary) / "graph.sqlite3")
            graph.index_repository(root)

            result = graph.query("exact_symbol")[0]
            self.assertEqual(result["semantic_status"], "exact")
            impact = graph.impact(result["node_id"])
            self.assertTrue(impact["exact_evidence"])
            self.assertFalse(impact["candidate_evidence_present"])


if __name__ == "__main__":
    unittest.main()
