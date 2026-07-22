from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from syntavra_runtime.semantic_indexes import LSIFImporter, SCIPJSONImporter, load_semantic_index


class SemanticIndexImporterTests(unittest.TestCase):
    def test_lsif_definitions_and_references_are_normalized(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "repo"
            root.mkdir()
            (root / "main.future").write_text("FutureType\nFutureType\n", encoding="utf-8")
            lsif = Path(temporary) / "index.lsif"
            records = [
                {"id": 1, "type": "vertex", "label": "metaData", "version": "0.6.0", "projectRoot": root.as_uri()},
                {"id": 2, "type": "vertex", "label": "document", "uri": (root / "main.future").as_uri(), "languageId": "future-language"},
                {"id": 3, "type": "vertex", "label": "range", "start": {"line": 0, "character": 0}, "end": {"line": 0, "character": 10}},
                {"id": 4, "type": "vertex", "label": "range", "start": {"line": 1, "character": 0}, "end": {"line": 1, "character": 10}},
                {"id": 5, "type": "vertex", "label": "resultSet"},
                {"id": 6, "type": "vertex", "label": "resultSet"},
                {"id": 7, "type": "vertex", "label": "moniker", "kind": "export", "identifier": "future/pkg/FutureType"},
                {"id": 8, "type": "vertex", "label": "definitionResult"},
                {"id": 9, "type": "edge", "label": "contains", "outV": 2, "inVs": [3, 4]},
                {"id": 10, "type": "edge", "label": "next", "outV": 3, "inV": 5},
                {"id": 11, "type": "edge", "label": "next", "outV": 4, "inV": 6},
                {"id": 12, "type": "edge", "label": "moniker", "outV": 5, "inV": 7},
                {"id": 13, "type": "edge", "label": "moniker", "outV": 6, "inV": 7},
                {"id": 14, "type": "edge", "label": "textDocument/definition", "outV": 6, "inV": 8},
                {"id": 15, "type": "edge", "label": "item", "outV": 8, "inVs": [3], "document": 2},
            ]
            lsif.write_text("\n".join(json.dumps(item) for item in records) + "\n", encoding="utf-8")
            bundle = LSIFImporter().load(lsif, repository_root=root, repository_commit="abc123")
            self.assertEqual(bundle.format, "lsif")
            self.assertEqual(len(bundle.nodes), 2)
            self.assertEqual(bundle.nodes[0].path, "main.future")
            self.assertTrue(all(node.metadata["exact_semantic"] for node in bundle.nodes))
            self.assertEqual(len(bundle.edges), 1)
            self.assertEqual(bundle.edges[0].edge_type, "defines")
            self.assertEqual(bundle.edges[0].metadata["repository_commit"], "abc123")

    def test_lsif_path_escape_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "repo"
            root.mkdir()
            outside = Path(temporary) / "outside.future"
            outside.write_text("x", encoding="utf-8")
            lsif = Path(temporary) / "escape.lsif"
            records = [
                {"id": 1, "type": "vertex", "label": "document", "uri": outside.as_uri(), "languageId": "future"},
                {"id": 2, "type": "vertex", "label": "range", "start": {"line": 0, "character": 0}, "end": {"line": 0, "character": 1}},
                {"id": 3, "type": "edge", "label": "contains", "outV": 1, "inVs": [2]},
            ]
            lsif.write_text("\n".join(json.dumps(item) for item in records), encoding="utf-8")
            with self.assertRaises(PermissionError):
                LSIFImporter().load(lsif, repository_root=root)

    def test_scip_json_symbols_occurrences_and_relationships(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "repo"
            root.mkdir()
            (root / "main.future").write_text("ignite\n", encoding="utf-8")
            scip = Path(temporary) / "index.scip.json"
            scip.write_text(
                json.dumps(
                    {
                        "metadata": {"version": "commit-42", "tool_info": {"name": "future-language"}},
                        "documents": [
                            {
                                "relative_path": "main.future",
                                "language": "future-language",
                                "symbols": [
                                    {
                                        "symbol": "future pkg FutureType#ignite().",
                                        "relationships": [
                                            {"symbol": "future pkg Interface#ignite().", "is_implementation": True}
                                        ],
                                    }
                                ],
                                "occurrences": [
                                    {
                                        "range": [0, 0, 0, 6],
                                        "symbol": "future pkg FutureType#ignite().",
                                        "symbol_roles": 1,
                                        "syntax_kind": 12,
                                    }
                                ],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            bundle = SCIPJSONImporter().load(scip, repository_root=root)
            self.assertEqual(bundle.format, "scip-json")
            self.assertEqual(bundle.repository_commit, "commit-42")
            self.assertEqual(len(bundle.nodes), 3)
            self.assertEqual({edge.edge_type for edge in bundle.edges}, {"implements", "resolves-to"})
            self.assertTrue(all(edge.metadata["exact_semantic"] for edge in bundle.edges))

    def test_binary_scip_requires_hash_pinned_conversion_service(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "repo"
            root.mkdir()
            path = Path(temporary) / "index.scip"
            path.write_bytes(b"binary-scip")
            with self.assertRaisesRegex(ValueError, "hash-pinned conversion service"):
                load_semantic_index(path, repository_root=root)


if __name__ == "__main__":
    unittest.main()
