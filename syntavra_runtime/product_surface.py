from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import os
import platform
import statistics
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .integration_matrix import IntegrationMatrix
from .release_identity import CHANNEL, VERSION
from .util import atomic_write_json, canonical_json, sha256_bytes


PRODUCT_COMMANDS: tuple[str, ...] = ("setup", "status", "run", "prove")
PROOF_WORKLOADS: tuple[str, ...] = (
    "coding-agent",
    "repository-task",
    "swe-bench",
    "oolong-long-context",
    "session-continuity",
    "tool-routing",
)


@dataclass(frozen=True)
class ProductMentalModel:
    command: str
    purpose: str
    output: str


MENTAL_MODEL: tuple[ProductMentalModel, ...] = (
    ProductMentalModel("setup", "install or repair integrations", "reversible install receipt"),
    ProductMentalModel("status", "show health, savings and continuity", "one product health snapshot"),
    ProductMentalModel("run", "enforce routing and execute through Syntavra", "auditable execution plan"),
    ProductMentalModel("prove", "validate measured external evidence", "fail-closed proof decision"),
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
        "minimal",
        ("syntavra.search", "syntavra.read", "syntavra.run", "syntavra.restore"),
        4,
        700,
        120,
        True,
        True,
        False,
    ),
    "balanced": MCPProfile(
        "balanced",
        (
            "syntavra.search", "syntavra.read", "syntavra.run", "syntavra.restore",
            "syntavra.session", "syntavra.metrics", "syntavra.sandbox", "syntavra.proof",
        ),
        8,
        1400,
        180,
        True,
        True,
        False,
    ),
    "audit": MCPProfile(
        "audit",
        (
            "syntavra.search", "syntavra.read", "syntavra.run", "syntavra.restore",
            "syntavra.session", "syntavra.metrics", "syntavra.sandbox", "syntavra.proof",
            "syntavra.evidence", "syntavra.config", "syntavra.backup", "syntavra.migrate",
        ),
        12,
        2600,
        300,
        True,
        True,
        False,
    ),
}


@dataclass(frozen=True)
class PlatformAdapter:
    host: str
    detection_commands: tuple[str, ...]
    config_candidates: tuple[str, ...]
    integration_mode: str
    supports_mcp: bool
    supports_hooks: bool
    supports_session_continuity: bool
    maturity: str = "contract-tested"


PLATFORM_ADAPTERS: tuple[PlatformAdapter, ...] = (
    PlatformAdapter("claude-code", ("claude",), ("~/.claude/settings.json", ".claude/settings.json"), "plugin+hooks", True, True, True),
    PlatformAdapter("codex", ("codex",), ("~/.codex/config.toml", "AGENTS.md"), "skill+mcp", True, False, True),
    PlatformAdapter("gemini-cli", ("gemini",), ("~/.gemini/settings.json", "GEMINI.md"), "extension+mcp", True, False, True),
    PlatformAdapter("vscode-copilot", ("code",), (".vscode/mcp.json", ".github/copilot-instructions.md"), "instructions+mcp", True, False, False),
    PlatformAdapter("jetbrains-copilot", (), (".idea/ai-assistant.xml", ".github/copilot-instructions.md"), "instructions+mcp", True, False, False),
    PlatformAdapter("cursor", ("cursor",), (".cursor/rules/syntavra.mdc", ".cursor/mcp.json"), "rules+mcp", True, False, True),
    PlatformAdapter("windsurf", ("windsurf",), (".windsurfrules", ".codeium/windsurf/mcp_config.json"), "rules+mcp", True, False, True),
    PlatformAdapter("opencode", ("opencode",), ("opencode.json", "~/.config/opencode/opencode.json"), "config+mcp", True, True, True),
    PlatformAdapter("cline", (), (".clinerules", ".vscode/mcp.json"), "rules+mcp", True, False, True),
    PlatformAdapter("roo-code", (), (".roo/rules/syntavra.md", ".vscode/mcp.json"), "rules+mcp", True, False, True),
    PlatformAdapter("qwen-code", ("qwen", "qwen-code"), ("QWEN.md", "~/.qwen/settings.json"), "agents+mcp", True, False, True),
    PlatformAdapter("kiro", (), (".kiro/steering/syntavra.md", ".kiro/settings/mcp.json"), "steering+mcp", True, False, True),
    PlatformAdapter("zed", ("zed",), (".zed/settings.json", "~/.config/zed/settings.json"), "rules+mcp", True, False, False),
    PlatformAdapter("pi", ("pi",), ("~/.pi/agent/settings.json",), "extension", False, True, True),
    PlatformAdapter("omp", ("omp",), ("~/.config/omp/config.json",), "plugin", False, True, True),
    PlatformAdapter("openclaw", ("openclaw",), ("~/.openclaw/config.json",), "plugin", True, True, True),
    PlatformAdapter("aider", ("aider",), (".aider.conf.yml", "~/.aider.conf.yml"), "env+wrapper", False, False, True),
    PlatformAdapter("continue", ("continue",), (".continue/config.yaml", "~/.continue/config.yaml"), "rules+mcp", True, False, True),
)


