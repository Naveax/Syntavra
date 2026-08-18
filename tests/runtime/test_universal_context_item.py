from __future__ import annotations

import copy
import unittest

from syntavra_runtime.context_pack import ContextPackItem
from syntavra_runtime.runtime_evidence import EvidenceEdge, EvidenceNode
from syntavra_runtime.universal_context_item import (
    ContextFreshness,
    ContextProvenance,
    ContextTrust,
    RecoveryHandle,
    UniversalContextItem,
)


HEX_A = "a" * 64
HEX_B = "b" * 64


class UniversalContextItemTests(unittest.TestCase):
    def test_exact_item_roundtrip_is_deterministic(self) -> None:
        provenance = ContextProvenance(
            source="repository",
            repository_commit="abc123",
            parent_item_ids=(f"sha256:{HEX_B}", f"sha256:{HEX_A}", f"sha256:{HEX_B}"),
            metadata={"path": "src/main.py"},
        )
        recovery = RecoveryHandle(
            kind="file-range",
            locator={"path": "src/main.py", "start_line": 2, "end_line": 4},
            integrity=HEX_A,
        )
        item = UniversalContextItem.build(
            kind="repository-definition",
            representation="exact",
            content={"text": "def run():\n    return 1\n", "path": "src/main.py"},
            provenance=provenance,
            trust=ContextTrust(level="verified", confidence=1.0, taint=("user-data", "user-data")),
            freshness=ContextFreshness(state="fresh", lease_id="lease-1"),
            recovery=(recovery,),
            metadata={"tokens": 8},
        )
        decoded = UniversalContextItem.from_dict(item.to_dict())
        self.assertEqual(decoded, item)
        self.assertTrue(decoded.verify_integrity())
        self.assertEqual(decoded.canonical_bytes(), item.canonical_bytes())
        self.assertEqual(decoded.provenance.parent_item_ids, tuple(sorted({f"sha256:{HEX_A}", f"sha256:{HEX_B}"})))
        self.assertEqual(decoded.trust.taint, ("user-data",))

    def test_stable_identity_excludes_trust_and_freshness_evaluation(self) -> None:
        provenance = ContextProvenance(source="tool:test", repository_commit="deadbeef")
        first = UniversalContextItem.build(
            kind="tool-result",
            representation="exact",
            content={"ok": True, "value": 7},
            provenance=provenance,
            trust=ContextTrust(level="observed", confidence=0.5),
            freshness=ContextFreshness(state="fresh"),
        )
        second = UniversalContextItem.build(
            kind="tool-result",
            representation="exact",
            content={"value": 7, "ok": True},
            provenance=provenance,
            trust=ContextTrust(level="verified", confidence=1.0, reasons=("verified-later",)),
            freshness=ContextFreshness(state="stale", expires_at="2026-08-19T00:00:00Z"),
        )
        self.assertEqual(first.item_id, second.item_id)
        self.assertEqual(first.content_sha256, second.content_sha256)
        self.assertNotEqual(first.to_dict()["trust"], second.to_dict()["trust"])

    def test_content_or_provenance_change_changes_identity(self) -> None:
        first = UniversalContextItem.build(
            kind="memory",
            representation="semantic",
            content={"fact": "alpha"},
            provenance=ContextProvenance(source="memory", repository_commit="one"),
        )
        changed_content = UniversalContextItem.build(
            kind="memory",
            representation="semantic",
            content={"fact": "beta"},
            provenance=ContextProvenance(source="memory", repository_commit="one"),
        )
        changed_provenance = UniversalContextItem.build(
            kind="memory",
            representation="semantic",
            content={"fact": "alpha"},
            provenance=ContextProvenance(source="memory", repository_commit="two"),
        )
        self.assertNotEqual(first.item_id, changed_content.item_id)
        self.assertNotEqual(first.item_id, changed_provenance.item_id)

    def test_tampered_content_fails_closed(self) -> None:
        item = UniversalContextItem.build(
            kind="tool-result",
            representation="exact",
            content={"stdout": "safe"},
            provenance=ContextProvenance(source="tool:test"),
        )
        tampered = copy.deepcopy(item.to_dict())
        tampered["content"]["stdout"] = "tampered"
        with self.assertRaisesRegex(ValueError, "content_sha256"):
            UniversalContextItem.from_dict(tampered)

    def test_tampered_identity_fails_closed(self) -> None:
        item = UniversalContextItem.build(
            kind="tool-result",
            representation="exact",
            content={"stdout": "safe"},
            provenance=ContextProvenance(source="tool:test"),
        )
        tampered = copy.deepcopy(item.to_dict())
        tampered["item_id"] = f"sha256:{HEX_B}"
        with self.assertRaisesRegex(ValueError, "item_id"):
            UniversalContextItem.from_dict(tampered)

    def test_invalid_trust_and_recovery_values_fail_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, "unknown trust level"):
            ContextTrust(level="magical", confidence=1.0)
        with self.assertRaisesRegex(ValueError, "confidence"):
            ContextTrust(level="observed", confidence=1.1)
        with self.assertRaisesRegex(ValueError, "unknown recovery kind"):
            RecoveryHandle(kind="telepathy", locator={"x": 1}, integrity=HEX_A)
        with self.assertRaisesRegex(ValueError, "non-empty"):
            RecoveryHandle(kind="artifact", locator={}, integrity=HEX_A)

    def test_context_pack_adapter_preserves_exact_recovery(self) -> None:
        legacy = ContextPackItem(
            tier="mandatory",
            kind="definition",
            path="src/app.py",
            start_line=10,
            end_line=14,
            text="def app():\n    return True\n",
            tokens=9,
            token_confidence="high",
            file_hash=HEX_A,
            reason="exact definition",
        )
        item = UniversalContextItem.from_context_pack_item(legacy, repository_commit="commit-1")
        self.assertTrue(item.verify_integrity())
        self.assertEqual(item.representation, "exact")
        self.assertEqual(item.content["path"], legacy.path)
        self.assertEqual(item.content["text"], legacy.text)
        self.assertEqual(item.provenance.source, "context-pack")
        self.assertEqual(item.provenance.repository_commit, "commit-1")
        self.assertEqual(item.trust.level, "verified")
        self.assertEqual(len(item.recovery), 1)
        handle = item.recovery[0]
        self.assertEqual(handle.kind, "file-range")
        self.assertEqual(handle.locator, {"path": "src/app.py", "start_line": 10, "end_line": 14})
        self.assertEqual(handle.integrity, f"sha256:{HEX_A}")
        self.assertTrue(handle.exact)

    def test_evidence_node_adapter_preserves_provenance(self) -> None:
        node = EvidenceNode(
            node_id=HEX_A,
            kind="file",
            label="src/app.py",
            source="coverage",
            confidence=0.75,
            repository_commit="commit-2",
            metadata={"suite": "unit"},
        )
        item = UniversalContextItem.from_evidence_node(node)
        self.assertTrue(item.verify_integrity())
        self.assertEqual(item.kind, "evidence-node:file")
        self.assertEqual(item.provenance.source, "coverage")
        self.assertEqual(item.provenance.repository_commit, "commit-2")
        self.assertEqual(item.provenance.metadata, {"suite": "unit"})
        self.assertEqual(item.trust.confidence, 0.75)
        self.assertEqual(item.recovery[0].kind, "evidence-node")
        self.assertEqual(item.recovery[0].locator["node_id"], HEX_A)

    def test_evidence_edge_adapter_preserves_observation_and_recovery(self) -> None:
        edge = EvidenceEdge(
            source=HEX_A,
            target=HEX_B,
            relation="COVERS",
            evidence=f"sha256:{HEX_A}",
            confidence=0.9,
            repository_commit="commit-3",
            observed_at="2026-08-18T12:00:00+00:00",
            metadata={"executed_lines": [1, 2, 3]},
        )
        item = UniversalContextItem.from_evidence_edge(edge)
        self.assertTrue(item.verify_integrity())
        self.assertEqual(item.content["relation"], "COVERS")
        self.assertEqual(item.provenance.observed_at, edge.observed_at)
        self.assertEqual(item.freshness.observed_at, edge.observed_at)
        self.assertEqual(item.recovery[0].kind, "evidence-edge")
        self.assertEqual(item.recovery[0].integrity, edge.evidence)
        self.assertEqual(item.trust.confidence, 0.9)


if __name__ == "__main__":
    unittest.main()
