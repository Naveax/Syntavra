from __future__ import annotations

import hashlib
import random
import sqlite3
import tempfile
import unittest
from pathlib import Path

from syntavra_runtime.evidence import EvidenceError, EvidenceStore
from syntavra_runtime.inference_skip_cache import (
    InferenceSkipCache,
    RepositoryFingerprint,
    build_identity,
)
from syntavra_runtime.retry_economics import (
    RetryAction,
    RetryEconomicsGovernor,
    WorkspaceStateFingerprint,
)


class RecoveryTo99PropertyMatrixTests(unittest.TestCase):
    SEED = 20260908

    def test_missing_exact_evidence_object_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = EvidenceStore(Path(temp) / "evidence", project_id="missing-object")
            raw = b"exact evidence must not silently disappear\n"
            handle = store.put(raw, kind="property")
            digest = handle.rsplit("/", 1)[-1]
            store._object_path(digest).unlink()
            with self.assertRaisesRegex(EvidenceError, "object missing"):
                store.get(handle)

    def test_corrupt_ciphertext_never_returns_partial_plaintext(self) -> None:
        rng = random.Random(self.SEED)
        with tempfile.TemporaryDirectory() as temp:
            store = EvidenceStore(Path(temp) / "evidence", project_id="corrupt-object")
            for index in range(16):
                raw = bytes(rng.randrange(0, 256) for _ in range(2048 + index * 31))
                handle = store.put(raw, kind="property")
                digest = handle.rsplit("/", 1)[-1]
                path = store._object_path(digest)
                encrypted = bytearray(path.read_bytes())
                position = max(24, min(len(encrypted) - 17, 40 + index))
                encrypted[position] ^= 0x01
                path.write_bytes(encrypted)
                with self.assertRaises(EvidenceError):
                    store.get(handle)

    def test_inference_skip_identity_mutation_matrix_never_aliases(self) -> None:
        rng = random.Random(self.SEED)
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp) / "project"
            project.mkdir()
            repository = RepositoryFingerprint("a" * 64, "property", True)
            base = build_identity(
                project=project,
                repository_fingerprint=repository,
                instruction="fix exact behavior",
                verifier=("python", "-c", "assert True"),
                mode="safe-autonomous",
                policy_fingerprint="policy-v1",
                runtime_fingerprint="runtime-v1",
            )
            keys = {base.key}
            for index in range(128):
                dimension = index % 5
                instruction = "fix exact behavior"
                verifier = ("python", "-c", "assert True")
                mode = "safe-autonomous"
                policy = "policy-v1"
                runtime = "runtime-v1"
                nonce = f"{index}-{rng.randrange(0, 10**9)}"
                if dimension == 0:
                    instruction += " " + nonce
                elif dimension == 1:
                    verifier = (*verifier, nonce)
                elif dimension == 2:
                    mode += ":" + nonce
                elif dimension == 3:
                    policy += ":" + nonce
                else:
                    runtime += ":" + nonce
                candidate = build_identity(
                    project=project,
                    repository_fingerprint=repository,
                    instruction=instruction,
                    verifier=verifier,
                    mode=mode,
                    policy_fingerprint=policy,
                    runtime_fingerprint=runtime,
                )
                self.assertNotEqual(candidate.key, base.key, (dimension, nonce))
                self.assertNotIn(candidate.key, keys)
                keys.add(candidate.key)
            self.assertEqual(len(keys), 129)

    def test_inference_skip_patch_corruption_invalidates_entry(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project = root / "project"
            project.mkdir()
            identity = build_identity(
                project=project,
                repository_fingerprint=RepositoryFingerprint("b" * 64, "property", True),
                instruction="change one value",
                verifier=("python", "-c", "assert True"),
                mode="safe-autonomous",
                policy_fingerprint="policy",
                runtime_fingerprint="runtime",
            )
            cache = InferenceSkipCache(root / "cache.sqlite3")
            patch = "diff --git a/a b/a\n--- a/a\n+++ b/a\n@@ -1 +1 @@\n-old\n+new\n"
            cache.record_verified(
                identity,
                patch=patch,
                rationale="verified",
                verification_hash=hashlib.sha256(b"verified").hexdigest(),
                verification_complete=True,
            )
            with sqlite3.connect(cache.path) as db:
                db.execute(
                    "UPDATE inference_skip_cache SET patch=? WHERE cache_key=?",
                    (patch + "corrupt\n", identity.key),
                )
                db.commit()
            self.assertIsNone(cache.lookup(identity))
            row = cache.inspect(identity.key)
            self.assertIsNotNone(row)
            self.assertEqual(int(row["valid"]), 0)
            self.assertEqual(row["invalid_reason"], "patch-integrity-failed")

    def test_retry_equivalence_property_matrix(self) -> None:
        rng = random.Random(self.SEED)
        governor = RetryEconomicsGovernor(max_presentations_per_pair=1)
        for index in range(96):
            failure = {"fingerprint": hashlib.sha256(f"failure-{index}".encode()).hexdigest()}
            state_a = WorkspaceStateFingerprint(
                hashlib.sha256(f"state-a-{index}-{rng.randrange(10**9)}".encode()).hexdigest(),
                "property",
                True,
            )
            state_b = WorkspaceStateFingerprint(
                hashlib.sha256(f"state-b-{index}-{rng.randrange(10**9)}".encode()).hexdigest(),
                "property",
                True,
            )
            first = governor.assess(failure, state_a)
            repeat = governor.assess(failure, state_a)
            changed = governor.assess(failure, state_b)
            self.assertEqual(first.action, RetryAction.ALLOW_REPAIR)
            self.assertTrue(first.allow_provider)
            self.assertEqual(repeat.action, RetryAction.STOP_EXACT_REPEAT)
            self.assertFalse(repeat.allow_provider)
            self.assertEqual(repeat.provider_calls_avoided, 1)
            self.assertEqual(changed.action, RetryAction.ALLOW_REPAIR)
            self.assertTrue(changed.allow_provider)
        self.assertEqual(governor.provider_calls_avoided, 96)

    def test_retry_uncertainty_is_never_used_as_inference_suppression(self) -> None:
        governor = RetryEconomicsGovernor()
        failure = {"fingerprint": "f" * 64}
        cases = (
            WorkspaceStateFingerprint("", "property", False, "unreadable"),
            WorkspaceStateFingerprint("", "property", True, "missing-digest"),
        )
        for state in cases:
            for _ in range(8):
                decision = governor.assess(failure, state)
                self.assertEqual(decision.action, RetryAction.ALLOW_UNCERTAIN)
                self.assertTrue(decision.allow_provider)
                self.assertEqual(decision.provider_calls_avoided, 0)
        self.assertEqual(governor.provider_calls_avoided, 0)


if __name__ == "__main__":
    unittest.main()
