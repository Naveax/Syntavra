from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from syntavra_runtime.cli import build_parser
from syntavra_runtime.competitive_cli import _skill_root


class FabricSkillRootNamespaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.project = self.root / "project"
        self.global_skill = self.root / "global-skill"
        self.child_skill = self.root / "child-skill"
        for path in (self.project, self.global_skill, self.child_skill):
            path.mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def _parse(self, *tail: str):
        return build_parser().parse_args([
            "--project", str(self.project),
            "--skill-root", str(self.global_skill),
            *tail,
        ])

    def test_global_skill_root_survives_fabric_child_defaults(self):
        args = self._parse("fabric", "install", "codex")
        self.assertEqual(args.skill_root, str(self.global_skill))
        self.assertIsNone(args.fabric_skill_root)
        self.assertEqual(_skill_root(args), self.global_skill.resolve())

    def test_fabric_skill_root_override_has_distinct_namespace(self):
        args = self._parse(
            "fabric", "verify-install", "codex",
            "--skill-root", str(self.child_skill),
        )
        self.assertEqual(args.skill_root, str(self.global_skill))
        self.assertEqual(args.fabric_skill_root, str(self.child_skill))
        self.assertEqual(_skill_root(args), self.child_skill.resolve())


if __name__ == "__main__":
    unittest.main()
