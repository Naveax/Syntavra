from __future__ import annotations

import unittest

from tools import verify_dual_engine_public_surface as surface


class DualEnginePromotionBoundaryTests(unittest.TestCase):
    def test_current_contract_is_one_atomic_endpoint(self) -> None:
        result = surface.verify()
        self.assertTrue(result["ok"])
        self.assertIn(result["inventory_state"], {"frozen", "promoted"})
        if result["inventory_state"] == "frozen":
            self.assertFalse(result["full"])
            self.assertEqual(result["claim"], surface.INCOMPLETE_CLAIM)
            self.assertEqual(result["rust"]["native_public_command_count"], 174)
            self.assertEqual(result["rust"]["launcher_bridge_command_count"], 71)
            self.assertEqual(result["rust"]["missing_native_public_command_count"], 71)
            self.assertEqual(result["rust"]["native_coverage_ppm"], 710_204)
        else:
            self.assertTrue(result["full"])
            self.assertEqual(result["claim"], surface.FULL_CLAIM)
            self.assertEqual(result["rust"]["native_public_command_count"], 245)
            self.assertEqual(result["rust"]["launcher_bridge_command_count"], 0)
            self.assertEqual(result["rust"]["missing_native_public_command_count"], 0)
            self.assertEqual(result["rust"]["native_coverage_ppm"], 1_000_000)

    def test_inventory_state_accepts_only_atomic_endpoints(self) -> None:
        self.assertEqual(surface._inventory_state(245, 174, 71), "frozen")
        self.assertEqual(surface._inventory_state(245, 245, 0), "promoted")
        for native, bridge in [(175, 70), (200, 45), (244, 1), (245, 1), (244, 0)]:
            with self.assertRaisesRegex(RuntimeError, "must remain atomic"):
                surface._inventory_state(245, native, bridge)
        with self.assertRaisesRegex(RuntimeError, "public command count drift"):
            surface._inventory_state(244, 174, 70)

    def test_require_full_remains_fail_closed_until_promotion(self) -> None:
        result = surface.verify()
        if result["inventory_state"] == "frozen":
            with self.assertRaisesRegex(RuntimeError, "full dual-engine parity not reached"):
                surface.verify(require_full=True)
        else:
            full = surface.verify(require_full=True)
            self.assertTrue(full["full"])
            self.assertEqual(full["claim"], surface.FULL_CLAIM)


if __name__ == "__main__":
    unittest.main()
