from __future__ import annotations

import os
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .backup import StateBackupManager
from .config_v6 import ConfigManager
from .identity import Authorizer
from .janitor import RuntimeJanitor
from .job_scheduler import DurableJobScheduler
from .mcp_registry import MCPToolRegistry, ToolDefinition
from .observability import Observability
from .plugin_sdk import PluginRegistry
from .policy_rollout import PolicyRolloutManager
from .runtime_pipeline import UnifiedRuntimePipeline
from .schema_registry import SchemaDefinition, SchemaRegistry
from .util import stable_project_id


def _object_schema(properties: dict[str, Any] | None = None, required: tuple[str, ...] = ()) -> dict[str, Any]:
    value: dict[str, Any] = {"type": "object", "properties": properties or {}}
    if required:
        value["required"] = list(required)
    return value


def _build_registry() -> MCPToolRegistry:
    registry = MCPToolRegistry()
    registry.register(ToolDefinition(
        "signalcore.v6.capabilities",
        "Inspect Unified Production Core security, lifecycle and extensibility capabilities",
        _object_schema(),
        lambda server, _: {
            "schema_version": 1,
            "runtime": "0.6.0",
            "canonical_pipeline": True,
            "encrypted_evidence": True,
            "authenticated_streaming_proxy": True,
            "valid_typed_data_envelopes": True,
            "configuration_provenance": True,
            "transactional_migrations": True,
            "structured_observability": True,
            "retention_gc": True,
            "plugin_registry": True,
            "durable_scheduler": True,
        },
    ))
    registry.register(ToolDefinition(
        "signalcore.pipeline.describe", "Describe the canonical V6 request/output pipeline", _object_schema(),
        lambda server, _: server.v6_pipeline.describe(), permissions=("evidence-read",),
    ))
    registry.register(ToolDefinition(
        "signalcore.config.effective", "Read effective canonical configuration and provenance", _object_schema(),
        lambda server, _: server.v6_config.load().to_dict(), permissions=("admin",),
    ))
    registry.register(ToolDefinition(
        "signalcore.evidence.stats", "Inspect encrypted evidence lifecycle statistics", _object_schema(),
        lambda server, _: server.evidence.stats(), permissions=("evidence-read",),
    ))
    registry.register(ToolDefinition(
        "signalcore.evidence.gc", "Plan or apply evidence retention garbage collection",
        _object_schema({
            "ttl_seconds": {"type": "number"}, "max_delete_bytes": {"type": "integer"}, "dry_run": {"type": "boolean"},
        }),
        lambda server, args: server.evidence.gc(
            ttl_seconds=float(args.get("ttl_seconds", 30 * 24 * 60 * 60)),
            max_delete_bytes=int(args.get("max_delete_bytes", 1024 * 1024 * 1024)),
            dry_run=bool(args.get("dry_run", True)),
        ),
        permissions=("evidence-write",), approval_required=True,
    ))
    registry.register(ToolDefinition(
        "signalcore.evidence.rotate_key", "Rotate and re-encrypt local evidence keys", _object_schema({"reencrypt": {"type": "boolean"}}),
        lambda server, args: server.evidence.rotate_key(reencrypt=bool(args.get("reencrypt", True))),
        permissions=("admin", "evidence-write"), approval_required=True, timeout_seconds=3600,
    ))
    registry.register(ToolDefinition(
        "signalcore.observability.metrics", "Read local structured V6 metrics", _object_schema({"format": {"type": "string"}}),
        lambda server, args: server.v6_observability.metrics.prometheus() if args.get("format") == "prometheus" else server.v6_observability.metrics.snapshot(),
        permissions=("admin",),
    ))
    registry.register(ToolDefinition(
        "signalcore.plugins.list", "List permissioned V6 plugins and quarantine state", _object_schema(),
        lambda server, _: server.v6_plugins.records(), permissions=("admin",),
    ))
    registry.register(ToolDefinition(
        "signalcore.scheduler.stats", "Inspect durable job scheduler queue and dead-letter state", _object_schema(),
        lambda server, _: server.v6_scheduler.stats(), permissions=("admin",),
    ))
    registry.register(ToolDefinition(
        "signalcore.backup.create", "Create an encrypted point-in-time state backup",
        _object_schema({"path": {"type": "string"}, "encrypt": {"type": "boolean"}}, ("path",)),
        lambda server, args: asdict(server.v6_backup.create(Path(args["path"]), encrypt=bool(args.get("encrypt", True)))),
        permissions=("admin", "filesystem-write"), approval_required=True, timeout_seconds=3600,
    ))
    registry.register(ToolDefinition(
        "signalcore.backup.verify", "Verify an encrypted state backup without restoring it",
        _object_schema({"path": {"type": "string"}, "encrypted": {"type": "boolean"}}, ("path",)),
        lambda server, args: server.v6_backup.verify(Path(args["path"]), encrypted=bool(args.get("encrypted", True))),
        permissions=("admin", "filesystem-read"), timeout_seconds=3600,
    ))
    return registry


