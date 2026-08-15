from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "publish-pre-release.yml"
SECRET_REFS = (
    "secrets.SYNTAVRA_PUBLISH_ARMED",
    "secrets.NPM_TOKEN",
    "secrets.CRATES_IO_TOKEN",
)


class PreReleaseSecretScopeContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.text = WORKFLOW.read_text(encoding="utf-8")

    def _jobs(self) -> dict[str, str]:
        matches = list(re.finditer(r"(?m)^  ([A-Za-z0-9_-]+):\n", self.text))
        result: dict[str, str] = {}
        for index, match in enumerate(matches):
            end = matches[index + 1].start() if index + 1 < len(matches) else len(self.text)
            result[match.group(1)] = self.text[match.start():end]
        return result

    @staticmethod
    def _steps(job: str) -> list[str]:
        marker = "    steps:\n"
        if marker not in job:
            return []
        body = job.split(marker, 1)[1]
        matches = list(re.finditer(r"(?m)^      - ", body))
        result: list[str] = []
        for index, match in enumerate(matches):
            end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
            result.append(body[match.start():end])
        return result

    def test_registry_and_arming_secrets_are_never_job_scoped(self) -> None:
        for job_name, job in self._jobs().items():
            head = job.split("    steps:\n", 1)[0]
            with self.subTest(job=job_name):
                for ref in SECRET_REFS:
                    self.assertNotIn(ref, head)

    def test_secret_refs_exist_only_on_shell_run_steps(self) -> None:
        found: set[str] = set()
        for job_name, job in self._jobs().items():
            for step in self._steps(job):
                refs = {ref for ref in SECRET_REFS if ref in step}
                if not refs:
                    continue
                found.update(refs)
                with self.subTest(job=job_name, refs=sorted(refs)):
                    self.assertIn("run:", step)
                    self.assertNotIn("uses:", step)
                    self.assertIn("        env:\n", step)
        self.assertEqual(found, set(SECRET_REFS))

    def test_checkout_setup_download_and_upload_actions_receive_no_publisher_secret(self) -> None:
        for job_name, job in self._jobs().items():
            for step in self._steps(job):
                if "uses:" not in step:
                    continue
                with self.subTest(job=job_name, step=step.splitlines()[0]):
                    for ref in SECRET_REFS:
                        self.assertNotIn(ref, step)

    def test_credential_preflight_scopes_tokens_to_only_the_steps_that_need_them(self) -> None:
        job = self._jobs()["credential-preflight"]
        steps = self._steps(job)
        rearm = next(step for step in steps if "Re-arm protected zero-write credential preflight" in step)
        npm_auth = next(step for step in steps if "Verify npm token authentication without publication" in step)
        exchange = next(step for step in steps if "Exchange trusted-publisher credentials without publication" in step)
        upload = next(step for step in steps if "Upload zero-write publish credential evidence" in step)

        self.assertIn("secrets.SYNTAVRA_PUBLISH_ARMED", rearm)
        self.assertIn("secrets.NPM_TOKEN", rearm)
        self.assertIn("secrets.CRATES_IO_TOKEN", rearm)
        self.assertIn("secrets.NPM_TOKEN", npm_auth)
        self.assertNotIn("secrets.CRATES_IO_TOKEN", npm_auth)
        self.assertIn("secrets.NPM_TOKEN", exchange)
        self.assertNotIn("secrets.CRATES_IO_TOKEN", exchange)
        for ref in SECRET_REFS:
            self.assertNotIn(ref, upload)

    def test_registry_tokens_are_scoped_to_rearm_and_irreversible_publish_steps(self) -> None:
        jobs = self._jobs()
        for job_name, token_ref, publish_marker in (
            ("publish-npm-installer", "secrets.NPM_TOKEN", "Publish npm installer with provenance"),
            ("publish-npm-sdk", "secrets.NPM_TOKEN", "Publish npm TypeScript SDK with provenance"),
            ("publish-rust-production", "secrets.CRATES_IO_TOKEN", "Publish production Rust graph in dependency order"),
            ("publish-legacy-native", "secrets.CRATES_IO_TOKEN", "Publish legacy non-production native companion"),
        ):
            steps = self._steps(jobs[job_name])
            containing = [step for step in steps if token_ref in step]
            with self.subTest(job=job_name):
                self.assertEqual(len(containing), 2)
                self.assertTrue(any("Re-arm" in step for step in containing))
                self.assertTrue(any(publish_marker in step for step in containing))

    def test_oidc_only_publish_jobs_do_not_receive_registry_tokens(self) -> None:
        jobs = self._jobs()
        for job_name in ("publish-pypi", "publish-vscode"):
            job = jobs[job_name]
            with self.subTest(job=job_name):
                self.assertNotIn("secrets.NPM_TOKEN", job)
                self.assertNotIn("secrets.CRATES_IO_TOKEN", job)
                self.assertIn("secrets.SYNTAVRA_PUBLISH_ARMED", job)

    def test_dry_run_records_step_scoped_secret_boundary(self) -> None:
        self.assertIn("'step_scoped_publisher_secrets': True", self.text)


if __name__ == "__main__":
    unittest.main()
