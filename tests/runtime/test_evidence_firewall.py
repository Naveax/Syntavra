from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from syntavra_runtime.evidence import EvidenceError, EvidenceStore
from syntavra_runtime.output_firewall import summarize, validate_critical_invariant

class EvidenceFirewallTests(unittest.TestCase):
    def setUp(self): self.temp=tempfile.TemporaryDirectory(); self.root=Path(self.temp.name); self.store=EvidenceStore(self.root/"evidence",project_id="project")
    def tearDown(self): self.temp.cleanup()
    def test_put_get_and_deduplicate(self):
        first=self.store.put(b"same"); second=self.store.put(b"same"); self.assertEqual(first,second); self.assertEqual(self.store.get(first),b"same"); self.assertTrue(self.store.verify(first))
    def test_scope_and_corruption_fail_closed(self):
        handle=self.store.put(b"evidence"); digest=handle.rsplit("/",1)[1]; (self.store.objects/digest[:2]/digest[2:]).write_bytes(b"corrupt")
        with self.assertRaises(EvidenceError): self.store.get(handle)
    def test_success_summary_is_bounded(self):
        out=self.root/"out"; err=self.root/"err"; out.write_text("ok\n"*10000+"100 passed in 2.0s\n"); err.write_text(""); result=summarize(("pytest",),stdout_path=out,stderr_path=err,exit_code=0,duration_seconds=2,evidence=self.store); self.assertLessEqual(result.visible_bytes,4300); self.assertIn("100 passed",result.summary); self.assertTrue(self.store.verify(result.evidence_handle))
    def test_failure_preserves_error_and_redacts_secret(self):
        out=self.root/"out"; err=self.root/"err"; out.write_text("starting\n"); err.write_text("api_key=supersecret\nERROR src/main.py:42 assertion failed\n"); result=summarize(("pytest",),stdout_path=out,stderr_path=err,exit_code=1,duration_seconds=1,evidence=self.store); self.assertIn("ERROR src/main.py:42",result.summary); self.assertNotIn("supersecret",result.summary); self.assertTrue(validate_critical_invariant(out.read_bytes()+b"\n"+err.read_bytes(),result))

if __name__ == "__main__": unittest.main()
