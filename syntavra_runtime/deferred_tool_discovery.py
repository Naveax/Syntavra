from __future__ import annotations

import json
import re
from collections import OrderedDict
from dataclasses import asdict, dataclass, field
from typing import Any, Mapping, Sequence

from .mcp_policy import MCPToolPolicy
from .tool_registry import MCP_PROFILES, ToolSchemaCompiler, normalize_profile
from .util import canonical_json, sha256_bytes


HEALTH_STATES = ("healthy", "degraded", "unavailable")
_WORD_RE = re.compile(r"[a-z0-9]+")
_ACTION_WORDS = {
    "read", "show", "inspect", "search", "find", "run", "execute", "call", "fetch",
    "open", "write", "edit", "apply", "install", "verify", "status", "map", "route",
    "query", "reveal", "expand", "compress", "sandbox", "provider", "session", "memory",
}
_NO_TOOL_PHRASES = (
    "no tool", "without tools", "reason only", "explain concept", "conceptual explanation",
    "just explain", "answer from context",
)


def _tokens(value: str) -> tuple[str, ...]:
    return tuple(_WORD_RE.findall(str(value).casefold()))


def _canonical_hash(value: Any) -> str:
    return sha256_bytes(canonical_json(value))


def _namespace_parts(tool_name: str) -> tuple[str, ...]:
    parts = tuple(part for part in str(tool_name).split(".") if part)
    return parts or (str(tool_name),)


def _family(tool_name: str) -> str:
    parts = _namespace_parts(tool_name)
    if parts and parts[0] == "syntavra":
        return parts[1] if len(parts) > 1 else "core"
    return parts[0]


@dataclass(frozen=True)
class HostToolCapabilities:
    host: str = "generic"
    max_tools: int = 32
    schema_budget_tokens: int = 2_000
    allowed_risks: tuple[str, ...] = (
        "read-or-plan",
        "safe-state-write",
        "sandbox-execute",
        "unsandboxed-execute",
        "network",
        "destructive",
    )
    namespace_prefixes: tuple[str, ...] = ("syntavra",)
    compact_schema: bool = True
    protocol_version: str = "mcp-1"

    def __post_init__(self) -> None:
        if not self.host.strip():
            raise ValueError("host capability identity cannot be empty")
        if self.max_tools < 1 or self.max_tools > 512:
            raise ValueError("max_tools must be between 1 and 512")
        if self.schema_budget_tokens < 64:
            raise ValueError("schema_budget_tokens must be at least 64")
        unknown = sorted(set(self.allowed_risks) - {
            "read-or-plan", "safe-state-write", "sandbox-execute", "unsandboxed-execute", "network", "destructive"
        })
        if unknown:
            raise ValueError(f"unknown host risk capabilities: {unknown}")
        if not self.namespace_prefixes:
            raise ValueError("at least one namespace prefix is required")

    @property
    def fingerprint(self) -> str:
        return _canonical_hash(asdict(self))


@dataclass(frozen=True)
class ToolHealth:
    state: str = "healthy"
    compatible: bool = True
    reason: str = ""

    def __post_init__(self) -> None:
        if self.state not in HEALTH_STATES:
            raise ValueError(f"unknown tool health state: {self.state}")


class ToolHealthRegistry:
    def __init__(self) -> None:
        self._states: dict[str, ToolHealth] = {}

    def set(self, tool_name: str, *, state: str = "healthy", compatible: bool = True, reason: str = "") -> ToolHealth:
        value = ToolHealth(state=state, compatible=bool(compatible), reason=str(reason))
        self._states[str(tool_name)] = value
        return value

    def get(self, tool_name: str) -> ToolHealth:
        return self._states.get(str(tool_name), ToolHealth())

    def snapshot(self) -> dict[str, dict[str, Any]]:
        return {name: asdict(self._states[name]) for name in sorted(self._states)}


@dataclass(frozen=True)
class ToolDescriptor:
    name: str
    family: str
    namespace: tuple[str, ...]
    description: str
    risk: str
    capability_fingerprint: str
    schema_fingerprint: str
    health: str
    compatible: bool

    def summary(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "family": self.family,
            "namespace": list(self.namespace),
            "risk": self.risk,
            "capability_fingerprint": self.capability_fingerprint,
            "health": self.health,
            "compatible": self.compatible,
        }


