from __future__ import annotations

import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any, TextIO

from .compression import ContentRouter, ReversibleContentStore
from .context_governor import evaluate
from .evidence import EvidenceStore
from .host_adapters import detect_hosts
from .output_governor import OutputGovernor
from .process_broker import ProcessBroker
from .sandbox import SandboxManager, SandboxPolicy
from .session_runtime import SessionRuntime
from .status import inspect_runtime
from .structural import StructuralIndex
from .util import stable_project_id


class MCPServer:
    """Dependency-free MCP JSON-RPC server for the complete v0.3 runtime plane."""

    VERSION = "0.3.0"

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

    @staticmethod
    def tools() -> list[dict[str, Any]]:
        def tool(name: str, description: str, properties: dict[str, Any] | None = None, required: list[str] | None = None) -> dict[str, Any]:
            schema: dict[str, Any] = {"type": "object", "properties": properties or {}}
            if required:
                schema["required"] = required
            return {"name": name, "description": description, "inputSchema": schema}

        return [
            tool("signalcore.status", "Inspect truthful runtime health"),
            tool("signalcore.host.detect", "Detect installed coding-agent hosts"),
            tool(
                "signalcore.process.submit", "Submit a durable zero-poll command",
                {"argv": {"type": "array", "items": {"type": "string"}}, "timeout": {"type": "number"}, "repository_tree": {"type": "string"}}, ["argv"],
            ),
            tool(
                "signalcore.process.completions", "Read durable completion events after a cursor",
                {"after": {"type": "integer"}, "limit": {"type": "integer"}},
            ),
            tool(
                "signalcore.inspect.impact", "Inspect transitive multi-language impact",
                {"query": {"type": "string"}, "max_depth": {"type": "integer"}}, ["query"],
            ),
            tool(
                "signalcore.inspect.map", "Build a query-conditioned repository map",
                {"query": {"type": "string"}, "token_budget": {"type": "integer"}, "max_depth": {"type": "integer"}}, ["query"],
            ),
            tool(
                "signalcore.context.evaluate", "Evaluate context pressure",
                {"used": {"type": "integer"}, "window": {"type": "integer"}, "churn": {"type": "number"}, "evidence_pressure": {"type": "number"}}, ["used", "window"],
            ),
            tool(
                "signalcore.compress", "Reversibly compress generic content",
                {"text": {"type": "string"}, "hint": {"type": "string"}, "path": {"type": "string"}, "budget_bytes": {"type": "integer"}}, ["text"],
            ),
            tool(
                "signalcore.expand", "Restore a compression or one exact chunk",
                {"compression_id": {"type": "string"}, "chunk": {"type": "integer"}}, ["compression_id"],
            ),
            tool(
                "signalcore.sandbox.plan", "Plan a fail-closed sandbox execution",
                {"argv": {"type": "array", "items": {"type": "string"}}, "backend": {"type": "string"}, "network": {"type": "string"}, "strict": {"type": "boolean"}}, ["argv"],
            ),
            tool(
                "signalcore.sandbox.execute", "Execute a command under sandbox policy",
                {"argv": {"type": "array", "items": {"type": "string"}}, "backend": {"type": "string"}, "network": {"type": "string"}, "strict": {"type": "boolean"}, "timeout": {"type": "number"}}, ["argv"],
            ),
            tool(
                "signalcore.session.open", "Open an immutable long-session runtime",
                {"session_id": {"type": "string"}, "metadata": {"type": "object"}},
            ),
            tool(
                "signalcore.session.append", "Append an immutable session event",
                {"session_id": {"type": "string"}, "event_type": {"type": "string"}, "payload": {"type": "object"}}, ["session_id", "event_type", "payload"],
            ),
            tool(
                "signalcore.session.context", "Assemble bounded active context",
                {"session_id": {"type": "string"}, "token_budget": {"type": "integer"}}, ["session_id"],
            ),
            tool(
                "signalcore.output.govern", "Render a correctness-preserving bounded answer",
                {"payload": {"type": "object"}, "profile": {"type": "string"}, "contract": {"type": "string"}}, ["payload"],
            ),
        ]

    def _index(self) -> StructuralIndex:
        index = StructuralIndex(
            self.state_root / "structural.sqlite3",
            repository_root=self.project,
            repository_id=stable_project_id(self.project),
        )
        index.index()
        return index

    def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        if name == "signalcore.status":
            return asdict(inspect_runtime(
                project_root=self.project,
                skill_root=self.skill_root,
                state_root=self.state_root,
                codex_home=self.codex_home,
                host=self.host,
            ))
        if name == "signalcore.host.detect":
            return {"hosts": detect_hosts(self.project)}
        if name == "signalcore.process.submit":
            job = self.broker.submit(
                tuple(arguments["argv"]),
                cwd=self.project,
                timeout=float(arguments.get("timeout", 1200)),
                repository_tree=str(arguments.get("repository_tree", "unknown")),
            )
            return {"event": "JOB_ACCEPTED", "job": asdict(job), "model_polling_calls": 0}
        if name == "signalcore.process.completions":
            result = self.broker.drain_completions(after=int(arguments.get("after", 0)), limit=int(arguments.get("limit", 100)))
            return {"cursor": result["cursor"], "events": [asdict(event) for event in result["events"]]}
        if name == "signalcore.inspect.impact":
            return self._index().inspect_impact(str(arguments["query"]), max_depth=int(arguments.get("max_depth", 4)))
        if name == "signalcore.inspect.map":
            return self._index().repository_map(
                str(arguments["query"]),
                token_budget=int(arguments.get("token_budget", 2000)),
                max_depth=int(arguments.get("max_depth", 4)),
            )
        if name == "signalcore.context.evaluate":
            return asdict(evaluate(
                int(arguments["used"]), int(arguments["window"]),
                churn=float(arguments.get("churn", 0)), evidence_pressure=float(arguments.get("evidence_pressure", 0)),
            ))
        if name == "signalcore.compress":
            return asdict(self.compressor.compress(
                str(arguments["text"]),
                hint=str(arguments.get("hint", "")),
                path=str(arguments.get("path", "")),
                budget_bytes=int(arguments.get("budget_bytes", 8192)),
            ))
        if name == "signalcore.expand":
            data = self.compression_store.restore(str(arguments["compression_id"]), chunk=arguments.get("chunk"))
            return {"bytes": len(data), "text": data.decode("utf-8", errors="replace")}
        if name in {"signalcore.sandbox.plan", "signalcore.sandbox.execute"}:
            policy = SandboxPolicy(
                backend=str(arguments.get("backend", "auto")),
                network=str(arguments.get("network", "none")),
                strict=bool(arguments.get("strict", True)),
                timeout_seconds=float(arguments.get("timeout", 1200)),
            )
            if name.endswith("plan"):
                return asdict(self.sandbox.plan(tuple(arguments["argv"]), policy=policy))
            return asdict(self.sandbox.execute(tuple(arguments["argv"]), policy=policy))
        if name == "signalcore.session.open":
            return asdict(self.sessions.create_session(session_id=arguments.get("session_id"), metadata=arguments.get("metadata") or {}))
        if name == "signalcore.session.append":
            return asdict(self.sessions.append(str(arguments["session_id"]), str(arguments["event_type"]), dict(arguments["payload"])))
        if name == "signalcore.session.context":
            return self.sessions.active_context(str(arguments["session_id"]), token_budget=int(arguments.get("token_budget", 32000)))
        if name == "signalcore.output.govern":
            return OutputGovernor(str(arguments.get("profile", "balanced"))).render(
                dict(arguments["payload"]), contract=str(arguments.get("contract", "generic")),
            )
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
                    "serverInfo": {"name": "signalcore", "version": self.VERSION},
                }
            elif method == "tools/list":
                result = {"tools": self.tools()}
            elif method == "tools/call":
                params = message.get("params") or {}
                value = self.call_tool(str(params.get("name")), params.get("arguments") or {})
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
