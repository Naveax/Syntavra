from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Iterable


@dataclass(frozen=True)
class IntegrationSpec:
    integration_id: str
    family: str
    transport: str
    install_mode: str
    capabilities: tuple[str, ...]
    certification: str = "internal-contract"
    live_receipt_required: bool = True


PROVIDERS: tuple[IntegrationSpec, ...] = tuple(
    IntegrationSpec(name, "provider", transport, "proxy-or-sdk", capabilities)
    for name, transport, capabilities in (
        ("openai", "responses+chat", ("stream", "usage", "cache", "tools")),
        ("anthropic", "messages", ("stream", "usage", "cache", "tools")),
        ("gemini", "generate-content", ("stream", "usage", "tools")),
        ("aws-bedrock", "converse", ("stream", "usage", "tools")),
        ("azure-openai", "openai-compatible", ("stream", "usage", "cache", "tools")),
        ("vertex-ai", "generate-content", ("stream", "usage", "tools")),
        ("openrouter", "openai-compatible", ("stream", "usage", "tools")),
        ("mistral", "openai-compatible", ("stream", "usage", "tools")),
        ("groq", "openai-compatible", ("stream", "usage", "tools")),
        ("cohere", "chat", ("stream", "usage", "tools")),
    )
)

FRAMEWORKS: tuple[IntegrationSpec, ...] = tuple(
    IntegrationSpec(name, "framework", transport, install_mode, capabilities)
    for name, transport, install_mode, capabilities in (
        ("openai-python", "sdk", "python-wrapper", ("sync", "async", "stream")),
        ("openai-node", "sdk", "typescript-wrapper", ("async", "stream")),
        ("anthropic-python", "sdk", "python-wrapper", ("sync", "async", "stream")),
        ("anthropic-node", "sdk", "typescript-wrapper", ("async", "stream")),
        ("google-genai", "sdk", "python-wrapper", ("sync", "async", "stream")),
        ("vercel-ai-sdk", "middleware", "typescript-middleware", ("stream", "tools")),
        ("litellm", "callback", "python-callback", ("providers", "usage", "cache")),
        ("langchain", "callback", "python-callback", ("events", "usage", "tools")),
        ("langgraph", "middleware", "python-middleware", ("state", "events", "tools")),
        ("agno", "adapter", "python-adapter", ("agents", "usage", "tools")),
        ("strands", "adapter", "python-adapter", ("agents", "usage", "tools")),
        ("asgi", "middleware", "python-middleware", ("http", "stream")),
        ("openclaw", "context-engine", "plugin", ("memory", "tools", "stream")),
        ("mcp", "stdio+http", "native", ("tools", "resources", "prompts")),
        ("openai-compatible", "http-proxy", "zero-code", ("providers", "stream", "usage")),
    )
)

HOSTS: tuple[IntegrationSpec, ...] = tuple(
    IntegrationSpec(name, "host", transport, install_mode, capabilities)
    for name, transport, install_mode, capabilities in (
        ("claude-code", "plugin+hooks", "auto", ("session", "pre-tool", "post-tool", "compact")),
        ("codex", "skill+mcp", "auto", ("session", "tools", "mcp")),
        ("gemini-cli", "extension+mcp", "auto", ("session", "tools", "mcp")),
        ("vscode-copilot", "instructions+mcp", "auto", ("instructions", "tools", "mcp")),
        ("jetbrains-copilot", "instructions+mcp", "auto", ("instructions", "tools", "mcp")),
        ("cursor", "rules+mcp", "auto", ("rules", "tools", "mcp")),
        ("windsurf", "rules+mcp", "auto", ("rules", "tools", "mcp")),
        ("opencode", "config+mcp", "auto", ("session", "tools", "mcp")),
        ("cline", "rules+mcp", "auto", ("rules", "tools", "mcp")),
        ("roo-code", "rules+mcp", "auto", ("rules", "tools", "mcp")),
        ("qwen-code", "agents+mcp", "auto", ("session", "tools", "mcp")),
        ("kiro", "steering+mcp", "auto", ("rules", "tools", "mcp")),
        ("zed", "rules+mcp", "auto", ("rules", "tools", "mcp")),
        ("pi", "extension", "auto", ("session", "tools")),
        ("omp", "plugin", "auto", ("session", "tools")),
        ("openclaw", "plugin", "auto", ("session", "memory", "tools")),
        ("aider", "env+wrapper", "auto", ("session", "repository")),
        ("continue", "rules+mcp", "auto", ("rules", "tools", "mcp")),
    )
)


class IntegrationMatrix:
    provider_target = 10
    framework_target = 15
    host_target = 18
    automatic_host_target = 14

    @staticmethod
    def all() -> tuple[IntegrationSpec, ...]:
        return PROVIDERS + FRAMEWORKS + HOSTS

    @staticmethod
    def by_id(integration_id: str) -> IntegrationSpec:
        for item in IntegrationMatrix.all():
            if item.integration_id == integration_id:
                return item
        raise KeyError(integration_id)

    @staticmethod
    def validate() -> dict[str, Any]:
        values = IntegrationMatrix.all()
        keys = [(item.family, item.integration_id) for item in values]
        automatic_hosts = sum(item.install_mode == "auto" for item in HOSTS)
        reasons: list[str] = []
        if len(keys) != len(set(keys)):
            reasons.append("duplicate-integration")
        if len(PROVIDERS) < IntegrationMatrix.provider_target:
            reasons.append("provider-target-missed")
        if len(FRAMEWORKS) < IntegrationMatrix.framework_target:
            reasons.append("framework-target-missed")
        if len(HOSTS) < IntegrationMatrix.host_target:
            reasons.append("host-target-missed")
        if automatic_hosts < IntegrationMatrix.automatic_host_target:
            reasons.append("automatic-host-target-missed")
        return {
            "ok": not reasons,
            "reasons": reasons,
            "providers": len(PROVIDERS),
            "frameworks": len(FRAMEWORKS),
            "hosts": len(HOSTS),
            "automatic_hosts": automatic_hosts,
            "live_certification_boundary": "external receipts are required before VERIFIED_LIVE",
        }

    @staticmethod
    def records(family: str | None = None) -> list[dict[str, Any]]:
        return [asdict(item) for item in IntegrationMatrix.all() if family is None or item.family == family]

    @staticmethod
    def certification_manifest(receipts: Iterable[dict[str, Any]] = ()) -> dict[str, Any]:
        receipt_ids = {str(item.get("integration_id")) for item in receipts if item.get("passed") is True}
        rows = []
        for item in IntegrationMatrix.all():
            row = asdict(item)
            row["live_certified"] = item.integration_id in receipt_ids
            rows.append(row)
        return {"matrix": IntegrationMatrix.validate(), "integrations": rows}
