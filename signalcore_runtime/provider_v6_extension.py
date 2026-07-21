from __future__ import annotations

import os
from dataclasses import replace
from typing import Any, Mapping

from .provider_registry import cache_identity
from .util import canonical_json, sha256_bytes


def install() -> None:
    from .provider_gateway import ProviderGateway

    if getattr(ProviderGateway, "_signalcore_v6_cache_identity", False):
        return
    original_prepare = ProviderGateway.prepare

    def prepare(
        self: Any,
        provider: str,
        request: Mapping[str, Any],
        *,
        model: str = "",
        cache_policy: str = "auto",
        replay_ttl_seconds: int = 900,
        prompt_cache_ttl_seconds: int = 300,
        explicit_cache_name: str = "",
        allow_tool_replay: bool = False,
        cache_identity_fields: Mapping[str, Any] | None = None,
    ) -> Any:
        resolved_model = str(model or request.get("model") or "unknown")
        fields = cache_identity(
            provider=provider,
            model=resolved_model,
            revision=str((cache_identity_fields or {}).get("revision") or os.environ.get("SIGNALCORE_MODEL_REVISION", "unknown")),
            endpoint_version=str((cache_identity_fields or {}).get("endpoint_version") or os.environ.get("SIGNALCORE_ENDPOINT_VERSION", "")),
            region=str((cache_identity_fields or {}).get("region") or os.environ.get("SIGNALCORE_PROVIDER_REGION", "")),
            system_fingerprint=str((cache_identity_fields or {}).get("system_fingerprint") or ""),
            tool_implementation_hash=str((cache_identity_fields or {}).get("tool_implementation_hash") or os.environ.get("SIGNALCORE_TOOL_IMPLEMENTATION_HASH", "")),
            security_policy_hash=str((cache_identity_fields or {}).get("security_policy_hash") or os.environ.get("SIGNALCORE_SECURITY_POLICY_HASH", "")),
            runtime_version=str((cache_identity_fields or {}).get("runtime_version") or os.environ.get("SIGNALCORE_RUNTIME_VERSION", "0.6.0")),
        )
        enriched = dict(request)
        enriched["signalcore_cache_identity"] = fields
        plan = original_prepare(
            self,
            provider,
            enriched,
            model=model,
            cache_policy=cache_policy,
            replay_ttl_seconds=replay_ttl_seconds,
            prompt_cache_ttl_seconds=prompt_cache_ttl_seconds,
            explicit_cache_name=explicit_cache_name,
            allow_tool_replay=allow_tool_replay,
        )
        prepared = dict(plan.prepared_request)
        prepared.pop("signalcore_cache_identity", None)
        fingerprint = sha256_bytes(canonical_json(fields))
        return replace(
            plan,
            prepared_request=prepared,
            reasons=tuple(dict.fromkeys((*plan.reasons, "cache-identity:" + fingerprint))),
        )

    ProviderGateway.prepare = prepare
    ProviderGateway._signalcore_v6_cache_identity = True
