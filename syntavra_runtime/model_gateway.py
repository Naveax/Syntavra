from __future__ import annotations

import json
import os
import ssl
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field, replace
from typing import Any, Mapping, Protocol, Sequence

from .provider_token_envelope import ProviderTokenEnvelope


class GatewayError(RuntimeError):
    pass


@dataclass(frozen=True)
class ContextEditingConfig:
    """Explicit, benchmark-gated provider-native context editing policy."""

    enabled: bool = False
    benchmark_admitted: bool = False
    clear_tool_uses: bool = True
    clear_thinking: bool = False
    trigger_input_tokens: int = 100_000
    keep_tool_uses: int = 3
    clear_at_least_tokens: int = 0
    keep_thinking_turns: int = 0
    exclude_tools: tuple[str, ...] = ()
    clear_tool_inputs: bool = False

    def __post_init__(self) -> None:
        if int(self.trigger_input_tokens) < 1:
            raise ValueError("context editing trigger_input_tokens must be positive")
        if int(self.keep_tool_uses) < 1:
            raise ValueError("context editing keep_tool_uses must be positive")
        if int(self.clear_at_least_tokens) < 0:
            raise ValueError("context editing clear_at_least_tokens must be non-negative")
        if int(self.keep_thinking_turns) < 0:
            raise ValueError("context editing keep_thinking_turns must be non-negative")
        tools = tuple(sorted({str(value).strip() for value in self.exclude_tools if str(value).strip()}))
        object.__setattr__(self, "trigger_input_tokens", int(self.trigger_input_tokens))
        object.__setattr__(self, "keep_tool_uses", int(self.keep_tool_uses))
        object.__setattr__(self, "clear_at_least_tokens", int(self.clear_at_least_tokens))
        object.__setattr__(self, "keep_thinking_turns", int(self.keep_thinking_turns))
        object.__setattr__(self, "exclude_tools", tools)


@dataclass(frozen=True)
class ProviderContextEditingCapability:
    provider: str
    native_mode: str
    supported: bool
    directly_admissible: bool
    reports_cleared_input_tokens: bool
    reason: str


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
    token_envelope: ProviderTokenEnvelope | None = None
    prepared_input_tokens: int = 0
    context_editing: ContextEditingConfig | None = None


@dataclass(frozen=True)
class ModelResult:
    text: str
    provider: str
    model: str
    usage: Mapping[str, int]
    response_id: str = ""
    finish_reason: str = ""
    raw: Mapping[str, Any] = field(default_factory=dict)
    diagnostics: Mapping[str, Any] = field(default_factory=dict)


class ModelGateway(Protocol):
    def complete(self, messages: Sequence[Mapping[str, str]], *, system: str = "") -> ModelResult: ...

    def prepare_input_tokens(self, tokens: int) -> None: ...


def provider_context_editing_capability(config: GatewayConfig) -> ProviderContextEditingCapability:
    provider = config.provider.strip().casefold()
    if provider in {"anthropic", "claude"}:
        return ProviderContextEditingCapability(
            provider=provider,
            native_mode="anthropic-selective-context-editing",
            supported=True,
            directly_admissible=True,
            reports_cleared_input_tokens=True,
            reason="native selective tool/thinking clearing is request-local and receipted",
        )
    if provider == "openai":
        endpoint = (config.endpoint or "https://api.openai.com/v1").rstrip("/")
        mode = config.api_mode.casefold()
        if mode == "auto":
            mode = "responses" if endpoint == "https://api.openai.com/v1" else "chat"
        if mode == "responses":
            return ProviderContextEditingCapability(
                provider=provider,
                native_mode="openai-responses-compaction",
                supported=True,
                directly_admissible=False,
                reports_cleared_input_tokens=False,
                reason="native compaction requires explicit continuation-state integration before automatic use",
            )
    return ProviderContextEditingCapability(
        provider=provider or "unknown",
        native_mode="portable-syntavra-fallback",
        supported=False,
        directly_admissible=False,
        reports_cleared_input_tokens=False,
        reason="no provider-native context editing contract is admitted for this transport",
    )


