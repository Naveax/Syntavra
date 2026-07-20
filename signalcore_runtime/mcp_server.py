from __future__ import annotations

import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any, TextIO

from .context_governor import evaluate
from .evidence import EvidenceStore
from .process_broker import ProcessBroker
from .status import inspect_runtime
from .structural import StructuralIndex
from .util import stable_project_id


class MCPServer:
    """Small dependency-free MCP-style JSON-RPC stdio server.

    It implements the core initialize/tools/list/tools/call surface required by
    local clients. Unknown methods fail explicitly instead of silently degrading
    to instruction-only behavior.
    """

    def __init__(self, *, project: Path, state_root: Path, skill_root: Path, codex_home: Path, host: str):
        self.project = project.resolve(strict=True)
        self.state_root = state_root
        self.skill_root = skill_root
        self.codex_home = codex_home
        self.host = host
        project_id = stable_project_id(self.project)
        self.evidence = EvidenceStore(state_root / "evidence", project_id=project_id)
        self.broker = ProcessBroker(state_root / "broker", self.evidence)

    @staticmethod
    def tools() -> list[dict[str, Any]]:
        return [
            {"name": "signalcore.status", "description": "Inspect runtime health", "inputSchema": {"type": "object"}},
            {
                "name": "signalcore.process.submit",
                "description": "Submit a durable zero-poll command",
                "inputSchema": {
                    "type": "object",
                    "required": ["argv"],
                    "properties": {
                        "argv": {"type": "array", "items": {"type": "string"}},
                        "timeout": {"type": "number"},
                        "repository_tree": {"type": "string"},
                    },
                },
            },
            {
                "name": "signalcore.process.completions",
                "description": "Read durable completion events after a cursor",
                "inputSchema": {
                    "type": "object",
                    "properties": {"after": {"type": "integer"}, "limit": {"type": "integer"}},
                },
            },
            {
                "name": "signalcore.inspect.impact",
                "description": "Inspect transitive reverse-call impact",
                "inputSchema": {
                    "type": "object",
                    "required": ["query"],
                    "properties": {"query": {"type": "string"}, "max_depth": {"type": "integer"}},
                },
            },
            {
                "name": "signalcore.context.evaluate",
                "description": "Evaluate context pressure",
                "inputSchema": {
                    "type": "object",
                    "required": ["used", "window"],
                    "properties": {"used": {"type": "integer"}, "window": {"type": "integer"}},
                },
            },
        ]

    def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        if name == "signalcore.status":
            return asdict(inspect_runtime(
                project_root=self.project,
                skill_root=self.skill_root,
                state_root=self.state_root,
                codex_home=self.codex_home,
                host=self.host,
            ))
        if name == "signalcore.process.submit":
            job = self.broker.submit(
                tuple(arguments["argv"]),
                cwd=self.project,
                timeout=float(arguments.get("timeout", 1200)),
                repository_tree=str(arguments.get("repository_tree", "unknown")),
            )
            return {"event": "JOB_ACCEPTED", "job": asdict(job), "model_polling_calls": 0}
        if name == "signalcore.process.completions":
            result = self.broker.drain_completions(
                after=int(arguments.get("after", 0)),
                limit=int(arguments.get("limit", 100)),
            )
            return {"cursor": result["cursor"], "events": [asdict(event) for event in result["events"]]}
        if name == "signalcore.inspect.impact":
            index = StructuralIndex(
                self.state_root / "structural.sqlite3",
                repository_root=self.project,
                repository_id=stable_project_id(self.project),
            )
            index.index()
            return index.inspect_impact(str(arguments["query"]), max_depth=int(arguments.get("max_depth", 3)))
        if name == "signalcore.context.evaluate":
            return asdict(evaluate(int(arguments["used"]), int(arguments["window"])))
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
                    "serverInfo": {"name": "signalcore", "version": "0.2.0"},
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
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32000, "message": f"{type(exc).__name__}: {exc}"},
            }

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
