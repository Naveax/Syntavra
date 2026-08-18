from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from syntavra_runtime.typed_context_objects import TYPE_SPECS, TypedContextObject, TypedContextObjectStore
from syntavra_runtime.universal_context_item import ContextProvenance, ContextTrust, RecoveryHandle, UniversalContextItem


FIXTURES = {
    "GitDiff": {"base": "a", "head": "b", "files": ["a.py"], "patch": "@@ -1 +1 @@"},
    "TestRun": {"command": ["pytest"], "exit_code": 0, "tests": [{"name": "x", "ok": True}]},
    "CompilerDiagnostics": {"tool": "pyright", "diagnostics": [{"path": "a.py", "line": 1, "severity": "error"}]},
    "ASTGraph": {"language": "python", "nodes": [{"id": "n1", "kind": "Function"}], "edges": []},
    "DependencyGraph": {"nodes": ["a", "b"], "edges": [["a", "b"]]},
    "SearchResultSet": {"query": "foo", "results": [{"path": "a.py", "score": 1.0}]},
    "LogStream": {"source": "server", "entries": [{"ts": "1", "message": "ready"}]},
    "BrowserDOM": {"url": "https://example.invalid", "nodes": [{"tag": "main", "text": "hello"}]},
    "TraceSet": {"traces": [{"trace_id": "t1", "spans": []}]},
    "MetricSeries": {"name": "latency_ms", "points": [[1, 10.0], [2, 11.5]]},
    "DataFrame": {"columns": ["a", "b"], "rows": [[1, 2], [3, 4]]},
    "FileSnapshot": {"path": "a.py", "content": "x = 1\n"},
    "SymbolSnapshot": {"symbol": "run", "kind": "function", "path": "a.py", "start_line": 1},
    "ToolSchemaSet": {"tools": [{"name": "repo.read", "schema": {"type": "object"}}]},
    "MemoryObservation": {"observation": "tests prefer pytest", "scope": "project"},
    "TaskStateSnapshot": {"task_id": "task-1", "state": {"phase": "verify", "done": False}},
}