def _context_editing_diagnostics(value: Mapping[str, Any]) -> dict[str, Any]:
    context_management = value.get("context_management")
    if not isinstance(context_management, Mapping):
        return {}
    applied = context_management.get("applied_edits")
    if not isinstance(applied, list):
        return {}
    rows: list[dict[str, Any]] = []
    total = 0
    for item in applied:
        if not isinstance(item, Mapping):
            continue
        cleared = item.get("cleared_input_tokens", 0)
        if isinstance(cleared, bool) or not isinstance(cleared, int) or cleared < 0:
            cleared = 0
        total += cleared
        row: dict[str, Any] = {
            "type": str(item.get("type") or "unknown"),
            "cleared_input_tokens": cleared,
        }
        for key in ("cleared_tool_uses", "cleared_thinking_turns"):
            amount = item.get(key)
            if isinstance(amount, int) and not isinstance(amount, bool) and amount >= 0:
                row[key] = amount
        rows.append(row)
    if not rows:
        return {}
    return {
        "context_editing": {
            "provider_native": True,
            "cleared_input_tokens": total,
            "applied_edits": tuple(rows),
        }
    }


class _HTTPGateway:
    def __init__(self, config: GatewayConfig) -> None:
        if not config.provider or not config.model:
            raise ValueError("provider and model are required")
        if not 1 <= config.max_output_tokens <= 1_000_000:
            raise ValueError("max_output_tokens is out of bounds")
        if not 0.0 <= config.temperature <= 2.0:
            raise ValueError("temperature must be between 0 and 2")
        if int(config.prepared_input_tokens) < 0:
            raise ValueError("prepared_input_tokens must be non-negative")
        if config.token_envelope is not None and not config.token_envelope.provider_call_admissible:
            raise ValueError("token envelope does not admit a provider call")
        self.config = config

    def prepare_input_tokens(self, tokens: int) -> None:
        """Bind the current compiled provider packet to the active envelope.

        Agent/runtime compilers call this immediately before dispatch. Replacing the
        immutable config prevents stale token counts from silently carrying across
        provider turns.
        """
        value = int(tokens)
        if value < 0:
            raise ValueError("prepared input tokens must be non-negative")
        self.config = replace(self.config, prepared_input_tokens=value)

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

    def _enforce_token_envelope(self) -> None:
        envelope = self.config.token_envelope
        if envelope is None:
            return
        prepared = int(self.config.prepared_input_tokens)
        if prepared <= 0 and envelope.original_input_tokens > 0:
            raise GatewayError(
                "token envelope requires tokenizer-observed prepared_input_tokens before provider dispatch"
            )
        if prepared > envelope.provider_input_budget_tokens:
            raise GatewayError(
                "prepared provider input exceeds token envelope: "
                f"{prepared}>{envelope.provider_input_budget_tokens}"
            )

    def _effective_output_limit(self) -> int:
        envelope = self.config.token_envelope
        if envelope is None:
            return self.config.max_output_tokens
        return max(
            1,
            min(
                int(self.config.max_output_tokens),
                int(envelope.provider_output_budget_tokens),
            ),
        )


