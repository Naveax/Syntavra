from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from typing import Any, Iterable, Mapping, Sequence

from .util import canonical_json, sha256_bytes


PROFILE_ALIASES: dict[str, str] = {
    "minimal": "minimal",
    "tiny": "minimal",
    "balanced": "balanced",
    "optimized": "balanced",
    "audit": "audit",
    "full": "audit",
    "auto": "auto",
}

MINIMAL_TOOLS: tuple[str, ...] = (
    "syntavra.status",
    "syntavra.inspect.map",
    "syntavra.process.submit",
    "syntavra.process.completions",
    "syntavra.output.capture",
    "syntavra.output.search",
    "syntavra.output.reveal",
    "syntavra.session.semantic_context",
    "syntavra.fabric.route",
    "syntavra.fabric.doctor",
)

BALANCED_TOOLS: tuple[str, ...] = (
    *MINIMAL_TOOLS,
    "syntavra.host.detect",
    "syntavra.inspect.impact",
    "syntavra.inspect.source",
    "syntavra.inspect.range",
    "syntavra.context.evaluate",
    "syntavra.output.verify",
    "syntavra.output.stats",
    "syntavra.session.open",
    "syntavra.session.append",
    "syntavra.session.search",
    "syntavra.session.context",
    "syntavra.session.compact",
    "syntavra.session.verify",
    "syntavra.sandbox.plan",
    "syntavra.sandbox.execute",
    "syntavra.fabric.profile",
    "syntavra.fabric.insights",
    "syntavra.provider.capabilities",
    "syntavra.provider.prepare",
    "syntavra.provider.capture",
    "syntavra.provider.replay",
    "syntavra.provider.verify",
    "syntavra.provider.stats",
    "syntavra.data.route",
    "syntavra.ecosystem.capabilities",
    "syntavra.context.pack",
)


@dataclass(frozen=True)
class MCPProfile:
    name: str
    exposed_tools: tuple[str, ...]
    max_active_tools: int
    tool_description_budget_tokens: int
    default_timeout_seconds: int
    require_routing_receipt: bool
    require_exact_evidence: bool
    allow_unknown_tools: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


MCP_PROFILES: dict[str, MCPProfile] = {
    "minimal": MCPProfile(
        "minimal", MINIMAL_TOOLS, len(MINIMAL_TOOLS), 800, 120, True, True, False,
    ),
    "balanced": MCPProfile(
        "balanced", BALANCED_TOOLS, len(BALANCED_TOOLS), 2_000, 180, True, True, False,
    ),
    "audit": MCPProfile(
        "audit", ("*",), 128, 16_000, 300, True, True, False,
    ),
}


def normalize_profile(profile: str | None, *, allow_auto: bool = False) -> str:
    requested = (profile or "minimal").strip().casefold() or "minimal"
    normalized = PROFILE_ALIASES.get(requested)
    if normalized is None or (normalized == "auto" and not allow_auto):
        raise ValueError(f"unknown Syntavra MCP profile: {requested}")
    return normalized


def profile_tools(profile: str, available_tools: Iterable[str]) -> tuple[str, ...]:
    normalized = normalize_profile(profile)
    available = tuple(dict.fromkeys(str(item) for item in available_tools))
    if normalized == "audit":
        return available
    allowlist = MCP_PROFILES[normalized].exposed_tools
    available_set = set(available)
    return tuple(name for name in allowlist if name in available_set)


# The aliases remain human-readable. Opaque p1/p2 argument names save a few more
# tokens but measurably increase tool-call errors on smaller models.
_COMMON_ARGUMENT_ALIASES: dict[str, str] = {
    "repository_tree": "repo_tree",
    "continuation_token": "cursor",
    "budget_bytes": "budget",
    "token_budget": "budget_tokens",
    "context_lines": "lines",
    "timeout_seconds": "timeout",
    "network_untrusted": "untrusted_network",
    "include_superseded": "include_old",
    "stop_on_failure": "stop_on_fail",
    "explicit_user_authorization": "user_approved",
}


@dataclass(frozen=True)
class SchemaCost:
    bytes: int
    tokens: int
    method: str


