from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from syntavra_runtime.cli import main


class ProviderProxyCLIV4Tests(unittest.TestCase):
    def test_proxy_dry_run_validates_fixed_origin_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stream = io.StringIO()
            with redirect_stdout(stream):
                code = main([
                    "--project", str(root),
                    "--state-root", str(root / ".state"),
                    "provider", "proxy",
                    "--provider", "openai",
                    "--upstream", "http://127.0.0.1:9999",
                    "--credential-env", "OPENAI_API_KEY",
                    "--allow-insecure-upstream",
                    "--listen-port", "0",
                    "--dry-run",
                ])
            result = json.loads(stream.getvalue())
            self.assertEqual(code, 0)
            self.assertTrue(result["ok"])
            self.assertEqual(result["config"]["provider"], "openai")
            self.assertEqual(result["config"]["listen_port"], 0)
            self.assertTrue(result["config"]["allow_insecure_upstream"])


if __name__ == "__main__":
    unittest.main()