class PlatformAdapterRegistry:
    @staticmethod
    def records() -> list[dict[str, Any]]:
        return [asdict(item) for item in PLATFORM_ADAPTERS]

    @staticmethod
    def detect() -> list[dict[str, Any]]:
        import shutil

        rows: list[dict[str, Any]] = []
        for item in PLATFORM_ADAPTERS:
            commands = [command for command in item.detection_commands if shutil.which(command)]
            existing_configs = [
                candidate for candidate in item.config_candidates
                if Path(os.path.expanduser(candidate)).exists()
            ]
            rows.append({
                **asdict(item),
                "detected": bool(commands or existing_configs),
                "detected_commands": commands,
                "existing_configs": existing_configs,
            })
        return rows

    @staticmethod
    def validate() -> dict[str, Any]:
        hosts = {item.host for item in PLATFORM_ADAPTERS}
        matrix_hosts = {
            item["integration_id"]
            for item in IntegrationMatrix.records("host")
        }
        missing = sorted(matrix_hosts - hosts)
        extra = sorted(hosts - matrix_hosts)
        return {
            "ok": not missing and not extra and len(hosts) >= 18,
            "adapters": len(hosts),
            "missing_matrix_hosts": missing,
            "extra_adapters": extra,
            "mcp_capable": sum(item.supports_mcp for item in PLATFORM_ADAPTERS),
            "continuity_capable": sum(item.supports_session_continuity for item in PLATFORM_ADAPTERS),
            "live_boundary": "live adapter certification requires external execution receipts",
        }


@dataclass(frozen=True)
class ToolRouteDecision:
    allowed: bool
    tool: str
    category: str
    profile: str
    reason: str
    requirements: tuple[str, ...]
    receipt_hash: str