@dataclass(frozen=True)
class DiscoveryReceipt:
    stage: str
    query_hash: str
    catalog_hash: str
    profile: str
    host_fingerprint: str
    status: str
    selected_families: tuple[str, ...]
    selected_tools: tuple[str, ...]
    schema_tokens: int
    token_budget: int
    reason: str
    receipt_hash: str


class DeferredToolDiscoveryEngine:
    """Two-stage deterministic tool discovery over the existing MCP registry/policy.

    Stage one virtualizes the catalog into namespace/family summaries and never
    returns input schemas. Stage two expands one explicit selector under the
    active MCP profile, host capabilities, compatibility/health state and an
    exact schema-token budget. Unknown and ambiguous discovery fail closed.
    """

    def __init__(
        self,
        *,
        profile: str = "minimal",
        health_registry: ToolHealthRegistry | None = None,
        cache_entries: int = 128,
    ) -> None:
        self.profile = normalize_profile(profile)
        self.policy = MCPToolPolicy(self.profile)
        self.health = health_registry or ToolHealthRegistry()
        self.cache_entries = max(1, min(4096, int(cache_entries)))
        self._cache: OrderedDict[str, dict[str, Any]] = OrderedDict()

    @staticmethod
    def classify_no_tool_needed(query: str) -> dict[str, Any]:
        normalized = " ".join(str(query).casefold().split())
        words = set(_tokens(normalized))
        if not normalized:
            return {"no_tool_needed": True, "reason": "empty-query", "confidence": 1.0}
        if any(phrase in normalized for phrase in _NO_TOOL_PHRASES) and not (words & _ACTION_WORDS):
            return {"no_tool_needed": True, "reason": "explicit-no-tool-intent", "confidence": 0.95}
        return {"no_tool_needed": False, "reason": "tool-need-not-dismissed", "confidence": 1.0}

    @staticmethod
    def _catalog_hash(catalog: Sequence[Mapping[str, Any]]) -> str:
        stable = [dict(row) for row in sorted(catalog, key=lambda item: str(item.get("name", "")))]
        return _canonical_hash(stable)

    def _cache_get(self, key: str) -> dict[str, Any] | None:
        value = self._cache.get(key)
        if value is None:
            return None
        self._cache.move_to_end(key)
        return json.loads(json.dumps(value, ensure_ascii=False))

    def _cache_put(self, key: str, value: dict[str, Any]) -> None:
        self._cache[key] = json.loads(json.dumps(value, ensure_ascii=False))
        self._cache.move_to_end(key)
        while len(self._cache) > self.cache_entries:
            self._cache.popitem(last=False)

    @staticmethod
    def _namespace_allowed(name: str, host: HostToolCapabilities) -> bool:
        return any(name == prefix or name.startswith(prefix + ".") for prefix in host.namespace_prefixes)

    def describe_catalog(self, catalog: Sequence[Mapping[str, Any]]) -> tuple[ToolDescriptor, ...]:
        descriptors: list[ToolDescriptor] = []
        for row in sorted(catalog, key=lambda item: str(item.get("name", ""))):
            name = str(row.get("name") or "")
            if not name:
                continue
            description = " ".join(str(row.get("description") or "").split())
            schema = row.get("inputSchema") if isinstance(row.get("inputSchema"), Mapping) else {"type": "object"}
            health = self.health.get(name)
            risk = MCPToolPolicy.risk(name, {})
            schema_fingerprint = _canonical_hash(schema)
            capability_fingerprint = _canonical_hash({
                "name": name,
                "family": _family(name),
                "description": description,
                "risk": risk,
                "schema": schema_fingerprint,
            })
            descriptors.append(ToolDescriptor(
                name=name,
                family=_family(name),
                namespace=_namespace_parts(name),
                description=description,
                risk=risk,
                capability_fingerprint=capability_fingerprint,
                schema_fingerprint=schema_fingerprint,
                health=health.state,
                compatible=health.compatible,
            ))
        return tuple(descriptors)

    @staticmethod
    def namespace_tree(descriptors: Sequence[ToolDescriptor]) -> dict[str, Any]:
        root: dict[str, Any] = {}
        for descriptor in sorted(descriptors, key=lambda item: item.name):
            node = root
            for part in descriptor.namespace:
                node = node.setdefault(part, {})
            node["$tool"] = descriptor.name
            node["$fingerprint"] = descriptor.capability_fingerprint
        return root

    @staticmethod
    def _score(query_tokens: set[str], descriptor: ToolDescriptor) -> int:
        if not query_tokens:
            return 0
        family_tokens = set(_tokens(descriptor.family))
        name_tokens = set(_tokens(descriptor.name))
        description_tokens = set(_tokens(descriptor.description))
        return 5 * len(query_tokens & family_tokens) + 3 * len(query_tokens & name_tokens) + len(query_tokens & description_tokens)

    def _visible_descriptors(
        self,
        catalog: Sequence[Mapping[str, Any]],
        host: HostToolCapabilities,
    ) -> tuple[ToolDescriptor, ...]:
        exposed = {row["name"] for row in self.policy.filter_catalog([dict(row) for row in catalog])}
        result = []
        for descriptor in self.describe_catalog(catalog):
            if descriptor.name not in exposed:
                continue
            if descriptor.risk not in host.allowed_risks:
                continue
            if not self._namespace_allowed(descriptor.name, host):
                continue
            if descriptor.health == "unavailable" or not descriptor.compatible:
                continue
            result.append(descriptor)
        return tuple(result)

    def stage1(
        self,
        catalog: Sequence[Mapping[str, Any]],
        *,
        query: str,
        host: HostToolCapabilities | None = None,
        family_limit: int = 6,
    ) -> dict[str, Any]:
        host = host or HostToolCapabilities(
            max_tools=MCP_PROFILES[self.profile].max_active_tools,
            schema_budget_tokens=MCP_PROFILES[self.profile].tool_description_budget_tokens,
        )
        catalog_hash = self._catalog_hash(catalog)
        no_tool = self.classify_no_tool_needed(query)
        cache_key = _canonical_hash({
            "stage": 1,
            "query": str(query),
            "catalog": catalog_hash,
            "profile": self.profile,
            "host": host.fingerprint,
            "health": self.health.snapshot(),
            "family_limit": int(family_limit),
        })
        cached = self._cache_get(cache_key)
        if cached is not None:
            cached["cache_hit"] = True
            return cached

        descriptors = self._visible_descriptors(catalog, host)
        base: dict[str, Any] = {
            "stage": 1,
            "profile": self.profile,
            "catalog_hash": catalog_hash,
            "host_fingerprint": host.fingerprint,
            "cache_hit": False,
            "no_tool_needed": no_tool,
            "namespace_tree": self.namespace_tree(descriptors),
            "families": [],
            "status": "ok",
            "reason": "family-candidates",
        }
        if no_tool["no_tool_needed"]:
            base["status"] = "no-tool-needed"
            base["reason"] = no_tool["reason"]
            base["receipt"] = self._receipt(base, stage=1, query=query, host=host, token_budget=0, schema_tokens=0)
            self._cache_put(cache_key, base)
            return base

        query_tokens = set(_tokens(query))
        by_family: dict[str, list[tuple[int, ToolDescriptor]]] = {}
        for descriptor in descriptors:
            score = self._score(query_tokens, descriptor)
            if score > 0:
                by_family.setdefault(descriptor.family, []).append((score, descriptor))
        family_rows: list[dict[str, Any]] = []
        for family, rows in by_family.items():
            score = max(score for score, _ in rows)
            members = tuple(sorted((descriptor for _, descriptor in rows), key=lambda item: item.name))
            family_rows.append({
                "family": family,
                "score": score,
                "tool_count": len(members),
                "tools": [item.name for item in members],
                "risks": sorted({item.risk for item in members}),
                "fingerprint": _canonical_hash([item.capability_fingerprint for item in members]),
            })
        family_rows.sort(key=lambda row: (-int(row["score"]), str(row["family"])))

        if not family_rows:
            base["status"] = "unknown"
            base["reason"] = "no-semantic-family-match"
        else:
            top_score = int(family_rows[0]["score"])
            tied = [row for row in family_rows if int(row["score"]) == top_score]
            if len(tied) > 1:
                base["status"] = "ambiguous"
                base["reason"] = "multiple-equal-family-matches"
                base["families"] = tied[: max(1, int(family_limit))]
            else:
                base["families"] = family_rows[: max(1, int(family_limit))]
        base["receipt"] = self._receipt(base, stage=1, query=query, host=host, token_budget=0, schema_tokens=0)
        self._cache_put(cache_key, base)
        return base

    def _select(
        self,
        descriptors: Sequence[ToolDescriptor],
        selector: str,
    ) -> tuple[ToolDescriptor, ...]:
        normalized = str(selector).strip()
        if not normalized:
            raise ValueError("stage2 selector cannot be empty")
        exact = tuple(item for item in descriptors if item.name == normalized)
        if exact:
            return exact
        family_matches = tuple(item for item in descriptors if item.family == normalized)
        if family_matches:
            return family_matches
        namespace_matches = tuple(
            item for item in descriptors
            if item.name == normalized or item.name.startswith(normalized.rstrip(".") + ".")
        )
        if namespace_matches:
            return namespace_matches
        raise KeyError(f"unknown discovery selector: {normalized}")

    def stage2(
        self,
        catalog: Sequence[Mapping[str, Any]],
        *,
        selector: str,
        query: str = "",
        host: HostToolCapabilities | None = None,
        token_budget: int | None = None,
    ) -> dict[str, Any]:
        host = host or HostToolCapabilities(
            max_tools=MCP_PROFILES[self.profile].max_active_tools,
            schema_budget_tokens=MCP_PROFILES[self.profile].tool_description_budget_tokens,
        )
        budget = int(token_budget if token_budget is not None else host.schema_budget_tokens)
        if budget < 64 or budget > host.schema_budget_tokens:
            raise ValueError("requested schema token budget exceeds negotiated host budget")
        descriptors = self._visible_descriptors(catalog, host)
        selected = self._select(descriptors, selector)
        selected = tuple(sorted(selected, key=lambda item: (-self._score(set(_tokens(query)), item), item.name)))
        selected = selected[: host.max_tools]
        by_name = {str(row.get("name")): dict(row) for row in catalog}

        fitted: list[dict[str, Any]] = []
        compilation_tokens = 0
        compiler = ToolSchemaCompiler()
        for descriptor in selected:
            candidate = fitted + [by_name[descriptor.name]]
            compiled, compilation = compiler.compile_catalog(candidate)
            if compilation.compiled.tokens > budget:
                if not fitted:
                    result = {
                        "stage": 2,
                        "status": "budget-exceeded",
                        "reason": "single-tool-schema-exceeds-budget",
                        "selector": selector,
                        "tools": [],
                        "schema_tokens": compilation.compiled.tokens,
                        "token_budget": budget,
                        "catalog_hash": self._catalog_hash(catalog),
                        "profile": self.profile,
                        "host_fingerprint": host.fingerprint,
                    }
                    result["receipt"] = self._receipt(result, stage=2, query=query, host=host, token_budget=budget, schema_tokens=compilation.compiled.tokens)
                    return result
                break
            fitted = candidate
            compilation_tokens = compilation.compiled.tokens

        compiled, compilation = ToolSchemaCompiler().compile_catalog(fitted)
        compilation_tokens = compilation.compiled.tokens if fitted else 0
        result = {
            "stage": 2,
            "status": "ok",
            "reason": "selector-expanded",
            "selector": selector,
            "profile": self.profile,
            "catalog_hash": self._catalog_hash(catalog),
            "host_fingerprint": host.fingerprint,
            "selected_count": len(fitted),
            "candidate_count": len(selected),
            "truncated_by_budget": len(fitted) < len(selected),
            "schema_tokens": compilation_tokens,
            "token_budget": budget,
            "tools": compiled,
            "capability_fingerprints": {item.name: item.capability_fingerprint for item in selected if item.name in {row["name"] for row in fitted}},
        }
        result["receipt"] = self._receipt(result, stage=2, query=query, host=host, token_budget=budget, schema_tokens=compilation_tokens)
        return result

    def negotiate(
        self,
        catalog: Sequence[Mapping[str, Any]],
        host: HostToolCapabilities,
    ) -> dict[str, Any]:
        descriptors = self._visible_descriptors(catalog, host)
        families = tuple(sorted({item.family for item in descriptors}))
        result = {
            "profile": self.profile,
            "host": host.host,
            "host_fingerprint": host.fingerprint,
            "catalog_hash": self._catalog_hash(catalog),
            "available_tools": len(descriptors),
            "max_tools": min(host.max_tools, MCP_PROFILES[self.profile].max_active_tools),
            "schema_budget_tokens": min(host.schema_budget_tokens, MCP_PROFILES[self.profile].tool_description_budget_tokens),
            "families": families,
            "allowed_risks": host.allowed_risks,
            "namespace_prefixes": host.namespace_prefixes,
            "compact_schema": host.compact_schema,
            "protocol_version": host.protocol_version,
        }
        result["negotiation_fingerprint"] = _canonical_hash(result)
        return result

    def virtualize(self, catalog: Sequence[Mapping[str, Any]], host: HostToolCapabilities | None = None) -> list[dict[str, Any]]:
        host = host or HostToolCapabilities(
            max_tools=MCP_PROFILES[self.profile].max_active_tools,
            schema_budget_tokens=MCP_PROFILES[self.profile].tool_description_budget_tokens,
        )
        descriptors = self._visible_descriptors(catalog, host)
        by_family: dict[str, list[ToolDescriptor]] = {}
        for item in descriptors:
            by_family.setdefault(item.family, []).append(item)
        return [
            {
                "kind": "virtual-tool-family",
                "namespace": f"syntavra.{family}",
                "family": family,
                "tool_count": len(rows),
                "risk_labels": sorted({item.risk for item in rows}),
                "capability_fingerprint": _canonical_hash([item.capability_fingerprint for item in sorted(rows, key=lambda value: value.name)]),
            }
            for family, rows in sorted(by_family.items())
        ]

    def _receipt(
        self,
        value: Mapping[str, Any],
        *,
        stage: int,
        query: str,
        host: HostToolCapabilities,
        token_budget: int,
        schema_tokens: int,
    ) -> dict[str, Any]:
        families = tuple(str(row.get("family")) for row in value.get("families", []) if isinstance(row, Mapping))
        tools = tuple(str(row.get("name")) for row in value.get("tools", []) if isinstance(row, Mapping))
        body = {
            "stage": f"stage-{stage}",
            "query_hash": _canonical_hash(str(query)),
            "catalog_hash": str(value.get("catalog_hash") or ""),
            "profile": self.profile,
            "host_fingerprint": host.fingerprint,
            "status": str(value.get("status") or ""),
            "selected_families": families,
            "selected_tools": tools,
            "schema_tokens": int(schema_tokens),
            "token_budget": int(token_budget),
            "reason": str(value.get("reason") or ""),
        }
        body["receipt_hash"] = _canonical_hash(body)
        return body

    def manifest(self) -> dict[str, Any]:
        return {
            "engine": "DeferredToolDiscoveryEngine",
            "profile": self.profile,
            "stages": ["family-virtualization", "explicit-selector-expansion"],
            "features": [
                "deferred-loading",
                "namespace-tree",
                "semantic-families",
                "capability-fingerprints",
                "host-negotiation",
                "schema-token-budget",
                "discovery-cache",
                "compatibility-health-registry",
                "risk-labels",
                "tool-virtualization",
                "no-tool-needed-classifier",
                "fail-closed-unknown-ambiguity",
            ],
            "cache_entries": self.cache_entries,
        }


__all__ = [
    "DeferredToolDiscoveryEngine",
    "DiscoveryReceipt",
    "HEALTH_STATES",
    "HostToolCapabilities",
    "ToolDescriptor",
    "ToolHealth",
    "ToolHealthRegistry",
]
