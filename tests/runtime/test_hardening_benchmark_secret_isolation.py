from __future__ import annotations

import importlib.util
import io
import json
import unittest
from contextlib import redirect_stdout
from pathlib import Path


class HardeningBenchmarkSecretIsolationTests(unittest.TestCase):
    @staticmethod
    def load_module():
        path = Path(__file__).resolve().parents[2] / "benchmarks" / "hardening_v3_benchmark.py"
        spec = importlib.util.spec_from_file_location("syntavra_hardening_v3_benchmark", path)
        if spec is None or spec.loader is None:
            raise RuntimeError("unable to load hardening benchmark")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_report_contains_only_constant_security_contract_status(self) -> None:
        module = self.load_module()
        output = io.StringIO()
        with redirect_stdout(output):
            exit_code = module.main(["--workers", "1", "--artifacts", "1"])
        self.assertEqual(exit_code, 0)
        rendered = output.getvalue()
        self.assertNotIn("secret-value", rendered)
        self.assertNotIn("redacted_text", rendered)
        self.assertNotIn("secret_types", rendered)
        payload = json.loads(rendered)
        self.assertEqual(
            payload["security_scan"],
            {"validated": True, "output_contains_secret_derived_values": False},
        )
        self.assertEqual(payload["readiness_gate"]["evidence"]["security_regressions"], 0)


if __name__ == "__main__":
    unittest.main()
