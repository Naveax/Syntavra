from __future__ import annotations

import hashlib
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from _support import candidates, task
from roblox_studio.adapters.base import EngineRequest
from roblox_studio.adapters.live import LiveStudioAdapter
from roblox_studio.adapters.simulated import SimulatedStudioAdapter
from roblox_studio.adapters.transcript import TranscriptAdapter, TranscriptEvent
from roblox_studio.budgets import BudgetLedger
from roblox_studio.context_knapsack import ContextCandidate, select_context
from roblox_studio.datamodel import analyze_luau
from roblox_studio.errors import ActivationError, BudgetError, ValidationError
from roblox_studio.evidence_ledger import EvidenceLedger, EvidenceRecord
from roblox_studio.memory import MemoryItem, ScopedMemory
from roblox_studio.output_virtualization import virtualize_output
from roblox_studio.profile import RobloxStudioOrchestrator


class ContextTests(unittest.TestCase):
    def test_mandatory_roles_selected(self):
        package=select_context(candidates(), required_roles=("definition","implementation"), token_budget=150)
        self.assertEqual(set(package.covered_roles), {"definition","implementation"})
        self.assertNotIn("noise", {item.candidate_id for item in package.selected})
    def test_missing_role_fails(self):
        with self.assertRaises(ValidationError): select_context(candidates(), required_roles=("validator",), token_budget=100)
    def test_budget_pressure_fails_closed(self):
        with self.assertRaises(BudgetError): select_context(candidates(), required_roles=("definition","implementation"), token_budget=100)
    def test_stale_noise_loses(self):
        package=select_context(candidates(), required_roles=("implementation",), token_budget=100)
        self.assertEqual(package.selected[0].candidate_id, "impl")


class EvidenceTests(unittest.TestCase):
    def record(self, evidence_id, branch="main", source_hash="a"):
        now=int(time.time())
        return EvidenceRecord(evidence_id,"task","script","ServerScriptService.Main",source_hash,"fp",branch,"c",1,now,"PROJECT","CLEAN","exact","summary",f"memory://{evidence_id}",now-1,now+60,None,("validator",))
    def test_append_recover_and_chain(self):
        with TemporaryDirectory() as temp:
            ledger=EvidenceLedger(Path(temp)/"e.db"); ledger.append(self.record("e1"))
            self.assertEqual(ledger.exact_recover("e1"), "exact"); self.assertTrue(ledger.verify_chain(project_fingerprint="fp", branch="main"))
    def test_branch_isolation(self):
        with TemporaryDirectory() as temp:
            ledger=EvidenceLedger(Path(temp)/"e.db"); ledger.append(self.record("e1","main")); ledger.append(self.record("e2","feature"))
            self.assertEqual(len(ledger.retrieve(project_fingerprint="fp", branch="main")),1)
    def test_contradiction_detection(self):
        with TemporaryDirectory() as temp:
            ledger=EvidenceLedger(Path(temp)/"e.db")
            contradictions=ledger.detect_contradictions((self.record("e1",source_hash="a"),self.record("e2",source_hash="b")))
            self.assertEqual(len(contradictions),1)


class AdapterTests(unittest.TestCase):
    def test_simulated_is_labeled(self):
        response=SimulatedStudioAdapter().execute(EngineRequest("inspect_project","task",{}))
        self.assertEqual(response.payload["mode"], "SIMULATED")
    def test_transcript_order_validation(self):
        with self.assertRaises(ValidationError): TranscriptAdapter((TranscriptEvent(1,"response",{}),))
    def test_transcript_response(self):
        adapter=TranscriptAdapter((TranscriptEvent(0,"response",{"capability":"inspect_project","status":"SUCCEEDED","evidence_references":["x"]}),))
        self.assertEqual(adapter.execute(EngineRequest("inspect_project","task",{})).status,"SUCCEEDED")
    def test_live_disabled(self):
        with self.assertRaises(ActivationError): LiveStudioAdapter(enabled=False,session=None,transport=None).execute(EngineRequest("inspect_project","task",{}))


class LuauTests(unittest.TestCase):
    def test_require_graph(self):
        requires,_=analyze_luau("local x=require(script.Parent.X)")
        self.assertEqual(requires,("script.Parent.X",))
    def test_remote_validation_finding(self):
        _,findings=analyze_luau("Remote.OnServerEvent:Connect(function(player, damage) end)")
        self.assertIn("MISSING_SERVER_VALIDATION", {item.code for item in findings})
    def test_loop_finding(self):
        _,findings=analyze_luau("while true do print('x') end")
        self.assertIn("EXPENSIVE_LOOP", {item.code for item in findings})
    def test_datastore_finding(self):
        _,findings=analyze_luau("store:GetAsync('x')")
        self.assertIn("UNSAFE_DATASTORE", {item.code for item in findings})


class OutputMemoryTests(unittest.TestCase):
    def test_output_preserves_marker_and_raw(self):
        with TemporaryDirectory() as temp:
            capsule=virtualize_output("ERROR boom\nnoise\nnoise\n", storage_root=Path(temp), family="roblox")
            self.assertIn("ERROR boom", capsule.critical_markers); self.assertEqual(capsule.duplicate_count,1); self.assertTrue(Path(capsule.raw_path).exists())
    def test_memory_project_branch_commit_isolation(self):
        with TemporaryDirectory() as temp:
            store=ScopedMemory(Path(temp)/"m.db"); now=int(time.time())
            store.put(MemoryItem("m","decision","fp","main","c1",("h",),now-1,now+60,1.0,None,"source_change","use sessions","PROJECT"))
            self.assertEqual(len(store.query(project_fingerprint="fp",branch="main",commit="c1")),1)
            self.assertEqual(len(store.query(project_fingerprint="other",branch="main",commit="c1")),0)
            self.assertEqual(len(store.query(project_fingerprint="fp",branch="feature",commit="c1")),0)


class VerticalSliceTests(unittest.TestCase):
    def test_signed_activation_to_verified_simulated_workflow_components(self):
        with TemporaryDirectory() as temp:
            orchestrator=RobloxStudioOrchestrator(Path(temp))
            result=orchestrator.run(task(), candidates())
            self.assertTrue(result.verified)
            self.assertGreaterEqual(result.node_count,2)
            self.assertTrue((Path(temp)/"checkpoint.json").exists())
            self.assertTrue(orchestrator.ledger.verify_chain(project_fingerprint=task().project_fingerprint,branch="main"))
            self.assertGreaterEqual(len(orchestrator.telemetry.replay(task().task_id)),2)


if __name__ == "__main__": unittest.main()
