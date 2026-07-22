from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from syntavra_runtime.unified_cli import main


class UniversalLanguageCLITests(unittest.TestCase):
    def invoke(self, project: Path, state: Path, *arguments: str) -> tuple[int, dict]:
        output = io.StringIO()
        with redirect_stdout(output):
            code = main(
                [
                    "--project",
                    str(project),
                    "--state-root",
                    str(state),
                    "run",
                    "language",
                    *arguments,
                ]
            )
        return code, json.loads(output.getvalue())

    def test_inventory_uses_universal_registry(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = root / "repo"
            project.mkdir()
            code, result = self.invoke(project, root / "state", "inventory")
            self.assertEqual(code, 0)
            self.assertTrue(result["ok"])
            self.assertTrue(result["universal_text_fallback"])
            self.assertGreater(result["language_registry"]["registered_languages"], 50)
            self.assertIn("lexical", result["evidence_levels"])
            self.assertIn("semantic", result["evidence_levels"])

    def test_detect_and_index_never_seen_language(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = root / "repo"
            project.mkdir()
            source = project / "engine.language-from-2040"
            source.write_text("construct HyperDrive\nHyperDrive HyperDrive\n", encoding="utf-8")
            code, detected = self.invoke(project, root / "state", "detect", source.name)
            self.assertEqual(code, 0)
            self.assertTrue(detected["detection"]["language_id"].startswith("unknown:"))
            self.assertEqual(detected["detection"]["capability_level"], "lexical")

            code, indexed = self.invoke(project, root / "state", "index")
            self.assertEqual(code, 0)
            self.assertTrue(indexed["ok"])
            self.assertEqual(indexed["unknown_language_files"], 1)

            code, queried = self.invoke(project, root / "state", "query", "HyperDrive")
            self.assertEqual(code, 0)
            self.assertTrue(queried["results"])
            self.assertTrue(all(item["semantic_status"] == "candidate" for item in queried["results"]))

    def test_import_index_reports_exact_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = root / "repo"
            project.mkdir()
            (project / "main.future").write_text("ignite\n", encoding="utf-8")
            index = project / "index.scip.json"
            commit = "a" * 40
            index.write_text(
                json.dumps(
                    {
                        "metadata": {"version": commit, "tool_info": {"name": "future-language"}},
                        "documents": [
                            {
                                "relative_path": "main.future",
                                "language": "future-language",
                                "symbols": [{"symbol": "future pkg ignite().", "relationships": []}],
                                "occurrences": [
                                    {
                                        "range": [0, 0, 0, 6],
                                        "symbol": "future pkg ignite().",
                                        "symbol_roles": 1,
                                    }
                                ],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            code, receipt = self.invoke(
                project,
                root / "state",
                "import-index",
                index.name,
                "--format",
                "scip-json",
                "--current-commit",
                commit,
            )
            self.assertEqual(code, 0)
            self.assertTrue(receipt["ok"])
            self.assertFalse(receipt["stale"])
            self.assertEqual(receipt["evidence_status"], "exact")
            self.assertGreaterEqual(receipt["nodes"], 2)

    def test_detect_rejects_project_escape(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = root / "repo"
            project.mkdir()
            outside = root / "outside.future"
            outside.write_text("secret", encoding="utf-8")
            with self.assertRaises(PermissionError):
                self.invoke(project, root / "state", "detect", str(outside))


if __name__ == "__main__":
    unittest.main()