def install() -> None:
    from .mcp_server import MCPServer
    from .provider_v6_extension import install as install_provider
    from .sandbox_v6_extension import install as install_sandbox

    install_provider()
    install_sandbox()
    if getattr(MCPServer, "_signalcore_v6_unified_extension", False):
        return
    registry = _build_registry()
    original_init = MCPServer.__init__
    original_tools = MCPServer.tools
    original_exposed = MCPServer.exposed_tools
    original_call = MCPServer.call_tool

    def extended_init(self: Any, *args: Any, **kwargs: Any) -> None:
        original_init(self, *args, **kwargs)
        project_id = stable_project_id(self.project)
        self.v6_config = ConfigManager(project_root=self.project, state_root=self.state_root)
        self.v6_observability = Observability(self.state_root / "observability")
        self.v6_plugins = PluginRegistry(allowed_permissions={
            "network", "filesystem-read", "filesystem-write", "evidence-read", "evidence-write",
            "session-read", "session-write", "provider-call", "process-execute", "admin",
        })
        self.v6_janitor = RuntimeJanitor()
        self.v6_janitor.register("evidence", lambda dry: self.evidence.gc(
            ttl_seconds=30 * 24 * 60 * 60, dry_run=dry,
        ))
        self.v6_scheduler = DurableJobScheduler(self.state_root / "scheduler-v6.sqlite3")
        rollout_key = os.environ.get("SIGNALCORE_POLICY_SIGNING_KEY", "").encode("utf-8") or None
        self.v6_rollout = PolicyRolloutManager(self.state_root / "policy-rollout-v6.sqlite3", signing_key=rollout_key)
        self.v6_backup = StateBackupManager(self.state_root, project_id=project_id)
        self.v6_schemas = SchemaRegistry()
        self.v6_schemas.register(SchemaDefinition(
            "canonical-request", 1, required=("schema_version", "identity", "payload"),
            properties={"schema_version": int, "identity": dict, "payload": dict},
        ))
        self.v6_pipeline = UnifiedRuntimePipeline(
            evidence=self.evidence,
            config=self.v6_config,
            observability=self.v6_observability,
            authorizer=Authorizer({"agent": ("provider.invoke",), "admin": ("*",)}),
        )
        self.v6_tool_registry = registry

    def extended_tools() -> list[dict[str, Any]]:
        catalog = list(original_tools())
        known = {row["name"] for row in catalog}
        catalog.extend(row for row in registry.tools() if row["name"] not in known)
        return catalog

    def extended_exposed(self: Any) -> list[dict[str, Any]]:
        selected = list(original_exposed(self))
        profile = os.environ.get("SIGNALCORE_MCP_PROFILE", "optimized").strip().casefold() or "optimized"
        if profile not in {"optimized", "full"}:
            return selected
        known = {row["name"] for row in selected}
        selected.extend(row for row in registry.tools() if row["name"] not in known)
        return selected

    def extended_call(self: Any, name: str, arguments: dict[str, Any]) -> Any:
        try:
            return self.v6_tool_registry.invoke(
                self, name, arguments,
                granted_permissions=("*",),
                approved=bool(arguments.get("_approved", False)),
            )
        except KeyError:
            return original_call(self, name, arguments)

    MCPServer.__init__ = extended_init
    MCPServer.tools = staticmethod(extended_tools)
    MCPServer.exposed_tools = extended_exposed
    MCPServer.call_tool = extended_call
    MCPServer._signalcore_v6_unified_extension = True
    MCPServer.V6_TOOL_REGISTRY = registry
