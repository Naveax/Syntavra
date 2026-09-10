from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from tools.certify_release_integrity import certify, render_tracked_manifest


class ReleaseIntegrityTests(unittest.TestCase):
    def _repo(self) -> tuple[tempfile.TemporaryDirectory[str], Path, str]:
        temp = tempfile.TemporaryDirectory()
        root = Path(temp.name)
        subprocess.run(["git", "init", "-q"], cwd=root, check=True)
        subprocess.run(["git", "config", "user.email", "release-integrity@example.invalid"], cwd=root, check=True)
        subprocess.run(["git", "config", "user.name", "Syntavra Release Integrity"], cwd=root, check=True)
        (root / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
        (root / "MANIFEST.sha256").write_text("legacy stale snapshot\n", encoding="utf-8")
        subprocess.run(["git", "add", "app.py", "MANIFEST.sha256"], cwd=root, check=True)
        subprocess.run(["git", "commit", "-qm", "fixture"], cwd=root, check=True)
        head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
        return temp, root, head

    def test_clean_exact_head_passes_even_when_legacy_snapshot_is_stale(self) -> None:
        temp, root, head = self._repo()
        self.addCleanup(temp.cleanup)
        report = certify(root, expected_head=head)
        self.assertTrue(report["ok"])
        self.assertEqual(report["claim"], "EXACT_HEAD_RELEASE_INTEGRITY_PROVEN")
        self.assertFalse(report["committed_legacy_manifest_matches_generated"])
        self.assertGreaterEqual(report["score"], 9.0)

    def test_dirty_tracked_source_fails_closed(self) -> None:
        temp, root, head = self._repo()
        self.addCleanup(temp.cleanup)
        (root / "app.py").write_text("VALUE = 2\n", encoding="utf-8")
        report = certify(root, expected_head=head)
        self.assertFalse(report["ok"])
        self.assertTrue(any(reason.startswith("dirty:") for reason in report["failures"]))

    def test_relevant_untracked_source_fails_closed(self) -> None:
        temp, root, head = self._repo()
        self.addCleanup(temp.cleanup)
        (root / "new.py").write_text("NEW = True\n", encoding="utf-8")
        report = certify(root, expected_head=head)
        self.assertFalse(report["ok"])
        self.assertTrue(any("new.py" in reason for reason in report["failures"]))

    def test_transient_dependency_tree_does_not_pollute_release_identity(self) -> None:
        temp, root, head = self._repo()
        self.addCleanup(temp.cleanup)
        transient = root / "syntavra_runtime.egg-info"
        transient.mkdir()
        (transient / "PKG-INFO").write_text("generated\n", encoding="utf-8")
        report = certify(root, expected_head=head)
        self.assertTrue(report["ok"])

    def test_wrong_expected_head_fails_closed(self) -> None:
        temp, root, _ = self._repo()
        self.addCleanup(temp.cleanup)
        report = certify(root, expected_head="0" * 40)
        self.assertFalse(report["ok"])
        self.assertTrue(any(reason.startswith("exact-head-mismatch:") for reason in report["failures"]))

    def test_generated_manifest_excludes_legacy_manifest_itself(self) -> None:
        temp, root, _ = self._repo()
        self.addCleanup(temp.cleanup)
        value = render_tracked_manifest(root)
        self.assertIn("  app.py\n", value)
        self.assertNotIn("MANIFEST.sha256", value)


if __name__ == "__main__":
    unittest.main()
