from __future__ import annotations

import copy
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from _support import PROFILE_PARENT
from roblox_studio.activation import ReplayStore, mint_envelope, verify_envelope
from roblox_studio.errors import ActivationError, ReplayDetected


class ActivationTests(unittest.TestCase):
    key = b"k" * 48
    now = 2_000_000_000

    def envelope(self, **overrides):
        kwargs = dict(
            key=self.key, session_id="studio-session-123", place_id="1001", project_id="project-1001",
            project_fingerprint="sha256:" + "a" * 64, studio_process_id=4242,
            capabilities=("inspect_project", "read_script"), transport_identity="bridge-abc-123",
            now=self.now, ttl_seconds=60, nonce="nonce-1234567890",
        )
        kwargs.update(overrides)
        return mint_envelope(**kwargs)

    def verify(self, envelope, store, **overrides):
        kwargs = dict(
            key=self.key, replay_store=store, allowed_capabilities=("inspect_project", "read_script"),
            expected_transport_identity="bridge-abc-123", expected_process_id=4242,
            expected_place_id="1001", expected_project_id="project-1001",
            expected_project_fingerprint="sha256:" + "a" * 64,
            process_attestor=lambda pid: pid == 4242, now=self.now,
        )
        kwargs.update(overrides)
        return verify_envelope(envelope, **kwargs)

    def test_valid_envelope(self):
        with TemporaryDirectory() as temp:
            session = self.verify(self.envelope(), ReplayStore(Path(temp) / "nonce.db"))
            self.assertEqual(session.project_id, "project-1001")

    def test_unsigned_rejected(self):
        with TemporaryDirectory() as temp:
            envelope = self.envelope(); envelope.pop("signature")
            with self.assertRaises(ActivationError): self.verify(envelope, ReplayStore(Path(temp) / "nonce.db"))

    def test_invalid_signature(self):
        with TemporaryDirectory() as temp:
            envelope = self.envelope(); envelope["signature"] = "bad"
            with self.assertRaises(ActivationError): self.verify(envelope, ReplayStore(Path(temp) / "nonce.db"))

    def test_expired(self):
        with TemporaryDirectory() as temp:
            with self.assertRaises(ActivationError): self.verify(self.envelope(now=self.now-500), ReplayStore(Path(temp) / "nonce.db"))

    def test_future_issued(self):
        with TemporaryDirectory() as temp:
            with self.assertRaises(ActivationError): self.verify(self.envelope(now=self.now+60), ReplayStore(Path(temp) / "nonce.db"))

    def test_replay(self):
        with TemporaryDirectory() as temp:
            store = ReplayStore(Path(temp) / "nonce.db"); envelope = self.envelope()
            self.verify(envelope, store)
            with self.assertRaises(ReplayDetected): self.verify(envelope, store)

    def test_wrong_process(self):
        with TemporaryDirectory() as temp:
            with self.assertRaises(ActivationError): self.verify(self.envelope(), ReplayStore(Path(temp) / "nonce.db"), expected_process_id=999)

    def test_attestor_failure(self):
        with TemporaryDirectory() as temp:
            with self.assertRaises(ActivationError): self.verify(self.envelope(), ReplayStore(Path(temp) / "nonce.db"), process_attestor=lambda pid: False)

    def test_wrong_project(self):
        with TemporaryDirectory() as temp:
            with self.assertRaises(ActivationError): self.verify(self.envelope(), ReplayStore(Path(temp) / "nonce.db"), expected_project_id="other")

    def test_wrong_place(self):
        with TemporaryDirectory() as temp:
            with self.assertRaises(ActivationError): self.verify(self.envelope(), ReplayStore(Path(temp) / "nonce.db"), expected_place_id="other")

    def test_fingerprint_mismatch(self):
        with TemporaryDirectory() as temp:
            with self.assertRaises(ActivationError): self.verify(self.envelope(), ReplayStore(Path(temp) / "nonce.db"), expected_project_fingerprint="sha256:"+"b"*64)

    def test_transport_mismatch(self):
        with TemporaryDirectory() as temp:
            with self.assertRaises(ActivationError): self.verify(self.envelope(), ReplayStore(Path(temp) / "nonce.db"), expected_transport_identity="other")

    def test_capability_escalation(self):
        with TemporaryDirectory() as temp:
            envelope = self.envelope(capabilities=("write_script",))
            with self.assertRaises(ActivationError): self.verify(envelope, ReplayStore(Path(temp) / "nonce.db"))

    def test_malformed_capability_set(self):
        with TemporaryDirectory() as temp:
            envelope = self.envelope(); payload = {k:v for k,v in envelope.items() if k != "signature"}; payload["capabilities"] = "inspect_project"
            from roblox_studio.activation import sign_payload
            envelope = {**payload, "signature": sign_payload(payload, self.key)}
            with self.assertRaises(ActivationError): self.verify(envelope, ReplayStore(Path(temp) / "nonce.db"))

    def test_cross_profile_confusion(self):
        with TemporaryDirectory() as temp:
            envelope = self.envelope(); payload = {k:v for k,v in envelope.items() if k != "signature"}; payload["profile_id"] = "generic"
            from roblox_studio.activation import sign_payload
            envelope = {**payload, "signature": sign_payload(payload, self.key)}
            with self.assertRaises(ActivationError): self.verify(envelope, ReplayStore(Path(temp) / "nonce.db"))

    def test_oversized_payload(self):
        with TemporaryDirectory() as temp:
            envelope = self.envelope(); envelope["extra"] = "x" * 20000
            with self.assertRaises(ActivationError): self.verify(envelope, ReplayStore(Path(temp) / "nonce.db"))

    def test_nonce_race_accepts_exactly_one(self):
        with TemporaryDirectory() as temp:
            store = ReplayStore(Path(temp) / "nonce.db"); envelope = self.envelope()
            outcomes=[]
            def worker():
                try: self.verify(envelope, store); outcomes.append("accepted")
                except ReplayDetected: outcomes.append("replay")
            threads=[threading.Thread(target=worker) for _ in range(8)]
            [thread.start() for thread in threads]; [thread.join() for thread in threads]
            self.assertEqual(outcomes.count("accepted"), 1)
            self.assertEqual(outcomes.count("replay"), 7)


if __name__ == "__main__": unittest.main()
