from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from syntavra_runtime.language_lsp import GenericLSPAdapter, LSPServiceManifest, LSPServiceRegistry
import syntavra_runtime.lsp_worker as lsp_worker


FAKE_SERVER = r'''
import json
import sys


def read_message():
    headers = {}
    while True:
        line = sys.stdin.buffer.readline()
        if not line:
            return None
        if line in (b"\r\n", b"\n"):
            break
        name, value = line.decode("ascii").split(":", 1)
        headers[name.strip().lower()] = value.strip()
    length = int(headers["content-length"])
    return json.loads(sys.stdin.buffer.read(length))


def send(payload):
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    sys.stdout.buffer.write(f"Content-Length: {len(body)}\r\n\r\n".encode("ascii") + body)
    sys.stdout.buffer.flush()


while True:
    message = read_message()
    if message is None:
        break
    method = message.get("method")
    if method == "initialize":
        send({"jsonrpc": "2.0", "id": message["id"], "result": {"capabilities": {"documentSymbolProvider": True}}})
    elif method == "textDocument/documentSymbol":
        send({
            "jsonrpc": "2.0",
            "id": message["id"],
            "result": [
                {
                    "name": "FutureType",
                    "kind": 5,
                    "range": {"start": {"line": 0, "character": 0}, "end": {"line": 4, "character": 1}},
                    "selectionRange": {"start": {"line": 0, "character": 6}, "end": {"line": 0, "character": 16}},
                    "children": [
                        {
                            "name": "ignite",
                            "kind": 12,
                            "range": {"start": {"line": 1, "character": 2}, "end": {"line": 3, "character": 3}},
                            "selectionRange": {"start": {"line": 1, "character": 5}, "end": {"line": 1, "character": 11}}
                        }
                    ]
                }
            ]
        })
    elif method == "shutdown":
        send({"jsonrpc": "2.0", "id": message["id"], "result": None})
    elif method == "exit":
        break
    elif "id" in message:
        send({"jsonrpc": "2.0", "id": message["id"], "result": None})
'''


class FakeBroker:
    def __init__(self, payload: dict, *, ok: bool = True):
        self.payload = payload
        self.ok = ok
        self.calls = []

    def run(self, command, *, policy, input_bytes):
        self.calls.append((tuple(command), policy, json.loads(input_bytes)))
        return SimpleNamespace(
            ok=self.ok,
            exit_code=0 if self.ok else 2,
            timed_out=False,
            output_limit_exceeded=False,
            stdout=json.dumps(self.payload),
            stderr="",
        )


