from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping

from .util import canonical_json, sha256_bytes


@dataclass(frozen=True)
class ModelCapabilities:
    provider: str
    model: str
    revision: str = "unknown"
    context_window: int = 0
    tool_support: bool = False
    image_support: bool = False
    audio_support: bool = False
    structured_output: bool = False
    reasoning: bool = False
    prompt_caching: bool = False
    batch: bool = False
    realtime: bool = False
    region: str = ""
    endpoint_version: str = ""

    @property
    def fingerprint(self) -> str:
        return sha256_bytes(canonical_json(asdict(self)))


class ProviderCapabilityRegistry:
    def __init__(self):
        self._models: dict[tuple[str, str, str], ModelCapabilities] = {}
        self._providers: dict[str, dict[str, Any]] = {}

    def register_provider(self, provider: str, *, aliases: tuple[str, ...] = (), endpoints: tuple[str, ...] = ()) -> None:
        canonical = provider.strip().casefold()
        if not canonical:
            raise ValueError("provider is required")
        self._providers[canonical] = {
            "provider": canonical,
            "aliases": tuple(sorted(set(alias.casefold() for alias in aliases))),
            "endpoints": tuple(sorted(set(endpoints))),
        }

    def register_model(self, capabilities: ModelCapabilities) -> None:
        if capabilities.context_window < 0:
            raise ValueError("context_window must be non-negative")
        key = (capabilities.provider.casefold(), capabilities.model, capabilities.revision)
        self._models[key] = capabilities

    def resolve(self, provider: str, model: str, *, revision: str = "unknown") -> ModelCapabilities | None:
        canonical = provider.casefold()
        for name, metadata in self._providers.items():
            if canonical == name or canonical in metadata["aliases"]:
                canonical = name
                break
        return self._models.get((canonical, model, revision)) or self._models.get((canonical, model, "unknown"))

    def catalog(self) -> dict[str, Any]:
        return {
            "providers": [self._providers[key] for key in sorted(self._providers)],
            "models": [asdict(self._models[key]) | {"fingerprint": self._models[key].fingerprint} for key in sorted(self._models)],
        }


def cache_identity(
    *,
    provider: str,
    model: str,
    revision: str = "unknown",
    endpoint_version: str = "",
    region: str = "",
    system_fingerprint: str = "",
    tool_implementation_hash: str = "",
    security_policy_hash: str = "",
    runtime_version: str = "",
) -> dict[str, str]:
    return {
        "provider": provider.casefold(),
        "model": model,
        "revision": revision or "unknown",
        "endpoint_version": endpoint_version,
        "region": region,
        "system_fingerprint": system_fingerprint,
        "tool_implementation_hash": tool_implementation_hash,
        "security_policy_hash": security_policy_hash,
        "runtime_version": runtime_version,
    }


DEFAULT_PROVIDER_PRESETS: tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...] = (
    ("openai", ("chatgpt",), ("https://api.openai.com/v1",)),
    ("anthropic", ("claude",), ("https://api.anthropic.com/v1",)),
    ("google", ("gemini",), ("https://generativelanguage.googleapis.com",)),
    ("vertex-ai", ("vertex",), ("https://aiplatform.googleapis.com",)),
    ("azure-openai", ("azure",), ("https://{resource}.openai.azure.com",)),
    ("aws-bedrock", ("bedrock",), ("https://bedrock-runtime.{region}.amazonaws.com",)),
    ("cohere", (), ("https://api.cohere.com/v2",)),
    ("mistral", (), ("https://api.mistral.ai/v1",)),
    ("groq", (), ("https://api.groq.com/openai/v1",)),
    ("cerebras", (), ("https://api.cerebras.ai/v1",)),
    ("together", (), ("https://api.together.xyz/v1",)),
    ("fireworks", (), ("https://api.fireworks.ai/inference/v1",)),
    ("baseten", (), ("https://model-{id}.api.baseten.co",)),
    ("replicate", (), ("https://api.replicate.com/v1",)),
    ("openrouter", (), ("https://openrouter.ai/api/v1",)),
    ("perplexity", (), ("https://api.perplexity.ai",)),
    ("xai", ("grok",), ("https://api.x.ai/v1",)),
    ("deepseek", (), ("https://api.deepseek.com/v1",)),
    ("moonshot", ("kimi",), ("https://api.moonshot.ai/v1",)),
    ("alibaba-qwen", ("dashscope", "qwen"), ("https://dashscope.aliyuncs.com/compatible-mode/v1",)),
    ("baidu", ("qianfan",), ("https://qianfan.baidubce.com/v2",)),
    ("tencent", ("hunyuan",), ("https://hunyuan.tencentcloudapi.com",)),
    ("zhipu", ("glm",), ("https://open.bigmodel.cn/api/paas/v4",)),
    ("nvidia-nim", ("nvidia",), ("https://integrate.api.nvidia.com/v1",)),
    ("sambanova", (), ("https://api.sambanova.ai/v1",)),
    ("cloudflare-workers-ai", ("cloudflare",), ("https://api.cloudflare.com/client/v4/accounts/{account}/ai/run",)),
    ("modal", (), ("https://{workspace}--{app}.modal.run",)),
    ("huggingface", ("hf",), ("https://api-inference.huggingface.co",)),
    ("ollama", (), ("http://127.0.0.1:11434/v1",)),
    ("lm-studio", ("lmstudio",), ("http://127.0.0.1:1234/v1",)),
    ("vllm", (), ("http://127.0.0.1:8000/v1",)),
    ("litellm", (), ("http://127.0.0.1:4000/v1",)),
    ("localai", (), ("http://127.0.0.1:8080/v1",)),
    ("ai21", (), ("https://api.ai21.com/studio/v1",)),
    ("ibm-watsonx", ("watsonx",), ("https://{region}.ml.cloud.ibm.com",)),
    ("databricks", (), ("https://{workspace}.databricks.com/serving-endpoints",)),
    ("snowflake-cortex", ("snowflake",), ("https://{account}.snowflakecomputing.com",)),
    ("oracle-oci", ("oci",), ("https://inference.generativeai.{region}.oci.oraclecloud.com",)),
    ("github-models", (), ("https://models.inference.ai.azure.com",)),
    ("scaleway", (), ("https://api.scaleway.ai/v1",)),
    ("nebius", (), ("https://api.studio.nebius.ai/v1",)),
    ("upstage", (), ("https://api.upstage.ai/v1",)),
    ("voyage", (), ("https://api.voyageai.com/v1",)),
    ("novita", (), ("https://api.novita.ai/v3/openai",)),
    ("anyscale", (), ("https://api.endpoints.anyscale.com/v1",)),
    ("lepton", (), ("https://{workspace}.lepton.run/api/v1",)),
    ("octoai", (), ("https://text.octoai.run/v1",)),
    ("friendli", (), ("https://api.friendli.ai/serverless/v1",)),
)


def default_provider_registry() -> ProviderCapabilityRegistry:
    registry = ProviderCapabilityRegistry()
    for provider, aliases, endpoints in DEFAULT_PROVIDER_PRESETS:
        registry.register_provider(provider, aliases=aliases, endpoints=endpoints)
    return registry
