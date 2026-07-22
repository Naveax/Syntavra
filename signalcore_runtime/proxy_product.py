from __future__ import annotations

import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .integration_matrix import PROVIDERS
from .release_identity import CHANNEL, VERSION
from .service_manager import ProviderProxyServiceManager, ServiceSpec


@dataclass(frozen=True)
class ProxyPreset:
    provider: str
    gateway_provider: str
    protocol: str
    default_upstream: str
    credential_env: str
    credential_header: str
    credential_prefix: str
    auth_strategy: str
    install_mode: str
    zero_code_compatible: bool
    live_certification: str = "external-receipt-required"


PRESETS: tuple[ProxyPreset, ...] = (
    ProxyPreset("openai", "openai", "responses+chat", "https://api.openai.com", "OPENAI_API_KEY", "Authorization", "Bearer ", "api-key", "native-proxy", True),
    ProxyPreset("anthropic", "anthropic", "messages", "https://api.anthropic.com", "ANTHROPIC_API_KEY", "x-api-key", "", "api-key", "native-proxy", True),
    ProxyPreset("gemini", "gemini", "generate-content", "https://generativelanguage.googleapis.com", "GEMINI_API_KEY", "x-goog-api-key", "", "api-key", "native-proxy", True),
    ProxyPreset("azure-openai", "openai", "openai-compatible", "", "AZURE_OPENAI_API_KEY", "api-key", "", "api-key+deployment-endpoint", "configured-endpoint", True),
    ProxyPreset("openrouter", "openai-compatible", "openai-compatible", "https://openrouter.ai/api", "OPENROUTER_API_KEY", "Authorization", "Bearer ", "api-key", "native-proxy", True),
    ProxyPreset("mistral", "openai-compatible", "openai-compatible", "https://api.mistral.ai", "MISTRAL_API_KEY", "Authorization", "Bearer ", "api-key", "native-proxy", True),
    ProxyPreset("groq", "openai-compatible", "openai-compatible", "https://api.groq.com/openai", "GROQ_API_KEY", "Authorization", "Bearer ", "api-key", "native-proxy", True),
    ProxyPreset("cohere", "openai-compatible", "chat", "https://api.cohere.com", "COHERE_API_KEY", "Authorization", "Bearer ", "api-key", "adapter-required", False),
    ProxyPreset("aws-bedrock", "anthropic", "converse", "", "AWS_PROFILE", "", "", "aws-sigv4", "signed-adapter-required", False),
    ProxyPreset("vertex-ai", "gemini", "generate-content", "", "GOOGLE_APPLICATION_CREDENTIALS", "", "", "google-oauth2", "signed-adapter-required", False),
)


