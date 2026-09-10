from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class OpenAIPromptCacheProfile:
    name: str
    modern: bool
    responses_api: bool
    lifetime_control: str


def _model_version(model: str) -> tuple[int, int] | None:
    name = str(model).strip().casefold()
    if not name.startswith("gpt-"):
        return None
    version = name[4:].split("-", 1)[0]
    parts = version.split(".")
    if not parts or not parts[0].isdigit():
        return None
    major = int(parts[0])
    minor = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
    return major, minor


def resolve_openai_prompt_cache_profile(
    provider: str,
    model: str,
    request: Mapping[str, Any],
) -> OpenAIPromptCacheProfile:
    """Resolve OpenAI cache controls without assuming compatible endpoints share semantics."""

    provider_name = str(provider).strip().casefold()
    responses_api = provider_name == "responses" or (
        provider_name == "openai" and "input" in request and "messages" not in request
    )
    direct_openai = provider_name in {"openai", "responses"}
    version = _model_version(model)
    modern = bool(direct_openai and responses_api and version is not None and version >= (5, 6))
    if modern:
        return OpenAIPromptCacheProfile(
            name="openai-responses-gpt56-v1",
            modern=True,
            responses_api=True,
            lifetime_control="prompt_cache_options.ttl",
        )
    if provider_name == "azure-openai":
        name = "openai-azure-legacy-v1"
    elif responses_api:
        name = "openai-responses-legacy-v1"
    else:
        name = "openai-chat-legacy-v1"
    return OpenAIPromptCacheProfile(
        name=name,
        modern=False,
        responses_api=responses_api,
        lifetime_control="prompt_cache_retention",
    )


def apply_openai_prompt_cache(
    request: dict[str, Any],
    *,
    cache_key: str,
    ttl_seconds: int,
    profile: OpenAIPromptCacheProfile,
) -> tuple[str, list[str]]:
    """Apply one mutually-exclusive OpenAI cache-control family."""

    request.setdefault("prompt_cache_key", cache_key[:64])
    reasons = ["openai-prompt-cache-key", f"openai-cache-profile:{profile.name}"]
    if profile.modern:
        if "prompt_cache_retention" in request:
            raise ValueError("prompt_cache_retention is incompatible with the GPT-5.6+ Responses cache profile")
        raw_options = request.get("prompt_cache_options")
        if raw_options is None:
            options: dict[str, Any] = {}
        elif isinstance(raw_options, Mapping):
            options = dict(raw_options)
        else:
            raise ValueError("prompt_cache_options must be an object")
        mode = str(options.get("mode") or "implicit")
        if mode not in {"implicit", "explicit"}:
            raise ValueError("prompt_cache_options.mode must be implicit or explicit")
        ttl = str(options.get("ttl") or "30m")
        if ttl != "30m":
            raise ValueError("GPT-5.6+ prompt_cache_options.ttl currently supports only 30m")
        options["mode"] = mode
        options["ttl"] = ttl
        request["prompt_cache_options"] = options
        reasons.extend(("openai-modern-cache-options", "openai-cache-ttl:30m"))
        if int(ttl_seconds) != 1800:
            reasons.append("openai-modern-cache-ttl-normalized-to-30m")
        return "provider-modern-cache-options", reasons

    if "prompt_cache_options" in request:
        raise ValueError("prompt_cache_options requires the GPT-5.6+ Responses cache profile")
    if int(ttl_seconds) >= 86400:
        request.setdefault("prompt_cache_retention", "24h")
        reasons.append("openai-legacy-retention:24h")
    return "provider-explicit-key", reasons


def cache_write_tokens(payload: Mapping[str, Any] | Any) -> int:
    """Return provider-reported cache-write input tokens as a diagnostic subset."""

    if not isinstance(payload, Mapping):
        return 0
    usage = payload.get("usage") if isinstance(payload.get("usage"), Mapping) else payload
    if not isinstance(usage, Mapping):
        return 0
    for details_name in ("input_tokens_details", "prompt_tokens_details"):
        details = usage.get(details_name)
        if not isinstance(details, Mapping):
            continue
        value = details.get("cache_write_tokens")
        if isinstance(value, bool):
            continue
        try:
            return max(0, int(value)) if value is not None else 0
        except (TypeError, ValueError):
            return 0
    return 0


__all__ = [
    "OpenAIPromptCacheProfile",
    "apply_openai_prompt_cache",
    "cache_write_tokens",
    "resolve_openai_prompt_cache_profile",
]
