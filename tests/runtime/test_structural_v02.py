from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from signalcore_runtime.structural import StructuralIndex


class StructuralV02Tests(unittest.TestCase):
    def test_transitive_reverse_impact_chain(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            length = 81
            for index in range(length):
                target = f"func_{index - 1}" if index else None
                body = f"def func_{index}():\n"
                body += f"    return {target}()\n" if target else "    return 0\n"
                name = f"test_module_{index:03d}.py" if index == length - 1 else f"module_{index:03d}.py"
                (root / name).write_text(body, encoding="utf-8")
            index = StructuralIndex(root / "state.sqlite3", repository_root=root, repository_id="r")
            result = index.index()
            self.assertEqual(result["changed"], length)
            impact = index.inspect_impact("func_0", max_depth=100)
            self.assertEqual(len(impact["affected_paths"]), length)
            self.assertTrue(any(path.startswith("test_") for path in impact["affected_tests"]))
            self.assertGreaterEqual(max(row["depth"] for row in impact["transitive_references"]), 80)


if __name__ == "__main__":
    unittest.main()
