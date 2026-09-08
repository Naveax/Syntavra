from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from syntavra_runtime.execution_sandbox import NativeSandboxBroker, SandboxPolicy


class SandboxCapabilityProbeTests(unittest.TestCase):
    @staticmethod
    def _which(name: str) -> str | None:
        if name == "bwrap":
            return None
        if name == "unshare":
            return "/usr/bin/unshare"
        return None

    def test_binary_presence_without_capability_falls_back_portably(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory).resolve()
            broker = NativeSandboxBroker(workspace / ".state")
            policy = SandboxPolicy(workspace=workspace, strict_native=False)
            with (
                patch("syntavra_runtime.execution_sandbox.shutil.which", side_effect=self._which),
                patch.object(NativeSandboxBroker, "_probe_native", return_value=(False, "operation not permitted")),
            ):
                backend = broker.backend(policy.normalized())
                self.assertEqual(backend.name, "portable-process-boundary")
                self.assertFalse(backend.available)
                self.assertEqual(backend.command_prefix, ())
                self.assertIn("operation not permitted", backend.detail)
                receipt = broker.run(
                    (sys.executable, "-c", "print('portable-ok')"),
                    policy=policy,
                )
            self.assertTrue(receipt.ok, receipt)
            self.assertEqual(receipt.backend.name, "portable-process-boundary")
            self.assertIn("portable-ok", receipt.stdout)

    def test_strict_native_rejects_probe_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory).resolve()
            broker = NativeSandboxBroker(workspace / ".state")
            policy = SandboxPolicy(workspace=workspace, strict_native=True)
            with (
                patch("syntavra_runtime.execution_sandbox.shutil.which", side_effect=self._which),
                patch.object(NativeSandboxBroker, "_probe_native", return_value=(False, "operation not permitted")),
            ):
                with self.assertRaisesRegex(RuntimeError, "required native sandbox controls unavailable"):
                    broker.run((sys.executable, "-c", "print('must-not-run')"), policy=policy)

    def test_successful_probe_admits_native_backend(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory).resolve()
            broker = NativeSandboxBroker(workspace / ".state")
            policy = SandboxPolicy(workspace=workspace, strict_native=False)
            with (
                patch("syntavra_runtime.execution_sandbox.shutil.which", side_effect=self._which),
                patch.object(NativeSandboxBroker, "_probe_native", return_value=(True, "probe passed")),
            ):
                backend = broker.backend(policy.normalized())
            self.assertEqual(backend.name, "unshare")
            self.assertTrue(backend.available)
            self.assertIn("probe passed", backend.detail)


if __name__ == "__main__":
    unittest.main()
