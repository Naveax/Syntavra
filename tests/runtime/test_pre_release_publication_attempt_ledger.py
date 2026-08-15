from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from tools.build_pre_release_publication_attempt_ledger import (
    ALL_TARGETS,
    PRODUCTION_TARGETS,
    build_ledger,
)


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "publish-pre-release.yml"
HEAD = "d" * 40


def job_results(**overrides: str) -> dict[str, str]:
    value = {
        "credential_preflight": "success",
        "rust_production": "success",
        "npm_installer": "success",
        "npm_sdk": "success",
        "pypi": "success",
        "vscode": "success",
        "legacy_native_companion": "skipped",
    }
    value.update(overrides)
    return value


def evidence_payload(target: str, *, visible: bool = True) -> dict:
    package = {
        "rust_contracts": "syntavra-contracts",
        "rust_core": "syntavra-core",
        "rust_cli": "syntavra-cli",
        "npm": "@syntavra/install",
        "npm_sdk": "@syntavra/sdk",
        "python": "syntavra-runtime",
        "vscode": "syntavra-vscode",
        "legacy_native_companion": "syntavra-native",
    }[target]
    registry = {
        "rust_contracts": "crates.io",
        "rust_core": "crates.io",
        "rust_cli": "crates.io",
        "npm": "npm",
        "npm_sdk": "npm",
        "python": "pypi",
        "vscode": "vscode-marketplace",
        "legacy_native_companion": "crates.io",
    }[target]
    return {
        "schema_version": 1,
        "product": "Syntavra",
        "version": "0.0.1",
        "channel": "pre-release",
        "target": target,
        "registry": registry,
        "package": package,
        "visibility_verified": visible,
        "publication_performed_by_checker": False,
        "canonical_readiness_mutated": False,
        "claim": "PUBLIC_VERSION_VISIBLE" if visible else "PUBLIC_VERSION_NOT_YET_VISIBLE",
    }


def write_evidence(root: Path, target: str, *, visible: bool = True, directory: str = "artifact") -> Path:
    path = root / directory / f"{target}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(evidence_payload(target, visible=visible), sort_keys=True) + "\n", encoding="utf-8")
    return path


