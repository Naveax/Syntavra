from __future__ import annotations

import json
import os
import ssl
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field, replace
from typing import Any, Mapping, Protocol, Sequence


class GatewayError(RuntimeError):
    pass


@dataclass(frozen=True)
class GatewayConfig:
    provider: str
    model: str
    endpoint: str = ""
    api_key_env: str = ""
    timeout_seconds: float = 120.0
    max_output_tokens: int = 8192
    temperature: float = 0.1
    api_mode: str = "auto"
    extra_headers: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ModelResult:
    text: str
    provider: str
    model: str
    usage: Mapping[str, int]
    response_id: str = ""
    finish_reason: str = ""
    raw: Mapping[str, Any] = field(default_factory=dict)


class ModelGateway(Protocol):
    def complete(self, messages: Sequence[Mapping[str, str]], *, system: str = "") -> ModelResult: ...


class _HTTPGateway:
    def __init__(self, config: GatewayConfig) -> None:
        if not config.provider or not config.model:
            raise ValueError("provider and model are required")
        if not 1 <= config.max_output_tokens <= 1_000_000:
            raise ValueError("max_output_tokens is out of bounds")
        if not 0.0 <= config.temperature <= 2.0:
            raise ValueError("temperature must be between 0 and 2")
        self.config = config

    def _api_key(self) -> str:
        if not self.config.api_key_env:
            return ""
        value = os.environ.get(self.config.api_key_env, "")
        if not value:
            raise GatewayError(f"required API key environment variable is missing: {self.config.api_key_env}")
        return value

    def _post(self, url: str, payload: Mapping[str, Any], headers: Mapping[str, str]) -> Mapping[str, Any]:
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise GatewayError("model endpoint must be an absolute http(s) URL")
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=body,
            headers={"Content-Type": "application/json", "User-Agent": "Syntavra/0.0.1", **dict(headers), **dict(self.config.extra_headers)},
            method="POST",
        )
        context = ssl.create_default_context() if parsed.scheme == "https" else None
        try:
            with urllib.request.urlopen(request, timeout=self.config.timeout_seconds, context=context) as response:
                raw = response.read()
        except urllib.error.HTTPError as error:
            detail = error.read(16384).decode("utf-8", errors="replace")
            raise GatewayError(f"model endpoint returned HTTP {error.code}: {detail}") from error
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise GatewayError(f"model endpoint request failed: {type(error).__name__}: {error}") from error
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as error:
            raise GatewayError("model endpoint returned invalid JSON") from error
        if not isinstance(value, Mapping):
            raise GatewayError("model endpoint response must be a JSON object")
        return value

    @staticmethod
    def _usage(value: Mapping[str, Any]) -> dict[str, int]:
        output: dict[str, int] = {}
        aliases = {
            "prompt_tokens": "input_tokens",
            "input_tokens": "input_tokens",
            "completion_tokens": "output_tokens",
            "output_tokens": "output_tokens",
            "total_tokens": "total_tokens",
            "cached_tokens": "cached_tokens",
            "cache_read_input_tokens": "cached_tokens",
            "reasoning_tokens": "reasoning_tokens",
        }

        def visit(item: Any) -> None:
            if isinstance(item, Mapping):
                for key, child in item.items():
                    normalized = aliases.get(str(key).casefold())
                    if normalized and isinstance(child, int) and child >= 0:
                        output[normalized] = max(output.get(normalized, 0), child)
                    else:
                        visit(child)
            elif isinstance(item, list):
                for child in item:
                    visit(child)

        visit(value)
        return output


class OpenAICompatibleGateway(_HTTPGateway):
    """OpenAI-compatible Responses or Chat Completions transport.

    The same transport covers OpenAI-compatible local servers, LM Studio,
    LocalAI, vLLM and NVIDIA NIM endpoints.
    """

    def complete(self, messages: Sequence[Mapping[str, str]], *, system: str = "") -> ModelResult:
        endpoint = (self.config.endpoint or "https://api.openai.com/v1").rstrip("/")
        mode = self.config.api_mode.casefold()
        if mode == "auto":
            mode = "responses" if endpoint == "https://api.openai.com/v1" else "chat"
        key = self._api_key()
        headers = {"Authorization": f"Bearer {key}"} if key else {}
        if mode == "responses":
            input_messages: list[dict[str, str]] = []
            if system:
                input_messages.append({"role": "developer", "content": system})
            input_messages.extend({"role": str(item["role"]), "content": str(item["content"])} for item in messages)
            payload = {
                "model": self.config.model,
                "input": input_messages,
                "max_output_tokens": self.config.max_output_tokens,
                "temperature": self.config.temperature,
                "store": False,
            }
            raw = self._post(endpoint + "/responses", payload, headers)
            text = str(raw.get("output_text") or "")
            if not text:
                parts: list[str] = []
                for item in raw.get("output", []) if isinstance(raw.get("output"), list) else []:
                    if not isinstance(item, Mapping):
                        continue
                    for content in item.get("content", []) if isinstance(item.get("content"), list) else []:
                        if isinstance(content, Mapping) and content.get("text"):
                            parts.append(str(content["text"]))
                text = "\n".join(parts)
            if not text:
                raise GatewayError("Responses API returned no text output")
            return ModelResult(text, self.config.provider, self.config.model, self._usage(raw), str(raw.get("id") or ""), str(raw.get("status") or ""), raw)
        if mode != "chat":
            raise GatewayError(f"unsupported OpenAI-compatible api_mode: {mode}")
        chat_messages: list[dict[str, str]] = []
        if system:
            chat_messages.append({"role": "system", "content": system})
        chat_messages.extend({"role": str(item["role"]), "content": str(item["content"])} for item in messages)
        raw = self._post(
            endpoint + "/chat/completions",
            {
                "model": self.config.model,
                "messages": chat_messages,
                "max_tokens": self.config.max_output_tokens,
                "temperature": self.config.temperature,
                "stream": False,
            },
            headers,
        )
        choices = raw.get("choices") if isinstance(raw.get("choices"), list) else []
        choice = choices[0] if choices and isinstance(choices[0], Mapping) else {}
        message = choice.get("message") if isinstance(choice.get("message"), Mapping) else {}
        text = str(message.get("content") or "")
        if not text:
            raise GatewayError("Chat Completions endpoint returned no text output")
        return ModelResult(text, self.config.provider, self.config.model, self._usage(raw), str(raw.get("id") or ""), str(choice.get("finish_reason") or ""), raw)


