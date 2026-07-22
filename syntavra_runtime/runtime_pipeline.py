from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Mapping, Protocol

from .unified_config import ConfigManager, ConfigSnapshot
from .data_router import DataRoutePolicy, DataRouteResult, DataRouter
from .evidence import EvidenceStore
from .identity import Authorizer, Principal
from .observability import Observability
from .security_scan import scan_text
from .util import canonical_json, sha256_bytes


class PipelineError(RuntimeError):
    pass


@dataclass(frozen=True)
class RequestIdentity:
    request_id: str
    project_id: str
    host: str
    provider: str
    model: str
    task_id: str = ""
    session_id: str = ""
    tenant: str = "local"


@dataclass(frozen=True)
class CanonicalRequestEnvelope:
    schema_version: int
    identity: RequestIdentity
    payload: Mapping[str, Any]
    query: str = ""
    content_hint: str = ""
    policy_overrides: Mapping[str, Any] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)
    created_at: float = 0.0

    @classmethod
    def create(
        cls,
        *,
        project_id: str,
        host: str,
        provider: str,
        model: str,
        payload: Mapping[str, Any],
        task_id: str = "",
        session_id: str = "",
        query: str = "",
        content_hint: str = "",
        policy_overrides: Mapping[str, Any] | None = None,
        metadata: Mapping[str, Any] | None = None,
        request_id: str = "",
    ) -> "CanonicalRequestEnvelope":
        return cls(
            schema_version=1,
            identity=RequestIdentity(
                request_id=request_id or "req-" + uuid.uuid4().hex,
                project_id=project_id,
                host=host,
                provider=provider,
                model=model,
                task_id=task_id,
                session_id=session_id,
            ),
            payload=dict(payload),
            query=query,
            content_hint=content_hint,
            policy_overrides=dict(policy_overrides or {}),
            metadata=dict(metadata or {}),
            created_at=time.time(),
        )

    def validate(self) -> None:
        if self.schema_version != 1:
            raise PipelineError("unsupported canonical request schema")
        if not self.identity.request_id.startswith("req-") or len(self.identity.request_id) > 128:
            raise PipelineError("invalid request id")
        if not self.identity.project_id or not self.identity.provider:
            raise PipelineError("project and provider identity are required")
        if not isinstance(self.payload, Mapping):
            raise PipelineError("canonical request payload must be an object")


@dataclass(frozen=True)
class CanonicalResponseEnvelope:
    schema_version: int
    request_id: str
    status: str
    raw_handle: str
    raw_hash: str
    routed: Mapping[str, Any]
    config_hash: str
    policy_hash: str
    security: Mapping[str, Any]
    timings_ms: Mapping[str, float]
    trace_id: str
    created_at: float


@dataclass
class PipelineContext:
    request: CanonicalRequestEnvelope
    principal: Principal
    config: ConfigSnapshot
    policy: dict[str, Any]
    request_handle: str = ""
    response_handle: str = ""
    response_hash: str = ""
    raw_response: Any = None
    route: DataRouteResult | None = None
    security: dict[str, Any] = field(default_factory=dict)
    timings: dict[str, float] = field(default_factory=dict)


class ProviderInvoker(Protocol):
    def __call__(self, request: Mapping[str, Any], context: PipelineContext) -> Any: ...


class PipelineHook(Protocol):
    def __call__(self, context: PipelineContext) -> None: ...


