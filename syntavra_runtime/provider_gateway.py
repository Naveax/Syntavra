from __future__ import annotations

import copy
import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from .competitive_fabric import CacheAligner
from .evidence import EvidenceStore
from .security_scan import scan_text
from .state import StateDB
from .usage_receipt_ledger import UsageReceiptLedger, normalize_provider_usage
from .util import canonical_json, sha256_bytes


_CREDENTIAL_KEYS = {
    "authorization", "api_key", "apikey", "x-api-key", "openai_api_key",
    "anthropic_api_key", "google_api_key", "access_token", "bearer_token",
}
_VOLATILE_REQUEST_KEYS = {
    "request_id", "client_request_id", "trace_id", "span_id", "timestamp",
    "created_at", "updated_at", "idempotency_key",
}


@dataclass(frozen=True)
class ProviderCapabilities:
    provider: str
    aliases: tuple[str, ...]
    implicit_prompt_cache: bool
    explicit_prompt_cache: bool
    prompt_cache_key_field: str
    cache_usage_fields: tuple[str, ...]
    request_family: str


@dataclass(frozen=True)
class ProviderPlan:
    provider: str
    model: str
    request_hash: str
    cache_key: str
    request_handle: str
    stable_prefix_hash: str
    stable_message_count: int
    prompt_cache_mode: str
    replay_cacheable: bool
    replay_hit: bool
    replay_response_handle: str
    prepared_request: dict[str, Any]
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class ProviderCapture:
    provider: str
    model: str
    request_hash: str
    response_hash: str
    response_handle: str
    visible_preview: str
    original_bytes: int
    preview_bytes: int
    replay_stored: bool
    receipt_sequence: int
    normalized_usage: dict[str, Any]
    secret_types: tuple[str, ...]
    injection_risk: bool


_CAPABILITIES = (
    ProviderCapabilities(
        provider="openai",
        aliases=("openai", "chatgpt", "responses", "azure-openai"),
        implicit_prompt_cache=True,
        explicit_prompt_cache=True,
        prompt_cache_key_field="prompt_cache_key",
        cache_usage_fields=(
            "usage.input_tokens_details.cached_tokens",
            "usage.prompt_tokens_details.cached_tokens",
        ),
        request_family="openai",
    ),
    ProviderCapabilities(
        provider="anthropic",
        aliases=("anthropic", "claude", "bedrock-anthropic", "vertex-anthropic"),
        implicit_prompt_cache=False,
        explicit_prompt_cache=True,
        prompt_cache_key_field="cache_control",
        cache_usage_fields=("usage.cache_read_input_tokens", "usage.cache_creation_input_tokens"),
        request_family="anthropic",
    ),
    ProviderCapabilities(
        provider="gemini",
        aliases=("gemini", "google", "google-ai", "vertex-gemini"),
        implicit_prompt_cache=True,
        explicit_prompt_cache=True,
        prompt_cache_key_field="cachedContent",
        cache_usage_fields=(
            "usageMetadata.cachedContentTokenCount",
            "usage.total_cached_tokens",
        ),
        request_family="gemini",
    ),
    ProviderCapabilities(
        provider="openai-compatible",
        aliases=("openrouter", "litellm", "vllm", "ollama", "lmstudio", "openai-compatible"),
        implicit_prompt_cache=False,
        explicit_prompt_cache=False,
        prompt_cache_key_field="",
        cache_usage_fields=("usage.prompt_tokens_details.cached_tokens",),
        request_family="openai",
    ),
)

_ALIAS_MAP = {alias: capabilities for capabilities in _CAPABILITIES for alias in capabilities.aliases}


