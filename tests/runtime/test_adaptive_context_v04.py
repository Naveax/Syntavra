from __future__ import annotations

import hashlib
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path

from syntavra_runtime.adaptive_context import AdaptiveContextEngine, AdaptivePolicy, ToolObservation
from syntavra_runtime.evidence import EvidenceStore
from syntavra_runtime.signalbench_hardened import HardwareIdentity, HardenedSignalBench, UsageReceipt


@dataclass
class Event:
    sequence: int
    event_type: str
    payload: dict


class FakeSession:
    def __init__(self): self.rows = {}
    def append(self, session_id, event_type, payload):
        event = Event(len(self.rows.setdefault(session_id, [])) + 1, event_type, payload); self.rows[session_id].append(event); return event
    def events(self, session_id, *, after=0, limit=1000): return [row for row in self.rows.get(session_id, []) if row.sequence > after][:limit]


def digest(value: str) -> str: return hashlib.sha256(value.encode()).hexdigest()


def row(task, arm, *, success=True, quota=10.0, hardware="hw"):
    return {"task_id": task, "arm_id": arm, "repetition": 1, "cache_mode": "cold", "success": success, "verifier_success": success, "verified_work": 1.0 if success else 0.0, "quota_cost": quota, "security_regressions": 0, "verifier_skips": 0, "repository_tree": "tree", "prompt_hash": digest("prompt"), "verifier_hash": digest("verifier"), "permissions_hash": digest("permissions"), "model": "same", "reasoning": "same", "context_window": 200000, "hardware_hash": digest(hardware)}


def receipt(task, arm, quota, *, hardware="hw"):
    return UsageReceipt.seal(task_id=task, arm_id=arm, repetition=1, cache_mode="cold", provider="test", request_id_hash=digest(f"req:{task}:{arm}"), provider_response_hash=digest(f"res:{task}:{arm}"), fresh_input_tokens=100, cached_input_tokens=0, output_tokens=10, reasoning_tokens=5, quota_cost=quota, hardware_hash=digest(hardware))


class AdaptiveContextTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); root = Path(self.temp.name)
        self.engine = AdaptiveContextEngine(root / "adaptive.db", evidence=EvidenceStore(root / "evidence", project_id="test"), policy=AdaptivePolicy.for_profile("balanced"))
    def tearDown(self): self.temp.cleanup()

    def test_diff_compacts_and_roundtrips(self):
        text = "\n".join(["diff --git a/a.py b/a.py", "--- a/a.py", "+++ b/a.py", "@@ -1 +1 @@", "-old = 1", "+new = 2"] * 100)
        result = self.engine.process(ToolObservation(command="git diff", stdout=text, scope_key="diff"))
        self.assertEqual(result.family, "diff"); self.assertTrue(result.quality_gate_passed); self.assertGreater(result.savings_ratio, .4)
        self.assertEqual(self.engine.restore(result.capture_id), text.encode()); self.assertTrue(self.engine.verify(result.capture_id)["ok"])

    def test_failures_and_locations_survive(self):
        text = ("tests/test_ok.py .\n" * 200) + "E AssertionError: 41 != 42\ntests/test_math.py:33: AssertionError\n2 failed, 198 passed\n"
        result = self.engine.process(ToolObservation(command="cd repo && pytest -q", stdout=text, scope_key="test"))
        self.assertIn("AssertionError", result.visible_text); self.assertIn("tests/test_math.py:33", result.visible_text); self.assertTrue(result.quality_gate_passed)

    def test_dedup_and_stable_exact_reference(self):
        text = "class A:\n    pass\n" * 400; observation = ToolObservation(command="cat src/a.py", stdout=text, path="src/a.py", tool_name="read", scope_key="session")
        first = self.engine.process(observation); second = self.engine.process(observation)
        self.assertFalse(first.repeated); self.assertTrue(second.repeated); self.assertEqual(first.capture_id, second.capture_id); self.assertEqual(first.exact_handle, second.exact_handle); self.assertGreater(second.savings_ratio, .95)

    def test_search_and_session_recall(self):
        session = FakeSession(); lines = [f"INFO request={i}" for i in range(300)]; lines.insert(173, "FATAL auth failure at src/security.py:91 token refresh rejected")
        result = self.engine.process(ToolObservation(command="service logs", stdout="\n".join(lines), scope_key="logs"), session_runtime=session, session_id="s")
        self.assertIn("src/security.py:91", self.engine.search(result.capture_id, "auth failure token refresh")[0].text)
        self.assertEqual(self.engine.recall(session, "s", "security.py auth")[0]["capture_id"], result.capture_id)

    def test_ambiguous_shell_is_not_command_classified(self):
        result = self.engine.process(ToolObservation(command="cat a.log | grep ERROR && pytest -q", stdout="1 passed in 0.1s"))
        self.assertNotEqual(result.family, "test-output")

    def test_small_output_no_pointer_overhead_and_secret_redaction(self):
        result = self.engine.process(ToolObservation(command="run", stdout="authorization=super-secret\nERROR denied at auth.py:7\n"))
        self.assertLessEqual(result.visible_bytes, result.original_bytes); self.assertNotIn("super-secret", result.visible_text); self.assertEqual(self.engine.restore(result.capture_id).decode(), "authorization=super-secret\nERROR denied at auth.py:7\n")

    def test_pathological_single_line_is_bounded_and_exact(self):
        text = "FATAL huge-line " + ("x" * 50000); result = self.engine.process(ToolObservation(command="service logs", stdout=text, scope_key="huge"))
        self.assertGreater(result.chunk_count, 1); self.assertTrue(result.quality_gate_passed); self.assertEqual(self.engine.restore(result.capture_id), text.encode()); self.assertTrue(self.engine.verify(result.capture_id)["ok"])


