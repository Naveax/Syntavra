from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

from syntavra_runtime.bounded_process import run_bounded_process


class BoundedProcessTests(unittest.TestCase):
    def run_python(self, script: str, *, timeout: float = 5, stdout_limit: int = 1024, stderr_limit: int = 1024):
        with tempfile.TemporaryDirectory() as temporary:
            return run_bounded_process(
                [sys.executable, "-c", script],
                cwd=temporary,
                environment={"PATH": os.environ.get("PATH", "")},
                input_bytes=None,
                timeout_seconds=timeout,
                stdout_limit=stdout_limit,
                stderr_limit=stderr_limit,
                creationflags=0,
                start_new_session=os.name != "nt",
            )

    def test_successful_output_is_exact(self) -> None:
        result = self.run_python("import sys; sys.stdout.write('alpha'); sys.stderr.write('beta')")
        self.assertTrue(result.ok)
        self.assertEqual(result.stdout, b"alpha")
        self.assertEqual(result.stderr, b"beta")
        self.assertEqual(result.stdout_bytes_seen, 5)
        self.assertEqual(result.stderr_bytes_seen, 4)

    def test_stdout_flood_is_killed_and_never_reported_success(self) -> None:
        result = self.run_python(
            "import os, sys\nwhile True:\n os.write(sys.stdout.fileno(), b'x' * 65536)",
            stdout_limit=4096,
        )
        self.assertFalse(result.ok)
        self.assertTrue(result.output_limit_exceeded)
        self.assertLessEqual(len(result.stdout), 4096)
        self.assertGreater(result.stdout_bytes_seen, 4096)

    def test_stderr_flood_is_killed(self) -> None:
        result = self.run_python(
            "import os, sys\nwhile True:\n os.write(sys.stderr.fileno(), b'e' * 65536)",
            stderr_limit=2048,
        )
        self.assertFalse(result.ok)
        self.assertTrue(result.output_limit_exceeded)
        self.assertLessEqual(len(result.stderr), 2048)

    def test_timeout_kills_process_tree(self) -> None:
        result = self.run_python("import time; time.sleep(30)", timeout=0.2)
        self.assertFalse(result.ok)
        self.assertTrue(result.timed_out)

    def test_large_input_does_not_deadlock(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            payload = b"z" * (2 * 1024 * 1024)
            result = run_bounded_process(
                [sys.executable, "-c", "import sys; data=sys.stdin.buffer.read(); print(len(data))"],
                cwd=temporary,
                environment={"PATH": os.environ.get("PATH", "")},
                input_bytes=payload,
                timeout_seconds=5,
                stdout_limit=1024,
                stderr_limit=1024,
                start_new_session=os.name != "nt",
            )
            self.assertTrue(result.ok)
            self.assertEqual(result.stdout.strip(), str(len(payload)).encode())


if __name__ == "__main__":
    unittest.main()