class ProviderGateway:
    """Provider-neutral request preparation, exact capture, safe replay, and usage accounting.

    Credentials are deliberately transport-only and are rejected from request payloads.
    Replay caching is restricted to deterministic, stream-free, tool-free requests unless
    the caller explicitly opts into tool replay.
    """

    schema_version = 1

    def __init__(
        self,
        path: Path,
        *,
        evidence: EvidenceStore,
        usage_ledger: UsageReceiptLedger,
    ):
        self.state = StateDB(path)
        self.evidence = evidence
        self.usage_ledger = usage_ledger
        self.aligner = CacheAligner()
        with self.state.transaction(immediate=True) as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS provider_request_audit(
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    provider TEXT NOT NULL,
                    model TEXT NOT NULL,
                    request_hash TEXT NOT NULL,
                    cache_key TEXT NOT NULL,
                    request_handle TEXT NOT NULL,
                    prompt_cache_mode TEXT NOT NULL,
                    replay_cacheable INTEGER NOT NULL,
                    created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS provider_request_hash_idx
                    ON provider_request_audit(provider,model,request_hash);
                CREATE TABLE IF NOT EXISTS provider_response_cache(
                    cache_key TEXT PRIMARY KEY,
                    provider TEXT NOT NULL,
                    model TEXT NOT NULL,
                    request_hash TEXT NOT NULL,
                    request_handle TEXT NOT NULL,
                    response_handle TEXT NOT NULL,
                    response_hash TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    expires_at REAL NOT NULL,
                    hit_count INTEGER NOT NULL DEFAULT 0,
                    last_hit_at REAL NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS provider_cache_expiry_idx
                    ON provider_response_cache(expires_at);
                """
            )

    @staticmethod
    def capabilities(provider: str | None = None) -> dict[str, Any]:
        if provider is None:
            return {row.provider: asdict(row) for row in _CAPABILITIES}
        normalized = str(provider).strip().casefold()
        row = _ALIAS_MAP.get(normalized)
        if row is None:
            raise ValueError(f"unsupported provider: {provider}")
        return asdict(row)

    @staticmethod
    def _capabilities(provider: str) -> ProviderCapabilities:
        normalized = str(provider).strip().casefold()
        row = _ALIAS_MAP.get(normalized)
        if row is None:
            raise ValueError(f"unsupported provider: {provider}")
        return row

    @staticmethod
    def _reject_credentials(value: Any, path: str = "request") -> None:
        normalized_credentials = {item.replace("_", "-") for item in _CREDENTIAL_KEYS}
        if isinstance(value, Mapping):
            for key, child in value.items():
                name = str(key).casefold().replace("_", "-")
                if name in normalized_credentials:
                    raise ValueError(f"credential field is transport-only: {path}.{key}")
                ProviderGateway._reject_credentials(child, f"{path}.{key}")
        elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            for index, child in enumerate(value):
                ProviderGateway._reject_credentials(child, f"{path}[{index}]")

    @staticmethod
    def _stable_copy(value: Any) -> Any:
        if isinstance(value, Mapping):
            return {
                str(key): ProviderGateway._stable_copy(child)
                for key, child in sorted(value.items(), key=lambda item: str(item[0]))
                if str(key) not in _VOLATILE_REQUEST_KEYS and not str(key).startswith("_")
            }
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            return [ProviderGateway._stable_copy(child) for child in value]
        return value

    @staticmethod
    def _message_sequence(request: Mapping[str, Any], family: str) -> list[Mapping[str, Any]]:
        if family == "gemini":
            contents = request.get("contents") or []
            return [item for item in contents if isinstance(item, Mapping)]
        messages = request.get("messages") or request.get("input") or []
        if isinstance(messages, str):
            return [{"role": "user", "content": messages}]
        if isinstance(messages, Sequence):
            return [item for item in messages if isinstance(item, Mapping)]
        return []

    @staticmethod
    def _has_tools(request: Mapping[str, Any]) -> bool:
        tools = request.get("tools") or request.get("functions")
        if not tools:
            return False
        choice = request.get("tool_choice") or request.get("toolConfig") or request.get("tool_config")
        return choice not in ("none", {"type": "none"}, {"function_calling_config": {"mode": "NONE"}})

    @staticmethod
    def _is_streaming(request: Mapping[str, Any]) -> bool:
        return bool(request.get("stream") or request.get("streaming"))

    @staticmethod
    def _temperature(request: Mapping[str, Any]) -> float:
        raw = request.get("temperature")
        if raw is None and isinstance(request.get("generationConfig"), Mapping):
            raw = request["generationConfig"].get("temperature")
        if raw is None and isinstance(request.get("generation_config"), Mapping):
            raw = request["generation_config"].get("temperature")
        try:
            return float(raw) if raw is not None else 0.0
        except (TypeError, ValueError):
            return 1.0

    @staticmethod
    def _apply_anthropic_cache_control(request: dict[str, Any], ttl_seconds: int) -> bool:
        system = request.get("system")
        marker: dict[str, Any] = {"type": "ephemeral"}
        if ttl_seconds >= 3600:
            marker["ttl"] = "1h"
        if isinstance(system, str) and system:
            request["system"] = [{"type": "text", "text": system, "cache_control": marker}]
            return True
        if isinstance(system, list) and system:
            blocks = copy.deepcopy(system)
            for index in range(len(blocks) - 1, -1, -1):
                if isinstance(blocks[index], Mapping):
                    blocks[index] = {**dict(blocks[index]), "cache_control": marker}
                    request["system"] = blocks
                    return True
        messages = request.get("messages")
        if isinstance(messages, list) and messages:
            prepared = copy.deepcopy(messages)
            first = prepared[0]
            if isinstance(first, Mapping):
                content = first.get("content")
                if isinstance(content, str):
                    prepared[0] = {
                        **dict(first),
                        "content": [{"type": "text", "text": content, "cache_control": marker}],
                    }
                    request["messages"] = prepared
                    return True
                if isinstance(content, list) and content:
                    blocks = copy.deepcopy(content)
                    if isinstance(blocks[-1], Mapping):
                        blocks[-1] = {**dict(blocks[-1]), "cache_control": marker}
                        prepared[0] = {**dict(first), "content": blocks}
                        request["messages"] = prepared
                        return True
        return False

    @staticmethod
    def _apply_prompt_cache(
        capabilities: ProviderCapabilities,
        request: dict[str, Any],
        cache_key: str,
        ttl_seconds: int,
        explicit_cache_name: str,
    ) -> tuple[str, list[str]]:
        reasons: list[str] = []
        if capabilities.provider == "openai":
            request.setdefault("prompt_cache_key", cache_key[:64])
            if ttl_seconds >= 86400:
                request.setdefault("prompt_cache_retention", "24h")
            return "provider-explicit-key", ["openai-prompt-cache-key"]
        if capabilities.provider == "anthropic":
            if ProviderGateway._apply_anthropic_cache_control(request, ttl_seconds):
                return "provider-explicit-breakpoint", ["anthropic-cache-control"]
            return "provider-cache-unavailable", ["no-cacheable-anthropic-prefix"]
        if capabilities.provider == "gemini":
            if explicit_cache_name:
                request["cachedContent"] = explicit_cache_name
                return "provider-explicit-resource", ["gemini-cached-content"]
            return "provider-implicit-prefix", ["gemini-implicit-cache-stable-prefix"]
        reasons.append("provider-has-no-declared-prompt-cache-control")
        return "stable-prefix-only", reasons

    def _lookup(self, cache_key: str) -> str:
        now = time.time()
        with self.state.transaction(immediate=True) as db:
            row = db.execute(
                "SELECT response_handle FROM provider_response_cache WHERE cache_key=? AND expires_at>?",
                (cache_key, now),
            ).fetchone()
            if row is None:
                db.execute("DELETE FROM provider_response_cache WHERE expires_at<=?", (now,))
                return ""
            db.execute(
                "UPDATE provider_response_cache SET hit_count=hit_count+1,last_hit_at=? WHERE cache_key=?",
                (now, cache_key),
            )
            return str(row["response_handle"])

    def prepare(
        self,
        provider: str,
        request: Mapping[str, Any],
        *,
        model: str = "",
        cache_policy: str = "auto",
        replay_ttl_seconds: int = 900,
        prompt_cache_ttl_seconds: int = 300,
        explicit_cache_name: str = "",
        allow_tool_replay: bool = False,
    ) -> ProviderPlan:
        capabilities = self._capabilities(provider)
        if cache_policy not in {"off", "auto", "read", "read-write"}:
            raise ValueError("cache_policy must be off, auto, read, or read-write")
        if replay_ttl_seconds < 1:
            raise ValueError("replay_ttl_seconds must be positive")
        self._reject_credentials(request)
        prepared = copy.deepcopy(dict(request))
        resolved_model = str(model or prepared.get("model") or "unknown")
        messages = self._message_sequence(prepared, capabilities.request_family)
        alignment = self.aligner.align(messages, keep_tail=1 if messages else 0)
        stable_request = self._stable_copy(prepared)
        request_hash = sha256_bytes(canonical_json(stable_request))
        cache_key = sha256_bytes(canonical_json({
            "schema": self.schema_version,
            "provider": capabilities.provider,
            "model": resolved_model,
            "request": stable_request,
        }))
        reasons: list[str] = []
        prompt_cache_mode = "disabled"
        if cache_policy != "off":
            prompt_cache_mode, prompt_reasons = self._apply_prompt_cache(
                capabilities,
                prepared,
                cache_key,
                max(0, int(prompt_cache_ttl_seconds)),
                explicit_cache_name,
            )
            reasons.extend(prompt_reasons)
        has_tools = self._has_tools(prepared)
        stream = self._is_streaming(prepared)
        deterministic = self._temperature(prepared) <= 0.0
        replay_cacheable = cache_policy != "off" and deterministic and not stream and (allow_tool_replay or not has_tools)
        if not deterministic:
            reasons.append("response-replay-disabled-temperature")
        if stream:
            reasons.append("response-replay-disabled-stream")
        if has_tools and not allow_tool_replay:
            reasons.append("response-replay-disabled-tools")
        request_bytes = canonical_json(dict(request))
        request_handle = self.evidence.put(
            request_bytes,
            kind="provider-request",
            metadata={
                "provider": capabilities.provider,
                "model": resolved_model,
                "request_hash": request_hash,
                "cache_key": cache_key,
            },
        )
        replay_handle = self._lookup(cache_key) if replay_cacheable and cache_policy in {"auto", "read", "read-write"} else ""
        if replay_handle:
            reasons.append("exact-response-replay-hit")
        with self.state.transaction(immediate=True) as db:
            db.execute(
                "INSERT INTO provider_request_audit(provider,model,request_hash,cache_key,request_handle,prompt_cache_mode,replay_cacheable,created_at) VALUES(?,?,?,?,?,?,?,?)",
                (
                    capabilities.provider,
                    resolved_model,
                    request_hash,
                    cache_key,
                    request_handle,
                    prompt_cache_mode,
                    int(replay_cacheable),
                    time.time(),
                ),
            )
        return ProviderPlan(
            provider=capabilities.provider,
            model=resolved_model,
            request_hash=request_hash,
            cache_key=cache_key,
            request_handle=request_handle,
            stable_prefix_hash=alignment.prefix_hash,
            stable_message_count=alignment.stable_message_count,
            prompt_cache_mode=prompt_cache_mode,
            replay_cacheable=replay_cacheable,
            replay_hit=bool(replay_handle),
            replay_response_handle=replay_handle,
            prepared_request=prepared,
            reasons=tuple(dict.fromkeys(reasons)),
        )

    def replay(self, plan_or_cache_key: ProviderPlan | str) -> dict[str, Any] | None:
        if isinstance(plan_or_cache_key, ProviderPlan):
            handle = plan_or_cache_key.replay_response_handle or self._lookup(plan_or_cache_key.cache_key)
        else:
            handle = self._lookup(str(plan_or_cache_key))
        if not handle:
            return None
        return json.loads(self.evidence.get(handle).decode("utf-8"))

    @staticmethod
    def _contains_tool_call(value: Any) -> bool:
        if isinstance(value, Mapping):
            for key, child in value.items():
                if str(key).casefold() in {"tool_calls", "tool_call", "function_call", "functioncall"} and child:
                    return True
                if ProviderGateway._contains_tool_call(child):
                    return True
        elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            return any(ProviderGateway._contains_tool_call(child) for child in value)
        return False

    @staticmethod
    def _collect_text(value: Any, output: list[str], depth: int = 0) -> None:
        if depth > 8:
            return
        if isinstance(value, str):
            if value.strip():
                output.append(value)
            return
        if isinstance(value, Mapping):
            preferred = ("output_text", "text", "content")
            for key in preferred:
                child = value.get(key)
                if isinstance(child, str):
                    ProviderGateway._collect_text(child, output, depth + 1)
            for key, child in value.items():
                if key in preferred or key in {"usage", "usageMetadata", "metadata", "id", "model"}:
                    continue
                ProviderGateway._collect_text(child, output, depth + 1)
            return
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            for child in value:
                ProviderGateway._collect_text(child, output, depth + 1)

    def capture(
        self,
        plan: ProviderPlan | Mapping[str, Any],
        response: Mapping[str, Any],
        *,
        store_replay: bool = True,
        replay_ttl_seconds: int = 900,
        preview_bytes: int = 4096,
        receipt: Mapping[str, Any] | None = None,
    ) -> ProviderCapture:
        if not isinstance(plan, ProviderPlan):
            plan = ProviderPlan(**dict(plan))
        response_data = dict(response)
        raw = canonical_json(response_data)
        response_hash = sha256_bytes(raw)
        response_handle = self.evidence.put(
            raw,
            kind="provider-response",
            metadata={
                "provider": plan.provider,
                "model": plan.model,
                "request_hash": plan.request_hash,
                "response_hash": response_hash,
            },
        )
        texts: list[str] = []
        self._collect_text(response_data, texts)
        normalized_text = "\n".join(dict.fromkeys(texts))
        security = scan_text(normalized_text)
        visible_raw = security.redacted_text.encode("utf-8")
        if len(visible_raw) > preview_bytes:
            marker = "\n[… exact provider response stored as evidence …]"
            keep = max(0, int(preview_bytes) - len(marker.encode("utf-8")))
            visible = visible_raw[:keep].decode("utf-8", errors="ignore").rstrip() + marker
        else:
            visible = security.redacted_text
        replay_stored = False
        if store_replay and plan.replay_cacheable and not self._contains_tool_call(response_data):
            now = time.time()
            with self.state.transaction(immediate=True) as db:
                db.execute(
                    """
                    INSERT INTO provider_response_cache(
                        cache_key,provider,model,request_hash,request_handle,response_handle,response_hash,
                        created_at,expires_at,hit_count,last_hit_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,0,0)
                    ON CONFLICT(cache_key) DO UPDATE SET
                        response_handle=excluded.response_handle,
                        response_hash=excluded.response_hash,
                        created_at=excluded.created_at,
                        expires_at=excluded.expires_at
                    """,
                    (
                        plan.cache_key,
                        plan.provider,
                        plan.model,
                        plan.request_hash,
                        plan.request_handle,
                        response_handle,
                        response_hash,
                        now,
                        now + max(1, int(replay_ttl_seconds)),
                    ),
                )
            replay_stored = True
        normalized_usage: dict[str, Any] = {}
        try:
            normalized_usage = asdict(normalize_provider_usage(plan.provider, response_data))
        except ValueError:
            pass
        receipt_sequence = 0
        if receipt:
            request_id = str(receipt.get("request_id") or response_data.get("id") or plan.request_hash)
            quota_cost = float(receipt.get("quota_cost") or 0.0)
            hardware_hash = str(receipt.get("hardware_hash") or "")
            if quota_cost > 0 and len(hardware_hash) == 64:
                entry = self.usage_ledger.record(
                    task_id=str(receipt.get("task_id") or "provider-task"),
                    arm_id=str(receipt.get("arm_id") or "syntavra-provider-gateway"),
                    repetition=max(1, int(receipt.get("repetition") or 1)),
                    cache_mode=str(receipt.get("cache_mode") or plan.prompt_cache_mode),
                    provider=plan.provider,
                    request_id=request_id,
                    provider_response=response_data,
                    usage_payload=response_data.get("usage") or response_data.get("usageMetadata") or response_data,
                    quota_cost=quota_cost,
                    hardware_hash=hardware_hash,
                )
                receipt_sequence = entry.sequence
        return ProviderCapture(
            provider=plan.provider,
            model=plan.model,
            request_hash=plan.request_hash,
            response_hash=response_hash,
            response_handle=response_handle,
            visible_preview=visible,
            original_bytes=len(raw),
            preview_bytes=len(visible.encode("utf-8")),
            replay_stored=replay_stored,
            receipt_sequence=receipt_sequence,
            normalized_usage=normalized_usage,
            secret_types=security.secret_types,
            injection_risk=security.injection_risk,
        )

    def stats(self) -> dict[str, Any]:
        now = time.time()
        with self.state.read() as db:
            requests = int(db.execute("SELECT COUNT(*) FROM provider_request_audit").fetchone()[0])
            cache_rows = db.execute(
                "SELECT COUNT(*),COALESCE(SUM(hit_count),0),COALESCE(SUM(CASE WHEN expires_at>? THEN 1 ELSE 0 END),0) FROM provider_response_cache",
                (now,),
            ).fetchone()
            providers = {
                str(row[0]): int(row[1])
                for row in db.execute("SELECT provider,COUNT(*) FROM provider_request_audit GROUP BY provider ORDER BY provider")
            }
        return {
            "requests": requests,
            "cache_entries": int(cache_rows[0]),
            "replay_hits": int(cache_rows[1]),
            "active_cache_entries": int(cache_rows[2]),
            "providers": providers,
            "database_integrity": self.state.integrity_check(),
        }

    def verify(self) -> dict[str, Any]:
        reasons: list[str] = []
        with self.state.read() as db:
            rows = [dict(row) for row in db.execute("SELECT * FROM provider_response_cache ORDER BY cache_key")]
        for row in rows:
            if not self.evidence.verify(str(row["request_handle"])):
                reasons.append(f"request-evidence:{row['cache_key']}")
            if not self.evidence.verify(str(row["response_handle"])):
                reasons.append(f"response-evidence:{row['cache_key']}")
                continue
            actual = sha256_bytes(self.evidence.get(str(row["response_handle"])))
            if actual != str(row["response_hash"]):
                reasons.append(f"response-hash:{row['cache_key']}")
        return {
            "ok": not reasons and self.state.integrity_check(),
            "entries": len(rows),
            "reasons": reasons,
            "database_integrity": self.state.integrity_check(),
        }
