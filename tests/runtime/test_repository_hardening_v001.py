from __future__ import annotations

import unittest

from tools.check_repository_hygiene import (
    check_repository,
    has_immutable_artifact_attestation,
)


class RepositoryHardeningV001Tests(unittest.TestCase):
    def test_repository_hardening_contract(self) -> None:
        result = check_repository()
        self.assertTrue(result["ok"], result["failures"])
        self.assertEqual(result["version"], "0.0.1")
        self.assertEqual(result["channel"], "pre-release")
        self.assertEqual(result["checks"]["typescript"], "6.0.3")

    def test_artifact_attestation_requires_immutable_sha(self) -> None:
        pinned = (
            "steps:\n"
            "  - uses: actions/attest@"
            "1e69f48acb82d1966a394da916b4c1698aa569d6 # v4\n"
        )
        mutable = "steps:\n  - uses: actions/attest@v4\n"
        short_sha = "steps:\n  - uses: actions/attest@1e69f48a\n"

        self.assertTrue(has_immutable_artifact_attestation(pinned))
        self.assertFalse(has_immutable_artifact_attestation(mutable))
        self.assertFalse(has_immutable_artifact_attestation(short_sha))


if __name__ == "__main__":
    unittest.main()