@dataclass(frozen=True)
class SchemaCompilation:
    raw: SchemaCost
    compiled: SchemaCost
    savings_ratio: float
    catalog_hash: str
    compiled_hash: str
    argument_aliases: dict[str, dict[str, str]]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ToolSchemaCompiler:
    """Deterministically reduce MCP discovery cost without changing tool identity.

    Tool names stay stable. Descriptions are tightened and selected long argument
    names receive readable aliases. ``decode_arguments`` accepts both aliases and
    original names so direct clients remain compatible.
    """

    def __init__(self) -> None:
        self._aliases: dict[str, dict[str, str]] = {}
        self.last_compilation: SchemaCompilation | None = None

    @staticmethod
    def _cost(value: Any) -> SchemaCost:
        payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        # Provider tokenizers are optional and differ by model. The fallback is
        # explicitly labelled LOCALLY_ESTIMATED rather than provider-observed.
        try:
            import tiktoken  # type: ignore

            encoder = tiktoken.get_encoding("o200k_base")
            tokens = len(encoder.encode(payload.decode("utf-8")))
            method = "LOCALLY_TOKENIZED:o200k_base"
        except (ImportError, KeyError, UnicodeError):
            tokens = max(1, math.ceil(len(payload) / 4))
            method = "LOCALLY_ESTIMATED:utf8-bytes-div-4"
        return SchemaCost(len(payload), tokens, method)

    @staticmethod
    def _compact_description(description: str) -> str:
        value = " ".join(str(description).split())
        replacements = (
            ("Inspect ", "Show "),
            ("Retrieve ", "Read "),
            ("Execute ", "Run "),
            ("through exact externalization", "with exact recovery"),
            ("query-conditioned ", "task-scoped "),
            ("after a cursor", "after cursor"),
            ("and signatures", "+ signatures"),
        )
        for old, new in replacements:
            value = value.replace(old, new)
        words = value.split()
        if len(words) > 12:
            value = " ".join(words[:12])
        return value.rstrip(".")

    @staticmethod
    def _compile_schema(tool_name: str, schema: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
        result = dict(schema)
        properties = schema.get("properties")
        aliases: dict[str, str] = {}
        if isinstance(properties, Mapping):
            compiled_properties: dict[str, Any] = {}
            for original, definition in properties.items():
                original_name = str(original)
                alias = _COMMON_ARGUMENT_ALIASES.get(original_name, original_name)
                if alias in compiled_properties and alias != original_name:
                    alias = original_name
                compiled_properties[alias] = definition
                if alias != original_name:
                    aliases[alias] = original_name
            result["properties"] = compiled_properties
        required = schema.get("required")
        if isinstance(required, Sequence) and not isinstance(required, (str, bytes, bytearray)):
            result["required"] = [_COMMON_ARGUMENT_ALIASES.get(str(name), str(name)) for name in required]
        # MCP already implies an object-shaped input schema; empty properties and
        # duplicated titles/defaults only add discovery cost.
        result.pop("title", None)
        result.pop("$schema", None)
        if not result.get("required"):
            result.pop("required", None)
        return result, aliases

    def compile_catalog(self, catalog: Sequence[Mapping[str, Any]]) -> tuple[list[dict[str, Any]], SchemaCompilation]:
        raw_catalog = [dict(row) for row in catalog]
        compiled: list[dict[str, Any]] = []
        aliases_by_tool: dict[str, dict[str, str]] = {}
        for row in raw_catalog:
            name = str(row.get("name", ""))
            schema = row.get("inputSchema") if isinstance(row.get("inputSchema"), Mapping) else {"type": "object"}
            compiled_schema, aliases = self._compile_schema(name, schema)
            item = dict(row)
            item["description"] = self._compact_description(str(row.get("description", "")))
            item["inputSchema"] = compiled_schema
            compiled.append(item)
            if aliases:
                aliases_by_tool[name] = aliases
        raw_cost = self._cost(raw_catalog)
        compiled_cost = self._cost(compiled)
        compilation = SchemaCompilation(
            raw=raw_cost,
            compiled=compiled_cost,
            savings_ratio=0.0 if raw_cost.tokens <= 0 else max(0.0, 1.0 - compiled_cost.tokens / raw_cost.tokens),
            catalog_hash=sha256_bytes(canonical_json(raw_catalog)),
            compiled_hash=sha256_bytes(canonical_json(compiled)),
            argument_aliases=aliases_by_tool,
        )
        self._aliases = aliases_by_tool
        self.last_compilation = compilation
        return compiled, compilation

    def decode_arguments(self, tool_name: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        aliases = self._aliases.get(tool_name, {})
        decoded = dict(arguments)
        for alias, original in aliases.items():
            if alias in decoded and original not in decoded:
                decoded[original] = decoded.pop(alias)
        return decoded


def profile_manifest(profile: str, catalog: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    normalized = normalize_profile(profile)
    by_name = {str(row.get("name")): dict(row) for row in catalog}
    selected_names = profile_tools(normalized, by_name)
    selected = [by_name[name] for name in selected_names]
    compiler = ToolSchemaCompiler()
    compiled, compilation = compiler.compile_catalog(selected)
    return {
        "profile": normalized,
        "selected_tools": selected_names,
        "selected_count": len(selected_names),
        "available_count": len(by_name),
        "compiled_catalog": compiled,
        "schema_compilation": compilation.to_dict(),
        "profile_hash": sha256_bytes(canonical_json({"profile": normalized, "tools": selected_names})),
        "within_budget": compilation.compiled.tokens <= MCP_PROFILES[normalized].tool_description_budget_tokens,
        "budget_tokens": MCP_PROFILES[normalized].tool_description_budget_tokens,
    }
