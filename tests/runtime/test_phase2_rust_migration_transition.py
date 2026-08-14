from __future__ import annotations

import unittest

from tools import report_phase2_rust_migration_transition as transition


class Phase2RustMigrationTransitionTests(unittest.TestCase):
    def _inventory(self, native: int, remaining: int) -> dict[str, object]:
        return {
            "ok": True,
            "python": {"derived_count": 245},
            "rust": {"native_count": native, "missing_count": remaining},
        }

    def test_accepts_only_frozen_and_promoted_endpoints(self) -> None:
        self.assertEqual(
            transition._inventory_state(self._inventory(174, 71)),
            "frozen-174-71",
        )
        self.assertEqual(
            transition._inventory_state(self._inventory(245, 0)),
            "promoted-245-0",
        )
        for native, remaining in [(175, 70), (200, 45), (244, 1), (245, 1), (244, 0)]:
            with self.assertRaisesRegex(AssertionError, "must remain atomic"):
                transition._inventory_state(self._inventory(native, remaining))

    def test_rejects_noncanonical_public_count_and_red_inventory(self) -> None:
        value = self._inventory(174, 71)
        value["python"] = {"derived_count": 244}
        with self.assertRaisesRegex(AssertionError, "must remain atomic"):
            transition._inventory_state(value)
        value = self._inventory(174, 71)
        value["ok"] = False
        with self.assertRaisesRegex(AssertionError, "inventory report is red"):
            transition._inventory_state(value)

    def test_promoted_ownership_requires_exact_empty_remaining_state(self) -> None:
        ownership = {
            "ok": True,
            "inventory_state": "promoted-245-0",
            "public_route_count": 245,
            "native_route_count": 245,
            "report_derived_remaining_count": 0,
            "owned_count": 0,
            "unowned_count": 0,
            "owner_module_count": 0,
            "module_unowned_count": 0,
            "duplicate_owner_count": 0,
            "promoted_public_native_set_equality": True,
            "unowned_routes": [],
            "module_unowned_routes": [],
            "duplicate_owner_routes": [],
            "selector_paths": {},
            "owner_modules": {},
            "owner_candidates": {},
        }
        transition._validate_promoted_ownership(ownership)
        for key, bad_value in [
            ("native_route_count", 244),
            ("report_derived_remaining_count", 1),
            ("promoted_public_native_set_equality", False),
            ("selector_paths", {"run fake": ["run", "fake"]}),
        ]:
            modified = dict(ownership)
            modified[key] = bad_value
            with self.assertRaises(AssertionError):
                transition._validate_promoted_ownership(modified)


if __name__ == "__main__":
    unittest.main()