class ToolRoutingEnforcer:
    READ_PREFIXES = ("read", "search", "grep", "find", "list", "fetch", "inspect")
    WRITE_PREFIXES = ("write", "edit", "patch", "update", "create", "delete", "move", "rename")
    EXEC_PREFIXES = ("run", "exec", "shell", "terminal", "bash", "powershell", "cmd")
    NETWORK_PREFIXES = ("http", "web", "browser", "download", "upload", "request")

    @classmethod
    def category(cls, tool: str) -> str:
        normalized = tool.strip().casefold().replace("-", ".").replace("_", ".")
        leaf = normalized.rsplit(".", 1)[-1]
        for prefix in cls.READ_PREFIXES:
            if leaf.startswith(prefix):
                return "read"
        for prefix in cls.WRITE_PREFIXES:
            if leaf.startswith(prefix):
                return "write"
        for prefix in cls.EXEC_PREFIXES:
            if leaf.startswith(prefix):
                return "execute"
        for prefix in cls.NETWORK_PREFIXES:
            if leaf.startswith(prefix):
                return "network"
        return "unknown"

    @classmethod
    def decide(
        cls,
        tool: str,
        *,
        profile: str = "minimal",
        sandboxed: bool = False,
        exact_evidence: bool = True,
        explicit_user_authorization: bool = False,
    ) -> ToolRouteDecision:
        if profile not in MCP_PROFILES:
            raise ValueError(f"unknown MCP profile: {profile}")
        category = cls.category(tool)
        requirements: list[str] = ["routing-receipt"]
        allowed = True
        reason = "policy-allowed"
        if category == "unknown" and not MCP_PROFILES[profile].allow_unknown_tools:
            allowed = False
            reason = "unknown-tool-fail-closed"
        if category in {"write", "execute", "network"}:
            requirements.extend(("exact-evidence", "explicit-user-authorization"))
            if not exact_evidence:
                allowed = False
                reason = "exact-evidence-required"
            elif not explicit_user_authorization:
                allowed = False
                reason = "explicit-user-authorization-required"
        if category == "execute":
            requirements.append("sandbox")
            if not sandboxed:
                allowed = False
                reason = "sandbox-required"
        body = {
            "tool": tool,
            "category": category,
            "profile": profile,
            "allowed": allowed,
            "reason": reason,
            "requirements": requirements,
        }
        return ToolRouteDecision(
            allowed,
            tool,
            category,
            profile,
            reason,
            tuple(requirements),
            sha256_bytes(canonical_json(body)),
        )


@dataclass(frozen=True)
class ProviderUsageReceipt:
    receipt_id: str
    provider: str
    model: str
    request_id: str
    session_id: str
    repository_hash: str
    integration_id: str
    observed_at: str
    wall_time_ms: float
    input_tokens: int
    cached_input_tokens: int
    output_tokens: int
    cost_usd: float
    quality_score: float
    success: bool
    synthetic: bool
    raw_usage_hash: str
    workload: str = "coding-agent"
    arm: str = "syntavra"
    task_id: str = ""
    repetition: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def billable_input_tokens(self) -> int:
        return max(0, self.input_tokens - self.cached_input_tokens)

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ProviderUsageReceipt":
        return cls(
            receipt_id=str(value.get("receipt_id", "")),
            provider=str(value.get("provider", "")),
            model=str(value.get("model", "")),
            request_id=str(value.get("request_id", "")),
            session_id=str(value.get("session_id", "")),
            repository_hash=str(value.get("repository_hash", "")),
            integration_id=str(value.get("integration_id", "")),
            observed_at=str(value.get("observed_at", "")),
            wall_time_ms=float(value.get("wall_time_ms", -1)),
            input_tokens=int(value.get("input_tokens", -1)),
            cached_input_tokens=int(value.get("cached_input_tokens", -1)),
            output_tokens=int(value.get("output_tokens", -1)),
            cost_usd=float(value.get("cost_usd", -1)),
            quality_score=float(value.get("quality_score", -1)),
            success=bool(value.get("success", False)),
            synthetic=bool(value.get("synthetic", True)),
            raw_usage_hash=str(value.get("raw_usage_hash", "")),
            workload=str(value.get("workload", "coding-agent")),
            arm=str(value.get("arm", "syntavra")),
            task_id=str(value.get("task_id", "")),
            repetition=int(value.get("repetition", 0)),
            metadata=dict(value.get("metadata") or {}),
        )

    def validate(self) -> tuple[str, ...]:
        reasons: list[str] = []
        required = {
            "receipt_id": self.receipt_id,
            "provider": self.provider,
            "model": self.model,
            "request_id": self.request_id,
            "session_id": self.session_id,
            "repository_hash": self.repository_hash,
            "integration_id": self.integration_id,
            "observed_at": self.observed_at,
            "raw_usage_hash": self.raw_usage_hash,
            "task_id": self.task_id,
        }
        reasons.extend(f"missing-{key}" for key, value in required.items() if not value)
        try:
            dt.datetime.fromisoformat(self.observed_at.replace("Z", "+00:00"))
        except ValueError:
            reasons.append("invalid-observed-at")
        if self.wall_time_ms < 0 or not math.isfinite(self.wall_time_ms):
            reasons.append("invalid-wall-time")
        if min(self.input_tokens, self.cached_input_tokens, self.output_tokens) < 0:
            reasons.append("invalid-token-count")
        if self.cached_input_tokens > self.input_tokens:
            reasons.append("cached-input-exceeds-input")
        if self.cost_usd < 0 or not math.isfinite(self.cost_usd):
            reasons.append("invalid-cost")
        if not 0.0 <= self.quality_score <= 1.0:
            reasons.append("invalid-quality-score")
        if self.workload not in PROOF_WORKLOADS:
            reasons.append("unsupported-workload")
        if self.arm not in {"baseline", "syntavra", "token-savior", "context-mode", "headroom", "volt-lcm"}:
            reasons.append("unsupported-arm")
        if self.repetition < 1:
            reasons.append("invalid-repetition")
        if len(self.raw_usage_hash) < 32:
            reasons.append("weak-raw-usage-hash")
        return tuple(dict.fromkeys(reasons))


