from __future__ import annotations

import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any, TextIO

from .adaptive_provider_router import AdaptiveProviderRouter
from .agent_config_auditor import AgentConfigAuditor
from .code_intelligence import CodeIntelligenceIndex
from .command_rewriter import CommandRewriteEngine
from .dashboard import LocalDashboard
from .memory_intelligence import MemoryIntelligenceStore
from .notifications import NotificationFeed
from .optimization_modes import OptimizationModeStore, SavingsLedger, render_statusline
from .prompt_cache_optimizer import PromptCacheOptimizer
from .repository_watcher import RepositoryWatcher
from .secret_redaction import SecretRedactor
from .subtask_router import AutomaticSubtaskDelegator
from .transcript_miner import TranscriptOpportunityMiner
from .wire_format import LosslessWireCodec
from .competitive_fabric import CompetitiveContextFabric, StructuralNavigator
from .compression import ContentRouter, ReversibleContentStore
from .context_governor import evaluate
from .context_pack import TaskContextAssembler
from .evidence import EvidenceStore
from .host_adapters import detect_hosts
from .host_output_pipeline import HostOutputPipeline
from .mcp_application import MCPApplicationPipeline
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
    _syntavra_native_mcp_pipeline = True

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
        self.application = MCPApplicationPipeline(self.state_root)
        self.mode_store = OptimizationModeStore(self.state_root)
        self.savings = SavingsLedger(self.state_root)
        self.rewriter = CommandRewriteEngine()
        self.cache_optimizer = PromptCacheOptimizer(self.state_root)
        self.notification_feed = NotificationFeed(self.state_root)
        self.memory_intelligence = MemoryIntelligenceStore(self.state_root / "memory-intelligence.sqlite3", notification_feed=self.notification_feed)
        self.wire_codec = LosslessWireCodec()
        self.secret_redactor = SecretRedactor()

    @property
    def product_mcp_policy(self):
        """Compatibility view over the native application policy."""
        self.application.refresh()
        return self.application.policy

    @property
    def product_mcp_analytics(self):
        return self.application.analytics

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
                "syntavra.context.pack", "Build minimum exact task context",
                {
                    "query": {"type": "string"},
                    "changed_paths": {"type": "array", "items": {"type": "string"}},
                    "token_budget": {"type": "integer"},
                    "max_depth": {"type": "integer"},
                },
                ["query"],
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
                "syntavra.usage.attribution.record", "Record source-level token attribution",
                {
                    "task_id": {"type": "string"}, "arm_id": {"type": "string"},
                    "repetition": {"type": "integer"}, "session_id": {"type": "string"},
                    "provider": {"type": "string"}, "model": {"type": "string"},
                    "request_id_hash": {"type": "string"}, "provider_receipt_hash": {"type": "string"},
                    "sources": {"type": "object"}, "confidence": {"type": "object"},
                    "baseline_tokens": {"type": "integer"}, "baseline_confidence": {"type": "string"},
                    "metadata": {"type": "object"},
                },
                ["task_id", "arm_id", "repetition", "session_id", "provider", "model",
                 "request_id_hash", "provider_receipt_hash", "sources", "confidence"],
            ),
            tool(
                "syntavra.usage.attribution.summary", "Show token savings by source and confidence",
                {"session_id": {"type": "string"}},
            ),
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
            tool("syntavra.mode", "Get or switch optimization mode", {"mode": {"type": "string"}}),
            tool("syntavra.statusline", "Render live savings statusline", {"verbose": {"type": "boolean"}}),
            tool("syntavra.command.rewrite", "Fail-closed pre-tool command rewrite", {"command": command_schema}, ["command"]),
            tool("syntavra.transcript.mine", "Find missed token-saving opportunities", {"source": {}}, ["source"]),
            tool("syntavra.cache.plan", "Plan stable prompt-cache prefix and expiry", {"messages": {"type": "array", "items": {"type": "object"}}, "provider": {"type": "string"}, "model": {"type": "string"}, "ttl": {"type": "integer"}}, ["messages", "provider", "model"]),
            tool("syntavra.cache.health", "Show cache expiry and amortization health"),
            tool("syntavra.config.audit", "Audit agent configuration token waste"),
            tool("syntavra.secret.redact", "Redact secrets before MCP response", {"value": {}}, ["value"]),
            tool("syntavra.wire", "Lossless compact MCP response wire codec", {"action": {"type": "string"}, "value": {}, "minimum_savings": {"type": "number"}}, ["action", "value"]),
            tool("syntavra.code.intelligence", "Query code graph analytics", {"action": {"type": "string"}, "query": {"type": "string"}, "paths": {"type": "array", "items": {"type": "string"}}, "target_name": {"type": "string"}}, ["action"]),
            tool("syntavra.memory.intelligence", "Extract, rank, search, backfill or export memory", {"action": {"type": "string"}, "text": {"type": "string"}, "query": {"type": "string"}, "path": {"type": "string"}, "limit": {"type": "integer"}}, ["action"]),
            tool("syntavra.provider.route", "Quota, rate-limit and complexity-aware provider routing", {"task": {"type": "string"}, "candidates": {"type": "array", "items": {"type": "object"}}, "changed_files": {"type": "integer"}, "token_estimate": {"type": "integer"}}, ["task", "candidates"]),
            tool("syntavra.subtask.plan", "Build specialized short-handoff subtasks", {"objective": {"type": "string"}, "context_paths": {"type": "array", "items": {"type": "string"}}, "max_tasks": {"type": "integer"}}, ["objective"]),
            tool("syntavra.repository.watch", "Poll changes and incrementally rebuild code index", {"iterations": {"type": "integer"}, "interval": {"type": "number"}}),
            tool("syntavra.dashboard.snapshot", "Read local dashboard state"),
        ]

    def exposed_tools(self) -> list[dict[str, Any]]:
        return self.application.list_tools(self.tools())

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
        if name == "syntavra.mode":
            return self.mode_store.set(str(arguments["mode"]), source="mcp") if arguments.get("mode") else self.mode_store.manifest()
        if name == "syntavra.statusline":
            return {"statusline": render_statusline(self.state_root, compact=not bool(arguments.get("verbose", False))), "savings": self.savings.summary()}
        if name == "syntavra.command.rewrite":
            return self.rewriter.rewrite(arguments["command"]).to_dict()
        if name == "syntavra.transcript.mine":
            return TranscriptOpportunityMiner().analyze(arguments["source"])
        if name == "syntavra.cache.plan":
            return asdict(self.cache_optimizer.plan(list(arguments["messages"]), provider=str(arguments["provider"]), model=str(arguments["model"]), ttl_seconds=arguments.get("ttl")))
        if name == "syntavra.cache.health":
            return self.cache_optimizer.health()
        if name == "syntavra.config.audit":
            return AgentConfigAuditor(self.project).audit()
        if name == "syntavra.secret.redact":
            value, receipt = self.secret_redactor.redact(arguments["value"]); return {"value": value, "receipt": receipt}
        if name == "syntavra.wire":
            return self.wire_codec.encode(arguments["value"], min_savings_ratio=float(arguments.get("minimum_savings", .08))) if str(arguments["action"]) == "encode" else self.wire_codec.decode(arguments["value"])
        if name == "syntavra.code.intelligence":
            index=CodeIntelligenceIndex(self.project); index.build(); action=str(arguments["action"]); query=str(arguments.get("query", "")); paths=list(arguments.get("paths") or [])
            methods={"report":index.report,"dead":index.dead_code,"untested":index.untested_symbols,"pagerank":index.pagerank,"hotspots":index.hotspots,"cycles":index.cycles,"coupling":index.coupling,"boundaries":index.module_boundaries,"duplicates":index.duplicates,"anti-patterns":index.anti_patterns}
            if action in methods: return methods[action]()
            if action == "call": return index.call_hierarchy(query)
            if action == "class": return index.class_hierarchy(query)
            if action == "provenance": return index.provenance(query)
            if action == "risk": return index.pr_risk(paths)
            if action == "signal": return index.signal_chain(query)
            if action == "delete": return index.delete_safe(query)
            if action == "refactor": return index.refactor_plan(query, target_name=str(arguments.get("target_name", "")))
            if action == "cross-repo": return index.cross_repo_contracts([Path(row) for row in paths])
            raise ValueError(f"unknown code-intelligence action: {action}")
        if name == "syntavra.memory.intelligence":
            action=str(arguments["action"]); limit=int(arguments.get("limit", 20))
            if action == "add": return asdict(self.memory_intelligence.add(str(arguments["text"])))
            if action == "extract": return {"observations":[asdict(row) for row in self.memory_intelligence.extract(str(arguments["text"]))]}
            if action == "search": return {"results":self.memory_intelligence.search(str(arguments["query"]),limit=limit)}
            if action == "rank": return {"results":self.memory_intelligence.ranked(limit=limit)}
            if action == "backfill": return self.memory_intelligence.backfill_embeddings(limit=limit)
            if action == "export": return self.memory_intelligence.export_jsonl(Path(str(arguments["path"])))
            if action == "stats": return self.memory_intelligence.stats()
            raise ValueError(f"unknown memory action: {action}")
        if name == "syntavra.provider.route":
            router=AdaptiveProviderRouter.from_mappings(list(arguments["candidates"])); return asdict(router.route(str(arguments["task"]),changed_files=int(arguments.get("changed_files",0)),token_estimate=int(arguments.get("token_estimate",0))))
        if name == "syntavra.subtask.plan":
            return asdict(AutomaticSubtaskDelegator().plan(str(arguments["objective"]),context_paths=list(arguments.get("context_paths") or []),max_tasks=int(arguments.get("max_tasks",8))))
        if name == "syntavra.repository.watch":
            watcher=RepositoryWatcher(self.project,self.state_root); rows=watcher.watch(iterations=int(arguments.get("iterations",1)),interval_seconds=float(arguments.get("interval",1)),callback=lambda changes:{"index":CodeIntelligenceIndex(self.project).build(),"changed":list(changes.changed)}); return {"changes":[asdict(row) for row in rows],"status":watcher.status()}
        if name == "syntavra.dashboard.snapshot":
            return LocalDashboard(project=self.project,state_root=self.state_root).snapshot()
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
        if name == "syntavra.context.pack":
            assembler = TaskContextAssembler(self._index(), self.navigator)
            return assembler.assemble(
                str(arguments["query"]),
                changed_paths=tuple(arguments.get("changed_paths") or ()),
                token_budget=int(arguments.get("token_budget", 8_000)),
                max_depth=int(arguments.get("max_depth", 4)),
            ).to_dict()
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
        if name == "syntavra.usage.attribution.record":
            return self.usage_ledger.record_attribution(
                task_id=str(arguments["task_id"]), arm_id=str(arguments["arm_id"]),
                repetition=int(arguments["repetition"]), session_id=str(arguments["session_id"]),
                provider=str(arguments["provider"]), model=str(arguments["model"]),
                request_id_hash=str(arguments["request_id_hash"]),
                provider_receipt_hash=str(arguments["provider_receipt_hash"]),
                sources=dict(arguments["sources"]), confidence=dict(arguments["confidence"]),
                baseline_tokens=(int(arguments["baseline_tokens"]) if arguments.get("baseline_tokens") is not None else None),
                baseline_confidence=str(arguments.get("baseline_confidence", "UNKNOWN")),
                metadata=dict(arguments.get("metadata") or {}),
            ).to_dict()
        if name == "syntavra.usage.attribution.summary":
            return self.usage_ledger.attribution_summary(session_id=arguments.get("session_id"))
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
            stdout = str(arguments.get("stdout", ""))
            stderr = str(arguments.get("stderr", ""))
            command_value = arguments["command"]
            command_text = command_value if isinstance(command_value, str) else " ".join(str(item) for item in command_value)
            artifact = self.externalizer.externalize(ToolPayload(
                command=command_text, stdout=stdout, stderr=stderr, tool_name="syntavra.fabric.compact",
                path="", scope_key=str(arguments.get("scope_key", "default")), metadata={"source": "pre-compaction"},
            ))
            result = asdict(self.fabric.compact(
                command_value, stdout, stderr, budget_bytes=int(arguments.get("budget_bytes", 4096)),
            ))
            result["exact_artifact_id"] = artifact.artifact_id
            result["exact_output_bytes"] = artifact.original_bytes
            result["recovery"] = {"tool": "syntavra.output.reveal", "artifact_id": artifact.artifact_id}
            return result
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
        if method == "initialize":
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "protocolVersion": message.get("params", {}).get("protocolVersion", "2025-06-18"),
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "syntavra", "version": self.VERSION},
                    "instructions": "Token/context optimization with exact recovery and fail-closed tool routing.",
                },
            }
        if method == "tools/list":
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "tools": self.exposed_tools(),
                    "_meta": {"syntavra": self.application.manifest()},
                },
            }
        if method == "tools/call":
            return self.application.call(self, message)
        if method == "ping":
            return {"jsonrpc": "2.0", "id": request_id, "result": {}}
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": -32601, "message": "Method not found"},
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
