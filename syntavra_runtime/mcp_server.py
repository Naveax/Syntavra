from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any, TextIO

from .competitive_fabric import CompetitiveContextFabric, StructuralNavigator
from .compression import ContentRouter, ReversibleContentStore
from .context_governor import evaluate
from .evidence import EvidenceStore
from .host_adapters import detect_hosts
from .host_output_pipeline import HostOutputPipeline
from .output_governor import OutputGovernor
from .process_broker import ProcessBroker
from .sandbox import SandboxManager, SandboxPolicy
from .session_retrieval import SessionSemanticRetriever
from .session_runtime import SessionRuntime
from .status import inspect_runtime
from .structural import StructuralIndex
from .tool_externalization import ToolOutputExternalizer
from .tool_externalization_types import ExternalizationPolicy, ToolPayload
from .usage_receipt_ledger import UsageReceiptLedger
from .util import stable_project_id


class MCPServer:
    """Dependency-free MCP JSON-RPC server for the complete v0.3 runtime plane."""

    VERSION = "0.0.1"

    def __init__(self, *, project: Path, state_root: Path, skill_root: Path, codex_home: Path, host: str):
        self.project = project.resolve(strict=True)
        self.state_root = state_root
        self.skill_root = skill_root
        self.codex_home = codex_home
        self.host = host
        project_id = stable_project_id(self.project)
        self.evidence = EvidenceStore(state_root / "evidence", project_id=project_id)
        self.broker = ProcessBroker(state_root / "broker", self.evidence)
        self.compression_store = ReversibleContentStore(state_root / "compression.sqlite3", evidence=self.evidence)
        self.compressor = ContentRouter(self.compression_store, repository_root=self.project)
        self.sandbox = SandboxManager(state_root / "sandbox", project=self.project, evidence=self.evidence)
        self.sessions = SessionRuntime(state_root / "sessions.sqlite3", project_id=project_id)
        self.externalizer = ToolOutputExternalizer(
            state_root / "tool-externalization.sqlite3",
            evidence=self.evidence,
            policy=ExternalizationPolicy.for_profile("balanced"),
        )
        self.usage_ledger = UsageReceiptLedger(state_root / "usage-receipts.sqlite3")
        self.output_pipeline = HostOutputPipeline(
            self.externalizer, usage_ledger=self.usage_ledger, sessions=self.sessions
        )
        self.session_retriever = SessionSemanticRetriever(self.sessions)
        self.fabric = CompetitiveContextFabric(
            state_root / "competitive-fabric.sqlite3", project=self.project, host=self.host
        )
        self.navigator = StructuralNavigator(self.project)

    @staticmethod
    def tools() -> list[dict[str, Any]]:
        def tool(
            name: str,
            description: str,
            properties: dict[str, Any] | None = None,
            required: list[str] | None = None,
        ) -> dict[str, Any]:
            schema: dict[str, Any] = {"type": "object", "properties": properties or {}}
            if required:
                schema["required"] = required
            return {"name": name, "description": description, "inputSchema": schema}

        command_schema = {
            "oneOf": [
                {"type": "string"},
                {"type": "array", "items": {"type": "string"}},
            ]
        }
        return [
            tool("syntavra.status", "Inspect truthful runtime health"),
            tool("syntavra.host.detect", "Detect installed coding-agent hosts"),
            tool(
                "syntavra.process.submit", "Submit a durable zero-poll command",
                {"argv": {"type": "array", "items": {"type": "string"}}, "timeout": {"type": "number"}, "repository_tree": {"type": "string"}}, ["argv"],
            ),
            tool(
                "syntavra.process.completions", "Read durable completion events after a cursor",
                {"after": {"type": "integer"}, "limit": {"type": "integer"}},
            ),
            tool(
                "syntavra.inspect.symbol", "Find exact multi-language symbols",
                {"query": {"type": "string"}, "limit": {"type": "integer"}}, ["query"],
            ),
            tool(
                "syntavra.inspect.source", "Retrieve exact bounded source for matching symbols",
                {"query": {"type": "string"}, "limit": {"type": "integer"}, "context_lines": {"type": "integer"}}, ["query"],
            ),
            tool(
                "syntavra.inspect.range", "Read an exact bounded project file range",
                {"path": {"type": "string"}, "start_line": {"type": "integer"}, "end_line": {"type": "integer"}, "max_bytes": {"type": "integer"}}, ["path"],
            ),
            tool(
                "syntavra.inspect.impact", "Inspect transitive multi-language impact",
                {"query": {"type": "string"}, "max_depth": {"type": "integer"}}, ["query"],
            ),
            tool(
                "syntavra.inspect.paths", "Inspect impact from changed paths",
                {"paths": {"type": "array", "items": {"type": "string"}}, "max_depth": {"type": "integer"}}, ["paths"],
            ),
            tool(
                "syntavra.inspect.map", "Build a query-conditioned repository map",
                {"query": {"type": "string"}, "token_budget": {"type": "integer"}, "max_depth": {"type": "integer"}}, ["query"],
            ),
            tool("syntavra.inspect.stats", "Inspect structural-index coverage and parser health"),
            tool(
                "syntavra.context.evaluate", "Evaluate context pressure",
                {"used": {"type": "integer"}, "window": {"type": "integer"}, "churn": {"type": "number"}, "evidence_pressure": {"type": "number"}}, ["used", "window"],
            ),
            tool(
                "syntavra.compress", "Reversibly compress generic content",
                {"text": {"type": "string"}, "hint": {"type": "string"}, "path": {"type": "string"}, "budget_bytes": {"type": "integer"}}, ["text"],
            ),
            tool(
                "syntavra.expand", "Restore a compression or one exact chunk",
                {"compression_id": {"type": "string"}, "chunk": {"type": "integer"}}, ["compression_id"],
            ),
            tool(
                "syntavra.compression.verify", "Verify exact compression reconstruction",
                {"compression_id": {"type": "string"}}, ["compression_id"],
            ),
            tool(
                "syntavra.sandbox.plan", "Plan a fail-closed sandbox execution",
                {"argv": {"type": "array", "items": {"type": "string"}}, "backend": {"type": "string"}, "network": {"type": "string"}, "strict": {"type": "boolean"}}, ["argv"],
            ),
            tool(
                "syntavra.sandbox.execute", "Execute a command under sandbox policy",
                {"argv": {"type": "array", "items": {"type": "string"}}, "backend": {"type": "string"}, "network": {"type": "string"}, "strict": {"type": "boolean"}, "timeout": {"type": "number"}}, ["argv"],
            ),
            tool(
                "syntavra.sandbox.batch", "Execute multiple commands in one sandbox call",
                {"commands": {"type": "array", "items": {"type": "array", "items": {"type": "string"}}}, "backend": {"type": "string"}, "network": {"type": "string"}, "strict": {"type": "boolean"}, "timeout": {"type": "number"}, "stop_on_failure": {"type": "boolean"}}, ["commands"],
            ),
            tool(
                "syntavra.session.open", "Open an immutable long-session runtime",
                {"session_id": {"type": "string"}, "metadata": {"type": "object"}},
            ),
            tool(
                "syntavra.session.append", "Append an immutable session event",
                {"session_id": {"type": "string"}, "event_type": {"type": "string"}, "payload": {"type": "object"}}, ["session_id", "event_type", "payload"],
            ),
            tool(
                "syntavra.session.context", "Assemble bounded active context",
                {"session_id": {"type": "string"}, "token_budget": {"type": "integer"}}, ["session_id"],
            ),
            tool(
                "syntavra.session.compact", "Build or refresh the exact summary DAG",
                {"session_id": {"type": "string"}, "leaf_size": {"type": "integer"}, "fanout": {"type": "integer"}, "force": {"type": "boolean"}}, ["session_id"],
            ),
            tool(
                "syntavra.session.expand", "Expand a summary DAG node to exact events",
                {"summary_id": {"type": "string"}}, ["summary_id"],
            ),
            tool(
                "syntavra.session.verify", "Verify immutable session hash lineage",
                {"session_id": {"type": "string"}}, ["session_id"],
            ),
            tool(
                "syntavra.session.checkpoint", "Create an exact session checkpoint",
                {"session_id": {"type": "string"}, "metadata": {"type": "object"}}, ["session_id"],
            ),
            tool(
                "syntavra.session.fork", "Fork a session from a verified checkpoint",
                {"session_id": {"type": "string"}, "metadata": {"type": "object"}}, ["session_id"],
            ),
            tool(
                "syntavra.session.merge", "Merge verified parent sessions",
                {"session_ids": {"type": "array", "items": {"type": "string"}}, "metadata": {"type": "object"}}, ["session_ids"],
            ),
            tool(
                "syntavra.output.capture", "Capture tool output through exact externalization",
                {"stdout": {"type": "string"}, "stderr": {"type": "string"}, "command": {"type": "string"}, "tool_name": {"type": "string"}, "path": {"type": "string"}, "scope_key": {"type": "string"}},
            ),
            tool(
                "syntavra.output.search", "Search exact externalized tool output",
                {"query": {"type": "string"}, "artifact_id": {"type": "string"}, "scope_key": {"type": "string"}, "limit": {"type": "integer"}}, ["query"],
            ),
            tool(
                "syntavra.output.reveal", "Progressively reveal selected externalized evidence",
                {"artifact_id": {"type": "string"}, "lens": {"type": "string"}, "query": {"type": "string"}, "budget_bytes": {"type": "integer"}, "continuation_token": {"type": "string"}},
            ),
            tool(
                "syntavra.output.verify", "Verify exact reconstruction and Merkle integrity",
                {"artifact_id": {"type": "string"}}, ["artifact_id"],
            ),
            tool("syntavra.output.stats", "Inspect externalization statistics"),
            tool(
                "syntavra.usage.record", "Record an attested provider usage receipt",
                {"task_id": {"type": "string"}, "arm_id": {"type": "string"}, "repetition": {"type": "integer"}, "cache_mode": {"type": "string"}, "provider": {"type": "string"}, "request_id": {"type": "string"}, "provider_response": {"type": "object"}, "usage": {"type": "object"}, "quota_cost": {"type": "number"}, "hardware_hash": {"type": "string"}},
                ["task_id", "arm_id", "repetition", "cache_mode", "provider", "request_id", "provider_response", "usage", "quota_cost", "hardware_hash"],
            ),
            tool("syntavra.usage.verify", "Verify the provider usage hash chain and signatures", {"require_hmac": {"type": "boolean"}}),
            tool(
                "syntavra.session.search", "Semantic and temporal search over exact session events",
                {"session_id": {"type": "string"}, "query": {"type": "string"}, "limit": {"type": "integer"}, "include_superseded": {"type": "boolean"}}, ["session_id", "query"],
            ),
            tool(
                "syntavra.session.semantic_context", "Build query-conditioned long-session context",
                {"session_id": {"type": "string"}, "query": {"type": "string"}, "budget_bytes": {"type": "integer"}, "include_superseded": {"type": "boolean"}}, ["session_id", "query"],
            ),
            tool(
                "syntavra.output.govern", "Render a correctness-preserving bounded answer",
                {"payload": {"type": "object"}, "profile": {"type": "string"}, "contract": {"type": "string"}}, ["payload"],
            ),
            tool(
                "syntavra.fabric.profile", "Select a minimal task-conditioned MCP tool surface",
                {"task": {"type": "string"}, "profile": {"type": "string"}},
            ),
            tool(
                "syntavra.fabric.route", "Choose the safest exact-preserving execution route",
                {"command": command_schema, "network_untrusted": {"type": "boolean"}, "repeated": {"type": "boolean"}}, ["command"],
            ),
            tool(
                "syntavra.fabric.compact", "Compact common command output while retaining failures and security signals",
                {"command": command_schema, "stdout": {"type": "string"}, "stderr": {"type": "string"}, "budget_bytes": {"type": "integer"}}, ["command", "stdout"],
            ),
            tool(
                "syntavra.fabric.cache_align", "Build a stable provider-prefix cache fingerprint",
                {"messages": {"type": "array", "items": {"type": "object"}}, "keep_tail": {"type": "integer"}}, ["messages"],
            ),
            tool(
                "syntavra.fabric.platform_plan", "Generate installation and enforcement plans for one or every host",
                {"host": {"type": "string"}, "scope": {"type": "string"}, "all": {"type": "boolean"}},
            ),
            tool("syntavra.fabric.doctor", "Diagnose competitive-fabric runtime coverage"),
            tool(
                "syntavra.fabric.insights", "Inspect local savings, reliability, cache, and routing analytics",
                {"since_seconds": {"type": "number"}},
            ),
        ]

    def exposed_tools(self) -> list[dict[str, Any]]:
        catalog = self.tools()
        requested = os.environ.get("SYNTAVRA_MCP_PROFILE", "optimized").strip().casefold() or "optimized"
        task = os.environ.get("SYNTAVRA_SESSION_TASK", "")
        plan = self.fabric.profile(task, (row["name"] for row in catalog), requested_profile=requested)
        selected = set(plan["selected_tools"])
        filtered = [row for row in catalog if row["name"] in selected]
        return filtered or catalog

    def _index(self) -> StructuralIndex:
        index = StructuralIndex(
            self.state_root / "structural.sqlite3",
            repository_root=self.project,
            repository_id=stable_project_id(self.project),
        )
        index.index()
        return index

    @staticmethod
    def _policy(arguments: dict[str, Any]) -> SandboxPolicy:
        return SandboxPolicy(
            backend=str(arguments.get("backend", "auto")),
            network=str(arguments.get("network", "none")),
            strict=bool(arguments.get("strict", True)),
            timeout_seconds=float(arguments.get("timeout", 1200)),
        )

    def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        if name == "syntavra.status":
            return asdict(inspect_runtime(
                project_root=self.project,
                skill_root=self.skill_root,
                state_root=self.state_root,
                codex_home=self.codex_home,
                host=self.host,
            ))
        if name == "syntavra.host.detect":
            return {"hosts": detect_hosts(self.project)}
        if name == "syntavra.process.submit":
            job = self.broker.submit(
                tuple(arguments["argv"]),
                cwd=self.project,
                timeout=float(arguments.get("timeout", 1200)),
                repository_tree=str(arguments.get("repository_tree", "unknown")),
            )
            return {"event": "JOB_ACCEPTED", "job": asdict(job), "model_polling_calls": 0}
        if name == "syntavra.process.completions":
            result = self.broker.drain_completions(after=int(arguments.get("after", 0)), limit=int(arguments.get("limit", 100)))
            return {"cursor": result["cursor"], "events": [asdict(event) for event in result["events"]]}
        if name == "syntavra.inspect.symbol":
            return self._index().inspect_symbol(str(arguments["query"]), limit=int(arguments.get("limit", 20)))
        if name == "syntavra.inspect.source":
            return self.navigator.symbol_source(
                self._index(), str(arguments["query"]), limit=int(arguments.get("limit", 8)),
                context_lines=int(arguments.get("context_lines", 2)),
            )
        if name == "syntavra.inspect.range":
            return self.navigator.read_range(
                str(arguments["path"]), start_line=int(arguments.get("start_line", 1)),
                end_line=arguments.get("end_line"), max_bytes=int(arguments.get("max_bytes", 64 * 1024)),
            )
        if name == "syntavra.inspect.impact":
            return self._index().inspect_impact(str(arguments["query"]), max_depth=int(arguments.get("max_depth", 4)))
        if name == "syntavra.inspect.paths":
            return self._index().impacted_by_paths(tuple(arguments["paths"]), max_depth=int(arguments.get("max_depth", 4)))
        if name == "syntavra.inspect.map":
            return self._index().repository_map(
                str(arguments["query"]), token_budget=int(arguments.get("token_budget", 2000)),
                max_depth=int(arguments.get("max_depth", 4)),
            )
        if name == "syntavra.inspect.stats":
            return self._index().stats()
        if name == "syntavra.context.evaluate":
            return asdict(evaluate(
                int(arguments["used"]), int(arguments["window"]),
                churn=float(arguments.get("churn", 0)), evidence_pressure=float(arguments.get("evidence_pressure", 0)),
            ))
        if name == "syntavra.compress":
            return asdict(self.compressor.compress(
                str(arguments["text"]), hint=str(arguments.get("hint", "")),
                path=str(arguments.get("path", "")), budget_bytes=int(arguments.get("budget_bytes", 8192)),
            ))
        if name == "syntavra.expand":
            data = self.compression_store.restore(str(arguments["compression_id"]), chunk=arguments.get("chunk"))
            return {"bytes": len(data), "text": data.decode("utf-8", errors="replace")}
        if name == "syntavra.compression.verify":
            compression_id = str(arguments["compression_id"])
            return {"compression_id": compression_id, "ok": self.compression_store.verify_roundtrip(compression_id)}
        if name in {"syntavra.sandbox.plan", "syntavra.sandbox.execute"}:
            policy = self._policy(arguments)
            if name.endswith("plan"):
                return asdict(self.sandbox.plan(tuple(arguments["argv"]), policy=policy))
            return asdict(self.sandbox.execute(tuple(arguments["argv"]), policy=policy))
        if name == "syntavra.sandbox.batch":
            results = self.sandbox.execute_batch(
                (tuple(command) for command in arguments["commands"]),
                policy=self._policy(arguments),
                stop_on_failure=bool(arguments.get("stop_on_failure", True)),
            )
            return {"results": [asdict(result) for result in results], "completed": len(results)}
        if name == "syntavra.output.capture":
            artifact = self.externalizer.externalize(ToolPayload(
                command=str(arguments.get("command", "")), stdout=str(arguments.get("stdout", "")),
                stderr=str(arguments.get("stderr", "")), tool_name=str(arguments.get("tool_name", "mcp")),
                path=str(arguments.get("path", "")), scope_key=str(arguments.get("scope_key", "default")),
                metadata=dict(arguments.get("metadata") or {}),
            ))
            return asdict(artifact)
        if name == "syntavra.output.search":
            return {"hits": [asdict(hit) for hit in self.externalizer.search(
                str(arguments["query"]), artifact_id=arguments.get("artifact_id"),
                scope_key=arguments.get("scope_key"), limit=int(arguments.get("limit", 8)),
            )]}
        if name == "syntavra.output.reveal":
            return asdict(self.externalizer.reveal(
                arguments.get("artifact_id"), lens=str(arguments.get("lens", "salient")),
                query=str(arguments.get("query", "")), budget_bytes=arguments.get("budget_bytes"),
                continuation_token=arguments.get("continuation_token"),
            ))
        if name == "syntavra.output.verify":
            return self.externalizer.verify(str(arguments["artifact_id"]))
        if name == "syntavra.output.stats":
            return self.externalizer.stats()
        if name == "syntavra.usage.record":
            entry = self.usage_ledger.record(
                task_id=str(arguments["task_id"]), arm_id=str(arguments["arm_id"]),
                repetition=int(arguments["repetition"]), cache_mode=str(arguments["cache_mode"]),
                provider=str(arguments["provider"]), request_id=str(arguments["request_id"]),
                provider_response=dict(arguments["provider_response"]), usage_payload=dict(arguments["usage"]),
                quota_cost=float(arguments["quota_cost"]), hardware_hash=str(arguments["hardware_hash"]),
            )
            return asdict(entry)
        if name == "syntavra.usage.verify":
            return self.usage_ledger.verify(require_hmac=bool(arguments.get("require_hmac", False)))
        if name == "syntavra.session.search":
            return {"hits": self.session_retriever.serializable(self.session_retriever.search(
                str(arguments["session_id"]), str(arguments["query"]), limit=int(arguments.get("limit", 12)),
                include_superseded=bool(arguments.get("include_superseded", False)),
            ))}
        if name == "syntavra.session.semantic_context":
            return asdict(self.session_retriever.context_pack(
                str(arguments["session_id"]), str(arguments["query"]),
                budget_bytes=int(arguments.get("budget_bytes", 8192)),
                include_superseded=bool(arguments.get("include_superseded", False)),
            ))
        if name == "syntavra.session.open":
            return asdict(self.sessions.create_session(session_id=arguments.get("session_id"), metadata=arguments.get("metadata") or {}))
        if name == "syntavra.session.append":
            return asdict(self.sessions.append(str(arguments["session_id"]), str(arguments["event_type"]), dict(arguments["payload"])))
        if name == "syntavra.session.context":
            return self.sessions.active_context(str(arguments["session_id"]), token_budget=int(arguments.get("token_budget", 32000)))
        if name == "syntavra.session.compact":
            summary_id = self.sessions.compact(
                str(arguments["session_id"]), leaf_size=int(arguments.get("leaf_size", 32)),
                fanout=int(arguments.get("fanout", 8)), force=bool(arguments.get("force", False)),
            )
            return {"summary_id": summary_id}
        if name == "syntavra.session.expand":
            return self.sessions.expand_summary(str(arguments["summary_id"]))
        if name == "syntavra.session.verify":
            return self.sessions.verify(str(arguments["session_id"]))
        if name == "syntavra.session.checkpoint":
            return asdict(self.sessions.checkpoint(str(arguments["session_id"]), metadata=dict(arguments.get("metadata") or {})))
        if name == "syntavra.session.fork":
            return asdict(self.sessions.fork(str(arguments["session_id"]), metadata=dict(arguments.get("metadata") or {})))
        if name == "syntavra.session.merge":
            return asdict(self.sessions.merge(tuple(arguments["session_ids"]), metadata=dict(arguments.get("metadata") or {})))
        if name == "syntavra.output.govern":
            return OutputGovernor(str(arguments.get("profile", "balanced"))).render(
                dict(arguments["payload"]), contract=str(arguments.get("contract", "generic")),
            )
        if name == "syntavra.fabric.profile":
            return self.fabric.profile(
                str(arguments.get("task", "")), (row["name"] for row in self.tools()),
                requested_profile=str(arguments.get("profile", "auto")),
            )
        if name == "syntavra.fabric.route":
            return asdict(self.fabric.route(
                arguments["command"], network_untrusted=bool(arguments.get("network_untrusted", False)),
                repeated=bool(arguments.get("repeated", False)),
            ))
        if name == "syntavra.fabric.compact":
            return asdict(self.fabric.compact(
                arguments["command"], str(arguments.get("stdout", "")), str(arguments.get("stderr", "")),
                budget_bytes=int(arguments.get("budget_bytes", 4096)),
            ))
        if name == "syntavra.fabric.cache_align":
            return asdict(self.fabric.align_cache(
                list(arguments["messages"]), keep_tail=int(arguments.get("keep_tail", 1)),
            ))
        if name == "syntavra.fabric.platform_plan":
            if bool(arguments.get("all", False)):
                return self.fabric.platforms.all_plans(project=self.project, scope=str(arguments.get("scope", "project")))
            return self.fabric.platforms.plan(
                str(arguments.get("host", self.host)), project=self.project,
                scope=str(arguments.get("scope", "project")),
            )
        if name == "syntavra.fabric.doctor":
            return self.fabric.doctor()
        if name == "syntavra.fabric.insights":
            return self.fabric.insights(since_seconds=arguments.get("since_seconds"))
        raise KeyError(name)

    def handle(self, message: dict[str, Any]) -> dict[str, Any] | None:
        method = message.get("method")
        request_id = message.get("id")
        if method == "notifications/initialized":
            return None
        try:
            if method == "initialize":
                result = {
                    "protocolVersion": message.get("params", {}).get("protocolVersion", "2025-06-18"),
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "syntavra", "version": self.VERSION},
                }
            elif method == "tools/list":
                result = {"tools": self.exposed_tools()}
            elif method == "tools/call":
                params = message.get("params") or {}
                tool_name = str(params.get("name"))
                arguments = params.get("arguments") or {}
                value = self.call_tool(tool_name, arguments)
                value = self.output_pipeline.capture_mcp_result(tool_name, arguments, value)
                result = {"content": [{"type": "text", "text": json.dumps(value, ensure_ascii=False, default=str)}]}
            elif method == "ping":
                result = {}
            else:
                raise KeyError(f"method-not-found:{method}")
            return {"jsonrpc": "2.0", "id": request_id, "result": result}
        except Exception as exc:
            return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32000, "message": f"{type(exc).__name__}: {exc}"}}

    def serve(self, input_stream: TextIO = sys.stdin, output_stream: TextIO = sys.stdout) -> int:
        for line in input_stream:
            if not line.strip():
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError as exc:
                response = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": str(exc)}}
            else:
                response = self.handle(message)
            if response is not None:
                output_stream.write(json.dumps(response, ensure_ascii=False, default=str) + "\n")
                output_stream.flush()
        return 0
