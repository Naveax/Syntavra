from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from signalcore_runtime.bootstrap import runtime_health, start_runtime
from signalcore_runtime.host_adapters import negotiate


class BootstrapCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name)
        (self.root / "skills" / "signal-core").mkdir(parents=True); (self.root / "skills" / "signal-core" / "SKILL.md").write_text("name: signal-core\n")
    def tearDown(self): self.temp.cleanup()
    def test_unknown_host_is_not_falsely_enforced(self): self.assertEqual(negotiate("unknown")["mode"], "UNSUPPORTED")
    def test_active_runtime_health(self): self.assertEqual(runtime_health(project=self.root, skill_root=self.root / "skills" / "signal-core", state_root=self.root / ".state", codex_home=self.root / ".codex", host="codex").state, "RUNTIME_ACTIVE")
    def test_start_writes_session(self):
        result = start_runtime("task", project=self.root, skill_root=self.root / "skills" / "signal-core", state_root=self.root / ".state", codex_home=self.root / ".codex", host="codex")
        self.assertTrue((self.root / ".state" / "sessions" / result["session"]["session_id"] / "session.json").is_file())
    def test_cli_context_json(self):
        result = subprocess.run([sys.executable, "-m", "signalcore_runtime", "--project", str(self.root), "context", "--used", "60", "--window", "100"], capture_output=True, text=True, cwd=Path(__file__).resolve().parents[2])
        self.assertEqual(result.returncode, 0, result.stderr); self.assertAlmostEqual(json.loads(result.stdout)["utilization"], 0.6)

if __name__ == "__main__": unittest.main()
