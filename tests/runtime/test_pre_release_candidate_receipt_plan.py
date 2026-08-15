from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.build_pre_release_candidate_receipt_plan import CandidatePlanError, build_plan


HEAD = "a" * 40


class PreReleaseCandidateReceiptPlanTests(unittest.TestCase):
    def _artifact_root(self, temp: Path) -> Path:
        root = temp / "artifacts"
        for relative in (
            "python",
            "npm-installer",
            "npm-sdk",
            "vscode",
            "rust-production",
            "rust-legacy",
        ):
            (root / relative).mkdir(parents=True, exist_ok=True)

        (root / "python" / "syntavra_runtime-0.0.1-py3-none-any.whl").write_bytes(b"wheel")
        (root / "python" / "syntavra_runtime-0.0.1.tar.gz").write_bytes(b"sdist")
        (root / "npm-installer" / "syntavra-install-0.0.1.tgz").write_bytes(b"npm")
        (root / "npm-sdk" / "syntavra-sdk-0.0.1.tgz").write_bytes(b"sdk")
        (root / "vscode" / "syntavra-vscode-0.0.1.vsix").write_bytes(b"vsix")
        (root / "rust-production" / "syntavra-contracts-0.0.1.crate").write_bytes(b"contracts")
        (root / "rust-production" / "syntavra-core-0.0.1.crate").write_bytes(b"core")
        (root / "rust-legacy" / "syntavra-native-0.0.1.crate").write_bytes(b"legacy")
        (root / "rust-production-state.json").write_text(
            json.dumps(
                {
                    "ok": True,
                    "package": "syntavra-cli",
                    "version": "0.0.1",
                    "local_build_check": "passed",
                    "package_state": "registry-dependency-publication-required",
                    "blocking_registry_dependency": "syntavra-contracts",
                    "publish_order": ["syntavra-contracts", "syntavra-core", "syntavra-cli"],
                    "registry_publication_performed": False,
                }
            ),
            encoding="utf-8",
        )
        return root

    def test_plan_freezes_all_current_release_targets_without_publication_claim(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self._artifact_root(Path(directory))
            plan = build_plan(root, exact_head=HEAD)

        self.assertEqual(plan["version"], "0.0.1")
        self.assertEqual(plan["channel"], "pre-release")
        self.assertFalse(plan["publication_performed"])
        self.assertEqual(plan["claim_boundary"], "REGISTRY_PUBLICATION_NOT_PERFORMED")
        self.assertEqual(
            tuple(plan["targets"]),
            ("python", "npm", "npm_sdk", "vscode", "native", "legacy_native_companion"),
        )
        self.assertEqual(plan["targets"]["npm_sdk"]["readiness"]["package"], "@syntavra/sdk")
        self.assertEqual(plan["targets"]["native"]["readiness"]["package"], "syntavra-cli")
        self.assertEqual(plan["targets"]["native"]["package_state"]["publish_order"][-1], "syntavra-cli")
        self.assertIsNone(plan["registry_receipts"]["pypi"])
        self.assertIsNone(plan["registry_receipts"]["npm_sdk"])
        self.assertIsNone(plan["registry_receipts"]["crates_io"]["syntavra-cli"])

    def test_plan_hashes_every_candidate_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self._artifact_root(Path(directory))
            plan = build_plan(root, exact_head=HEAD)

        rows = []
        for target in plan["targets"].values():
            rows.extend(target["artifacts"])
        self.assertGreaterEqual(len(rows), 8)
        for row in rows:
            self.assertEqual(len(row["sha256"]), 64)
            self.assertGreater(row["bytes"], 0)

    def test_missing_sdk_artifact_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self._artifact_root(Path(directory))
            (root / "npm-sdk" / "syntavra-sdk-0.0.1.tgz").unlink()
            with self.assertRaisesRegex(CandidatePlanError, "npm sdk artifact count"):
                build_plan(root, exact_head=HEAD)

    def test_published_readiness_state_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self._artifact_root(Path(directory))
            fake_readiness = {
                "version": "0.0.1",
                "channel": "pre-release",
                "python": {"published": False},
                "npm": {"published": False},
                "npm_sdk": {"published": True},
                "vscode": {"published": False},
                "native": {"published": False},
                "legacy_native_companion": {"published": False},
                "claim_boundary": "Registry publication requires owner credentials and successful release receipts.",
            }
            readiness_path = Path(directory) / "publish-readiness.json"
            readiness_path.write_text(json.dumps(fake_readiness), encoding="utf-8")
            with patch("tools.build_pre_release_candidate_receipt_plan.READINESS_PATH", readiness_path):
                with self.assertRaisesRegex(CandidatePlanError, "unexpectedly claims published state: npm_sdk"):
                    build_plan(root, exact_head=HEAD)


if __name__ == "__main__":
    unittest.main()