class GenericLSPTests(unittest.TestCase):
    @staticmethod
    def python_digest() -> str:
        return hashlib.sha256(Path(sys.executable).resolve().read_bytes()).hexdigest()

    def manifest(self, **overrides) -> LSPServiceManifest:
        value = {
            "id": "future-lsp",
            "languages": ["future-language"],
            "server_command": [sys.executable, "fake-server.py"],
            "server_executable_sha256": self.python_digest(),
            "initialization_options": {"semanticMode": True},
            "strict_native": False,
        }
        value.update(overrides)
        return LSPServiceManifest.from_mapping(value, source="test")

    def test_manifest_rejects_shell_command_and_missing_hash(self) -> None:
        with self.assertRaises(ValueError):
            LSPServiceManifest.from_mapping(
                {"id": "bad", "languages": ["x"], "server_command": "x --stdio", "server_executable_sha256": "0" * 64},
                source="test",
            )
        with self.assertRaises(ValueError):
            LSPServiceManifest.from_mapping(
                {"id": "bad", "languages": ["x"], "server_command": ["x"]},
                source="test",
            )

    def test_adapter_requires_explicit_authorization(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            adapter = GenericLSPAdapter(
                self.manifest(),
                workspace=root,
                state_root=root / "state",
                broker=FakeBroker({}),
            )
            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop("SYNTAVRA_ALLOW_LSP_SERVICES", None)
                with self.assertRaises(PermissionError):
                    adapter.parse(path="main.future", text="type Future", evidence_ref="sha256:test")

    def test_adapter_validates_graph_and_uses_network_isolated_child_policy(self) -> None:
        payload = {
            "protocol": "syntavra-lsp-bridge",
            "nodes": [
                {
                    "node_id": "lsp:n1",
                    "kind": "class",
                    "name": "FutureType",
                    "qualified_name": "main.future:FutureType",
                    "start_line": 1,
                    "end_line": 4,
                }
            ],
            "edges": [],
            "diagnostics": ["server-capabilities:ok"],
            "server_stderr_truncated": False,
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            broker = FakeBroker(payload)
            adapter = GenericLSPAdapter(
                self.manifest(),
                workspace=root,
                state_root=root / "state",
                broker=broker,
            )
            with patch.dict(os.environ, {"SYNTAVRA_ALLOW_LSP_SERVICES": "1"}, clear=False):
                result = adapter.parse(path="main.future", text="type Future", evidence_ref="sha256:test")
            self.assertEqual(result.capability_level, "semantic")
            self.assertTrue(result.nodes[0]["metadata"]["exact_semantic"])
            _, policy, request = broker.calls[0]
            self.assertEqual(policy.network_hosts, ())
            self.assertTrue(policy.allow_child_processes)
            self.assertEqual(policy.max_stdout_bytes, self.manifest().max_output_bytes)
            self.assertEqual(request["server_executable_sha256"], self.python_digest())

    def test_adapter_rejects_unknown_edge_and_truncated_server_stderr(self) -> None:
        base = {
            "protocol": "syntavra-lsp-bridge",
            "nodes": [{"node_id": "n1", "kind": "class", "name": "A", "start_line": 1}],
            "edges": [{"source": "n1", "target": "missing", "edge_type": "contains"}],
            "server_stderr_truncated": False,
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            adapter = GenericLSPAdapter(self.manifest(), workspace=root, state_root=root / "state", broker=FakeBroker(base))
            with patch.dict(os.environ, {"SYNTAVRA_ALLOW_LSP_SERVICES": "1"}, clear=False):
                with self.assertRaises(ValueError):
                    adapter.parse(path="main.future", text="A", evidence_ref="sha256:test")
            truncated = dict(base)
            truncated["edges"] = []
            truncated["server_stderr_truncated"] = True
            adapter = GenericLSPAdapter(self.manifest(), workspace=root, state_root=root / "state2", broker=FakeBroker(truncated))
            with patch.dict(os.environ, {"SYNTAVRA_ALLOW_LSP_SERVICES": "1"}, clear=False):
                with self.assertRaises(ValueError):
                    adapter.parse(path="main.future", text="A", evidence_ref="sha256:test")

    def test_real_stdio_worker_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            server = root / "fake_lsp_server.py"
            server.write_text(textwrap.dedent(FAKE_SERVER), encoding="utf-8")
            source = root / "main.future"
            source.write_text("type FutureType {\n  fn ignite() {}\n}\n", encoding="utf-8")
            request = {
                "protocol": "syntavra-lsp-bridge",
                "workspace": str(root),
                "path": source.name,
                "language_id": "future-language",
                "text": source.read_text(encoding="utf-8"),
                "server_command": [sys.executable, str(server)],
                "server_executable_sha256": self.python_digest(),
                "initialization_options": {},
                "timeout_seconds": 5,
                "max_message_bytes": 1024 * 1024,
            }
            environment = dict(os.environ)
            environment["SYNTAVRA_WORKSPACE"] = str(root)
            completed = subprocess.run(
                [sys.executable, str(Path(lsp_worker.__file__).resolve())],
                input=json.dumps(request).encode("utf-8"),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=root,
                env=environment,
                timeout=10,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr.decode("utf-8", errors="replace"))
            result = json.loads(completed.stdout)
            self.assertEqual(result["protocol"], "syntavra-lsp-bridge")
            self.assertEqual([item["name"] for item in result["nodes"]], ["FutureType", "ignite"])
            self.assertEqual(result["edges"][0]["edge_type"], "contains")

    def test_registry_discovery_is_data_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            directory = root / ".syntavra" / "lsp-services"
            directory.mkdir(parents=True)
            (directory / "future.json").write_text(
                json.dumps(
                    {
                        "id": "future",
                        "languages": ["future-language"],
                        "server_command": [sys.executable, "fake_lsp_server.py"],
                        "server_executable_sha256": self.python_digest(),
                        "strict_native": False,
                    }
                ),
                encoding="utf-8",
            )
            registry = LSPServiceRegistry()
            registry.discover(root)
            self.assertEqual(registry.inventory()["services"], 1)
            self.assertFalse(registry.inventory()["execution_authorized"])


if __name__ == "__main__":
    unittest.main()
