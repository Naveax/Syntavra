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
