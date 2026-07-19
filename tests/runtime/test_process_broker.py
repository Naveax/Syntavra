from __future__ import annotations

import sys
import tempfile
import time
import unittest
from pathlib import Path

from signalcore_runtime.evidence import EvidenceStore
from signalcore_runtime.process_broker import ProcessBroker

class ProcessBrokerTests(unittest.TestCase):
    def setUp(self): self.temp=tempfile.TemporaryDirectory(); self.root=Path(self.temp.name); self.broker=ProcessBroker(self.root/"broker",EvidenceStore(self.root/"evidence",project_id="p"),heartbeat_interval=0.05)
    def tearDown(self): self.temp.cleanup()
    def test_synchronous_success_and_failure(self):
        success=self.broker.run((sys.executable,"-c","print('ok')"),cwd=self.root,timeout=5); self.assertEqual(success.exit_code,0); self.assertTrue(success.evidence_handle)
        failure=self.broker.run((sys.executable,"-c","import sys; print('ERROR root'); sys.exit(3)"),cwd=self.root,timeout=5); self.assertEqual(failure.exit_code,3); self.assertIn("ERROR root",failure.summary)
    def test_timeout(self): self.assertTrue(self.broker.run((sys.executable,"-c","import time; time.sleep(5)"),cwd=self.root,timeout=0.2).timed_out)
    def test_background_job_completes_without_broker_poll_loop(self):
        job=self.broker.submit((sys.executable,"-c","print('background')"),cwd=self.root,timeout=5); self.assertIn(job.state,{"QUEUED","RUNNING","COMPLETED"}); deadline=time.time()+10
        while time.time()<deadline:
            current=self.broker.show(job.job_id)
            if current.state in {"COMPLETED","FAILED"}: break
            time.sleep(0.1)
        self.assertEqual(self.broker.show(job.job_id).state,"COMPLETED"); self.assertTrue(self.broker.completions.is_file())

if __name__ == "__main__": unittest.main()
