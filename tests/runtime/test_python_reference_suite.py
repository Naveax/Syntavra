from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tools.certify_python_reference_suite import (
    CI_HEAD_ENV,
    CREDENTIAL_ENV,
    _isolated_env,
    _validate_family_report,
    load_plan,
)


class PythonReferenceSuiteArchitectureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.repo = Path(__file__).resolve().parents[2]
        cls.contract, cls.catalog, cls.plan = load_plan(cls.repo)

    def test_plan_is_derived_from_q_catalog_and_has_one_meta_certifier(self) -> None:
        self.assertEqual(len(self.catalog["families"]), 14)
        self.assertEqual(len(self.plan), 15)
        self.assertEqual(
            [row["family"] for row in self.plan[:-1]],
            [row["family"] for row in self.catalog["families"]],
        )
        self.assertEqual(self.plan[-1]["family"], "fixture-golden-catalog")
        self.assertTrue(self.plan[-1]["meta"])
        self.assertTrue(all(not row["meta"] for row in self.plan[:-1]))
        self.assertEqual(self.plan[-2]["family"], "core-legacy-route-reference")
        self.assertEqual(self.plan[-2]["section"], "T")

    def test_every_planned_certifier_exists(self) -> None:
        for row in self.plan:
            self.assertTrue((self.repo / row["certifier"]).is_file(), row)

    def test_family_report_validation_rejects_nondeterminism_outside_q_catalog(self) -> None:
        good = {
            "ok": True,
            "family": "fixture-family",
            "nondeterministic_fields": ["timestamp"],
        }
        _validate_family_report(
            report=good,
            expected_family="fixture-family",
            expected_head="head",
            expected_nondeterministic=["timestamp", "temporary fixture path"],
            meta=False,
        )
        with self.assertRaisesRegex(AssertionError, "unexpected nondeterminism drift"):
            _validate_family_report(
                report={**good, "nondeterministic_fields": ["timestamp", "surprise"]},
                expected_family="fixture-family",
                expected_head="head",
                expected_nondeterministic=["timestamp", "temporary fixture path"],
                meta=False,
            )

    def test_family_report_validation_accepts_explicit_normalization_projection(self) -> None:
        _validate_family_report(
            report={
                "ok": True,
                "family": "core-legacy-route-reference",
                "normalization": {
                    "explicit_nondeterministic_fields": ["temporary fixture path"]
                },
            },
            expected_family="core-legacy-route-reference",
            expected_head="head",
            expected_nondeterministic=["temporary fixture path"],
            meta=False,
        )

    def test_family_report_validation_rejects_duplicate_nondeterminism_declarations(self) -> None:
        with self.assertRaisesRegex(AssertionError, "duplicate nondeterministic field declaration"):
            _validate_family_report(
                report={
                    "ok": True,
                    "family": "x",
                    "nondeterministic_fields": ["timestamp", "timestamp"],
                },
                expected_family="x",
                expected_head="head",
                expected_nondeterministic=["timestamp"],
                meta=False,
            )

    def test_family_report_validation_rejects_red_or_wrong_family_or_wrong_exact_head(self) -> None:
        with self.assertRaisesRegex(AssertionError, "ok=false"):
            _validate_family_report(
                report={"ok": False, "family": "x", "nondeterministic_fields": []},
                expected_family="x",
                expected_head="head",
                expected_nondeterministic=[],
                meta=False,
            )
        with self.assertRaisesRegex(AssertionError, "family drift"):
            _validate_family_report(
                report={"ok": True, "family": "wrong", "nondeterministic_fields": []},
                expected_family="x",
                expected_head="head",
                expected_nondeterministic=[],
                meta=False,
            )
        with self.assertRaisesRegex(AssertionError, "exact-head drift"):
            _validate_family_report(
                report={
                    "ok": True,
                    "family": "x",
                    "exact_head": "wrong",
                    "nondeterministic_fields": [],
                },
                expected_family="x",
                expected_head="head",
                expected_nondeterministic=[],
                meta=False,
            )

    def test_isolated_environment_removes_credentials_preserves_runtime_home_and_sanitizes_ci_head(self) -> None:
        with tempfile.TemporaryDirectory(prefix="syntavra-reference-suite-env-") as directory:
            scratch = Path(directory)
            seeded = {key: "secret" for key in CREDENTIAL_ENV}
            seeded.update(
                {
                    "PATH": os.environ.get("PATH", ""),
                    "PYTHONPATH": "old",
                    "HOME": "/runner/home",
                    "USERPROFILE": "/runner/profile",
                    "XDG_CACHE_HOME": "/runner/cache",
                    "XDG_CONFIG_HOME": "/runner/config",
                    "XDG_DATA_HOME": "/runner/data",
                    "GITHUB_SHA": "merge-sha-not-checkout-head",
                }
            )
            with mock.patch.dict(os.environ, seeded, clear=True):
                env = _isolated_env(self.repo, scratch, self.contract)
        for key in CREDENTIAL_ENV:
            self.assertNotIn(key, env)
        for key in CI_HEAD_ENV:
            self.assertNotIn(key, env)
        self.assertEqual(env["HTTP_PROXY"], "http://127.0.0.1:9")
        self.assertEqual(env["HTTPS_PROXY"], "http://127.0.0.1:9")
        self.assertEqual(env["ALL_PROXY"], "http://127.0.0.1:9")
        self.assertEqual(env["NO_PROXY"], "127.0.0.1,localhost,::1")
        self.assertEqual(env["PYTHONPATH"], str(self.repo))
        self.assertEqual(env["PIP_NO_INDEX"], "1")
        self.assertEqual(env["UV_OFFLINE"], "1")
        self.assertEqual(env["npm_config_offline"], "true")
        self.assertEqual(env["HOME"], "/runner/home")
        self.assertEqual(env["USERPROFILE"], "/runner/profile")
        self.assertEqual(env["XDG_CACHE_HOME"], "/runner/cache")
        self.assertEqual(env["XDG_CONFIG_HOME"], "/runner/config")
        self.assertEqual(env["XDG_DATA_HOME"], "/runner/data")
        self.assertTrue(env["TMPDIR"].startswith(str(scratch)))
        self.assertTrue(env["TMP"].startswith(str(scratch)))
        self.assertTrue(env["TEMP"].startswith(str(scratch)))

    def test_suite_contract_is_fail_closed(self) -> None:
        execution = self.contract["execution"]
        self.assertTrue(execution["python_only"])
        self.assertTrue(execution["continue_after_failure"])
        self.assertTrue(execution["isolated_temp"])
        self.assertFalse(execution["isolated_home"])
        self.assertTrue(execution["preserve_runtime_home"])
        self.assertFalse(execution["isolated_xdg"])
        self.assertTrue(execution["preserve_runtime_xdg"])
        self.assertTrue(execution["sanitize_ci_head_environment"])
        self.assertTrue(execution["repository_status_must_be_preserved"])
        self.assertTrue(execution["external_credentials_removed"])
        self.assertTrue(execution["family_json_artifact_required"])
        self.assertTrue(execution["unexpected_nondeterminism_forbidden"])
        self.assertTrue(execution["missing_fixture_forbidden"])
        self.assertTrue(execution["contract_drift_forbidden"])
        self.assertEqual(self.contract["expected_behavior_family_count"], 14)
        self.assertEqual(self.contract["expected_total_certifiers"], 15)
        self.assertEqual(self.contract["expected_skipped"], 0)


if __name__ == "__main__":
    unittest.main()