class HardenedSignalBenchTests(unittest.TestCase):
    def test_clean_superiority_passes(self):
        rows = []; receipts = []
        for index in range(12):
            task = f"t{index}"; rows += [row(task, "plain", quota=10), row(task, "syntavra", quota=2)]; receipts += [receipt(task, "plain", 10), receipt(task, "syntavra", 2)]
        result = HardenedSignalBench.compare(rows, baseline_arm="plain", candidate_arm="syntavra", receipts=receipts)
        self.assertTrue(result["claimable_superiority"]); self.assertGreater(result["failure_inclusive_efficiency_ratio"], 4.9)

    def test_failures_consume_quota_and_block_claim(self):
        rows = []; receipts = []
        for index in range(12):
            task = f"t{index}"; success = index < 6; rows += [row(task, "plain", quota=10), row(task, "candidate", success=success, quota=1)]; receipts += [receipt(task, "plain", 10), receipt(task, "candidate", 1)]
        result = HardenedSignalBench.compare(rows, baseline_arm="plain", candidate_arm="candidate", receipts=receipts)
        self.assertFalse(result["claimable_superiority"]); self.assertEqual(result["pass_rates"]["candidate"], .5); self.assertEqual(result["total_quota"]["candidate"], 12)

    def test_identity_receipt_and_hardware_tampering_block_claim(self):
        rows = []; receipts = []
        for index in range(10):
            task = f"t{index}"; base = row(task, "plain", quota=10); candidate = row(task, "candidate", quota=2)
            if index == 2: candidate["repository_tree"] = "other"
            sealed = receipt(task, "candidate", 2)
            if index == 4: sealed = UsageReceipt(**{**sealed.__dict__, "quota_cost": .1})
            if index == 6: sealed = receipt(task, "candidate", 2, hardware="other")
            rows += [base, candidate]; receipts += [receipt(task, "plain", 10), sealed]
        result = HardenedSignalBench.compare(rows, baseline_arm="plain", candidate_arm="candidate", receipts=receipts)
        self.assertFalse(result["claimable_superiority"]); self.assertTrue(result["identity_mismatches"]); self.assertTrue(result["receipt_errors"])

    def test_hardware_identity_is_stable(self):
        identity = HardwareIdentity("linux", "x86_64", "cpu", 8, 16_000_000_000, "gpu", "python3.13")
        self.assertEqual(len(identity.digest), 64); self.assertEqual(identity.digest, HardwareIdentity(**identity.__dict__).digest)


if __name__ == "__main__": unittest.main()
