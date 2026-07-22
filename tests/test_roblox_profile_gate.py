from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "syntavra" / "scripts"
PROFILE = ROOT / "skills" / "syntavra" / "profiles" / "roblox_studio"
sys.path.insert(0, str(SCRIPTS))

loader_spec = importlib.util.spec_from_file_location("syntavra_profile_loader_test", SCRIPTS / "profile_loader.py")
loader = importlib.util.module_from_spec(loader_spec)
assert loader_spec and loader_spec.loader
sys.modules[loader_spec.name] = loader
loader_spec.loader.exec_module(loader)

activation_spec = importlib.util.spec_from_file_location("syntavra_roblox_activation_test", PROFILE / "activation.py")
activation = importlib.util.module_from_spec(activation_spec)
assert activation_spec and activation_spec.loader
sys.modules[activation_spec.name] = activation
activation_spec.loader.exec_module(activation)


class RobloxProfileGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.state_root = Path(self.temp.name) / "state"
        activation.create_pairing_key(self.state_root)
        self.key = activation.load_pairing_key(self.state_root)
        metadata = json.loads((PROFILE / "profile.json").read_text(encoding="utf-8"))
        self.capabilities = metadata["allowed_capabilities"][:2]

    def envelope(self, **overrides):
        value = activation.mint_studio_envelope(
            key=self.key,
            studio_session_id="studio-session-123456",
            place_id="place-12345678",
            project_fingerprint="sha256:" + "a" * 64,
            studio_pid=4242,
            capabilities=self.capabilities,
            ttl_seconds=60,
            now=1_800_000_000,
        )
        if overrides:
            value.update(overrides)
            payload = {key: value[key] for key in value if key != "signature"}
            value["signature"] = activation.sign_payload(payload, self.key)
        return value

    def verify(self, envelope):
        metadata = json.loads((PROFILE / "profile.json").read_text(encoding="utf-8"))
        policy = metadata["activation"]
        with patch.object(activation, "_process_name", return_value="RobloxStudioBeta.exe"):
            return activation.verify_studio_envelope(
                envelope,
                state_root=self.state_root,
                allowed_capabilities=metadata["allowed_capabilities"],
                accepted_process_names=policy["accepted_process_names"],
                maximum_ttl_seconds=policy["maximum_session_ttl_seconds"],
                clock_skew_seconds=policy["clock_skew_seconds"],
                require_process_attestation=True,
                now=1_800_000_010,
            )

    def test_profile_is_hidden_from_discovery(self):
        self.assertEqual(loader.discoverable_profiles(), [])

    def test_missing_envelope_is_blocked(self):
        with self.assertRaises(activation.ProfileLockedError):
            self.verify(None)

    def test_cli_transport_is_blocked(self):
        with self.assertRaises(activation.ProfileLockedError):
            self.verify(self.envelope(host="cli", transport="cli"))

    def test_bad_signature_is_blocked(self):
        envelope = self.envelope()
        envelope["signature"] = "invalid"
        with self.assertRaises(activation.InvalidActivationEnvelope):
            self.verify(envelope)

    def test_expired_envelope_is_blocked(self):
        with self.assertRaises(activation.InvalidActivationEnvelope):
            self.verify(self.envelope(issued_at=1_799_999_000, expires_at=1_799_999_060))

    def test_forbidden_capability_is_blocked(self):
        with self.assertRaises(activation.InvalidActivationEnvelope):
            self.verify(self.envelope(capabilities=["commit_unrestricted_changes"]))

    def test_process_attestation_is_required(self):
        metadata = json.loads((PROFILE / "profile.json").read_text(encoding="utf-8"))
        policy = metadata["activation"]
        with patch.object(activation, "_process_name", return_value="python"):
            with self.assertRaises(activation.ProfileLockedError):
                activation.verify_studio_envelope(
                    self.envelope(), state_root=self.state_root,
                    allowed_capabilities=metadata["allowed_capabilities"],
                    accepted_process_names=policy["accepted_process_names"],
                    maximum_ttl_seconds=120, clock_skew_seconds=5,
                    require_process_attestation=True, now=1_800_000_010,
                )

    def test_valid_envelope_is_authorized_once(self):
        envelope = self.envelope()
        session = self.verify(envelope)
        self.assertEqual(session.profile_id, "roblox_studio")
        self.assertEqual(session.place_id, "place-12345678")
        with self.assertRaises(activation.ReplayDetected):
            self.verify(envelope)

    def test_loader_rejects_missing_session(self):
        with self.assertRaises(Exception):
            loader.load_profile("roblox_studio", activation_envelope=None, state_root=self.state_root)

    def test_pairing_key_is_private_on_posix(self):
        if os.name != "nt":
            mode = activation.pairing_key_path(self.state_root).stat().st_mode & 0o777
            self.assertEqual(mode, 0o600)


if __name__ == "__main__":
    unittest.main()