class AnthropicGateway(_HTTPGateway):
    def complete(self, messages: Sequence[Mapping[str, str]], *, system: str = "") -> ModelResult:
        endpoint = (self.config.endpoint or "https://api.anthropic.com/v1").rstrip("/")
        key = self._api_key()
        raw = self._post(
            endpoint + "/messages",
            {
                "model": self.config.model,
                "system": system,
                "messages": [{"role": str(item["role"]), "content": str(item["content"])} for item in messages if str(item["role"]) in {"user", "assistant"}],
                "max_tokens": self.config.max_output_tokens,
                "temperature": self.config.temperature,
            },
            {"x-api-key": key, "anthropic-version": "2023-06-01"},
        )
        content = raw.get("content") if isinstance(raw.get("content"), list) else []
        text = "\n".join(str(item.get("text")) for item in content if isinstance(item, Mapping) and item.get("type") == "text" and item.get("text"))
        if not text:
            raise GatewayError("Anthropic Messages API returned no text output")
        return ModelResult(text, self.config.provider, self.config.model, self._usage(raw), str(raw.get("id") or ""), str(raw.get("stop_reason") or ""), raw)


class GeminiGateway(_HTTPGateway):
    def complete(self, messages: Sequence[Mapping[str, str]], *, system: str = "") -> ModelResult:
        endpoint = (self.config.endpoint or "https://generativelanguage.googleapis.com/v1beta").rstrip("/")
        key = self._api_key()
        contents = []
        for item in messages:
            role = "model" if str(item["role"]) == "assistant" else "user"
            contents.append({"role": role, "parts": [{"text": str(item["content"])}]})
        payload: dict[str, Any] = {
            "contents": contents,
            "generationConfig": {
                "maxOutputTokens": self.config.max_output_tokens,
                "temperature": self.config.temperature,
                "responseMimeType": "application/json",
            },
        }
        if system:
            payload["systemInstruction"] = {"parts": [{"text": system}]}
        raw = self._post(
            f"{endpoint}/models/{urllib.parse.quote(self.config.model, safe='-_.~')}:generateContent",
            payload,
            {"x-goog-api-key": key},
        )
        candidates = raw.get("candidates") if isinstance(raw.get("candidates"), list) else []
        candidate = candidates[0] if candidates and isinstance(candidates[0], Mapping) else {}
        content = candidate.get("content") if isinstance(candidate.get("content"), Mapping) else {}
        parts = content.get("parts") if isinstance(content.get("parts"), list) else []
        text = "\n".join(str(item.get("text")) for item in parts if isinstance(item, Mapping) and item.get("text"))
        if not text:
            raise GatewayError("Gemini generateContent returned no text output")
        return ModelResult(text, self.config.provider, self.config.model, self._usage(raw), "", str(candidate.get("finishReason") or ""), raw)


class SequenceModelGateway:
    """Deterministic in-process gateway for tests and replay workflows."""

    def __init__(self, responses: Sequence[str | Mapping[str, Any]], *, model: str = "sequence") -> None:
        self.responses = list(responses)
        self.model = model
        self.index = 0

    def complete(self, messages: Sequence[Mapping[str, str]], *, system: str = "") -> ModelResult:
        del messages, system
        if self.index >= len(self.responses):
            raise GatewayError("sequence gateway exhausted")
        value = self.responses[self.index]
        self.index += 1
        text = json.dumps(value, ensure_ascii=False) if isinstance(value, Mapping) else str(value)
        return ModelResult(text, "sequence", self.model, {})


def create_gateway(config: GatewayConfig) -> ModelGateway:
    provider = config.provider.strip().casefold()
    normalized = config
    if provider == "openai" and not config.api_key_env:
        normalized = replace(config, api_key_env="OPENAI_API_KEY")
    elif provider == "nvidia-nim":
        normalized = replace(
            config,
            endpoint=config.endpoint or "https://integrate.api.nvidia.com/v1",
            api_key_env=config.api_key_env or "NVIDIA_API_KEY",
            api_mode="chat" if config.api_mode == "auto" else config.api_mode,
        )
    elif provider in {"anthropic", "claude"} and not config.api_key_env:
        normalized = replace(config, api_key_env="ANTHROPIC_API_KEY")
    elif provider in {"gemini", "google"} and not config.api_key_env:
        normalized = replace(config, api_key_env="GEMINI_API_KEY")
    if provider in {"openai", "openai-compatible", "local", "lm-studio", "localai", "nvidia-nim", "vllm"}:
        return OpenAICompatibleGateway(normalized)
    if provider in {"anthropic", "claude"}:
        return AnthropicGateway(normalized)
    if provider in {"gemini", "google"}:
        return GeminiGateway(normalized)
    raise ValueError(f"unsupported model gateway provider: {config.provider}")


__all__ = [
    "AnthropicGateway",
    "GatewayConfig",
    "GatewayError",
    "GeminiGateway",
    "ModelGateway",
    "ModelResult",
    "OpenAICompatibleGateway",
    "SequenceModelGateway",
    "create_gateway",
]
