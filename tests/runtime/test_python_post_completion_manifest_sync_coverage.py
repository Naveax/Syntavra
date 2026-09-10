from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/python-post-completion-243-280.yml"

AUTHORITY_PATHS = (
    "contracts/python/capability-completeness-registry-v1.json",
    "contracts/python/README.md",
    "docs/SYNTAVRA_PYTHON_FIRST_CONTINUATION_CHECKLIST.md",
    "docs/SYNTAVRA_PYTHON_FIRST_LIVE_CHECKPOINT.md",
    "docs/SYNTAVRA_PYTHON_FIRST_ROADMAP_APPENDIX.md",
    "docs/SYNTAVRA_PYTHON_POST_COMPLETION_280.md",
)
HYPEREFFICIENCY_AUTHORITY_PATHS = (
    "contracts/python/hyperefficiency-roadmap-v1.json",
    "contracts/python/hyperefficiency/**",
    "docs/UNIFIED_PLAN.md",
    "docs/plans/HYPEREFFICIENCY_MASTER_ROADMAP_V6.md",
    "docs/plans/hyperefficiency/**",
    "tests/runtime/test_hyperefficiency_roadmap.py",
    "tools/validate_hyperefficiency_roadmap.py",
    ".github/workflows/hyperefficiency-roadmap.yml",
)
RELEASE_TRUST_PATHS = (
    ".github/workflows/publish-pre-release.yml",
    ".github/workflows/post-r38-release-provenance-diagnostic.yml",
    ".github/workflows/pre-release-candidate-receipt-plan.yml",
    ".github/workflows/python-completion-certificate.yml",
    ".github/workflows/release-main-merge-gate.yml",
    "tests/runtime/test_release_action_pins.py",
    "tests/runtime/test_pre_release_publish_workflow_contract.py",
    "tests/runtime/test_pre_release_publication_attempt_ledger.py",
    "tests/runtime/test_release_main_protection.py",
    "tools/certify_python_completion_certificate_v1.py",
)
RUNTIME_HARDEN_PATHS = (
    "syntavra_runtime/host_installation.py",
    "tests/runtime/test_host_installation_v4.py",
)
EXACT_HEAD_INTEGRITY_PATHS = (
    "tools/certify_release_integrity.py",
    "tests/runtime/test_release_integrity.py",
)


class PythonPostCompletionManifestSyncCoverageTests(unittest.TestCase):
    def test_current_authority_paths_trigger_pull_request_and_push_sync(self):
        workflow = WORKFLOW.read_text(encoding="utf-8")
        for relative in AUTHORITY_PATHS:
            token = f'- "{relative}"'
            self.assertGreaterEqual(workflow.count(token), 2, f"post-completion verification must watch {relative}")

    def test_hyperefficiency_authority_paths_trigger_pull_request_and_push_sync(self):
        workflow = WORKFLOW.read_text(encoding="utf-8")
        for relative in HYPEREFFICIENCY_AUTHORITY_PATHS:
            token = f'- "{relative}"'
            self.assertGreaterEqual(workflow.count(token), 2, f"post-completion verification must watch {relative}")

    def test_release_trust_paths_trigger_pull_request_and_push_sync(self):
        workflow = WORKFLOW.read_text(encoding="utf-8")
        for relative in RELEASE_TRUST_PATHS:
            token = f'- "{relative}"'
            self.assertGreaterEqual(workflow.count(token), 2, f"post-completion verification must watch {relative}")

    def test_runtime_hardening_paths_trigger_pull_request_and_push_sync(self):
        workflow = WORKFLOW.read_text(encoding="utf-8")
        for relative in RUNTIME_HARDEN_PATHS:
            token = f'- "{relative}"'
            self.assertGreaterEqual(workflow.count(token), 2, f"post-completion verification must watch {relative}")

    def test_exact_head_release_integrity_paths_trigger_pull_request_and_push_sync(self):
        workflow = WORKFLOW.read_text(encoding="utf-8")
        for relative in EXACT_HEAD_INTEGRITY_PATHS:
            token = f'- "{relative}"'
            self.assertGreaterEqual(workflow.count(token), 2, f"exact-head release integrity must watch {relative}")

    def test_manifest_enforcement_is_read_only_exact_head_and_reversible(self):
        workflow = WORKFLOW.read_text(encoding="utf-8")
        self.assertGreaterEqual(workflow.count('- "MANIFEST.sha256"'), 2)
        self.assertIn("contents: read", workflow)
        self.assertNotIn("contents: write", workflow)
        self.assertIn("python tools/certify_release_integrity.py", workflow)
        self.assertIn("--expected-head", workflow)
        self.assertIn("--manifest-output /tmp/post-completion-generated-manifest.sha256", workflow)
        self.assertIn("python tools/refresh_manifest.py", workflow)
        self.assertIn("python tools/refresh_manifest.py --check", workflow)
        self.assertIn("cp MANIFEST.sha256 /tmp/MANIFEST.committed.sha256", workflow)
        self.assertIn("cp /tmp/MANIFEST.committed.sha256 MANIFEST.sha256", workflow)
        self.assertIn("python tools/validate.py", workflow)
        self.assertNotIn("git push", workflow)
        self.assertNotIn("git commit", workflow)
        self.assertNotIn("HEAD_REF", workflow)
        self.assertNotIn("github.head_ref", workflow)


if __name__ == "__main__":
    unittest.main()
