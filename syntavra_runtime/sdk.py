from __future__ import annotations

import inspect
from dataclasses import asdict, dataclass, is_dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping, Protocol, runtime_checkable

from .evidence import EvidenceStore
from .provider_gateway import ProviderCapture, ProviderGateway, ProviderPlan
from .usage_receipt_ledger import UsageReceiptLedger
from .util import stable_project_id


@runtime_checkable
class SyncTransport(Protocol):
    def __call__(self, request: Mapping[str, Any]) -> Any: ...


@runtime_checkable
class AsyncTransport(Protocol):
    def __call__(self, request: Mapping[str, Any]) -> Awaitable[Any]: ...


@dataclass(frozen=True)
class SDKInvocation:
    provider: str
    model: str
    request_hash: str
    cache_key: str
    replayed: bool
    response: dict[str, Any]
    capture: ProviderCapture | None
    reasons: tuple[str, ...]


def _mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if is_dataclass(value):
        return asdict(value)
    for name in ("model_dump", "dict", "to_dict"):
        method = getattr(value, name, None)
        if callable(method):
            result = method()
            if isinstance(result, Mapping):
                return dict(result)
    if hasattr(value, "__dict__"):
        return {
            str(key): item
            for key, item in vars(value).items()
            if not str(key).startswith("_")
        }
    raise TypeError("provider response must be mapping-like")


class SyntavraClient:
    """Dependency-free SDK facade around Syntavra's exact provider gateway.

    The caller supplies the provider SDK transport. Syntavra owns request
    stabilization, safe deterministic replay, exact evidence, preview redaction,
    usage normalization, and optional attested receipts.
    """

    def __init__(
        self,
        state_root: Path | str,
        *,
        project: Path | str = ".",
        project_id: str | None = None,
        signing_key: bytes | None = None,
    ):
        self.state_root = Path(state_root).resolve(strict=False)
        self.state_root.mkdir(parents=True, exist_ok=True)
        project_path = Path(project).resolve(strict=False)
        resolved_project_id = project_id or stable_project_id(project_path)
        self.evidence = EvidenceStore(self.state_root / "evidence", project_id=resolved_project_id)
        self.usage_ledger = UsageReceiptLedger(
            self.state_root / "usage-receipts.sqlite3",
            signing_key=signing_key,
        )
        self.gateway = ProviderGateway(
            self.state_root / "provider-gateway.sqlite3",
            evidence=self.evidence,
            usage_ledger=self.usage_ledger,
        )

    def prepare(
        self,
        provider: str,
        request: Mapping[str, Any],
        **options: Any,
    ) -> ProviderPlan:
        return self.gateway.prepare(provider, request, **options)

    def capture(
        self,
        plan: ProviderPlan | Mapping[str, Any],
        response: Any,
        **options: Any,
    ) -> ProviderCapture:
        return self.gateway.capture(plan, _mapping(response), **options)

    def invoke(
        self,
        provider: str,
        request: Mapping[str, Any],
        transport: SyncTransport,
        *,
        model: str = "",
        cache_policy: str = "auto",
        replay_ttl_seconds: int = 900,
        prompt_cache_ttl_seconds: int = 300,
        explicit_cache_name: str = "",
        allow_tool_replay: bool = False,
        store_replay: bool = True,
        preview_bytes: int = 4096,
        receipt: Mapping[str, Any] | None = None,
    ) -> SDKInvocation:
        plan = self.gateway.prepare(
            provider,
            request,
            model=model,
            cache_policy=cache_policy,
            replay_ttl_seconds=replay_ttl_seconds,
            prompt_cache_ttl_seconds=prompt_cache_ttl_seconds,
            explicit_cache_name=explicit_cache_name,
            allow_tool_replay=allow_tool_replay,
        )
        replayed = self.gateway.replay(plan)
        if replayed is not None:
            return SDKInvocation(
                plan.provider,
                plan.model,
                plan.request_hash,
                plan.cache_key,
                True,
                replayed,
                None,
                tuple((*plan.reasons, "sdk-transport-bypassed-by-exact-replay")),
            )
        raw_response = transport(plan.prepared_request)
        if inspect.isawaitable(raw_response):
            raise TypeError("sync invoke received an awaitable transport result; use ainvoke")
        response = _mapping(raw_response)
        capture = self.gateway.capture(
            plan,
            response,
            store_replay=store_replay,
            replay_ttl_seconds=replay_ttl_seconds,
            preview_bytes=preview_bytes,
            receipt=receipt,
        )
        return SDKInvocation(
            plan.provider,
            plan.model,
            plan.request_hash,
            plan.cache_key,
            False,
            response,
            capture,
            plan.reasons,
        )

    async def ainvoke(
        self,
        provider: str,
        request: Mapping[str, Any],
        transport: AsyncTransport | SyncTransport,
        **options: Any,
    ) -> SDKInvocation:
        model = str(options.pop("model", ""))
        cache_policy = str(options.pop("cache_policy", "auto"))
        replay_ttl_seconds = int(options.pop("replay_ttl_seconds", 900))
        prompt_cache_ttl_seconds = int(options.pop("prompt_cache_ttl_seconds", 300))
        explicit_cache_name = str(options.pop("explicit_cache_name", ""))
        allow_tool_replay = bool(options.pop("allow_tool_replay", False))
        store_replay = bool(options.pop("store_replay", True))
        preview_bytes = int(options.pop("preview_bytes", 4096))
        receipt = options.pop("receipt", None)
        if options:
            raise TypeError(f"unknown ainvoke options: {', '.join(sorted(options))}")

        plan = self.gateway.prepare(
            provider,
            request,
            model=model,
            cache_policy=cache_policy,
            replay_ttl_seconds=replay_ttl_seconds,
            prompt_cache_ttl_seconds=prompt_cache_ttl_seconds,
            explicit_cache_name=explicit_cache_name,
            allow_tool_replay=allow_tool_replay,
        )
        replayed = self.gateway.replay(plan)
        if replayed is not None:
            return SDKInvocation(
                plan.provider,
                plan.model,
                plan.request_hash,
                plan.cache_key,
                True,
                replayed,
                None,
                tuple((*plan.reasons, "sdk-transport-bypassed-by-exact-replay")),
            )
        result = transport(plan.prepared_request)
        raw_response = await result if inspect.isawaitable(result) else result
        response = _mapping(raw_response)
        capture = self.gateway.capture(
            plan,
            response,
            store_replay=store_replay,
            replay_ttl_seconds=replay_ttl_seconds,
            preview_bytes=preview_bytes,
            receipt=receipt,
        )
        return SDKInvocation(
            plan.provider,
            plan.model,
            plan.request_hash,
            plan.cache_key,
            False,
            response,
            capture,
            plan.reasons,
        )

    def wrap(
        self,
        provider: str,
        transport: SyncTransport,
        **defaults: Any,
    ) -> Callable[[Mapping[str, Any]], SDKInvocation]:
        def wrapped(request: Mapping[str, Any], **overrides: Any) -> SDKInvocation:
            return self.invoke(provider, request, transport, **{**defaults, **overrides})
        return wrapped

    def stats(self) -> dict[str, Any]:
        return {
            "gateway": self.gateway.stats(),
            "usage_ledger": self.usage_ledger.verify(require_hmac=False),
        }

    def verify(self, *, require_hmac: bool = False) -> dict[str, Any]:
        gateway = self.gateway.verify()
        ledger = self.usage_ledger.verify(require_hmac=require_hmac)
        return {
            "ok": bool(gateway["ok"] and ledger["ok"]),
            "gateway": gateway,
            "usage_ledger": ledger,
        }