class OpenAICompatibleGateway(_HTTPGateway):
    """OpenAI-compatible Responses or Chat Completions transport.

    The same transport covers OpenAI-compatible local servers, LM Studio,
    LocalAI, vLLM and NVIDIA NIM endpoints.
    """

    def complete(self, messages: Sequence[Mapping[str, str]], *, system: str = "") -> ModelResult:
        self._enforce_token_envelope()
        endpoint = (self.config.endpoint or "https://api.openai.com/v1").rstrip("/")
        mode = self.config.api_mode.casefold()
        if mode == "auto":
            mode = "responses" if endpoint == "https://api.openai.com/v1" else "chat"
        key = self._api_key()
        headers = {"Authorization": f"Bearer {key}"} if key else {}
        output_limit = self._effective_output_limit()
        if mode == "responses":
            input_messages: list[dict[str, str]] = []
            if system:
                input_messages.append({"role": "developer", "content": system})
            input_messages.extend({"role": str(item["role"]), "content": str(item["content"])} for item in messages)
            payload = {
                "model": self.config.model,
                "input": input_messages,
                "max_output_tokens": output_limit,
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
                "max_tokens": output_limit,
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
    def _native_context_management(self) -> Mapping[str, Any] | None:
        policy = self.config.context_editing
        if policy is None or not policy.enabled or not policy.benchmark_admitted:
            return None
        capability = provider_context_editing_capability(self.config)
        if not capability.directly_admissible:
            return None
        edits: list[dict[str, Any]] = []
        if policy.clear_thinking:
            thinking: dict[str, Any] = {"type": "clear_thinking_20251015"}
            if policy.keep_thinking_turns:
                thinking["keep"] = {
                    "type": "thinking_turns",
                    "value": policy.keep_thinking_turns,
                }
            edits.append(thinking)
        if policy.clear_tool_uses:
            tool_edit: dict[str, Any] = {
                "type": "clear_tool_uses_20250919",
                "trigger": {"type": "input_tokens", "value": policy.trigger_input_tokens},
                "keep": {"type": "tool_uses", "value": policy.keep_tool_uses},
            }
            if policy.clear_at_least_tokens:
                tool_edit["clear_at_least"] = {
                    "type": "input_tokens",
                    "value": policy.clear_at_least_tokens,
                }
            if policy.exclude_tools:
                tool_edit["exclude_tools"] = list(policy.exclude_tools)
            if policy.clear_tool_inputs:
                tool_edit["clear_tool_inputs"] = True
            edits.append(tool_edit)
        return {"edits": edits} if edits else None

    def complete(self, messages: Sequence[Mapping[str, str]], *, system: str = "") -> ModelResult:
        self._enforce_token_envelope()
        endpoint = (self.config.endpoint or "https://api.anthropic.com/v1").rstrip("/")
        key = self._api_key()
        payload: dict[str, Any] = {
            "model": self.config.model,
            "system": system,
            "messages": [{"role": str(item["role"]), "content": str(item["content"])} for item in messages if str(item["role"]) in {"user", "assistant"}],
            "max_tokens": self._effective_output_limit(),
            "temperature": self.config.temperature,
        }
        headers = {"x-api-key": key, "anthropic-version": "2023-06-01"}
        native_context = self._native_context_management()
        if native_context is not None:
            payload["context_management"] = native_context
            headers["anthropic-beta"] = "context-management-2025-06-27"
        raw = self._post(endpoint + "/messages", payload, headers)
        content = raw.get("content") if isinstance(raw.get("content"), list) else []
        text = "\n".join(str(item.get("text")) for item in content if isinstance(item, Mapping) and item.get("type") == "text" and item.get("text"))
        if not text:
            raise GatewayError("Anthropic Messages API returned no text output")
        return ModelResult(
            text,
            self.config.provider,
            self.config.model,
            self._usage(raw),
            str(raw.get("id") or ""),
            str(raw.get("stop_reason") or ""),
            raw,
            _context_editing_diagnostics(raw),
        )


class GeminiGateway(_HTTPGateway):
    def complete(self, messages: Sequence[Mapping[str, str]], *, system: str = "") -> ModelResult:
        self._enforce_token_envelope()
        endpoint = (self.config.endpoint or "https://generativelanguage.googleapis.com/v1beta").rstrip("/")
        key = self._api_key()
        contents = []
        for item in messages:
            role = "model" if str(item["role"]) == "assistant" else "user"
            contents.append({"role": role, "parts": [{"text": str(item["content"])}]})
        payload: dict[str, Any] = {
            "contents": contents,
            "generationConfig": {
                "maxOutputTokens": self._effective_output_limit(),
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
        self.prepared_input_tokens = 0
        self.prepared_history: list[int] = []

    def prepare_input_tokens(self, tokens: int) -> None:
        value = max(0, int(tokens))
        self.prepared_input_tokens = value
        self.prepared_history.append(value)

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
    "ContextEditingConfig",
    "GatewayConfig",
    "GatewayError",
    "GeminiGateway",
    "ModelGateway",
    "ModelResult",
    "OpenAICompatibleGateway",
    "ProviderContextEditingCapability",
    "SequenceModelGateway",
    "create_gateway",
    "provider_context_editing_capability",
]
