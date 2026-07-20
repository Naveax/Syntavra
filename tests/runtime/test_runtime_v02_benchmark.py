from __future__ import annotations

import unittest

from benchmarks.runtime_v02_benchmark import run


class RuntimeV02BenchmarkTests(unittest.TestCase):
    def test_internal_benchmark_invariants(self):
        result = run(output_lines=20_000)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["claim"], "5X_NOT_PROVEN")
        self.assertEqual(result["structural"]["transitive_recall"], 1.0)
        self.assertLess(result["structural"]["direct_only_recall"], 0.05)


if __name__ == "__main__":
    unittest.main()
