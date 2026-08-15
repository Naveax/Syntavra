from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TRUST_WORKFLOWS = (
    ".github/workflows/publish-pre-release.yml",
    ".github/workflows/post-r38-release-provenance-diagnostic.yml",
    ".github/workflows/pre-release-candidate-receipt-plan.yml",
    ".github/workflows/python-publication-registry-reference.yml",
    ".github/workflows/pre-release-publisher-prerequisites.yml",
    ".github/workflows/release-main-merge-gate.yml",
)
PINNED_ACTIONS = {
    "actions/checkout": "11d5960a326750d5838078e36cf38b85af677262",
    "actions/setup-python": "a26af69be951a213d495a4c3e4e4022e16d87065",
    "actions/setup-node": "49933ea5288caeca8642d1e84afbd3f7d6820020",
    "actions/upload-artifact": "ea165f8d65b6e75b540449e92b4886f43607fa02",
    "actions/download-artifact": "d3f86a106a0bac45b974a628896c90dbdf5c8093",
    "actions/attest": "1e69f48acb82d1966a394da916b4c1698aa569d6",
    "pypa/gh-action-pypi-publish": "dc37677b2e1c63e2034f94d8a5b11f265b73ba33",
}
USES_RE = re.compile(r"(?m)^\s*-?\s*uses:\s*([^\s#]+)")
HEX40_RE = re.compile(r"^[0-9a-f]{40}$")


class ReleaseActionPinContractTests(unittest.TestCase):
    def test_every_external_release_action_is_exactly_pinned(self) -> None:
        seen: set[str] = set()
        for relative in TRUST_WORKFLOWS:
            text = (ROOT / relative).read_text(encoding="utf-8")
            refs = USES_RE.findall(text)
            self.assertTrue(refs, relative)
            for ref in refs:
                if ref.startswith("./"):
                    continue
                with self.subTest(workflow=relative, ref=ref):
                    self.assertIn("@", ref)
                    slug, revision = ref.rsplit("@", 1)
                    self.assertIn(
                        slug,
                        PINNED_ACTIONS,
                        f"unreviewed external action in release trust chain: {slug}",
                    )
                    self.assertRegex(revision, HEX40_RE)
                    self.assertEqual(revision, PINNED_ACTIONS[slug])
                    seen.add(slug)
        self.assertEqual(seen, set(PINNED_ACTIONS))

    def test_mutable_release_action_refs_are_forbidden(self) -> None:
        forbidden = re.compile(
            r"(?m)^\s*-?\s*uses:\s*[^\s#]+@(v\d+(?:\.\d+)*|main|master|release/[^\s#]+)\b"
        )
        for relative in TRUST_WORKFLOWS:
            text = (ROOT / relative).read_text(encoding="utf-8")
            with self.subTest(workflow=relative):
                self.assertIsNone(forbidden.search(text))

    def test_expected_pin_values_are_full_lowercase_git_shas(self) -> None:
        for slug, revision in PINNED_ACTIONS.items():
            with self.subTest(action=slug):
                self.assertRegex(revision, HEX40_RE)

    def test_release_main_merge_gate_runs_pin_contract(self) -> None:
        text = (ROOT / ".github/workflows/release-main-merge-gate.yml").read_text(encoding="utf-8")
        self.assertIn("tests.runtime.test_release_action_pins", text)

    def test_publish_pr_contract_tracks_pin_policy_changes(self) -> None:
        text = (ROOT / ".github/workflows/publish-pre-release.yml").read_text(encoding="utf-8")
        self.assertIn("tests/runtime/test_release_action_pins.py", text)
        self.assertIn("tests.runtime.test_release_action_pins", text)
        self.assertIn("'immutable_release_action_pins': True", text)


if __name__ == "__main__":
    unittest.main()