class ReceiptValidator:
    @staticmethod
    def load(path: Path) -> list[ProviderUsageReceipt]:
        value = json.loads(path.read_text(encoding="utf-8"))
        rows = value.get("receipts", value) if isinstance(value, dict) else value
        if not isinstance(rows, list):
            raise ValueError("receipt file must contain a list or {'receipts': [...]} object")
        return [ProviderUsageReceipt.from_mapping(item) for item in rows if isinstance(item, Mapping)]

    @staticmethod
    def evaluate(receipts: Iterable[ProviderUsageReceipt]) -> dict[str, Any]:
        rows = list(receipts)
        invalid = [
            {"receipt_id": row.receipt_id, "reasons": list(row.validate())}
            for row in rows if row.validate()
        ]
        duplicate_ids = sorted({row.receipt_id for row in rows if sum(item.receipt_id == row.receipt_id for item in rows) > 1})
        live = [row for row in rows if not row.synthetic and not row.validate()]
        return {
            "ok": bool(rows) and not invalid and not duplicate_ids,
            "version": VERSION,
            "channel": CHANNEL,
            "total": len(rows),
            "valid": len(rows) - len(invalid),
            "live": len(live),
            "synthetic": sum(row.synthetic for row in rows),
            "invalid": invalid,
            "duplicate_receipt_ids": duplicate_ids,
            "claim_boundary": "validated receipts are evidence inputs, not automatic superiority proof",
        }