class TypedContextObjectStoreTests(unittest.TestCase):
    def test_declared_type_registry_matches_roadmap_surface(self) -> None:
        self.assertEqual(set(TYPE_SPECS), set(FIXTURES))
        self.assertEqual(len(TYPE_SPECS), 16)

    def test_all_declared_types_roundtrip_deterministically(self) -> None:
        for object_type, payload in FIXTURES.items():
            with self.subTest(object_type=object_type):
                value = TypedContextObject(
                    object_type=object_type,
                    representation="exact",
                    payload=payload,
                    metadata={"fixture": object_type},
                )
                decoded = TypedContextObject.from_dict(value.to_dict())
                self.assertEqual(decoded, value)
                self.assertEqual(decoded.canonical_bytes(), value.canonical_bytes())
                self.assertTrue(value.object_sha256.startswith("sha256:"))
                self.assertEqual(len(value.object_sha256), 71)

    def test_all_representations_compile_to_universal_items(self) -> None:
        for representation in ("exact", "structural", "semantic", "bounded-preview"):
            with self.subTest(representation=representation):
                value = TypedContextObject(
                    object_type="SearchResultSet",
                    representation=representation,
                    payload=FIXTURES["SearchResultSet"],
                )
                item = value.to_universal(provenance=ContextProvenance(source="test"))
                self.assertEqual(item.representation, representation)
                self.assertEqual(item.kind, "typed-context:SearchResultSet")
                self.assertTrue(item.verify_integrity())
                self.assertEqual(TypedContextObject.from_universal(item), value)

    def test_missing_required_payload_field_fails_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, "missing required payload fields"):
            TypedContextObject(
                object_type="GitDiff",
                representation="exact",
                payload={"base": "a", "head": "b", "files": []},
            )

    def test_unknown_type_and_representation_fail_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, "unknown typed context object"):
            TypedContextObject(object_type="MagicBlob", representation="exact", payload={})
        with self.assertRaisesRegex(ValueError, "unknown representation"):
            TypedContextObject(object_type="TraceSet", representation="telepathic", payload=FIXTURES["TraceSet"])

    def test_representation_changes_universal_identity(self) -> None:
        provenance = ContextProvenance(source="test")
        exact = TypedContextObject("FileSnapshot", "exact", FIXTURES["FileSnapshot"])
        semantic = TypedContextObject("FileSnapshot", "semantic", FIXTURES["FileSnapshot"])
        exact_item = exact.to_universal(provenance=provenance)
        semantic_item = semantic.to_universal(provenance=provenance)
        self.assertNotEqual(exact_item.item_id, semantic_item.item_id)
        self.assertNotEqual(exact.object_sha256, semantic.object_sha256)

    def test_provenance_changes_universal_identity_without_changing_typed_digest(self) -> None:
        value = TypedContextObject("TaskStateSnapshot", "exact", FIXTURES["TaskStateSnapshot"])
        first = value.to_universal(provenance=ContextProvenance(source="agent", repository_commit="one"))
        second = value.to_universal(provenance=ContextProvenance(source="agent", repository_commit="two"))
        self.assertEqual(first.content["payload"], second.content["payload"])
        self.assertEqual(first.metadata["typed_object_sha256"], second.metadata["typed_object_sha256"])
        self.assertNotEqual(first.item_id, second.item_id)

    def test_kind_type_mismatch_fails_closed(self) -> None:
        value = TypedContextObject("TraceSet", "structural", FIXTURES["TraceSet"])
        item = UniversalContextItem.build(
            kind="typed-context:MetricSeries",
            representation="structural",
            content=value.to_dict(),
            provenance=ContextProvenance(source="test"),
            metadata={
                "typed_context_schema_version": value.schema_version,
                "typed_object_type": value.object_type,
                "typed_object_sha256": value.object_sha256,
            },
        )
        with self.assertRaisesRegex(ValueError, "kind/object_type mismatch"):
            TypedContextObject.from_universal(item)

    def test_typed_digest_mismatch_fails_closed_even_on_valid_universal_item(self) -> None:
        value = TypedContextObject("TraceSet", "structural", FIXTURES["TraceSet"])
        item = UniversalContextItem.build(
            kind="typed-context:TraceSet",
            representation="structural",
            content=value.to_dict(),
            provenance=ContextProvenance(source="test"),
            metadata={
                "typed_context_schema_version": value.schema_version,
                "typed_object_type": value.object_type,
                "typed_object_sha256": "sha256:" + "a" * 64,
            },
        )
        self.assertTrue(item.verify_integrity())
        with self.assertRaisesRegex(ValueError, "typed context object digest mismatch"):
            TypedContextObject.from_universal(item)

    def test_store_roundtrip_uses_evidence_store_and_preserves_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = TypedContextObjectStore(Path(directory) / "typed.sqlite3")
            value = TypedContextObject("FileSnapshot", "exact", FIXTURES["FileSnapshot"])
            receipt = store.put(
                value,
                provenance=ContextProvenance(source="repository", repository_commit="abc"),
                trust=ContextTrust(level="verified", confidence=1.0),
                recovery=(RecoveryHandle(
                    kind="file-range",
                    locator={"path": "a.py", "start_line": 1, "end_line": 1},
                    integrity="b" * 64,
                ),),
                observed_at="2026-08-18T00:00:00+00:00",
            )
            self.assertEqual(receipt["object_type"], "FileSnapshot")
            loaded = store.require(receipt["item_id"])
            self.assertEqual(loaded, value)
            universal = store.get_universal(receipt["item_id"])
            self.assertIsNotNone(universal)
            assert universal is not None
            self.assertEqual(universal.recovery[0].kind, "file-range")
            self.assertTrue(store.verify_item(receipt["item_id"])["ok"])
            self.assertEqual(store.stats()["items"], 1)

    def test_store_inherits_evidence_secret_rejection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = TypedContextObjectStore(Path(directory) / "typed.sqlite3")
            value = TypedContextObject(
                "FileSnapshot",
                "exact",
                {"path": "secret.txt", "content": "sk-proj-" + "A" * 32},
            )
            with self.assertRaisesRegex(ValueError, "contains secret-like material"):
                store.put(value, provenance=ContextProvenance(source="repository"))
            self.assertEqual(store.stats()["items"], 0)


if __name__ == "__main__":
    unittest.main()
