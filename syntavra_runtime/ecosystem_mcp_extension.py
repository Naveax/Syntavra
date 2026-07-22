from __future__ import annotations

import os
from dataclasses import asdict
from typing import Any

from .framework_adapters import framework_capabilities
from .host_adapters import coverage_report
from .long_session_planner import ContextPlanPolicy, LongSessionPlanner
from .output_governor import PROFILES
from .real_task_corpus import RealTaskCorpus


_ECOSYSTEM_NAMES = frozenset({
    "syntavra.ecosystem.capabilities",
    "syntavra.session.plan",
    "syntavra.session.stress",
    "syntavra.corpus.validate",
    "syntavra.corpus.manifest",
})


def _tool(
    name: str,
    description: str,
    properties: dict[str, Any] | None = None,
    required: list[str] | None = None,
) -> dict[str, Any]:
    schema: dict[str, Any] = {"type": "object", "properties": properties or {}}
    if required:
        schema["required"] = required
    return {"name": name, "description": description, "inputSchema": schema}


def ecosystem_tools() -> list[dict[str, Any]]:
    policy = {
        "token_budget": {"type": "integer"},
        "recent_events": {"type": "integer"},
        "chars_per_token": {"type": "number"},
        "summary_preview_chars": {"type": "integer"},
        "event_preview_chars": {"type": "integer"},
        "max_candidates": {"type": "integer"},
        "forced_event_types": {"type": "array", "items": {"type": "string"}},
    }
    return [
        _tool(
            "syntavra.ecosystem.capabilities",
            "Inspect SDK, framework, host, output, and benchmark product surfaces",
        ),
        _tool(
            "syntavra.session.plan",
            "Build query-aware bounded context with exact immutable references",
            {
                "session_id": {"type": "string"},
                "query": {"type": "string"},
                "policy": {"type": "object", "properties": policy},
            },
            ["session_id", "query"],
        ),
        _tool(
            "syntavra.session.stress",
            "Stress-test query-aware planning while verifying exact session history",
            {
                "session_id": {"type": "string"},
                "queries": {"type": "array", "items": {"type": "string"}},
                "policy": {"type": "object", "properties": policy},
            },
            ["session_id", "queries"],
        ),
        _tool(
            "syntavra.corpus.validate",
            "Validate a strict real-task corpus and identical-arm parity",
            {
                "tasks": {"type": "array", "items": {"type": "object"}},
                "arms": {"type": "array", "items": {"type": "object"}},
                "minimum_tasks": {"type": "integer"},
                "minimum_arms": {"type": "integer"},
                "minimum_repetitions": {"type": "integer"},
            },
            ["tasks", "arms"],
        ),
        _tool(
            "syntavra.corpus.manifest",
            "Create a deterministic randomized paired-run manifest",
            {
                "tasks": {"type": "array", "items": {"type": "object"}},
                "arms": {"type": "array", "items": {"type": "object"}},
                "repetitions": {"type": "integer"},
                "cache_modes": {"type": "array", "items": {"type": "string"}},
                "seed": {"type": "integer"},
            },
            ["tasks", "arms"],
        ),
    ]


def _policy(value: Any) -> ContextPlanPolicy:
    if not value:
        return ContextPlanPolicy()
    data = dict(value)
    if "forced_event_types" in data:
        data["forced_event_types"] = tuple(str(item) for item in data["forced_event_types"])
    return ContextPlanPolicy(**data)


def install() -> None:
    """Extend MCPServer with productization and evidence-readiness surfaces."""

    from .mcp_server import MCPServer

    if getattr(MCPServer, "_syntavra_ecosystem_extension_v4", False):
        return

    original_init = MCPServer.__init__
    original_tools = MCPServer.tools
    original_exposed_tools = MCPServer.exposed_tools
    original_call_tool = MCPServer.call_tool

    def extended_init(self: Any, *args: Any, **kwargs: Any) -> None:
        original_init(self, *args, **kwargs)
        self.long_session_planner = LongSessionPlanner(self.sessions)

    def extended_tools() -> list[dict[str, Any]]:
        catalog = list(original_tools())
        known = {row["name"] for row in catalog}
        catalog.extend(row for row in ecosystem_tools() if row["name"] not in known)
        return catalog

    def extended_exposed_tools(self: Any) -> list[dict[str, Any]]:
        selected = list(original_exposed_tools(self))
        profile = os.environ.get("SYNTAVRA_MCP_PROFILE", "optimized").strip().casefold() or "optimized"
        if profile not in {"optimized", "full"}:
            return selected
        known = {row["name"] for row in selected}
        selected.extend(row for row in ecosystem_tools() if row["name"] not in known)
        return selected

    def extended_call_tool(self: Any, name: str, arguments: dict[str, Any]) -> Any:
        if name == "syntavra.ecosystem.capabilities":
            return {
                "frameworks": framework_capabilities(),
                "hosts": coverage_report(),
                "output_profiles": {key: asdict(value) for key, value in PROFILES.items()},
                "long_session": {
                    "query_aware_planning": True,
                    "exact_references": True,
                    "temporal_supersession": True,
                    "stress_report": True,
                },
                "benchmark": {
                    "strict_real_task_corpus": True,
                    "executable_arms": True,
                    "deterministic_paired_schedule": True,
                    "superiority_claim_requires_completed_external_evidence": True,
                },
            }
        if name == "syntavra.session.plan":
            return self.long_session_planner.plan(
                str(arguments["session_id"]),
                str(arguments["query"]),
                policy=_policy(arguments.get("policy")),
            )
        if name == "syntavra.session.stress":
            return self.long_session_planner.stress_report(
                str(arguments["session_id"]),
                tuple(str(item) for item in arguments["queries"]),
                policy=_policy(arguments.get("policy")),
            )
        if name in {"syntavra.corpus.validate", "syntavra.corpus.manifest"}:
            corpus = RealTaskCorpus.from_values(arguments["tasks"], arguments["arms"])
            if name.endswith("validate"):
                return corpus.validate(
                    minimum_tasks=int(arguments.get("minimum_tasks", 50)),
                    minimum_arms=int(arguments.get("minimum_arms", 3)),
                    minimum_repetitions=int(arguments.get("minimum_repetitions", 30)),
                )
            return corpus.manifest(
                repetitions=int(arguments.get("repetitions", 30)),
                cache_modes=arguments.get("cache_modes"),
                seed=int(arguments.get("seed", 1337)),
            )
        return original_call_tool(self, name, arguments)

    MCPServer.__init__ = extended_init
    MCPServer.tools = staticmethod(extended_tools)
    MCPServer.exposed_tools = extended_exposed_tools
    MCPServer.call_tool = extended_call_tool
    MCPServer._syntavra_ecosystem_extension_v4 = True
    MCPServer.ECOSYSTEM_TOOLS = _ECOSYSTEM_NAMES
