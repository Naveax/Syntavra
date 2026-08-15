#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "publish-pre-release.yml"
TEST = ROOT / "tests" / "runtime" / "test_pre_release_publish_workflow_contract.py"

PINS = {
    "actions/checkout@v4": "actions/checkout@11d5960a326750d5838078e36cf38b85af677262 # v4",
    "actions/setup-python@v5": "actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065 # v5",
    "actions/setup-node@v4": "actions/setup-node@49933ea5288caeca8642d1e84afbd3f7d6820020 # v4",
    "actions/upload-artifact@v4": "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02 # v4",
    "actions/download-artifact@v4": "actions/download-artifact@d3f86a106a0bac45b974a628896c90dbdf5c8093 # v4",
    "pypa/gh-action-pypi-publish@release/v1": "pypa/gh-action-pypi-publish@dc37677b2e1c63e2034f94d8a5b11f265b73ba33 # release/v1",
}

EXPECTED = {
    "actions/checkout": "11d5960a326750d5838078e36cf38b85af677262",
    "actions/setup-python": "a26af69be951a213d495a4c3e4e4022e16d87065",
    "actions/setup-node": "49933ea5288caeca8642d1e84afbd3f7d6820020",
    "actions/upload-artifact": "ea165f8d65b6e75b540449e92b4886f43607fa02",
    "actions/download-artifact": "d3f86a106a0bac45b974a628896c90dbdf5c8093",
    "pypa/gh-action-pypi-publish": "dc37677b2e1c63e2034f94d8a5b11f265b73ba33",
}


def replace_exact(text: str, old: str, new: str, *, minimum: int = 1) -> str:
    count = text.count(old)
    if count < minimum:
        raise SystemExit(f"expected at least {minimum} occurrence(s) of {old!r}, found {count}")
    return text.replace(old, new)


def patch_workflow() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    for old, new in PINS.items():
        text = replace_exact(text, old, new)

    gate = "                  'step_scoped_publisher_secrets': True,\n"
    replacement = gate + "                  'immutable_external_action_pins': True,\n"
    if text.count(gate) != 1:
        raise SystemExit("dry-run step-scoped gate anchor drift")
    text = text.replace(gate, replacement, 1)

    for old in PINS:
        if old in text:
            raise SystemExit(f"mutable action ref remains after patch: {old}")
    WORKFLOW.write_text(text, encoding="utf-8", newline="\n")


def patch_test() -> None:
    text = TEST.read_text(encoding="utf-8")
    text = replace_exact(
        text,
        "actions/setup-node@v4",
        "actions/setup-node@49933ea5288caeca8642d1e84afbd3f7d6820020",
    )
    text = replace_exact(
        text,
        "pypa/gh-action-pypi-publish@release/v1",
        "pypa/gh-action-pypi-publish@dc37677b2e1c63e2034f94d8a5b11f265b73ba33",
    )

    anchor = "    def test_current_release_targets_and_publish_commands_are_explicit(self) -> None:\n"
    if text.count(anchor) != 1:
        raise SystemExit("workflow contract insertion anchor drift")

    method = '''    def test_external_actions_are_immutable_full_sha_pins(self) -> None:\n        text = self.text\n        refs = re.findall(r"(?m)^\\s*-?\\s*uses:\\s*([^\\s#]+)", text)\n        self.assertTrue(refs)\n        external = [ref for ref in refs if not ref.startswith("./")]\n        self.assertTrue(external)\n\n        expected = {\n            "actions/checkout": "11d5960a326750d5838078e36cf38b85af677262",\n            "actions/setup-python": "a26af69be951a213d495a4c3e4e4022e16d87065",\n            "actions/setup-node": "49933ea5288caeca8642d1e84afbd3f7d6820020",\n            "actions/upload-artifact": "ea165f8d65b6e75b540449e92b4886f43607fa02",\n            "actions/download-artifact": "d3f86a106a0bac45b974a628896c90dbdf5c8093",\n            "pypa/gh-action-pypi-publish": "dc37677b2e1c63e2034f94d8a5b11f265b73ba33",\n        }\n        observed: dict[str, set[str]] = {}\n        for ref in external:\n            self.assertRegex(\n                ref,\n                r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+@[0-9a-f]{40}$",\n                ref,\n            )\n            action, sha = ref.rsplit("@", 1)\n            observed.setdefault(action, set()).add(sha)\n\n        self.assertEqual(set(observed), set(expected))\n        for action, sha in expected.items():\n            self.assertEqual(observed[action], {sha}, action)\n\n        joined = "\\n".join(external)\n        for mutable in ("@v4", "@v5", "@release/v1", "@main", "@master"):\n            self.assertNotIn(mutable, joined)\n        self.assertIn("immutable_external_action_pins': True", text)\n\n'''
    text = text.replace(anchor, method + anchor, 1)
    TEST.write_text(text, encoding="utf-8", newline="\n")


if __name__ == "__main__":
    patch_workflow()
    patch_test()