class MeasuredBenchmarkGate:
    minimum_pairs = 30
    minimum_repositories = 5
    minimum_tasks = 10
    minimum_workload_families = 3
    quality_non_inferiority_margin = 0.01
    success_non_inferiority_margin = 0.02

    @staticmethod
    def _pair_key(row: ProviderUsageReceipt) -> tuple[str, str, int, str, str]:
        return row.repository_hash, row.task_id, row.repetition, row.provider, row.model

    @classmethod
    def evaluate(cls, receipts: Iterable[ProviderUsageReceipt]) -> dict[str, Any]:
        rows = list(receipts)
        validation = ReceiptValidator.evaluate(rows)
        reasons: list[str] = []
        if not validation["ok"]:
            reasons.append("receipt-validation-failed")
        if any(row.synthetic for row in rows):
            reasons.append("synthetic-receipts-present")
        grouped: dict[tuple[str, str, int, str, str], dict[str, ProviderUsageReceipt]] = {}
        for row in rows:
            grouped.setdefault(cls._pair_key(row), {})[row.arm] = row
        pairs = [value for value in grouped.values() if "baseline" in value and "syntavra" in value]
        if len(pairs) < cls.minimum_pairs:
            reasons.append("insufficient-paired-runs")
        repositories = {pair["syntavra"].repository_hash for pair in pairs}
        tasks = {pair["syntavra"].task_id for pair in pairs}
        workloads = {pair["syntavra"].workload for pair in pairs}
        if len(repositories) < cls.minimum_repositories:
            reasons.append("insufficient-repositories")
        if len(tasks) < cls.minimum_tasks:
            reasons.append("insufficient-tasks")
        if len(workloads) < cls.minimum_workload_families:
            reasons.append("insufficient-workload-diversity")

        token_ratios: list[float] = []
        wall_ratios: list[float] = []
        cost_ratios: list[float] = []
        quality_deltas: list[float] = []
        success_deltas: list[float] = []
        for pair in pairs:
            baseline = pair["baseline"]
            syntavra = pair["syntavra"]
            if baseline.provider != syntavra.provider or baseline.model != syntavra.model:
                reasons.append("provider-or-model-parity-failed")
                continue
            baseline_tokens = baseline.billable_input_tokens + baseline.output_tokens
            syntavra_tokens = syntavra.billable_input_tokens + syntavra.output_tokens
            if baseline_tokens <= 0 or baseline.wall_time_ms <= 0 or baseline.cost_usd <= 0:
                reasons.append("invalid-baseline-denominator")
                continue
            token_ratios.append(syntavra_tokens / baseline_tokens)
            wall_ratios.append(syntavra.wall_time_ms / baseline.wall_time_ms)
            cost_ratios.append(syntavra.cost_usd / baseline.cost_usd)
            quality_deltas.append(syntavra.quality_score - baseline.quality_score)
            success_deltas.append(float(syntavra.success) - float(baseline.success))

        mean_quality_delta = statistics.fmean(quality_deltas) if quality_deltas else -1.0
        mean_success_delta = statistics.fmean(success_deltas) if success_deltas else -1.0
        if mean_quality_delta < -cls.quality_non_inferiority_margin:
            reasons.append("quality-non-inferiority-failed")
        if mean_success_delta < -cls.success_non_inferiority_margin:
            reasons.append("success-non-inferiority-failed")
        if not token_ratios:
            reasons.append("no-measurable-pairs")

        metrics = {
            "pairs": len(pairs),
            "repositories": len(repositories),
            "tasks": len(tasks),
            "workloads": len(workloads),
            "mean_token_ratio": statistics.fmean(token_ratios) if token_ratios else None,
            "median_token_ratio": statistics.median(token_ratios) if token_ratios else None,
            "mean_wall_time_ratio": statistics.fmean(wall_ratios) if wall_ratios else None,
            "mean_cost_ratio": statistics.fmean(cost_ratios) if cost_ratios else None,
            "mean_quality_delta": mean_quality_delta if quality_deltas else None,
            "mean_success_delta": mean_success_delta if success_deltas else None,
        }
        ok = not reasons
        return {
            "ok": ok,
            "claim": "MEASURED_AGENT_BENCHMARK_VERIFIED" if ok else "MEASURED_AGENT_BENCHMARK_NOT_PROVEN",
            "external_superiority": "EXTERNAL_SUPERIORITY_ELIGIBLE_FOR_REVIEW" if ok else "EXTERNAL_SUPERIORITY_NOT_PROVEN",
            "reasons": sorted(set(reasons)),
            "metrics": metrics,
            "requirements": {
                "minimum_pairs": cls.minimum_pairs,
                "minimum_repositories": cls.minimum_repositories,
                "minimum_tasks": cls.minimum_tasks,
                "minimum_workload_families": cls.minimum_workload_families,
                "quality_non_inferiority_margin": cls.quality_non_inferiority_margin,
                "success_non_inferiority_margin": cls.success_non_inferiority_margin,
            },
        }