class ProxyProductRegistry:
    @staticmethod
    def by_provider(provider: str) -> ProxyPreset:
        normalized = provider.strip().casefold()
        for item in PRESETS:
            if item.provider == normalized:
                return item
        raise KeyError(provider)

    @staticmethod
    def records() -> list[dict[str, Any]]:
        return [asdict(item) for item in PRESETS]

    @staticmethod
    def validate() -> dict[str, Any]:
        matrix = {item.integration_id for item in PROVIDERS}
        presets = {item.provider for item in PRESETS}
        missing = sorted(matrix - presets)
        extra = sorted(presets - matrix)
        unsafe = [
            item.provider for item in PRESETS
            if item.default_upstream and not item.default_upstream.startswith("https://")
        ]
        return {
            "ok": not missing and not extra and not unsafe,
            "providers": len(PRESETS),
            "zero_code_compatible": sum(item.zero_code_compatible for item in PRESETS),
            "adapter_required": sum(not item.zero_code_compatible for item in PRESETS),
            "missing": missing,
            "extra": extra,
            "unsafe_upstreams": unsafe,
            "live_boundary": "preset validation is not live provider certification",
        }

    @classmethod
    def plan(cls, provider: str, *, upstream: str = "") -> dict[str, Any]:
        item = cls.by_provider(provider)
        resolved_upstream = upstream or item.default_upstream
        reasons: list[str] = []
        if not resolved_upstream:
            reasons.append("explicit-upstream-required")
        if resolved_upstream and not resolved_upstream.startswith("https://"):
            reasons.append("https-upstream-required")
        if not item.zero_code_compatible:
            reasons.append(item.install_mode)
        ready = not reasons
        return {
            "ok": ready,
            "version": VERSION,
            "channel": CHANNEL,
            "provider": asdict(item),
            "resolved_upstream": resolved_upstream,
            "control_token_required": True,
            "stream_mode": "commit-before-forward",
            "credential_policy": "transport-only",
            "reasons": reasons,
            "live_certification": "NOT_CERTIFIED" if item.live_certification else "UNKNOWN",
        }

    @classmethod
    def command(
        cls,
        provider: str,
        *,
        project: Path,
        state_root: Path,
        upstream: str = "",
        listen_host: str = "127.0.0.1",
        listen_port: int = 8787,
        cache_policy: str = "auto",
    ) -> tuple[str, ...]:
        product = cls.plan(provider, upstream=upstream)
        if not product["ok"]:
            raise ValueError("proxy preset is not directly runnable: " + ",".join(product["reasons"]))
        item = cls.by_provider(provider)
        command = [
            sys.executable,
            "-m",
            "signalcore_runtime",
            "--project",
            str(project.resolve(strict=False)),
            "--state-root",
            str(state_root.resolve(strict=False)),
            "provider",
            "proxy",
            "--provider",
            item.gateway_provider,
            "--upstream",
            str(product["resolved_upstream"]),
            "--listen-host",
            listen_host,
            "--listen-port",
            str(listen_port),
            "--credential-env",
            item.credential_env,
            "--credential-header",
            item.credential_header,
            "--cache-policy",
            cache_policy,
        ]
        if item.credential_prefix:
            command.extend(("--credential-prefix", item.credential_prefix))
        return tuple(command)

    @classmethod
    def service_spec(
        cls,
        provider: str,
        *,
        project: Path,
        state_root: Path,
        upstream: str = "",
        listen_host: str = "127.0.0.1",
        listen_port: int = 8787,
        cache_policy: str = "auto",
        environment_file: str = "",
    ) -> ServiceSpec:
        return ServiceSpec(
            name=f"signalcore-proxy-{provider.replace('_', '-').replace('.', '-')}",
            command=cls.command(
                provider,
                project=project,
                state_root=state_root,
                upstream=upstream,
                listen_host=listen_host,
                listen_port=listen_port,
                cache_policy=cache_policy,
            ),
            environment_file=environment_file,
            working_directory=str(project.resolve(strict=False)),
            description=f"SignalCore v{VERSION} pre-release {provider} provider proxy",
            restart_seconds=3,
        )

    @classmethod
    def service(
        cls,
        action: str,
        provider: str,
        *,
        project: Path,
        state_root: Path,
        home: Path | None = None,
        upstream: str = "",
        listen_host: str = "127.0.0.1",
        listen_port: int = 8787,
        cache_policy: str = "auto",
        environment_file: str = "",
        platform_name: str | None = None,
        apply: bool = False,
        activate: bool = False,
    ) -> dict[str, Any]:
        spec = cls.service_spec(
            provider,
            project=project,
            state_root=state_root,
            upstream=upstream,
            listen_host=listen_host,
            listen_port=listen_port,
            cache_policy=cache_policy,
            environment_file=environment_file,
        )
        manager = ProviderProxyServiceManager(home)
        if action == "plan":
            return {"ok": True, "action": action, "spec": asdict(spec), "plan": asdict(manager.plan(spec, platform_name=platform_name))}
        if action == "install":
            return manager.install(spec, platform_name=platform_name, activate=activate, dry_run=not apply)
        if action == "verify":
            return manager.verify(spec, platform_name=platform_name)
        if action == "uninstall":
            return manager.uninstall(spec, platform_name=platform_name, deactivate=activate, dry_run=not apply)
        raise ValueError(f"unsupported proxy service action: {action}")
