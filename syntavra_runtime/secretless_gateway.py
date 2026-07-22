from .platform_common import *


class SecretlessProviderGateway:
    """Provider transport plan that never places provider secrets in agent state."""

    PROVIDERS = {
        "openai": {"credential_env": "OPENAI_API_KEY", "header": "Authorization", "prefix": "Bearer ", "protocol": "openai"},
        "anthropic": {"credential_env": "ANTHROPIC_API_KEY", "header": "x-api-key", "prefix": "", "protocol": "anthropic"},
        "gemini": {"credential_env": "GEMINI_API_KEY", "header": "x-goog-api-key", "prefix": "", "protocol": "gemini"},
        "mistral": {"credential_env": "MISTRAL_API_KEY", "header": "Authorization", "prefix": "Bearer ", "protocol": "openai-compatible"},
        "groq": {"credential_env": "GROQ_API_KEY", "header": "Authorization", "prefix": "Bearer ", "protocol": "openai-compatible"},
        "openrouter": {"credential_env": "OPENROUTER_API_KEY", "header": "Authorization", "prefix": "Bearer ", "protocol": "openai-compatible"},
        "azure-openai": {"credential_env": "AZURE_OPENAI_API_KEY", "header": "api-key", "prefix": "", "protocol": "azure-openai"},
        "bedrock": {"credential_env": "AWS_PROFILE", "header": "", "prefix": "", "protocol": "sigv4-adapter"},
        "vertex": {"credential_env": "GOOGLE_APPLICATION_CREDENTIALS", "header": "", "prefix": "", "protocol": "oauth2-adapter"},
        "local": {"credential_env": "", "header": "", "prefix": "", "protocol": "openai-compatible"},
    }

    @staticmethod
    def sanitize_environment(environment: Mapping[str, str]) -> dict[str, str]:
        secret_names = {
            value["credential_env"] for value in SecretlessProviderGateway.PROVIDERS.values() if value["credential_env"]
        }
        return {
            key: value for key, value in environment.items()
            if key not in secret_names and not re.search(r"(?i)(api[_-]?key|token|password|secret|credential)", key)
        }

    @classmethod
    def plan(cls, provider: str, *, upstream: str = "", credential_source: str = "os-broker") -> dict[str, Any]:
        key = provider.casefold()
        if key not in cls.PROVIDERS:
            raise ValueError(f"unsupported provider: {provider}")
        spec = cls.PROVIDERS[key]
        return {
            "ok": True,
            "version": VERSION,
            "channel": CHANNEL,
            "provider": key,
            "protocol": spec["protocol"],
            "upstream": upstream,
            "agent_environment_contains_secret": False,
            "credential_source": credential_source,
            "transport_injection": {
                "credential_env": spec["credential_env"],
                "header": spec["header"],
                "prefix": spec["prefix"],
                "visibility": "gateway-process-only",
            },
            "child_process_secret_inheritance": "denied",
            "logs": "redacted",
            "receipt": sha256_bytes(canonical_json({"provider": key, "protocol": spec["protocol"], "credential_source": credential_source, "upstream": upstream})),
        }
