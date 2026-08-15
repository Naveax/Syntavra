from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TRUST_WORKFLOWS = (
    ROOT / ".github" / "workflows" / "publish-pre-release.yml",
    ROOT / ".github" / "workflows" / "post-r38-release-provenance-diagnostic.yml",
    ROOT / ".github" / "workflows" / "pre-release-candidate-receipt-plan.yml",
    ROOT / ".github" / "workflows" / "python-publication-registry-reference.yml",
    ROOT / ".github" / "workflows" / "pre-release-publisher-prerequisites.yml",
    ROOT / ".github" / "workflows" / "release-main-merge-gate.yml",
)
MUTABLE_PINS = {
    "actions/checkout@v4": "actions/checkout@11d5960a326750d5838078e36cf38b85af677262 # v4",
    "actions/setup-python@v5": "actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065 # v5",
    "actions/setup-node@v4": "actions/setup-node@49933ea5288caeca8642d1e84afbd3f7d6820020 # v4",
    "actions/upload-artifact@v4": "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02 # v4",
    "actions/download-artifact@v4": "actions/download-artifact@d3f86a106a0bac45b974a628896c90dbdf5c8093 # v4",
    "actions/attest@v4": "actions/attest@1e69f48acb82d1966a394da916b4c1698aa569d6 # v4",
}
PUBLISH = TRUST_WORKFLOWS[0]
MERGE_GATE = TRUST_WORKFLOWS[-1]
PREREQ_TEST = ROOT / "tests" / "runtime" / "test_pre_release_publisher_prerequisites.py"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one match, found {count}")
    return text.replace(old, new, 1)


def pin_authority_workflows() -> None:
    totals = {old: 0 for old in MUTABLE_PINS}
    # publish-pre-release.yml was already pinned by PR #131. The remaining
    # authority workflows are patched here while the final contract covers all six.
    for path in TRUST_WORKFLOWS[1:]:
        text = path.read_text(encoding="utf-8")
        for old, new in MUTABLE_PINS.items():
            count = text.count(old)
            totals[old] += count
            if count:
                text = text.replace(old, new)
        path.write_text(text, encoding="utf-8", newline="\n")

    for old, count in totals.items():
        if count < 1:
            raise SystemExit(f"expected mutable release-authority ref was not present: {old}")


def hook_publish_policy() -> None:
    text = PUBLISH.read_text(encoding="utf-8")
    text = replace_once(
        text,
        "      - 'tests/runtime/test_pre_release_secret_scope.py'\n      - 'tests/runtime/test_release_main_protection.py'\n",
        "      - 'tests/runtime/test_pre_release_secret_scope.py'\n      - 'tests/runtime/test_release_action_pins.py'\n      - 'tests/runtime/test_release_main_protection.py'\n",
        "publish PR path hook",
    )
    text = replace_once(
        text,
        "          python -m unittest tests.runtime.test_pre_release_secret_scope -v\n          python -m unittest tests.runtime.test_release_main_protection -v\n",
        "          python -m unittest tests.runtime.test_pre_release_secret_scope -v\n          python -m unittest tests.runtime.test_release_action_pins -v\n          python -m unittest tests.runtime.test_release_main_protection -v\n",
        "publish PR test hook",
    )
    text = replace_once(
        text,
        "                  'immutable_external_action_pins': True,\n",
        "                  'immutable_external_action_pins': True,\n                  'immutable_release_action_pins': True,\n",
        "dry-run release action pin gate",
    )
    PUBLISH.write_text(text, encoding="utf-8", newline="\n")


def hook_merge_gate_policy() -> None:
    text = MERGE_GATE.read_text(encoding="utf-8")
    text = replace_once(
        text,
        "          python -m unittest tests.runtime.test_pre_release_secret_scope -v\n          python -m unittest tests.runtime.test_pre_release_registry_availability -v\n",
        "          python -m unittest tests.runtime.test_pre_release_secret_scope -v\n          python -m unittest tests.runtime.test_release_action_pins -v\n          python -m unittest tests.runtime.test_pre_release_registry_availability -v\n",
        "release-main merge gate policy hook",
    )
    MERGE_GATE.write_text(text, encoding="utf-8", newline="\n")


def update_prerequisite_contract() -> None:
    text = PREREQ_TEST.read_text(encoding="utf-8")
    text = replace_once(
        text,
        'self.assertIn("actions/upload-artifact@v4", text)',
        'self.assertIn("actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02", text)',
        "publisher prerequisite upload-artifact assertion",
    )
    PREREQ_TEST.write_text(text, encoding="utf-8", newline="\n")


def verify_no_known_mutable_refs() -> None:
    for path in TRUST_WORKFLOWS:
        text = path.read_text(encoding="utf-8")
        for old in MUTABLE_PINS:
            if old in text:
                raise SystemExit(f"{path}: mutable release authority action remains: {old}")
        if "pypa/gh-action-pypi-publish@release/v1" in text:
            raise SystemExit(f"{path}: mutable PyPI publish action remains")


def main() -> None:
    pin_authority_workflows()
    hook_publish_policy()
    hook_merge_gate_policy()
    update_prerequisite_contract()
    verify_no_known_mutable_refs()


if __name__ == "__main__":
    main()
