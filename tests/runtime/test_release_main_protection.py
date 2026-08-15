from __future__ import annotations

import unittest
from pathlib import Path

from tools.check_release_main_protection import (
    REQUIRED_RULE_TYPES,
    REQUIRED_STATUS_CONTEXTS,
    build_report,
)


ROOT = Path(__file__).resolve().parents[2]
PUBLISH_WORKFLOW = ROOT / ".github" / "workflows" / "publish-pre-release.yml"
MERGE_GATE_WORKFLOW = ROOT / ".github" / "workflows" / "release-main-merge-gate.yml"
HEAD = "c" * 40


def branch(*, protected: bool = True, sha: str = HEAD) -> dict:
    return {"name": "main", "commit": {"sha": sha}, "protected": protected}


def required_rules(*, contexts: list[str] | None = None) -> list[dict]:
    if contexts is None:
        contexts = sorted(REQUIRED_STATUS_CONTEXTS)
    return [
        {"type": "pull_request", "parameters": {"required_approving_review_count": 0}},
        {
            "type": "required_status_checks",
            "parameters": {
                "strict_required_status_checks_policy": True,
                "required_status_checks": [{"context": context} for context in contexts],
            },
        },
        {"type": "non_fast_forward"},
        {"type": "deletion"},
    ]


class ReleaseMainProtectionReportTests(unittest.TestCase):
    def test_complete_effective_rules_are_ready(self) -> None:
        report = build_report(exact_head=HEAD, branch=branch(), rules=required_rules())
        self.assertTrue(report["release_main_ready"])
        self.assertEqual(report["claim"], "RELEASE_MAIN_PROTECTION_READY")
        self.assertEqual(set(report["active_rule_types"]), set(REQUIRED_RULE_TYPES))
        self.assertEqual(
            set(report["required_status_check_contract"]), set(REQUIRED_STATUS_CONTEXTS)
        )
        self.assertEqual(report["missing_required_status_check_contexts"], [])
        self.assertTrue(report["required_status_checks_present"])
        self.assertFalse(report["publication_performed"])
        self.assertFalse(report["repository_mutated"])

    def test_unprotected_branch_fails_closed(self) -> None:
        report = build_report(exact_head=HEAD, branch=branch(protected=False), rules=required_rules())
        self.assertFalse(report["release_main_ready"])
        self.assertEqual(report["claim"], "RELEASE_MAIN_PROTECTION_INCOMPLETE")

    def test_missing_pull_request_rule_fails_closed(self) -> None:
        rules = [rule for rule in required_rules() if rule["type"] != "pull_request"]
        report = build_report(exact_head=HEAD, branch=branch(), rules=rules)
        self.assertFalse(report["release_main_ready"])
        self.assertIn("pull_request", report["missing_rule_types"])

    def test_status_rule_without_contexts_fails_closed(self) -> None:
        report = build_report(exact_head=HEAD, branch=branch(), rules=required_rules(contexts=[]))
        self.assertFalse(report["release_main_ready"])
        self.assertFalse(report["required_status_checks_present"])
        self.assertEqual(
            set(report["missing_required_status_check_contexts"]), set(REQUIRED_STATUS_CONTEXTS)
        )

    def test_random_required_check_does_not_satisfy_release_contract(self) -> None:
        report = build_report(
            exact_head=HEAD,
            branch=branch(),
            rules=required_rules(contexts=["package-provenance"]),
        )
        self.assertFalse(report["release_main_ready"])
        self.assertFalse(report["required_status_checks_present"])
        self.assertIn("release-main-merge-gate", report["missing_required_status_check_contexts"])

    def test_extra_status_checks_are_allowed_when_exact_gate_is_present(self) -> None:
        report = build_report(
            exact_head=HEAD,
            branch=branch(),
            rules=required_rules(contexts=["release-main-merge-gate", "package-provenance"]),
        )
        self.assertTrue(report["required_status_checks_present"])
        self.assertTrue(report["release_main_ready"])

    def test_force_push_and_deletion_guards_are_required(self) -> None:
        rules = [
            rule
            for rule in required_rules()
            if rule["type"] not in {"non_fast_forward", "deletion"}
        ]
        report = build_report(exact_head=HEAD, branch=branch(), rules=rules)
        self.assertFalse(report["release_main_ready"])
        self.assertEqual(report["missing_rule_types"], ["deletion", "non_fast_forward"])

    def test_stale_or_wrong_main_head_fails_closed(self) -> None:
        report = build_report(exact_head=HEAD, branch=branch(sha="d" * 40), rules=required_rules())
        self.assertFalse(report["branch"]["exact_head_matches"])
        self.assertFalse(report["release_main_ready"])

    def test_exact_head_is_strict_hex(self) -> None:
        with self.assertRaisesRegex(ValueError, "40 hexadecimal"):
            build_report(exact_head=("c" * 39) + "z", branch=branch(), rules=required_rules())


class ReleaseMainProtectionWorkflowContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.publish_text = PUBLISH_WORKFLOW.read_text(encoding="utf-8")
        cls.merge_gate_text = MERGE_GATE_WORKFLOW.read_text(encoding="utf-8")

    def test_publish_authority_observes_effective_main_rules(self) -> None:
        text = self.publish_text
        self.assertIn("rules/branches/main", text)
        self.assertIn("check_release_main_protection.py", text)
        self.assertIn("pre-release-main-protection", text)
        self.assertIn("--require-ready", text)

    def test_publish_mode_requires_protection_but_dry_run_only_records_it(self) -> None:
        text = self.publish_text
        self.assertIn("if [ \"$MODE\" = 'publish' ]; then", text)
        self.assertIn("protection_args+=(--require-ready)", text)
        self.assertIn("release_main_protection", text)

    def test_pr_contract_covers_release_main_protection_tests(self) -> None:
        text = self.publish_text
        self.assertIn("tests.runtime.test_release_main_protection", text)
        self.assertIn("tools/check_release_main_protection.py", text)

    def test_merge_gate_is_always_on_for_main_pull_requests(self) -> None:
        text = self.merge_gate_text
        self.assertIn("pull_request:", text)
        self.assertIn("branches:", text)
        self.assertIn("- main", text)
        self.assertNotIn("paths:", text)
        self.assertIn("name: release-main-merge-gate", text)

    def test_merge_gate_runs_release_contracts_and_manifest_check(self) -> None:
        text = self.merge_gate_text
        self.assertIn("tests.runtime.test_release_main_protection", text)
        self.assertIn("tests.runtime.test_pre_release_publish_credentials", text)
        self.assertIn("tests.runtime.test_pre_release_publish_workflow_contract", text)
        self.assertIn("tests.runtime.test_pre_release_candidate_receipt_plan", text)
        self.assertIn("tests.runtime.test_python_publication_registry_reference", text)
        self.assertIn("tools/validate_release.py --smoke", text)
        self.assertIn("tools/refresh_manifest.py --check", text)


if __name__ == "__main__":
    unittest.main()
