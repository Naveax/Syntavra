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
