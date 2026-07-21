from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from signalcore_runtime.session_product import SessionContinuityController


class SessionProductV001Tests(unittest.TestCase):
    def test_resume_compact_and_exact_continuity_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            controller = SessionContinuityController(root / "sessions.sqlite3", project_id="project-1")
            opened = controller.open_or_resume("session-1", metadata={"goal": "test"})
            self.assertTrue(opened["ok"])
            self.assertFalse(opened["continuity_restored"])
            for index in range(70):
                controller.append("session-1", "tool-result", {"result": index, "path": f"src/{index}.py"})
            compacted = controller.compact_once("session-1")
            self.assertTrue(compacted["ok"], compacted)
            self.assertGreater(compacted["wall_time_ms"], 0)
            resumed = controller.open_or_resume("session-1")
            self.assertTrue(resumed["continuity_restored"])
            receipt = controller.continuity_receipt("session-1", token_budget=4096)
            self.assertTrue(receipt["exact_recovery"], receipt)
            self.assertTrue(receipt["continuity_restored"], receipt)
            self.assertFalse(receipt["forced_restart"])
            self.assertEqual(receipt["events"], 70)

    def test_background_compaction_is_observable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            controller = SessionContinuityController(root / "sessions.sqlite3", project_id="project-2")
            session = controller.open_or_resume()["session"]["session_id"]
            for index in range(8):
                controller.append(session, "decision", {"decision": index})
            controller.start(interval_seconds=0.05, min_events=4)
            deadline = time.time() + 2.0
            while time.time() < deadline:
                if controller.status()["last_cycle"]["compacted"]:
                    break
                time.sleep(0.02)
            controller.stop()
            status = controller.status()
            self.assertFalse(status["worker_alive"])
            self.assertGreaterEqual(status["last_cycle"]["compacted"], 1, status)
            self.assertGreater(status["analytics"]["continuity"]["compaction_wall_time_ms"], 0)


if __name__ == "__main__":
    unittest.main()