class PublicationAttemptLedgerTests(unittest.TestCase):
    def test_full_production_visibility_is_complete_without_optional_legacy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for target in PRODUCTION_TARGETS:
                write_evidence(root, target)
            ledger = build_ledger(
                exact_head=HEAD,
                visibility_root=root,
                job_results=job_results(),
                legacy_requested=False,
            )
        self.assertTrue(ledger["production_publication_fully_visible"])
        self.assertTrue(ledger["all_requested_visibility_verified"])
        self.assertFalse(ledger["partial_publication_observed"])
        self.assertEqual(ledger["claim"], "REQUESTED_PUBLICATION_VISIBILITY_COMPLETE")
        self.assertFalse(ledger["canonical_readiness_mutated"])
        self.assertFalse(ledger["registry_receipts_admitted"])
        self.assertEqual(ledger["integrity_errors"], [])

    def test_partial_publication_is_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_evidence(root, "rust_contracts")
            ledger = build_ledger(
                exact_head=HEAD,
                visibility_root=root,
                job_results=job_results(rust_production="failure", npm_installer="skipped", npm_sdk="skipped", pypi="skipped", vscode="skipped"),
                legacy_requested=False,
            )
        self.assertTrue(ledger["partial_publication_observed"])
        self.assertEqual(ledger["visible_requested_targets"], ["rust_contracts"])
        self.assertEqual(ledger["claim"], "PARTIAL_PUBLICATION_VISIBILITY_OBSERVED")
        self.assertIn("rust_core", ledger["missing_requested_targets"])

    def test_no_artifacts_is_conservative_not_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ledger = build_ledger(
                exact_head=HEAD,
                visibility_root=Path(tmp) / "missing",
                job_results=job_results(rust_production="failure", npm_installer="skipped", npm_sdk="skipped", pypi="skipped", vscode="skipped"),
                legacy_requested=False,
            )
        self.assertFalse(ledger["production_publication_fully_visible"])
        self.assertFalse(ledger["partial_publication_observed"])
        self.assertEqual(ledger["claim"], "NO_REQUESTED_PUBLICATION_VISIBILITY_CONFIRMED")
        self.assertEqual(set(ledger["missing_requested_targets"]), set(PRODUCTION_TARGETS))

    def test_optional_legacy_is_required_only_when_requested(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for target in PRODUCTION_TARGETS:
                write_evidence(root, target)
            without_legacy = build_ledger(
                exact_head=HEAD,
                visibility_root=root,
                job_results=job_results(),
                legacy_requested=False,
            )
            with_legacy = build_ledger(
                exact_head=HEAD,
                visibility_root=root,
                job_results=job_results(legacy_native_companion="failure"),
                legacy_requested=True,
            )
        self.assertTrue(without_legacy["all_requested_visibility_verified"])
        self.assertFalse(with_legacy["all_requested_visibility_verified"])
        self.assertTrue(with_legacy["partial_publication_observed"])
        self.assertIn("legacy_native_companion", with_legacy["missing_requested_targets"])

    def test_evidence_is_hashed_and_boundary_fields_are_checked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = write_evidence(root, "python")
            ledger = build_ledger(
                exact_head=HEAD,
                visibility_root=root,
                job_results=job_results(pypi="success", rust_production="failure", npm_installer="skipped", npm_sdk="skipped", vscode="skipped"),
                legacy_requested=False,
            )
            expected_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        self.assertEqual(ledger["targets"]["python"]["evidence"]["sha256"], expected_hash)
        self.assertEqual(ledger["targets"]["python"]["state"], "visible")

    def test_duplicate_target_evidence_is_integrity_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_evidence(root, "npm", directory="a")
            write_evidence(root, "npm", directory="b")
            ledger = build_ledger(
                exact_head=HEAD,
                visibility_root=root,
                job_results=job_results(rust_production="failure", npm_sdk="skipped", pypi="skipped", vscode="skipped"),
                legacy_requested=False,
            )
        self.assertIn("duplicate-target-evidence:npm", ledger["integrity_errors"])

    def test_successful_job_without_visibility_evidence_is_integrity_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ledger = build_ledger(
                exact_head=HEAD,
                visibility_root=Path(tmp),
                job_results=job_results(),
                legacy_requested=False,
            )
        for target in PRODUCTION_TARGETS:
            self.assertIn(f"successful-job-without-visible-evidence:{target}", ledger["integrity_errors"])


class PublicationAttemptLedgerWorkflowContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.text = WORKFLOW.read_text(encoding="utf-8")

    def test_attempt_boundary_downloads_only_visibility_artifacts_from_same_run(self) -> None:
        text = self.text
        start = text.index("  publication-attempt-boundary:")
        block = text[start:]
        self.assertIn("actions: read", block)
        self.assertIn("actions/download-artifact@d3f86a106a0bac45b974a628896c90dbdf5c8093", block)
        self.assertIn("pattern: pre-release-publication-visibility-*", block)
        self.assertIn("path: /tmp/publication-visibility-artifacts", block)
        self.assertIn("continue-on-error: true", block)
        self.assertNotIn("run-id:", block)
        self.assertNotIn("github-token:", block)

    def test_attempt_boundary_builds_and_uploads_noncanonical_ledger(self) -> None:
        text = self.text
        start = text.index("  publication-attempt-boundary:")
        block = text[start:]
        self.assertIn("build_pre_release_publication_attempt_ledger.py", block)
        self.assertIn("--visibility-root /tmp/publication-visibility-artifacts", block)
        self.assertIn("--job-results-json /tmp/publication-job-results.json", block)
        self.assertIn("--output /tmp/publication-attempt-ledger.json", block)
        self.assertIn("pre-release-publication-attempt-ledger-${{ env.TARGET_HEAD }}", block)
        self.assertIn("if: always()", block)

    def test_ledger_builder_never_mutates_canonical_readiness(self) -> None:
        source = (ROOT / "tools" / "build_pre_release_publication_attempt_ledger.py").read_text(encoding="utf-8")
        self.assertIn('"canonical_readiness_mutated": False', source)
        self.assertIn('"registry_receipts_admitted": False', source)
        self.assertNotIn("write_text(READINESS", source)
        self.assertNotIn("publish-readiness.json').write", source)


if __name__ == "__main__":
    unittest.main()
