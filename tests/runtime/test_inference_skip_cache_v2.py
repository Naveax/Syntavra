from __future__ import annotations

import hashlib
import sys
import tempfile
import unittest
from pathlib import Path

from syntavra_runtime.inference_skip_cache import (
    InferenceSkipCache,
    RepositoryFingerprint,
    build_identity,
)


PATCH = (
    "diff --git a/value.txt b/value.txt\n"
    "--- a/value.txt\n"
    "+++ b/value.txt\n"
    "@@ -1 +1 @@\n"
    "-old\n"
    "+new\n"
)


class InferenceSkipCacheV2Tests(unittest.TestCase):
    @staticmethod
    def _project(root: Path) -> Path:
        project = root / "project"
        project.mkdir()
        (project / "pyproject.toml").write_text(
            "[project]\nname='identity-fixture'\nversion='0.0.1'\n",
            encoding="utf-8",
        )
        return project

    @staticmethod
    def _repo() -> RepositoryFingerprint:
        return RepositoryFingerprint("a" * 64, "test-exact", True)

    def _identity(self, project: Path, **overrides):
        values = {
            "project": project,
            "repository_fingerprint": self._repo(),
            "instruction": "change old to new",
            "verifier": (sys.executable, "-c", "assert True"),
            "mode": "safe-autonomous",
            "policy_fingerprint": "policy-v1",
        }
        values.update(overrides)
        return build_identity(**values)

    def test_identity_contains_all_required_v2_fingerprint_classes(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            identity = self._identity(self._project(Path(temp)))
            values = (
                identity.task_family_hash,
                identity.dependency_hash,
                identity.toolchain_hash,
                identity.environment_hash,
                identity.tool_schema_hash,
                identity.security_hash,
                identity.verifier_contract_hash,
            )
            self.assertTrue(all(len(value) == 64 for value in values))
            self.assertEqual(len(identity.key), 64)

    def test_every_explicit_contract_dimension_changes_exact_key(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = self._project(Path(temp))
            base = self._identity(project)
            cases = (
                {"task_family": "review-agent"},
                {"dependency_fingerprint": "deps-v2"},
                {"toolchain_fingerprint": "toolchain-v2"},
                {"environment_fingerprint": "environment-v2"},
                {"tool_schema_fingerprint": "tools-v2"},
                {"security_fingerprint": "security-v2"},
                {"verifier_contract_fingerprint": "verifier-contract-v2"},
                {"runtime_fingerprint": "runtime-v2"},
            )
            for change in cases:
                with self.subTest(change=change):
                    self.assertNotEqual(self._identity(project, **change).key, base.key)

    def test_dependency_manifest_mutation_changes_key_even_with_frozen_repository_digest(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = self._project(Path(temp))
            first = self._identity(project)
            (project / "pyproject.toml").write_text(
                "[project]\nname='identity-fixture'\nversion='0.0.2'\n",
                encoding="utf-8",
            )
            second = self._identity(project)
            self.assertNotEqual(first.dependency_hash, second.dependency_hash)
            self.assertNotEqual(first.key, second.key)

    def test_near_match_is_refused_and_recorded_as_prevented_semantic_hit(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project = self._project(root)
            cache = InferenceSkipCache(root / "cache.sqlite3")
            first = self._identity(project, security_fingerprint="security-a")
            cache.record_verified(
                first,
                patch=PATCH,
                rationale="verified",
                verification_hash=hashlib.sha256(b"verified").hexdigest(),
                verification_complete=True,
            )
            changed = self._identity(project, security_fingerprint="security-b")
            self.assertIsNone(cache.lookup(changed))
            rows = cache.preventions()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["event_type"], "EXACT_IDENTITY_NEAR_MISS")
            self.assertIn("SECURITY_HASH_MISMATCH", rows[0]["reasons"])
            self.assertEqual(cache.prevention_stats()["occurrences"], 1)

    def test_exact_verified_hit_still_replays_after_v2_hardening(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project = self._project(root)
            cache = InferenceSkipCache(root / "cache.sqlite3")
            identity = self._identity(project)
            verification_hash = hashlib.sha256(b"verified").hexdigest()
            cache.record_verified(
                identity,
                patch=PATCH,
                rationale="verified",
                verification_hash=verification_hash,
                verification_complete=True,
            )
            hit = cache.lookup(identity)
            self.assertIsNotNone(hit)
            assert hit is not None
            self.assertEqual(hit.patch, PATCH)
            self.assertEqual(hit.verification_hash, verification_hash)
            self.assertEqual(hit.hit_count, 1)
            self.assertEqual(cache.prevention_stats()["occurrences"], 0)

    def test_patch_integrity_rejection_records_prevention(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project = self._project(root)
            cache = InferenceSkipCache(root / "cache.sqlite3")
            identity = self._identity(project)
            cache.record_verified(
                identity,
                patch=PATCH,
                rationale="verified",
                verification_hash=hashlib.sha256(b"verified").hexdigest(),
                verification_complete=True,
            )
            import sqlite3

            with sqlite3.connect(cache.path) as db:
                db.execute(
                    "UPDATE inference_skip_cache SET patch=? WHERE cache_key=?",
                    (PATCH + "corrupt\n", identity.key),
                )
                db.commit()
            self.assertIsNone(cache.lookup(identity))
            row = cache.inspect(identity.key)
            self.assertIsNotNone(row)
            assert row is not None
            self.assertEqual(row["invalid_reason"], "patch-integrity-failed")
            prevention = cache.preventions()[0]
            self.assertEqual(prevention["event_type"], "UNSAFE_EXACT_HIT_REJECTED")
            self.assertIn("PATCH_INTEGRITY_FAILED", prevention["reasons"])


if __name__ == "__main__":
    unittest.main()
