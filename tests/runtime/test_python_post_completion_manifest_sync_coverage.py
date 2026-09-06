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
RELEASE_TRUST_PATHS = (
    ".github/workflows/publish-pre-release.yml",
    ".github/workflows/post-r38-release-provenance-diagnostic.yml",
    ".github/workflows/pre-release-candidate-receipt-plan.yml",
    ".github/workflows/python-completion-certificate.yml",
    "tests/runtime/test_release_action_pins.py",
    "tests/runtime/test_pre_release_publish_workflow_contract.py",
    "tests/runtime/test_pre_release_publication_attempt_ledger.py",
    "tools/certify_python_completion_certificate_v1.py",
)
RUNTIME_HARDEN_PATHS = (
    "syntavra_runtime/host_installation.py",
    "tests/runtime/test_host_installation_v4.py",
)


class PythonPostCompletionManifestSyncCoverageTests(unittest.TestCase):
    def test_current_authority_paths_trigger_pull_request_and_push_sync(self):
        workflow = WORKFLOW.read_text(encoding="utf-8")
        for relative in AUTHORITY_PATHS:
            token = f'- "{relative}"'
            self.assertGreaterEqual(
                workflow.count(token),
                2,
                f"post-completion manifest sync must watch {relative} on pull_request and push",
            )

    def test_release_trust_paths_trigger_pull_request_and_push_sync(self):
        workflow = WORKFLOW.read_text(encoding="utf-8")
        for relative in RELEASE_TRUST_PATHS:
            token = f'- "{relative}"'
            self.assertGreaterEqual(
                workflow.count(token),
                2,
                f"post-completion manifest sync must watch release trust path {relative}",
            )

    def test_runtime_hardening_paths_trigger_pull_request_and_push_sync(self):
        workflow = WORKFLOW.read_text(encoding="utf-8")
        for relative in RUNTIME_HARDEN_PATHS:
            token = f'- "{relative}"'
            self.assertGreaterEqual(
                workflow.count(token),
                2,
                f"post-completion manifest sync must watch runtime hardening path {relative}",
            )

    def test_manifest_sync_enforcement_remains_fail_closed(self):
        workflow = WORKFLOW.read_text(encoding="utf-8")
        self.assertGreaterEqual(workflow.count('- "MANIFEST.sha256"'), 2)
        self.assertIn("contents: write", workflow)
        self.assertIn("python tools/refresh_manifest.py", workflow)
        self.assertIn('git push origin "HEAD:${HEAD_REF}"', workflow)
        self.assertIn("Require committed manifest to be exact", workflow)
        self.assertIn("git diff --exit-code -- MANIFEST.sha256", workflow)


if __name__ == "__main__":
    unittest.main()
