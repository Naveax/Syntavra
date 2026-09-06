from __future__ import annotations

import copy
import tempfile
import tomllib
import unittest
from pathlib import Path

from syntavra_runtime.backup import BackupError, StateBackupManager
from tools import report_missing_native_public_routes as public_surface
from tools import report_python_public_execution_contract as execution_contract
from tools.certify_python_core_legacy_reference import (
    CONTRACT_RELATIVE,
    _dp_explicit_routes,
    _parser_leaf_index,
    _read_json,
    _strict_hash,
)


class PythonCoreLegacyReferenceArchitectureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.repo = Path(__file__).resolve().parents[2]
        cls.contract = _read_json(cls.repo / CONTRACT_RELATIVE)
        cls.canonical = set(public_surface.python_public_routes())
        cls.dp_routes, cls.dp_rows = _dp_explicit_routes(cls.repo, cls.canonical)
        cls.targets = sorted(cls.canonical - cls.dp_routes)
        cls.execution = execution_contract.route_execution_manifest()

    def test_selection_is_derived_and_exact(self) -> None:
        selection = self.contract["selection"]
        self.assertEqual(len(self.canonical), selection["expected_canonical_route_count"])
        self.assertEqual(len(self.dp_routes), selection["expected_dp_explicit_route_count"])
        self.assertEqual(len(self.targets), selection["expected_target_route_count"])
        self.assertTrue(selection["hardcoded_target_route_list_forbidden"])

    def test_source_counts_match_derived_execution_authority(self) -> None:
        by_route = {row["route"]: row for row in self.execution}
        counts: dict[str, int] = {}
        for route in self.targets:
            sources = by_route[route]["sources"]
            self.assertEqual(len(sources), 1, (route, sources))
            source = sources[0]
            counts[source] = counts.get(source, 0) + 1
        self.assertEqual(counts, self.contract["selection"]["expected_source_counts"])

    def test_every_parser_owned_target_has_a_leaf_parser(self) -> None:
        index = _parser_leaf_index()
        by_route = {row["route"]: row for row in self.execution}
        missing = []
        for route in self.targets:
            row = by_route[route]
            if not row["parser_owned"]:
                continue
            source = row["sources"][0]
            if (source, route) not in index:
                missing.append((source, route))
        self.assertEqual(missing, [])

    def test_installed_read_only_routes_are_value_variants_not_fake_parser_leaves(self) -> None:
        by_route = {row["route"]: row for row in self.execution}
        installed = [route for route in self.targets if by_route[route]["sources"] == ["engine-installed-read-only"]]
        self.assertEqual(len(installed), 17)
        self.assertTrue(all(not by_route[route]["parser_owned"] for route in installed))
        self.assertTrue(all(route.startswith("engine route ") for route in installed))

    def test_invalid_encrypted_backup_is_owned_by_backup_domain(self) -> None:
        with tempfile.TemporaryDirectory(prefix="syntavra-backup-domain-") as directory:
            root = Path(directory)
            state = root / "state"
            source = root / "not-a-backup.scbackup"
            source.write_text("not a sealed backup", encoding="utf-8")
            manager = StateBackupManager(state, project_id="fixture-project")
            with self.assertRaisesRegex(BackupError, "invalid encrypted backup"):
                manager.verify(source, encrypted=True)

    def test_behavior_freeze_parser_pack_is_exact_pinned(self) -> None:
        pyproject = tomllib.loads((self.repo / "pyproject.toml").read_text(encoding="utf-8"))
        required = "tree-sitter-language-pack==1.16.2"
        self.assertIn(required, pyproject["project"]["dependencies"])
        self.assertIn(required, pyproject["project"]["optional-dependencies"]["code-intelligence"])
        self.assertNotIn("tree-sitter-language-pack>=0.9", pyproject["project"]["dependencies"])

    def test_bootstrap_hash_policy_allows_discovery_but_strict_requires_hashes(self) -> None:
        bootstrap = copy.deepcopy(self.contract)
        bootstrap["strict"] = False
        for key in bootstrap["derived_freeze"]:
            bootstrap["derived_freeze"][key] = None
            _strict_hash(bootstrap, key, "0" * 64)

        strict = copy.deepcopy(bootstrap)
        strict["strict"] = True
        for key in strict["derived_freeze"]:
            with self.subTest(key=key):
                with self.assertRaisesRegex(AssertionError, "strict core/legacy reference missing hash"):
                    _strict_hash(strict, key, "0" * 64)

    def test_current_strict_contract_is_complete_when_promoted(self) -> None:
        if not self.contract["strict"]:
            self.skipTest("core/legacy reference remains in bootstrap mode")
        self.assertEqual(self.contract["claim"], "CORE_LEGACY_ROUTE_REFERENCE_FROZEN")
        for key, value in self.contract["derived_freeze"].items():
            with self.subTest(key=key):
                self.assertIsInstance(value, str)
                self.assertEqual(len(value), 64)
                _strict_hash(self.contract, key, value)

    def test_strict_hash_mismatch_fails_closed(self) -> None:
        strict = copy.deepcopy(self.contract)
        strict["strict"] = True
        key = "expected_route_contract_sha256"
        strict["derived_freeze"][key] = "1" * 64
        with self.assertRaisesRegex(AssertionError, "core/legacy reference drift"):
            _strict_hash(strict, key, "2" * 64)

    def test_rust_promotion_remains_blocked(self) -> None:
        self.assertFalse(self.contract["rust_native_promotion_credit"])
        self.assertEqual(self.contract["frozen_rust_native_count"], 174)


if __name__ == "__main__":
    unittest.main()
