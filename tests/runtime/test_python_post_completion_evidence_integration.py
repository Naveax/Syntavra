from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from syntavra_runtime.artifacts import ArtifactStore
from syntavra_runtime.evidence_store import EvidenceStoreV2
from syntavra_runtime.post_completion_evidence import EvidenceMutationJournal, MutationEvent, SecretAwareArtifactStore
from syntavra_runtime.secret_redaction import SecretRedactor


class PythonPostCompletionEvidenceIntegrationTests(unittest.TestCase):
    def test_mutation_lifecycle_persists_in_canonical_evidence_store_journal(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = EvidenceStoreV2(Path(temporary) / "evidence.sqlite3")
            lifecycle = EvidenceMutationJournal(store)
            receipt = lifecycle.persist(MutationEvent("ingest", "item-1", metadata={"source": "unit"}))
            self.assertTrue(receipt["ok"])
            self.assertTrue(receipt["canonical_store_reused"])
            rows = store.journal(item_id="item-1")
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["action"], "lifecycle-ingest")
            self.assertEqual(rows[0]["details"]["lifecycle_action"], "ingest")
            self.assertEqual(rows[0]["details"]["lifecycle_receipt_id"], receipt["lifecycle_receipt_id"])
            self.assertTrue(store.verify_journal()["ok"])

            lifecycle.persist(MutationEvent("derive", "item-2", predecessor_ids=("item-1",)))
            lifecycle.persist(MutationEvent("compress", "item-3", predecessor_ids=("item-2",)))
            lifecycle.persist(MutationEvent("supersede", "item-4", predecessor_ids=("item-3",), successor_id="item-4", reason="new evidence"))
            lifecycle.persist(MutationEvent("revoke", "item-4", reason="invalidated"))
            self.assertTrue(store.verify_journal()["ok"])
            self.assertEqual(store.stats()["journal"], 5)

    def test_secret_aware_facade_reuses_canonical_artifact_store(self):
        with tempfile.TemporaryDirectory() as temporary:
            backend = ArtifactStore(Path(temporary) / "artifacts")
            facade = SecretAwareArtifactStore(backend, SecretRedactor())
            safe = facade.put("public evidence", kind="evidence")
            self.assertTrue(safe["ok"])
            record = backend.record(safe["artifact_id"])
            self.assertEqual(record.kind, "evidence")
            self.assertEqual(backend.read(record.artifact_id), b"public evidence")

            with self.assertRaises(ValueError):
                facade.put("api_key=sk-test-secret-value", kind="evidence")

            externalized = facade.put(
                "api_key=sk-test-secret-value",
                kind="evidence",
                secret_policy="encrypted-reference",
                encrypted_reference="sha256:" + "a" * 64,
            )
            self.assertTrue(externalized["ok"])
            self.assertEqual(backend.read(externalized["artifact_id"]), b"")


if __name__ == "__main__":
    unittest.main()