class UnifiedRuntimePipeline:
    """One canonical execution path for host, provider and output surfaces.

    The pipeline is dependency-injected so existing provider transports remain usable.
    Every successful call follows the same identity/config/security/evidence/routing
    order and emits one typed response envelope.
    """

    STAGES = (
        "validate", "authorize", "configure", "request-security", "request-evidence",
        "context", "provider", "response-evidence", "response-security", "data-route", "deliver",
    )

    def __init__(
        self,
        *,
        evidence: EvidenceStore,
        config: ConfigManager,
        observability: Observability,
        authorizer: Authorizer | None = None,
        data_router: DataRouter | None = None,
        hooks: Mapping[str, tuple[PipelineHook, ...]] | None = None,
    ):
        self.evidence = evidence
        self.config_manager = config
        self.observability = observability
        self.authorizer = authorizer or Authorizer()
        self.data_router = data_router or DataRouter(evidence)
        self.hooks = {str(key): tuple(value) for key, value in (hooks or {}).items()}

    def execute(
        self,
        envelope: CanonicalRequestEnvelope,
        principal: Principal,
        provider: ProviderInvoker,
    ) -> CanonicalResponseEnvelope:
        envelope.validate()
        started = time.perf_counter()
        with self.observability.span("runtime.pipeline", attributes={
            "request_id": envelope.identity.request_id,
            "provider": envelope.identity.provider,
            "host": envelope.identity.host,
        }) as span:
            config = self._time("configure", lambda: self.config_manager.load(task=envelope.policy_overrides))
            policy = self._policy(config, envelope)
            context = PipelineContext(envelope, principal, config, policy)
            self._stage("validate", context, lambda: envelope.validate())
            self._stage("authorize", context, lambda: self.authorizer.require(principal, "provider.invoke"))
            self._stage("request-security", context, lambda: self._scan_request(context))
            self._stage("request-evidence", context, lambda: self._capture_request(context))
            self._run_hooks("context", context)
            self._stage("provider", context, lambda: self._invoke(context, provider))
            self._stage("response-evidence", context, lambda: self._capture_response(context))
            self._stage("response-security", context, lambda: self._scan_response(context))
            self._stage("data-route", context, lambda: self._route(context))
            self._run_hooks("deliver", context)
            span.attributes.update({
                "request_handle": context.request_handle,
                "response_handle": context.response_handle,
                "route": context.route.route if context.route else "",
            })
            context.timings["total"] = (time.perf_counter() - started) * 1000
            route = context.route
            if route is None:
                raise PipelineError("pipeline completed without routed response")
            result = CanonicalResponseEnvelope(
                schema_version=1,
                request_id=envelope.identity.request_id,
                status="ok",
                raw_handle=context.response_handle,
                raw_hash=context.response_hash,
                routed={
                    "family": route.family,
                    "route": route.route,
                    "visible": route.visible,
                    "visible_bytes": route.visible_bytes,
                    "original_bytes": route.original_bytes,
                    "reduction_ratio": route.reduction_ratio,
                    "limitations": list(route.limitations),
                },
                config_hash=config.config_hash,
                policy_hash=sha256_bytes(canonical_json(policy)),
                security=dict(context.security),
                timings_ms=dict(context.timings),
                trace_id=span.context.trace_id,
                created_at=time.time(),
            )
            self.observability.metrics.inc("pipeline_requests_total", labels={"status": "ok"})
            self.observability.metrics.observe("pipeline_total_ms", context.timings["total"])
            return result

    def describe(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "canonical": True,
            "stages": list(self.STAGES),
            "fail_closed": True,
            "exact_evidence": True,
            "typed_delivery": True,
        }

    def _stage(self, name: str, context: PipelineContext, operation: Callable[[], Any]) -> Any:
        started = time.perf_counter()
        try:
            self._run_hooks("before:" + name, context)
            result = operation()
            self._run_hooks("after:" + name, context)
            return result
        except Exception:
            self.observability.metrics.inc("pipeline_stage_errors_total", labels={"stage": name})
            raise
        finally:
            context.timings[name] = (time.perf_counter() - started) * 1000

    @staticmethod
    def _time(name: str, operation: Callable[[], Any]) -> Any:
        del name
        return operation()

    def _run_hooks(self, name: str, context: PipelineContext) -> None:
        for hook in self.hooks.get(name, ()):
            hook(context)

    @staticmethod
    def _policy(config: ConfigSnapshot, envelope: CanonicalRequestEnvelope) -> dict[str, Any]:
        values = dict(config.values)
        routing = dict(values.get("routing") or {})
        security = dict(values.get("security") or {})
        return {
            "runtime": dict(values.get("runtime") or {}),
            "routing": routing,
            "security": security,
            "provider": dict(values.get("provider") or {}),
            "request_overrides": dict(envelope.policy_overrides),
        }

    @staticmethod
    def _scan_request(context: PipelineContext) -> None:
        text = canonical_json(dict(context.request.payload)).decode("utf-8", errors="replace")
        result = scan_text(text)
        context.security.update({
            "request_secret_types": list(result.secret_types),
            "request_injection_risk": result.injection_risk,
        })
        if result.secret_types:
            raise PipelineError("provider credentials or secrets are forbidden in canonical request payloads")

    def _capture_request(self, context: PipelineContext) -> None:
        context.request_handle = self.evidence.put(
            canonical_json(asdict(context.request)),
            kind="canonical-request",
            metadata={
                "request_id": context.request.identity.request_id,
                "provider": context.request.identity.provider,
                "model": context.request.identity.model,
                "host": context.request.identity.host,
            },
        )

    @staticmethod
    def _invoke(context: PipelineContext, provider: ProviderInvoker) -> None:
        context.raw_response = provider(context.request.payload, context)

    def _capture_response(self, context: PipelineContext) -> None:
        raw = context.raw_response if isinstance(context.raw_response, bytes) else (
            context.raw_response.encode("utf-8") if isinstance(context.raw_response, str)
            else canonical_json(context.raw_response)
        )
        context.response_hash = sha256_bytes(raw)
        context.response_handle = self.evidence.put(
            raw,
            kind="canonical-response",
            metadata={
                "request_id": context.request.identity.request_id,
                "request_handle": context.request_handle,
                "provider": context.request.identity.provider,
                "model": context.request.identity.model,
                "response_hash": context.response_hash,
            },
        )

    @staticmethod
    def _scan_response(context: PipelineContext) -> None:
        raw = context.raw_response if isinstance(context.raw_response, str) else (
            context.raw_response.decode("utf-8", errors="replace") if isinstance(context.raw_response, bytes)
            else json.dumps(context.raw_response, ensure_ascii=False, default=str)
        )
        result = scan_text(raw)
        context.security.update({
            "response_secret_types": list(result.secret_types),
            "response_injection_risk": result.injection_risk,
        })

    def _route(self, context: PipelineContext) -> None:
        routing = context.policy.get("routing") or {}
        table = routing.get("table") or {}
        context.route = self.data_router.route(
            context.raw_response,
            hint=context.request.content_hint,
            query=context.request.query,
            policy=DataRoutePolicy(
                budget_bytes=int(routing.get("budget_bytes") or 8192),
                max_rows=int(table.get("max_rows") or 8),
                max_columns=int(table.get("max_columns") or 12),
            ),
        )
