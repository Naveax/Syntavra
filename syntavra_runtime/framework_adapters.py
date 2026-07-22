from __future__ import annotations

import inspect
import threading
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from .sdk import SDKInvocation, SyntavraClient


def _call_path(root: Any, path: tuple[str, ...], **kwargs: Any) -> Any:
    target = root
    for name in path:
        target = getattr(target, name)
    return target(**kwargs)


@dataclass(frozen=True)
class FrameworkCapability:
    name: str
    mode: str
    dependency_required: bool
    exact_capture: bool
    deterministic_replay: bool


class OpenAIResponsesTransport:
    def __init__(self, client: Any):
        self.client = client

    def __call__(self, request: Mapping[str, Any]) -> Any:
        return _call_path(self.client, ("responses", "create"), **dict(request))


class OpenAIChatTransport:
    def __init__(self, client: Any):
        self.client = client

    def __call__(self, request: Mapping[str, Any]) -> Any:
        return _call_path(self.client, ("chat", "completions", "create"), **dict(request))


class AnthropicMessagesTransport:
    def __init__(self, client: Any):
        self.client = client

    def __call__(self, request: Mapping[str, Any]) -> Any:
        return _call_path(self.client, ("messages", "create"), **dict(request))


class LiteLLMTransport:
    def __init__(self, completion: Callable[..., Any]):
        self.completion = completion

    def __call__(self, request: Mapping[str, Any]) -> Any:
        return self.completion(**dict(request))


class GeminiGenerateTransport:
    """Duck-typed adapter for google.genai or a model object."""

    def __init__(self, client_or_model: Any):
        self.client_or_model = client_or_model

    def __call__(self, request: Mapping[str, Any]) -> Any:
        value = dict(request)
        models = getattr(self.client_or_model, "models", None)
        if models is not None and callable(getattr(models, "generate_content", None)):
            return models.generate_content(**value)
        method = getattr(self.client_or_model, "generate_content", None)
        if callable(method):
            return method(**value)
        raise TypeError("Gemini adapter requires models.generate_content or generate_content")


class SyntavraMiddleware:
    """Framework-neutral callable middleware with sync and async entrypoints."""

    def __init__(
        self,
        client: SyntavraClient,
        *,
        provider: str,
        transport: Callable[[Mapping[str, Any]], Any],
        defaults: Mapping[str, Any] | None = None,
    ):
        self.client = client
        self.provider = provider
        self.transport = transport
        self.defaults = dict(defaults or {})

    def __call__(self, request: Mapping[str, Any], **overrides: Any) -> SDKInvocation:
        return self.client.invoke(
            self.provider,
            request,
            self.transport,
            **{**self.defaults, **overrides},
        )

    async def ainvoke(self, request: Mapping[str, Any], **overrides: Any) -> SDKInvocation:
        return await self.client.ainvoke(
            self.provider,
            request,
            self.transport,
            **{**self.defaults, **overrides},
        )


