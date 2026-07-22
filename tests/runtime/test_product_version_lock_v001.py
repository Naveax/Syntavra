from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

import syntavra_runtime
from syntavra_runtime.release_identity import CHANNEL, VERSION


ROOT = Path(__file__).resolve().parents[2]


class ProductVersionLockV001Tests(unittest.TestCase):
    def test_all_active_product_metadata_is_locked_to_v001_pre_release(self) -> None:
        self.assertEqual((ROOT / "VERSION").read_text(encoding="utf-8").strip(), "0.0.1")
        self.assertEqual(VERSION, "0.0.1")
        self.assertEqual(CHANNEL, "pre-release")
        self.assertEqual(syntavra_runtime.__version__, "0.0.1")
        self.assertEqual(syntavra_runtime.__release_channel__, "pre-release")

        pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertRegex(pyproject, r'(?m)^version\s*=\s*"0\.0\.1"\s*$')
        self.assertIn('"Development Status :: 2 - Pre-Alpha"', pyproject)

        package = json.loads((ROOT / "sdk" / "typescript" / "package.json").read_text(encoding="utf-8"))
        self.assertEqual(package["version"], "0.0.1")
        self.assertEqual(package["publishConfig"]["tag"], "next")

        marketplace = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))
        self.assertEqual(marketplace["version"], "0.0.1")

        gemini = json.loads((ROOT / "gemini-extension.json").read_text(encoding="utf-8"))
        self.assertEqual(gemini["version"], "0.0.1")

        release = json.loads((ROOT / "release" / "pre-release.json").read_text(encoding="utf-8"))
        self.assertEqual(release["version"], "0.0.1")
        self.assertEqual(release["channel"], "pre-release")
        self.assertTrue(release["publish_as_prerelease"])
        self.assertFalse(release["stable"])
        self.assertTrue(release["version_locked"])

    def test_public_readme_and_changelog_keep_owner_locked_policy(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        self.assertIn("0.0.1", readme)
        self.assertIn("pre-release", readme.casefold())
        self.assertIn("owner explicitly", readme.casefold())
        self.assertIn("0.0.1", changelog)
        self.assertIn("owner explicitly", changelog.casefold())


if __name__ == "__main__":
    unittest.main()