class SessionAnalyticsStore:
    """Append-only, local-first analytics with no prompt or response content."""

    schema_version = 1

    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, event: Mapping[str, Any]) -> dict[str, Any]:
        allowed = {
            "event_id", "observed_at", "session_id", "repository_hash", "kind", "provider",
            "model", "input_tokens", "cached_input_tokens", "output_tokens", "wall_time_ms",
            "cost_usd", "quality_score", "success", "compaction_ms", "continuity_restored",
            "tool_route_allowed", "metadata",
        }
        row = {key: event[key] for key in allowed if key in event}
        row.setdefault("event_id", hashlib.sha256(canonical_json(dict(event))).hexdigest())
        row.setdefault("observed_at", dt.datetime.now(dt.timezone.utc).isoformat())
        row.setdefault("kind", "agent-turn")
        row["schema_version"] = self.schema_version
        encoded = json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        with self.path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(encoded + "\n")
        return {"ok": True, "event_id": row["event_id"], "path": str(self.path)}

    def rows(self) -> list[dict[str, Any]]:
        if not self.path.is_file():
            return []
        result: list[dict[str, Any]] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            value = json.loads(line)
            if isinstance(value, dict):
                result.append(value)
        return result

    def report(self) -> dict[str, Any]:
        rows = self.rows()
        sessions = {str(row.get("session_id")) for row in rows if row.get("session_id")}
        repositories = {str(row.get("repository_hash")) for row in rows if row.get("repository_hash")}
        input_tokens = sum(max(0, int(row.get("input_tokens", 0))) for row in rows)
        cached_tokens = sum(max(0, int(row.get("cached_input_tokens", 0))) for row in rows)
        output_tokens = sum(max(0, int(row.get("output_tokens", 0))) for row in rows)
        wall_time_ms = sum(max(0.0, float(row.get("wall_time_ms", 0.0))) for row in rows)
        cost_usd = sum(max(0.0, float(row.get("cost_usd", 0.0))) for row in rows)
        compaction_ms = sum(max(0.0, float(row.get("compaction_ms", 0.0))) for row in rows)
        continuity = sum(bool(row.get("continuity_restored")) for row in rows)
        route_denied = sum(row.get("tool_route_allowed") is False for row in rows)
        return {
            "version": VERSION,
            "channel": CHANNEL,
            "events": len(rows),
            "sessions": len(sessions),
            "repositories": len(repositories),
            "usage": {
                "input_tokens": input_tokens,
                "cached_input_tokens": cached_tokens,
                "billable_input_tokens": max(0, input_tokens - cached_tokens),
                "output_tokens": output_tokens,
                "wall_time_ms": wall_time_ms,
                "cost_usd": cost_usd,
            },
            "continuity": {
                "restores": continuity,
                "compaction_wall_time_ms": compaction_ms,
            },
            "routing": {"denied": route_denied},
            "privacy": "content-free local aggregate",
        }