class LangChainCallbackHandler:
    """Dependency-free callback compatible with LangChain's callback method names.

    It does not import LangChain. Requests are prepared on ``on_llm_start`` and
    exact responses are captured on ``on_llm_end``. Callers can still use the
    normal LangChain execution path; Syntavra only observes evidence and usage.
    """

    def __init__(
        self,
        client: SyntavraClient,
        *,
        provider: str = "openai-compatible",
        model: str = "",
        cache_policy: str = "off",
        preview_bytes: int = 4096,
    ):
        self.client = client
        self.provider = provider
        self.model = model
        self.cache_policy = cache_policy
        self.preview_bytes = preview_bytes
        self._lock = threading.Lock()
        self._pending: dict[str, Any] = {}
        self.captures: list[Any] = []

    @staticmethod
    def _run_key(run_id: Any) -> str:
        return str(run_id or uuid.uuid4())

    def on_llm_start(
        self,
        serialized: Mapping[str, Any],
        prompts: list[str],
        *,
        run_id: Any = None,
        **kwargs: Any,
    ) -> None:
        request = {
            "model": self.model or str(serialized.get("name") or serialized.get("id") or "langchain"),
            "messages": [{"role": "user", "content": prompt} for prompt in prompts],
            "temperature": kwargs.get("temperature", 0),
        }
        plan = self.client.prepare(
            self.provider,
            request,
            model=request["model"],
            cache_policy=self.cache_policy,
        )
        with self._lock:
            self._pending[self._run_key(run_id)] = plan

    def on_chat_model_start(
        self,
        serialized: Mapping[str, Any],
        messages: list[list[Any]],
        *,
        run_id: Any = None,
        **kwargs: Any,
    ) -> None:
        flattened: list[dict[str, Any]] = []
        for group in messages:
            for message in group:
                content = getattr(message, "content", str(message))
                role = getattr(message, "type", getattr(message, "role", "user"))
                flattened.append({"role": str(role), "content": content})
        request = {
            "model": self.model or str(serialized.get("name") or serialized.get("id") or "langchain"),
            "messages": flattened,
            "temperature": kwargs.get("temperature", 0),
        }
        plan = self.client.prepare(
            self.provider,
            request,
            model=request["model"],
            cache_policy=self.cache_policy,
        )
        with self._lock:
            self._pending[self._run_key(run_id)] = plan

    @staticmethod
    def _response_payload(response: Any) -> dict[str, Any]:
        for name in ("model_dump", "dict", "to_dict"):
            method = getattr(response, name, None)
            if callable(method):
                value = method()
                if isinstance(value, Mapping):
                    return dict(value)
        if isinstance(response, Mapping):
            return dict(response)
        generations = getattr(response, "generations", None)
        if generations is not None:
            output: list[list[dict[str, Any]]] = []
            for group in generations:
                rows: list[dict[str, Any]] = []
                for generation in group:
                    text = getattr(generation, "text", "")
                    message = getattr(generation, "message", None)
                    rows.append({
                        "text": text,
                        "message": getattr(message, "content", None) if message is not None else None,
                    })
                output.append(rows)
            usage = getattr(response, "llm_output", None)
            return {"generations": output, "metadata": usage or {}}
        return {"output_text": str(response)}

    def on_llm_end(self, response: Any, *, run_id: Any = None, **_: Any) -> None:
        key = self._run_key(run_id)
        with self._lock:
            plan = self._pending.pop(key, None)
        if plan is None:
            return
        capture = self.client.capture(
            plan,
            self._response_payload(response),
            store_replay=False,
            preview_bytes=self.preview_bytes,
        )
        with self._lock:
            self.captures.append(capture)

    def on_llm_error(self, error: BaseException, *, run_id: Any = None, **_: Any) -> None:
        key = self._run_key(run_id)
        with self._lock:
            plan = self._pending.pop(key, None)
        if plan is None:
            return
        capture = self.client.capture(
            plan,
            {
                "error": {
                    "type": type(error).__name__,
                    "message": str(error),
                }
            },
            store_replay=False,
            preview_bytes=self.preview_bytes,
        )
        with self._lock:
            self.captures.append(capture)


def framework_capabilities() -> dict[str, Any]:
    rows = (
        FrameworkCapability("openai-responses", "duck-typed-transport", True, True, True),
        FrameworkCapability("openai-chat", "duck-typed-transport", True, True, True),
        FrameworkCapability("anthropic-messages", "duck-typed-transport", True, True, True),
        FrameworkCapability("gemini-generate-content", "duck-typed-transport", True, True, True),
        FrameworkCapability("litellm", "callable-transport", True, True, True),
        FrameworkCapability("langchain-callback", "dependency-free-observer", False, True, False),
        FrameworkCapability("generic-middleware", "sync-async-callable", False, True, True),
    )
    return {
        "frameworks": [row.__dict__ for row in rows],
        "dependency_policy": "optional provider/framework SDKs are never imported by Syntavra",
    }
