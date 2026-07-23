from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from syntavra_runtime.command_compactors import CommandCompactorRegistry
from syntavra_runtime.competitive_fabric import CommandCompactor, StructuralNavigator
from syntavra_runtime.context_pack import TaskContextAssembler
from syntavra_runtime.mcp_server import MCPServer
from syntavra_runtime.structural import StructuralIndex
from syntavra_runtime.token_attribution import TokenAttributionLedger
from syntavra_runtime.tool_registry import (
    BALANCED_TOOLS,
    MCP_PROFILES,
    MINIMAL_TOOLS,
    ToolSchemaCompiler,
    normalize_profile,
)


class TokenSaverUnificationV001Tests(unittest.TestCase):
    def test_profiles_have_one_canonical_source_and_compatibility_aliases(self) -> None:
        self.assertEqual(normalize_profile("tiny"), "minimal")
        self.assertEqual(normalize_profile("optimized"), "balanced")
        self.assertEqual(normalize_profile("full"), "audit")
        self.assertEqual(len(MINIMAL_TOOLS), 8)
        self.assertEqual(len(BALANCED_TOOLS), 36)
        self.assertEqual(MCP_PROFILES["minimal"].exposed_tools, MINIMAL_TOOLS)
        self.assertEqual(MCP_PROFILES["balanced"].exposed_tools, BALANCED_TOOLS)

    def test_schema_compiler_is_deterministic_and_decodes_readable_aliases(self) -> None:
        catalog = [{
            "name": "syntavra.example",
            "description": "Retrieve a query-conditioned repository response through exact externalization with more explanatory words than needed",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "repository_tree": {"type": "string"},
                    "continuation_token": {"type": "string"},
                    "budget_bytes": {"type": "integer"},
                },
                "required": ["repository_tree", "continuation_token"],
            },
        }]
        compiler = ToolSchemaCompiler()
        first, receipt = compiler.compile_catalog(catalog)
        second, repeated = compiler.compile_catalog(catalog)
        self.assertEqual(first, second)
        self.assertEqual(receipt.compiled_hash, repeated.compiled_hash)
        self.assertLess(receipt.compiled.tokens, receipt.raw.tokens)
        properties = first[0]["inputSchema"]["properties"]
        self.assertIn("repo_tree", properties)
        self.assertIn("cursor", properties)
        decoded = compiler.decode_arguments("syntavra.example", {"repo_tree": "abc", "cursor": "next", "budget": 10})
        self.assertEqual(decoded["repository_tree"], "abc")
        self.assertEqual(decoded["continuation_token"], "next")
        self.assertEqual(decoded["budget_bytes"], 10)

    def test_native_mcp_pipeline_filters_compiles_and_authorizes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            skill = root / "skill"
            skill.mkdir()
            (skill / "SKILL.md").write_text("name: syntavra\n", encoding="utf-8")
            with patch.dict(os.environ, {"SYNTAVRA_MCP_PROFILE": "minimal", "SYNTAVRA_SCHEMA_MODE": "compact"}, clear=False):
                server = MCPServer(project=root, state_root=root / "state", skill_root=skill, codex_home=root / ".codex", host="codex")
                listed = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
                self.assertEqual(len(listed["result"]["tools"]), 8)
                manifest = listed["result"]["_meta"]["syntavra"]
                self.assertEqual(manifest["policy"]["profile"], "minimal")
                self.assertEqual(manifest["schema"]["mode"], "compact")
                denied = server.handle({
                    "jsonrpc": "2.0", "id": 2, "method": "tools/call",
                    "params": {"name": "syntavra.evidence.rotate_key", "arguments": {}},
                })
                self.assertEqual(denied["error"]["code"], -32001)

    def test_token_attribution_keeps_confidence_and_provider_linkage(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            ledger = TokenAttributionLedger(Path(directory) / "usage.sqlite3")
            digest = hashlib.sha256(b"provider").hexdigest()
            receipt = ledger.record(
                task_id="task-1",
                arm_id="syntavra-minimal",
                repetition=1,
                session_id="session-1",
                provider="openai",
                model="model",
                request_id_hash=hashlib.sha256(b"request").hexdigest(),
                provider_receipt_hash=digest,
                sources={"tool_schema": 100, "repository_context": 300, "tool_output": 200, "assistant_output": 50},
                confidence={
                    "tool_schema": "LOCALLY_TOKENIZED",
                    "repository_context": "LOCALLY_TOKENIZED",
                    "tool_output": "LOCALLY_TOKENIZED",
                    "assistant_output": "PROVIDER_OBSERVED",
                },
                baseline_tokens=1_000,
                baseline_confidence="LOCALLY_TOKENIZED",
            )
            self.assertEqual(receipt.observed_tokens, 650)
            self.assertEqual(receipt.avoided_tokens, 350)
            summary = ledger.summary(session_id="session-1")
            self.assertEqual(summary["avoided_tokens"], 350)
            self.assertEqual(summary["sources"]["repository_context"], 300)
            self.assertEqual(summary["confidence"]["PROVIDER_OBSERVED"], 1)

    def test_command_registry_has_specific_compactors_and_retains_failures(self) -> None:
        registry = CommandCompactorRegistry()
        self.assertGreaterEqual(registry.manifest()["count"], 60)
        result = CommandCompactor(registry).compact(
            ["pytest", "-q"],
            "test_auth.py:10: AssertionError: expected 200 got 401\n" + "\n".join(f"test_{i} passed" for i in range(200)) + "\n199 passed, 1 failed",
            budget_bytes=1200,
        )
        self.assertEqual(result.compactor, "pytest")
        self.assertIn("AssertionError", result.visible_text)
        self.assertGreater(result.savings_ratio, 0.5)

    def test_task_context_pack_prioritizes_definition_and_verifier(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "src").mkdir()
            (root / "tests").mkdir()
            (root / "src" / "auth.py").write_text(
                "def refresh_token(value):\n    return value.strip()\n\ndef login(value):\n    return refresh_token(value)\n",
                encoding="utf-8",
            )
            (root / "tests" / "test_auth.py").write_text(
                "from src.auth import refresh_token\n\ndef test_refresh():\n    assert refresh_token(' a ') == 'a'\n",
                encoding="utf-8",
            )
            index = StructuralIndex(root / "state.sqlite3", repository_root=root, repository_id="repo")
            pack = TaskContextAssembler(index, StructuralNavigator(root)).assemble("refresh_token", token_budget=2_000)
            self.assertTrue(pack.items)
            self.assertEqual(pack.items[0].tier, "mandatory")
            self.assertIn("src/auth.py", pack.affected_paths)
            self.assertTrue(any("unittest" in command for command in pack.required_verifiers))
            self.assertEqual(len(pack.pack_hash), 64)

    def test_signalbench_arm_templates_cover_competitors_without_claims(self) -> None:
        value = json.loads(Path("benchmarks/signalbench/arms.example.json").read_text(encoding="utf-8"))
        arms = {row["arm_id"] for row in value["arms"]}
        self.assertEqual(len(arms), 8)
        self.assertTrue({"plain-host", "caveman", "rtk", "token-savior", "syntavra-minimal", "syntavra-balanced"} <= arms)
        self.assertIn("Templates only", value["claim_boundary"])

    def test_external_signalbench_adapter_requires_real_bound_output(self) -> None:
        adapter = Path("benchmarks/signalbench/adapters/external_cli.py").resolve()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspace"
            workspace.mkdir()
            request = root / "request.json"
            output = root / "output.json"
            request.write_text(json.dumps({"task": {"prompt": "verify"}}), encoding="utf-8")

            missing = subprocess.run(
                [sys.executable, str(adapter), "--product", "missing", "--request", str(request),
                 "--output", str(output), "--workspace", str(workspace)],
                cwd=Path.cwd(), capture_output=True, text=True, check=False,
            )
            self.assertEqual(missing.returncode, 2)
            self.assertFalse(output.exists())

            fake = root / "fake_arm.py"
            fake.write_text(
                "import json, os, pathlib\n"
                "path=pathlib.Path(os.environ['SIGNALBENCH_AGENT_RESULT'])\n"
                "path.write_text(json.dumps({'metrics': {'fresh_input_tokens': 10, 'cached_input_tokens': 2, 'output_tokens': 3, 'reasoning_tokens': 1, 'quota_cost': 0.01}, 'provider_receipt': {'provider': 'test', 'model': 'test', 'request_id': 'req', 'response_hash': 'a'*64}}))\n",
                encoding="utf-8",
            )
            env = dict(os.environ)
            env["SYNTAVRA_SIGNALBENCH_FAKE_COMMAND_JSON"] = json.dumps([sys.executable, str(fake)])
            completed = subprocess.run(
                [sys.executable, str(adapter), "--product", "fake", "--request", str(request),
                 "--output", str(output), "--workspace", str(workspace)],
                cwd=Path.cwd(), env=env, capture_output=True, text=True, check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            value = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(value["metrics"]["fresh_input_tokens"], 10)
            self.assertEqual(value["provider_receipt"]["provider"], "test")

    def test_platform_registry_distinguishes_contract_from_live_target(self) -> None:
        value = json.loads(Path("skills/syntavra/data/platforms.json").read_text(encoding="utf-8"))
        rows = {row["id"]: row for row in value["platforms"]}
        for target in ("codex", "claude-code", "cursor"):
            self.assertEqual(rows[target]["evidence_level"], "PRIMARY_CERTIFICATION_TARGET")
            self.assertNotEqual(rows[target]["verified_scope"], "live-external")


if __name__ == "__main__":
    unittest.main()
