from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from syntavra_runtime.language_services import (
    LanguageServiceManifest,
    LanguageServiceRegistry,
    SandboxedLanguageServiceAdapter,
)


class FakeBroker:
    def __init__(self, payload: dict, *, ok: bool = True):
        self.payload = payload
        self.ok = ok
        self.calls = []

    def run(self, command, *, policy, input_bytes):
        self.calls.append((tuple(command), policy, json.loads(input_bytes)))
        return SimpleNamespace(
            ok=self.ok,
            exit_code=0 if self.ok else 1,
            timed_out=False,
            stdout=json.dumps(self.payload),
            stderr="",
        )


class LanguageServiceTests(unittest.TestCase):
    def executable(self, root: Path, content: bytes = b"analyzer") -> tuple[Path, str]:
        path = root / "analyzer"
        path.write_bytes(content)
        path.chmod(0o700)
        return path, hashlib.sha256(content).hexdigest()

    def manifest(self, executable: Path, digest: str, **overrides):
        value = {
            "id": "future-language-analyzer",
            "languages": ["future-language"],
            "command": [str(executable), "--json"],
            "executable_sha256": digest,
            "capabilities": ["syntax", "semantic", "definitions", "references"],
            "strict_native": False,
        }
        value.update(overrides)
        return LanguageServiceManifest.from_mapping(value, source="test")

    def test_shell_string_commands_are_forbidden(self) -> None:
        with self.assertRaises(ValueError):
            LanguageServiceManifest.from_mapping(
                {
                    "id": "bad",
                    "languages": ["future"],
                    "command": "analyzer --json",
                    "executable_sha256": "0" * 64,
                    "capabilities": ["syntax"],
                },
                source="test",
            )

    def test_executable_hash_mismatch_is_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            executable, _ = self.executable(root)
            manifest = self.manifest(executable, "0" * 64)
            with self.assertRaises(PermissionError):
                SandboxedLanguageServiceAdapter(
                    manifest,
                    workspace=root,
                    state_root=root / "state",
                    broker=FakeBroker({}),
                )

    def test_execution_requires_explicit_authorization(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            executable, digest = self.executable(root)
            adapter = SandboxedLanguageServiceAdapter(
                self.manifest(executable, digest),
                workspace=root,
                state_root=root / "state",
                broker=FakeBroker({"nodes": [], "edges": []}),
            )
            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop("SYNTAVRA_ALLOW_LANGUAGE_SERVICES", None)
                with self.assertRaises(PermissionError):
                    adapter.parse(path="main.future", text="symbol", evidence_ref="sha256:test")

    def test_valid_service_upgrades_evidence_to_semantic(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            executable, digest = self.executable(root)
            broker = FakeBroker(
                {
                    "protocol": "syntavra-language-service",
                    "nodes": [
                        {
                            "node_id": "n1",
                            "kind": "function",
                            "name": "ignite",
                            "qualified_name": "main.future::ignite",
                            "start_line": 1,
                            "end_line": 2,
                        }
                    ],
                    "edges": [],
                    "diagnostics": ["verified"],
                }
            )
            adapter = SandboxedLanguageServiceAdapter(
                self.manifest(executable, digest),
                workspace=root,
                state_root=root / "state",
                broker=broker,
            )
            with patch.dict(os.environ, {"SYNTAVRA_ALLOW_LANGUAGE_SERVICES": "1"}, clear=False):
                result = adapter.parse(path="main.future", text="ignite", evidence_ref="sha256:test")
            self.assertEqual(result.capability_level, "semantic")
            self.assertEqual(result.nodes[0]["metadata"]["exact_semantic"], True)
            self.assertEqual(result.nodes[0]["metadata"]["service_executable_sha256"], digest)
            self.assertEqual(broker.calls[0][2]["operation"], "analyze")
            self.assertEqual(broker.calls[0][1].network_hosts, ())
            self.assertFalse(broker.calls[0][1].allow_child_processes)

    def test_unknown_edge_endpoints_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            executable, digest = self.executable(root)
            broker = FakeBroker(
                {
                    "nodes": [{"node_id": "n1", "kind": "function", "name": "ignite", "start_line": 1}],
                    "edges": [{"source": "n1", "target": "missing", "edge_type": "calls"}],
                }
            )
            adapter = SandboxedLanguageServiceAdapter(
                self.manifest(executable, digest),
                workspace=root,
                state_root=root / "state",
                broker=broker,
            )
            with patch.dict(os.environ, {"SYNTAVRA_ALLOW_LANGUAGE_SERVICES": "1"}, clear=False):
                with self.assertRaises(ValueError):
                    adapter.parse(path="main.future", text="ignite", evidence_ref="sha256:test")

    def test_manifest_discovery_is_data_only_and_conflicts_fail(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            executable, digest = self.executable(root)
            directory = root / ".syntavra" / "language-services"
            directory.mkdir(parents=True)
            (directory / "future.json").write_text(
                json.dumps(
                    {
                        "id": "future",
                        "languages": ["future"],
                        "command": [str(executable)],
                        "executable_sha256": digest,
                        "capabilities": ["syntax"],
                        "strict_native": False,
                    }
                ),
                encoding="utf-8",
            )
            registry = LanguageServiceRegistry()
            registry.discover(root)
            self.assertEqual(registry.inventory()["services"], 1)
            self.assertEqual(registry.for_language("future")[0].service_id, "future")
            conflicting = LanguageServiceManifest.from_mapping(
                {
                    "id": "future",
                    "languages": ["future"],
                    "command": [str(executable), "--different"],
                    "executable_sha256": digest,
                    "capabilities": ["syntax"],
                    "strict_native": False,
                },
                source="test",
            )
            with self.assertRaises(ValueError):
                registry.register(conflicting)


if __name__ == "__main__":
    unittest.main()
