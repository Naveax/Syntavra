from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from syntavra_runtime.evidence_store import EvidenceStoreV2
from syntavra_runtime.universal_context_item import (
    ContextFreshness,
    ContextProvenance,
    ContextTrust,
    RecoveryHandle,
    UniversalContextItem,
)


HEX_A = "a" * 64


def make_item(
    *,
    content: dict | None = None,
    trust: ContextTrust | None = None,
    freshness: ContextFreshness | None = None,
    parents: tuple[str, ...] = (),
    recovery: tuple[RecoveryHandle, ...] = (),
    metadata: dict | None = None,
) -> UniversalContextItem:
    return UniversalContextItem.build(
        kind="test-evidence",
        representation="exact",
        content=content or {"value": 1},
        provenance=ContextProvenance(
            source="test-suite",
            repository_commit="fixture",
            parent_item_ids=parents,
        ),
        trust=trust or ContextTrust(level="observed", confidence=0.5),
        freshness=freshness or ContextFreshness(state="fresh"),
        recovery=recovery,
        metadata=metadata or {},
    )


class EvidenceStoreV2Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "evidence.sqlite3"
        self.store = EvidenceStoreV2(self.path)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_put_get_roundtrip_is_content_addressed(self) -> None:
        item = make_item(
            recovery=(RecoveryHandle(kind="artifact", locator={"artifact": "fixture"}, integrity=HEX_A),),
        )
        receipt = self.store.put(item, observed_at="2026-08-18T00:00:00+00:00")
        self.assertTrue(receipt["ok"])
        self.assertEqual(receipt["action"], "put-new")
        self.assertEqual(receipt["item_id"], item.item_id)
        loaded = self.store.require(item.item_id)
        self.assertEqual(loaded, item)
        self.assertTrue(loaded.verify_integrity())
        verification = self.store.verify_item(item.item_id)
        self.assertTrue(verification["ok"])
        self.assertEqual(verification["content_sha256"], item.content_sha256)
        self.assertTrue(verification["recovery"][0]["integrity_shape_ok"])

    def test_trust_and_freshness_update_keeps_identity_and_is_journaled(self) -> None:
        first = make_item(
            trust=ContextTrust(level="observed", confidence=0.4),
            freshness=ContextFreshness(state="fresh"),
        )
        second = make_item(
            trust=ContextTrust(level="verified", confidence=1.0, reasons=("reviewed",)),
            freshness=ContextFreshness(state="stale", expires_at="2026-08-19T00:00:00+00:00"),
        )
        self.assertEqual(first.item_id, second.item_id)
        self.store.put(first, observed_at="2026-08-18T00:00:00+00:00")
        receipt = self.store.put(second, observed_at="2026-08-18T01:00:00+00:00")
        self.assertEqual(receipt["action"], "evaluation-update")
        loaded = self.store.require(first.item_id)
        self.assertEqual(loaded.item_id, first.item_id)
        self.assertEqual(loaded.trust.level, "verified")
        self.assertEqual(loaded.trust.confidence, 1.0)
        self.assertEqual(loaded.freshness.state, "stale")
        actions = [row["action"] for row in self.store.journal(item_id=first.item_id)]
        self.assertEqual(actions, ["put-new", "evaluation-update"])

    def test_observing_identical_item_does_not_create_new_identity(self) -> None:
        item = make_item()
        first = self.store.put(item, observed_at="2026-08-18T00:00:00+00:00")
        second = self.store.put(item, observed_at="2026-08-18T01:00:00+00:00")
        self.assertEqual(first["item_id"], second["item_id"])
        self.assertEqual(second["action"], "observe-existing")
        self.assertEqual(self.store.stats()["items"], 1)

    def test_lineage_records_external_parent_without_requiring_parent_row(self) -> None:
        parent = "sha256:" + "b" * 64
        child = make_item(parents=(parent,))
        self.store.put(child, observed_at="2026-08-18T00:00:00+00:00")
        parents = self.store.lineage(child.item_id, direction="parents")
        self.assertEqual(parents, [{"item_id": parent, "relation": "DERIVED_FROM", "created_at": "2026-08-18T00:00:00+00:00"}])
        children = self.store.lineage(parent, direction="children")
        self.assertEqual(children[0]["item_id"], child.item_id)

    def test_duplicate_lineage_edge_is_deduplicated(self) -> None:
        parent = "sha256:" + "c" * 64
        child = make_item(parents=(parent,))
        self.store.put(child, observed_at="2026-08-18T00:00:00+00:00")
        self.store.put(child, observed_at="2026-08-18T01:00:00+00:00")
        self.assertEqual(len(self.store.lineage(child.item_id, direction="parents")), 1)

    def test_secret_bearing_item_is_rejected_before_storage(self) -> None:
        secret = "sk-proj-" + "A" * 32
        item = make_item(content={"token": secret})
        with self.assertRaisesRegex(ValueError, "contains secret-like material"):
            self.store.put(item)
        self.assertEqual(self.store.stats()["items"], 0)
        self.assertEqual(self.store.stats()["journal"], 0)

    def test_pre_redacted_item_may_be_stored_without_silent_mutation(self) -> None:
        item = make_item(content={"token": "<redacted:openai-key:0123456789ab>"})
        receipt = self.store.put(item)
        self.assertTrue(receipt["ok"])
        self.assertEqual(self.store.require(item.item_id).content, item.content)

    def test_secret_policy_cannot_be_weakened_by_constructor_flag(self) -> None:
        with self.assertRaisesRegex(ValueError, "unsupported secret policy"):
            EvidenceStoreV2(self.path, secret_policy="allow-pre-redacted")

    def test_retention_prunes_only_expired_unpinned_items(self) -> None:
        expired = make_item(content={"id": "expired"})
        pinned = make_item(content={"id": "pinned"})
        fresh = make_item(content={"id": "fresh"})
        self.store.put(expired, expires_at="2026-08-17T00:00:00+00:00")
        self.store.put(pinned, expires_at="2026-08-17T00:00:00+00:00", pinned=True)
        self.store.put(fresh, expires_at="2026-08-20T00:00:00+00:00")
        result = self.store.prune_expired(before="2026-08-18T00:00:00+00:00")
        self.assertEqual(result["removed"], [expired.item_id])
        self.assertIsNone(self.store.get(expired.item_id))
        self.assertIsNotNone(self.store.get(pinned.item_id))
        self.assertIsNotNone(self.store.get(fresh.item_id))
        actions = [row["action"] for row in self.store.journal()]
        self.assertIn("prune-expired", actions)

    def test_pin_and_retention_changes_are_explicit_and_journaled(self) -> None:
        item = make_item()
        self.store.put(item)
        self.store.pin(item.item_id, pinned=True)
        self.store.set_expiry(item.item_id, "2026-08-20T00:00:00+00:00")
        self.store.pin(item.item_id, pinned=False)
        actions = [row["action"] for row in self.store.journal(item_id=item.item_id)]
        self.assertEqual(actions, ["put-new", "pin", "retention-update", "unpin"])

    def test_journal_hash_chain_verifies_and_detects_tampering(self) -> None:
        first = make_item(content={"id": 1})
        second = make_item(content={"id": 2})
        self.store.put(first, observed_at="2026-08-18T00:00:00+00:00")
        self.store.put(second, observed_at="2026-08-18T01:00:00+00:00")
        verified = self.store.verify_journal()
        self.assertTrue(verified["ok"])
        self.assertEqual(verified["events"], 2)
        self.assertTrue(str(verified["head_hash"]).startswith("sha256:"))

        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE evidence_journal SET actor = 'tampered' WHERE sequence = 1")
            db.commit()
        tampered = self.store.verify_journal()
        self.assertFalse(tampered["ok"])
        self.assertIn(1, tampered["failures"])

    def test_bad_direction_and_missing_items_fail_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, "direction"):
            self.store.lineage("missing", direction="sideways")
        with self.assertRaises(KeyError):
            self.store.require("sha256:" + "d" * 64)
        with self.assertRaises(KeyError):
            self.store.pin("sha256:" + "d" * 64)


if __name__ == "__main__":
    unittest.main()