class ProductSurface:
    @staticmethod
    def manifest(profile: str = "minimal") -> dict[str, Any]:
        if profile not in MCP_PROFILES:
            raise ValueError(profile)
        return {
            "version": VERSION,
            "channel": CHANNEL,
            "mental_model": [asdict(item) for item in MENTAL_MODEL],
            "default_mcp_profile": MCP_PROFILES[profile].to_dict(),
            "platform_adapters": PlatformAdapterRegistry.validate(),
            "integration_matrix": IntegrationMatrix.validate(),
            "proxy": {
                "surface": "OpenAI-compatible local control plane plus Python and TypeScript clients",
                "credential_policy": "transport-only",
                "stream_policy": "commit-before-forward",
                "usage_policy": "provider receipt required",
                "status": "pre-release",
            },
            "proof": {
                "workloads": list(PROOF_WORKLOADS),
                "measured_fields": ["provider tokens", "provider cost", "wall time", "quality", "success"],
                "external_claim": "fail-closed",
            },
        }

    @staticmethod
    def setup_bundle(project_root: Path, state_root: Path, profile: str = "minimal") -> dict[str, Any]:
        manifest = ProductSurface.manifest(profile)
        product_path = state_root / "product.json"
        mcp_path = state_root / "mcp-profile.json"
        adapters_path = state_root / "platform-adapters.json"
        atomic_write_json(product_path, manifest, mode=0o600)
        atomic_write_json(mcp_path, MCP_PROFILES[profile].to_dict(), mode=0o600)
        atomic_write_json(adapters_path, {"adapters": PlatformAdapterRegistry.records()}, mode=0o600)
        return {
            "ok": True,
            "project_root": str(project_root),
            "profile": profile,
            "files": [str(product_path), str(mcp_path), str(adapters_path)],
        }

    @staticmethod
    def readiness(state_root: Path, receipts: Sequence[ProviderUsageReceipt] = ()) -> dict[str, Any]:
        required_files = [state_root / "product.json", state_root / "mcp-profile.json", state_root / "platform-adapters.json"]
        file_checks = {path.name: path.is_file() for path in required_files}
        benchmark = MeasuredBenchmarkGate.evaluate(receipts)
        checks = {
            "narrow_product_surface": len(PRODUCT_COMMANDS) == 4,
            "platform_adapter_contracts": PlatformAdapterRegistry.validate()["ok"],
            "integration_matrix": IntegrationMatrix.validate()["ok"],
            "setup_bundle": all(file_checks.values()),
            "measured_agent_benchmark": benchmark["ok"],
        }
        return {
            "ok": all(checks.values()),
            "claim": "DAILY_CODING_AGENT_READY" if all(checks.values()) else "DAILY_CODING_AGENT_READINESS_NOT_PROVEN",
            "checks": checks,
            "files": file_checks,
            "benchmark": benchmark,
            "version": VERSION,
            "channel": CHANNEL,
        }


def write_receipt_schema(path: Path) -> dict[str, Any]:
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://syntavra.dev/schemas/provider-usage-receipt-v1.json",
        "title": "Syntavra Provider Usage Receipt",
        "type": "object",
        "required": [
            "receipt_id", "provider", "model", "request_id", "session_id", "repository_hash",
            "integration_id", "observed_at", "wall_time_ms", "input_tokens", "cached_input_tokens",
            "output_tokens", "cost_usd", "quality_score", "success", "synthetic", "raw_usage_hash",
            "workload", "arm", "task_id", "repetition",
        ],
        "properties": {
            "receipt_id": {"type": "string", "minLength": 1},
            "provider": {"type": "string", "minLength": 1},
            "model": {"type": "string", "minLength": 1},
            "request_id": {"type": "string", "minLength": 1},
            "session_id": {"type": "string", "minLength": 1},
            "repository_hash": {"type": "string", "minLength": 16},
            "integration_id": {"type": "string", "minLength": 1},
            "observed_at": {"type": "string", "format": "date-time"},
            "wall_time_ms": {"type": "number", "minimum": 0},
            "input_tokens": {"type": "integer", "minimum": 0},
            "cached_input_tokens": {"type": "integer", "minimum": 0},
            "output_tokens": {"type": "integer", "minimum": 0},
            "cost_usd": {"type": "number", "minimum": 0},
            "quality_score": {"type": "number", "minimum": 0, "maximum": 1},
            "success": {"type": "boolean"},
            "synthetic": {"type": "boolean"},
            "raw_usage_hash": {"type": "string", "minLength": 32},
            "workload": {"enum": list(PROOF_WORKLOADS)},
            "arm": {"enum": ["baseline", "syntavra", "token-savior", "context-mode", "headroom", "volt-lcm"]},
            "task_id": {"type": "string", "minLength": 1},
            "repetition": {"type": "integer", "minimum": 1},
            "metadata": {"type": "object"},
        },
        "additionalProperties": True,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    return schema
