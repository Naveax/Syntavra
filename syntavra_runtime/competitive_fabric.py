from __future__ import annotations

import json
import math
import os
import re
import shlex
import shutil
import statistics
import time
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .host_adapters import KNOWN_HOSTS, host_spec, negotiate
from .security_scan import scan_text
from .state import StateDB
from .util import canonical_json, sha256_bytes

_VOLATILE_FIELDS = {
    "created_at", "updated_at", "timestamp", "request_id", "response_id",
    "trace_id", "span_id", "latency_ms", "duration_ms", "usage", "cost",
}
_ERROR_RE = re.compile(
    r"(?i)\b(error|failed|failure|panic|assertion|traceback|exception|fatal|denied|"
    r"timeout|segmentation fault|not found|permission denied)\b"
)
_TEST_SUMMARY_RE = re.compile(
    r"(?i)(?:\b\d+\s+(?:passed|failed|errors?|skipped|xfailed|xpassed)\b|"
    r"tests?:\s*\d+|test suites?:|failures?:\s*\d+|successes?:\s*\d+)"
)
_LOCATION_RE = re.compile(
    r"(?:[^\s:]+\.(?:py|rs|js|jsx|ts|tsx|java|cs|go|rb|php|lua|luau|cpp|c|h):\d+|"
    r"File \"[^\"]+\", line \d+|\bat [\w.$<>]+\([^)]*:\d+\))"
)
_DIFF_HEADER_RE = re.compile(r"^(?:diff --git|index |--- |\+\+\+ |@@ )")
_SHELL_UNSAFE_RE = re.compile(r"(?:\|\||&&|[|`]|\$\(|<<|\n|\r)")
_DESTRUCTIVE_RE = re.compile(
    r"(?i)(?:\brm\s+-rf\s+(?:/|~)|\bgit\s+reset\s+--hard|\bgit\s+clean\s+-[a-z]*[fxd]|"
    r"\bmkfs\.|\bformat\s+[a-z]:|remove-item\s+.*-recurse.*-force\s+[a-z]:\\)"
)


@dataclass(frozen=True)
class ToolProfile:
    name: str
    tools: tuple[str, ...]
    manifest_token_budget: int
    purpose: str


@dataclass(frozen=True)
class RouteDecision:
    family: str
    mode: str
    normalized_command: tuple[str, ...]
    replacement_argv: tuple[str, ...]
    recommended_tools: tuple[str, ...]
    reasons: tuple[str, ...]
    capture_required: bool
    background: bool
    sandbox: bool
    repeat_key: str
    safe_to_rewrite: bool


@dataclass(frozen=True)
class CacheAlignment:
    prefix_hash: str
    stable_message_count: int
    volatile_tail_count: int
    cacheable_bytes: int
    volatile_fields: tuple[str, ...]
    canonical_prefix: str


@dataclass(frozen=True)
class CompactionResult:
    family: str
    visible_text: str
    original_bytes: int
    visible_bytes: int
    savings_ratio: float
    exact_required: bool
    secret_types: tuple[str, ...]
    injection_risk: bool
    injection_reasons: tuple[str, ...]
    retained_error_lines: int


CORE_TOOLS = (
    "syntavra.status",
    "syntavra.host.detect",
    "syntavra.context.evaluate",
    "syntavra.inspect.map",
    "syntavra.inspect.impact",
    "syntavra.output.capture",
    "syntavra.output.search",
    "syntavra.output.reveal",
    "syntavra.output.verify",
    "syntavra.session.open",
    "syntavra.session.append",
    "syntavra.session.semantic_context",
    "syntavra.fabric.profile",
    "syntavra.fabric.route",
    "syntavra.fabric.doctor",
)

PROFILES: dict[str, ToolProfile] = {
    "tiny": ToolProfile(
        "tiny",
        (
            "syntavra.status", "syntavra.inspect.map", "syntavra.output.capture",
            "syntavra.output.search", "syntavra.output.reveal",
            "syntavra.session.semantic_context", "syntavra.fabric.route",
            "syntavra.fabric.doctor",
        ),
        700,
        "Minimal hot-loop surface for small coding tasks.",
    ),
    "optimized": ToolProfile(
        "optimized",
        CORE_TOOLS + (
            "syntavra.process.submit", "syntavra.process.completions",
            "syntavra.compress", "syntavra.expand", "syntavra.sandbox.execute",
            "syntavra.session.search", "syntavra.output.stats",
            "syntavra.fabric.compact", "syntavra.fabric.cache_align",
            "syntavra.fabric.insights", "syntavra.fabric.platform_plan",
        ),
        1800,
        "Default Pareto surface: structural navigation, exact output economy, memory, and safety.",
    ),
    "full": ToolProfile("full", (), 8000, "Expose every installed Syntavra tool."),
}

_INTENT_TOOLS: tuple[tuple[re.Pattern[str], tuple[str, ...]], ...] = (
    (re.compile(r"(?i)\b(test|pytest|jest|vitest|cargo test|go test|ci|build|lint)\b"),
     ("syntavra.process.submit", "syntavra.process.completions", "syntavra.output.search")),
    (re.compile(r"(?i)\b(symbol|function|class|call graph|impact|dependency|codebase|repository)\b"),
     ("syntavra.inspect.map", "syntavra.inspect.impact")),
    (re.compile(r"(?i)\b(log|trace|output|stdout|stderr|search output|reveal)\b"),
     ("syntavra.output.capture", "syntavra.output.search", "syntavra.output.reveal", "syntavra.output.verify")),
    (re.compile(r"(?i)\b(memory|remember|session|decision|previous|history|compact)\b"),
     ("syntavra.session.open", "syntavra.session.append", "syntavra.session.search", "syntavra.session.semantic_context")),
    (re.compile(r"(?i)\b(untrusted|sandbox|network|download|curl|wget|security)\b"),
     ("syntavra.sandbox.plan", "syntavra.sandbox.execute", "syntavra.output.verify")),
    (re.compile(r"(?i)\b(token|context|cache|profile|manifest|compression)\b"),
     ("syntavra.compress", "syntavra.expand", "syntavra.context.evaluate", "syntavra.fabric.profile", "syntavra.fabric.cache_align")),
)


class ToolSurfacePlanner:
    """Deterministically minimize MCP manifest cost without hiding required capabilities."""

    @staticmethod
    def _estimate_tokens(names: Sequence[str]) -> int:
        return sum(max(8, math.ceil((len(name) + 48) / 4)) for name in names)

    def plan(
        self,
        task: str,
        *,
        host: str,
        available_tools: Iterable[str],
        requested_profile: str = "auto",
    ) -> dict[str, Any]:
        available = tuple(dict.fromkeys(str(name) for name in available_tools))
        if requested_profile not in {*PROFILES, "auto"}:
            raise ValueError(f"unknown tool profile: {requested_profile}")
        if requested_profile == "auto":
            word_count = len(task.split())
            matched = sum(bool(pattern.search(task)) for pattern, _ in _INTENT_TOOLS)
            profile_name = "tiny" if word_count <= 12 and matched <= 1 else "optimized"
        else:
            profile_name = requested_profile
        profile = PROFILES[profile_name]
        if profile_name == "full":
            selected = list(available)
        else:
            wanted = set(profile.tools)
            for pattern, tools in _INTENT_TOOLS:
                if pattern.search(task):
                    wanted.update(tools)
            selected = [name for name in available if name in wanted]
            # Unknown future fabric tools stay visible in optimized mode rather than
            # becoming unreachable after an upgrade.
            selected.extend(name for name in available if name.startswith("syntavra.fabric.") and name not in selected)
        selected = list(dict.fromkeys(selected))
        estimated = self._estimate_tokens(selected)
        return {
            "profile": profile_name,
            "purpose": profile.purpose,
            "selected_tools": selected,
            "selected_count": len(selected),
            "available_count": len(available),
            "omitted_count": max(0, len(available) - len(selected)),
            "estimated_manifest_tokens": estimated,
            "manifest_budget": profile.manifest_token_budget,
            "within_budget": estimated <= profile.manifest_token_budget or profile_name == "full",
            "host": host,
            "host_mode": negotiate(host).get("mode"),
            "profile_hash": sha256_bytes(canonical_json({"profile": profile_name, "tools": selected})),
        }


class CacheAligner:
    """Build cache-stable request fingerprints while preserving exact message payloads."""

    @staticmethod
    def _stable_copy(value: Any, volatile: set[str]) -> Any:
        if isinstance(value, Mapping):
            result: dict[str, Any] = {}
            for key in sorted(value, key=str):
                name = str(key)
                if name in _VOLATILE_FIELDS or name.startswith("_"):
                    volatile.add(name)
                    continue
                result[name] = CacheAligner._stable_copy(value[key], volatile)
            return result
        if isinstance(value, (list, tuple)):
            return [CacheAligner._stable_copy(item, volatile) for item in value]
        return value

    def align(self, messages: Sequence[Mapping[str, Any]], *, keep_tail: int = 1) -> CacheAlignment:
        if keep_tail < 0:
            raise ValueError("keep_tail must be non-negative")
        stable_count = max(0, len(messages) - keep_tail)
        volatile: set[str] = set()
        stable = [self._stable_copy(message, volatile) for message in messages[:stable_count]]
        canonical = canonical_json(stable).decode("utf-8")
        return CacheAlignment(
            prefix_hash=sha256_bytes(canonical.encode("utf-8")),
            stable_message_count=stable_count,
            volatile_tail_count=len(messages) - stable_count,
            cacheable_bytes=len(canonical.encode("utf-8")),
            volatile_fields=tuple(sorted(volatile)),
            canonical_prefix=canonical,
        )

    def align_request(self, payload: Mapping[str, Any], *, message_key: str = "messages", keep_tail: int = 1) -> dict[str, Any]:
        messages = payload.get(message_key) or []
        if not isinstance(messages, Sequence) or isinstance(messages, (str, bytes, bytearray)):
            raise TypeError(f"{message_key} must be a message sequence")
        alignment = self.align(messages, keep_tail=keep_tail)
        return {"alignment": asdict(alignment), "request": dict(payload)}


class SafeCommandRouter:
    """Classify shell work and choose exact-preserving Syntavra execution paths."""

    LONG_RUNNING = {
        "pytest", "py.test", "cargo", "npm", "pnpm", "yarn", "gradle", "mvn",
        "dotnet", "go", "make", "cmake", "ninja", "docker", "podman", "terraform",
        "tox", "nox", "ruff", "mypy", "eslint", "vitest", "jest", "ctest",
    }
    NETWORK = {"curl", "wget", "iwr", "invoke-webrequest", "pip", "uv", "npm", "pnpm", "yarn", "cargo", "git", "gh"}

    @staticmethod
    def _parse(command: str | Iterable[str]) -> tuple[tuple[str, ...], bool]:
        if isinstance(command, str):
            unsafe = bool(_SHELL_UNSAFE_RE.search(command))
            try:
                return tuple(shlex.split(command, posix=os.name != "nt")), unsafe
            except ValueError:
                return tuple(command.split()), True
        return tuple(str(item) for item in command), False

    @staticmethod
    def _family(argv: tuple[str, ...]) -> str:
        if not argv:
            return "empty"
        exe = Path(argv[0]).name.casefold()
        joined = " ".join(argv).casefold()
        if exe == "git":
            return "git"
        if exe == "gh":
            return "github"
        if exe in {"pytest", "py.test", "jest", "vitest", "ctest"} or " test" in f" {joined}":
            return "test"
        if exe in {"grep", "rg", "find", "fd", "ls", "tree"}:
            return "search"
        if exe in {"cat", "head", "tail", "sed", "type", "get-content"}:
            return "read"
        if exe in {"npm", "pnpm", "yarn", "pip", "uv", "cargo"}:
            return "package"
        if exe in {"kubectl", "aws", "gcloud", "az"}:
            return "cloud"
        if exe in {"docker", "podman"}:
            return "container"
        if exe in {"curl", "wget", "iwr", "invoke-webrequest"}:
            return "network"
        if exe in {"make", "cmake", "ninja", "gradle", "mvn", "dotnet", "go"}:
            return "build"
        return "generic"

    def route(
        self,
        command: str | Iterable[str],
        *,
        network_untrusted: bool = False,
        repeated: bool = False,
    ) -> RouteDecision:
        argv, ambiguous = self._parse(command)
        normalized = tuple(argv)
        joined = " ".join(normalized)
        family = self._family(normalized)
        reasons: list[str] = []
        if not argv:
            reasons.append("empty-command")
        if _DESTRUCTIVE_RE.search(joined):
            return RouteDecision(
                family, "blocked", normalized, (), (), ("destructive-command",), False,
                False, False, sha256_bytes(joined.encode("utf-8")), False,
            )
        exe = Path(argv[0]).name.casefold() if argv else ""
        long_running = exe in self.LONG_RUNNING or family in {"test", "build"}
        sandbox = network_untrusted and exe in self.NETWORK
        capture = family not in {"empty"}
        recommended = ["syntavra.output.capture"] if capture else []
        if family in {"search", "read"}:
            recommended.extend(("syntavra.inspect.map", "syntavra.output.search"))
        if family in {"test", "build"}:
            recommended.extend(("syntavra.process.submit", "syntavra.process.completions"))
        if sandbox:
            recommended.insert(0, "syntavra.sandbox.execute")
        if repeated and family in {"read", "search", "git", "github"}:
            reasons.append("repeat-read-elision-eligible")
            recommended.append("syntavra.output.search")
        replacement: tuple[str, ...] = ()
        safe_to_rewrite = not ambiguous
        mode = "execute-and-capture"
        if sandbox and safe_to_rewrite:
            replacement = ("syntavra", "sandbox", "execute", "--", *argv)
            mode = "sandbox-replace"
            reasons.append("untrusted-network-command")
        elif long_running and safe_to_rewrite:
            replacement = ("syntavra", "run", "--background", "--", *argv)
            mode = "background-replace"
            reasons.append("long-running-command")
        elif ambiguous:
            reasons.append("ambiguous-shell-syntax-no-rewrite")
        else:
            reasons.append("exact-command-preserved")
        return RouteDecision(
            family=family,
            mode=mode,
            normalized_command=normalized,
            replacement_argv=replacement,
            recommended_tools=tuple(dict.fromkeys(recommended)),
            reasons=tuple(reasons),
            capture_required=capture,
            background=long_running,
            sandbox=sandbox,
            repeat_key=sha256_bytes(canonical_json({"argv": normalized, "family": family})),
            safe_to_rewrite=safe_to_rewrite,
        )


class CommandCompactor:
    """Deterministic multi-family output compaction with exact-evidence handoff."""

    @staticmethod
    def _bounded(text: str, budget_bytes: int) -> str:
        raw = text.encode("utf-8")
        if len(raw) <= budget_bytes:
            return text
        marker = "\n[… exact output externalized; search or reveal for omitted evidence …]"
        keep = max(0, budget_bytes - len(marker.encode("utf-8")))
        return raw[:keep].decode("utf-8", errors="ignore").rstrip() + marker

    @staticmethod
    def _dedup(lines: Iterable[str]) -> list[str]:
        return list(dict.fromkeys(line for line in lines if line.strip()))

    @staticmethod
    def _json_preview(text: str) -> str | None:
        try:
            value = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            return None

        def shrink(item: Any, depth: int = 0) -> Any:
            if depth >= 4 and isinstance(item, (dict, list)):
                return f"<{type(item).__name__}:{len(item)}>"
            if isinstance(item, dict):
                keys = sorted(item, key=str)
                result = {str(key): shrink(item[key], depth + 1) for key in keys[:30]}
                if len(keys) > 30:
                    result["<omitted_keys>"] = len(keys) - 30
                return result
            if isinstance(item, list):
                if len(item) <= 10:
                    return [shrink(value, depth + 1) for value in item]
                return {
                    "<length>": len(item),
                    "<head>": [shrink(value, depth + 1) for value in item[:5]],
                    "<tail>": [shrink(value, depth + 1) for value in item[-2:]],
                }
            if isinstance(item, str) and len(item) > 300:
                return item[:180] + f"…<{len(item)} chars>…" + item[-60:]
            return item

        return json.dumps(shrink(value), ensure_ascii=False, sort_keys=True, indent=2)

    def _select(self, family: str, text: str) -> tuple[list[str], int]:
        lines = text.splitlines()
        errors = self._dedup(line for line in lines if _ERROR_RE.search(line) or _LOCATION_RE.search(line))
        if family == "test":
            summaries = self._dedup(line for line in lines if _TEST_SUMMARY_RE.search(line))
            failures = self._dedup(
                line for line in lines
                if line.lstrip().startswith(("E ", "F ", "FAILED", ">")) or _ERROR_RE.search(line)
            )
            return summaries[-12:] + failures[:48] + errors[:32], len(errors)
        if family == "git":
            status = self._dedup(
                line for line in lines
                if line.startswith(("On branch ", "Your branch ", "Changes ", "Untracked ", "diff --git", "@@ "))
                or line[:2] in {" M", "M ", "A ", " D", "D ", "??"}
                or _DIFF_HEADER_RE.match(line)
            )
            changed = [line for line in lines if line.startswith(("+", "-")) and not line.startswith(("+++", "---"))]
            return status[:80] + changed[:24] + changed[-12:] + errors[:24], len(errors)
        if family in {"search", "read"}:
            grouped: dict[str, list[str]] = defaultdict(list)
            for line in lines:
                if not line.strip():
                    continue
                key = line.split(":", 1)[0] if ":" in line else str(Path(line).parent)
                grouped[key].append(line)
            selected = [f"results={sum(map(len, grouped.values()))} groups={len(grouped)}"]
            for key, values in list(grouped.items())[:40]:
                selected.append(f"[{key}] {len(values)}")
                selected.extend(values[:4])
            return selected + errors[:24], len(errors)
        if family in {"package", "cloud", "container", "network"}:
            preview = self._json_preview(text)
            if preview is not None:
                return preview.splitlines(), len(errors)
            headings = self._dedup(
                line for line in lines
                if re.search(r"(?i)\b(name|version|status|state|image|id|resource|package|total|warning)\b", line)
            )
            return errors[:32] + headings[:48] + lines[:20] + lines[-10:], len(errors)
        if family in {"build", "github"}:
            summaries = self._dedup(
                line for line in lines
                if _ERROR_RE.search(line) or _LOCATION_RE.search(line)
                or re.search(r"(?i)\b(success|succeeded|complete|completed|warning|changed files?|checks?)\b", line)
            )
            return summaries[:80] + lines[-20:], len(errors)
        preview = self._json_preview(text)
        if preview is not None:
            return preview.splitlines(), len(errors)
        return errors[:40] + lines[:24] + lines[-12:], len(errors)

    def compact(
        self,
        command: str | Iterable[str],
        stdout: str,
        stderr: str = "",
        *,
        budget_bytes: int = 4096,
    ) -> CompactionResult:
        if budget_bytes < 256:
            raise ValueError("budget_bytes must be at least 256")
        router = SafeCommandRouter()
        family = router.route(command).family
        combined = stdout.rstrip("\n")
        if stderr:
            combined += ("\n[stderr]\n" if combined else "[stderr]\n") + stderr
        security = scan_text(combined)
        selected, retained_errors = self._select(family, security.redacted_text)
        header = (
            f"Syntavra compact family={family} lines={len(security.normalized_text.splitlines())} "
            f"secrets={len(security.secret_types)} injection_risk={str(security.injection_risk).lower()}"
        )
        visible = self._bounded(header + "\n" + "\n".join(self._dedup(selected)), budget_bytes)
        original_bytes = len(combined.encode("utf-8"))
        visible_bytes = len(visible.encode("utf-8"))
        return CompactionResult(
            family=family,
            visible_text=visible,
            original_bytes=original_bytes,
            visible_bytes=visible_bytes,
            savings_ratio=0.0 if original_bytes == 0 else max(0.0, 1.0 - visible_bytes / original_bytes),
            exact_required=visible_bytes < original_bytes,
            secret_types=security.secret_types,
            injection_risk=security.injection_risk,
            injection_reasons=security.injection_reasons,
            retained_error_lines=retained_errors,
        )


class InsightLedger:
    """Append-only local analytics for savings, reliability, routing, and cache behavior."""

    def __init__(self, path: Path):
        self.state = StateDB(path)
        with self.state.transaction(immediate=True) as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS fabric_events(
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_type TEXT NOT NULL,
                    family TEXT NOT NULL,
                    host TEXT NOT NULL,
                    raw_bytes INTEGER NOT NULL,
                    visible_bytes INTEGER NOT NULL,
                    latency_ms REAL NOT NULL,
                    success INTEGER NOT NULL,
                    cache_hit INTEGER NOT NULL,
                    metadata_json TEXT NOT NULL,
                    created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS fabric_event_type_idx ON fabric_events(event_type,created_at);
                CREATE INDEX IF NOT EXISTS fabric_family_idx ON fabric_events(family,created_at);
                """
            )

    def record(
        self,
        event_type: str,
        *,
        family: str = "generic",
        host: str = "unknown",
        raw_bytes: int = 0,
        visible_bytes: int = 0,
        latency_ms: float = 0.0,
        success: bool = True,
        cache_hit: bool = False,
        metadata: Mapping[str, Any] | None = None,
    ) -> int:
        with self.state.transaction(immediate=True) as db:
            cursor = db.execute(
                "INSERT INTO fabric_events(event_type,family,host,raw_bytes,visible_bytes,latency_ms,success,cache_hit,metadata_json,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    str(event_type), str(family), str(host), max(0, int(raw_bytes)), max(0, int(visible_bytes)),
                    max(0.0, float(latency_ms)), int(bool(success)), int(bool(cache_hit)),
                    json.dumps(dict(metadata or {}), ensure_ascii=False, sort_keys=True), time.time(),
                ),
            )
        return int(cursor.lastrowid)

    @staticmethod
    def _percentile(values: list[float], fraction: float) -> float:
        if not values:
            return 0.0
        ordered = sorted(values)
        index = min(len(ordered) - 1, max(0, math.ceil(len(ordered) * fraction) - 1))
        return float(ordered[index])

    def metrics(self, *, since_seconds: float | None = None) -> dict[str, Any]:
        sql = "SELECT * FROM fabric_events"
        params: list[Any] = []
        if since_seconds is not None:
            sql += " WHERE created_at>=?"
            params.append(time.time() - max(0.0, float(since_seconds)))
        sql += " ORDER BY event_id"
        with self.state.read() as db:
            rows = [dict(row) for row in db.execute(sql, params)]
        raw = sum(int(row["raw_bytes"]) for row in rows)
        visible = sum(int(row["visible_bytes"]) for row in rows)
        latencies = [float(row["latency_ms"]) for row in rows]
        families = Counter(str(row["family"]) for row in rows)
        event_types = Counter(str(row["event_type"]) for row in rows)
        hosts = Counter(str(row["host"]) for row in rows)
        successes = sum(int(row["success"]) for row in rows)
        cache_hits = sum(int(row["cache_hit"]) for row in rows)
        return {
            "events": len(rows),
            "success_rate": 1.0 if not rows else successes / len(rows),
            "cache_hit_rate": 0.0 if not rows else cache_hits / len(rows),
            "raw_bytes": raw,
            "visible_bytes": visible,
            "saved_bytes": max(0, raw - visible),
            "savings_ratio": 0.0 if raw == 0 else max(0.0, 1.0 - visible / raw),
            "latency_ms": {
                "mean": statistics.fmean(latencies) if latencies else 0.0,
                "p50": self._percentile(latencies, 0.50),
                "p95": self._percentile(latencies, 0.95),
                "max": max(latencies, default=0.0),
            },
            "families": dict(families.most_common()),
            "event_types": dict(event_types.most_common()),
            "hosts": dict(hosts.most_common()),
            "database_integrity": self.state.integrity_check(),
        }


class PlatformPlanBuilder:
    """Generate portable installation and enforcement plans for every registered host."""

    @staticmethod
    def _mcp_entry() -> dict[str, Any]:
        return {"command": "syntavra", "args": ["mcp"]}

    def plan(self, host: str, *, project: Path, scope: str = "project") -> dict[str, Any]:
        spec = host_spec(host)
        if host not in KNOWN_HOSTS:
            raise ValueError(f"unknown host: {host}")
        if scope not in {"project", "user"}:
            raise ValueError("scope must be project or user")
        files: list[dict[str, Any]] = []
        if spec.config_path:
            config: dict[str, Any] = {"mcpServers": {"syntavra": self._mcp_entry()}}
            if spec.supports_pre_tool_hook or spec.supports_post_tool_hook:
                config["hooks"] = {
                    "PreToolUse": [{"type": "command", "command": "syntavra hook pre"}],
                    "PostToolUse": [{"type": "command", "command": "syntavra hook post"}],
                    "PreCompact": [{"type": "command", "command": "syntavra hook pre-compact"}],
                    "SessionStart": [{"type": "command", "command": "syntavra hook session-start"}],
                }
            files.append({"path": spec.config_path, "merge": config})
        if spec.skill_path:
            files.append({
                "path": spec.skill_path.rstrip("/") + "/SKILL.md" if not spec.skill_path.endswith(".md") else spec.skill_path,
                "source": "bundled syntavra skill",
            })
        negotiation = negotiate(host, runtime_available=True, installed=None)
        return {
            "host": host,
            "display_name": spec.display_name,
            "scope": scope,
            "project": str(project.resolve(strict=False)),
            "mode": negotiation["mode"],
            "enforced": negotiation["enforced"],
            "verified_adapter": spec.verified,
            "files": files,
            "capabilities": asdict(spec),
            "validation": ["syntavra doctor", f"syntavra host negotiate --host-name {host}", "syntavra status"],
        }

    def all_plans(self, *, project: Path, scope: str = "project") -> dict[str, Any]:
        plans = [self.plan(host, project=project, scope=scope) for host in sorted(KNOWN_HOSTS) if host != "generic-mcp"]
        return {
            "hosts": plans,
            "host_count": len(plans),
            "enforced_count": sum(bool(plan["enforced"]) for plan in plans),
            "verified_count": sum(bool(plan["verified_adapter"]) for plan in plans),
        }


class StructuralNavigator:
    """Exact symbol-source and bounded file-range retrieval over StructuralIndex metadata."""

    def __init__(self, project: Path):
        self.project = project.resolve(strict=False)

    def _path(self, relative: str) -> Path:
        candidate = (self.project / relative).resolve(strict=False)
        try:
            candidate.relative_to(self.project)
        except ValueError as exc:
            raise PermissionError(f"path escapes project: {relative}") from exc
        return candidate

    def read_range(
        self,
        relative: str,
        *,
        start_line: int = 1,
        end_line: int | None = None,
        max_bytes: int = 64 * 1024,
    ) -> dict[str, Any]:
        path = self._path(relative)
        if not path.is_file():
            raise FileNotFoundError(relative)
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        start = max(1, int(start_line))
        end = len(lines) if end_line is None else min(len(lines), max(start, int(end_line)))
        selected = "\n".join(lines[start - 1:end])
        raw = selected.encode("utf-8")
        truncated = len(raw) > max_bytes
        if truncated:
            marker = "\n[… bounded source range; request a narrower line interval …]"
            keep = max(0, max_bytes - len(marker.encode("utf-8")))
            selected = raw[:keep].decode("utf-8", errors="ignore").rstrip() + marker
        return {
            "path": relative,
            "start_line": start,
            "end_line": end,
            "text": selected,
            "bytes": len(selected.encode("utf-8")),
            "file_hash": sha256_bytes(path.read_bytes()),
            "truncated": truncated,
        }

    def symbol_source(
        self,
        index: Any,
        query: str,
        *,
        limit: int = 8,
        context_lines: int = 2,
        max_bytes_per_symbol: int = 48 * 1024,
    ) -> dict[str, Any]:
        result = index.inspect_symbol(query, limit=max(1, int(limit)))
        sources: list[dict[str, Any]] = []
        for symbol in result.get("symbols", []):
            start = max(1, int(symbol.get("line", 1)) - max(0, int(context_lines)))
            end_line = int(symbol.get("end_line") or symbol.get("line") or start) + max(0, int(context_lines))
            source = self.read_range(
                str(symbol["path"]),
                start_line=start,
                end_line=end_line,
                max_bytes=max_bytes_per_symbol,
            )
            sources.append({"symbol": symbol, "source": source})
        return {"query": query, "matches": sources, "match_count": len(sources)}


class CompetitiveContextFabric:
    """Unified runtime surface that subsumes specialized token/context skills."""

    def __init__(self, path: Path, *, project: Path, host: str):
        self.project = project.resolve(strict=False)
        self.host = host
        self.planner = ToolSurfacePlanner()
        self.cache = CacheAligner()
        self.router = SafeCommandRouter()
        self.compactor = CommandCompactor()
        self.platforms = PlatformPlanBuilder()
        self.insight = InsightLedger(path)

    def profile(self, task: str, available_tools: Iterable[str], *, requested_profile: str = "auto") -> dict[str, Any]:
        started = time.perf_counter()
        result = self.planner.plan(task, host=self.host, available_tools=available_tools, requested_profile=requested_profile)
        self.insight.record(
            "profile", family=result["profile"], host=self.host,
            latency_ms=(time.perf_counter() - started) * 1000,
            metadata={"selected": result["selected_count"], "available": result["available_count"]},
        )
        return result

    def route(self, command: str | Iterable[str], *, network_untrusted: bool = False, repeated: bool = False) -> RouteDecision:
        started = time.perf_counter()
        result = self.router.route(command, network_untrusted=network_untrusted, repeated=repeated)
        self.insight.record(
            "route", family=result.family, host=self.host,
            latency_ms=(time.perf_counter() - started) * 1000,
            success=result.mode != "blocked", cache_hit=repeated,
            metadata={"mode": result.mode, "capture": result.capture_required},
        )
        return result

    def compact(self, command: str | Iterable[str], stdout: str, stderr: str = "", *, budget_bytes: int = 4096) -> CompactionResult:
        started = time.perf_counter()
        result = self.compactor.compact(command, stdout, stderr, budget_bytes=budget_bytes)
        self.insight.record(
            "compact", family=result.family, host=self.host,
            raw_bytes=result.original_bytes, visible_bytes=result.visible_bytes,
            latency_ms=(time.perf_counter() - started) * 1000,
            success=True, metadata={"injection_risk": result.injection_risk, "secrets": list(result.secret_types)},
        )
        return result

    def align_cache(self, messages: Sequence[Mapping[str, Any]], *, keep_tail: int = 1) -> CacheAlignment:
        started = time.perf_counter()
        result = self.cache.align(messages, keep_tail=keep_tail)
        self.insight.record(
            "cache-align", family="provider-request", host=self.host,
            raw_bytes=result.cacheable_bytes, visible_bytes=result.cacheable_bytes,
            latency_ms=(time.perf_counter() - started) * 1000,
            cache_hit=False, metadata={"stable_messages": result.stable_message_count},
        )
        return result

    def doctor(self) -> dict[str, Any]:
        backends = {
            "docker": shutil.which("docker"),
            "podman": shutil.which("podman"),
            "bwrap": shutil.which("bwrap") if os.name != "nt" else None,
        }
        spec = host_spec(self.host)
        checks = {
            "analytics_database": self.insight.state.integrity_check(),
            "project_exists": self.project.exists(),
            "known_host": self.host in KNOWN_HOSTS,
            "mcp_available": spec.supports_mcp,
            "result_replacement": spec.supports_result_replacement,
            "enforced_mode": negotiate(self.host).get("enforced", False),
            "strict_sandbox_available": any(backends.values()),
            "platform_registry_size": len(KNOWN_HOSTS),
        }
        return {
            "ok": all(bool(value) for key, value in checks.items() if key not in {"strict_sandbox_available", "result_replacement"}),
            "host": self.host,
            "checks": checks,
            "sandbox_backends": backends,
            "negotiation": negotiate(self.host),
            "profile_names": sorted(PROFILES),
            "limitations": [
                key for key, value in checks.items()
                if not value and key in {"strict_sandbox_available", "result_replacement", "enforced_mode"}
            ],
        }

    def insights(self, *, since_seconds: float | None = None) -> dict[str, Any]:
        return self.insight.metrics(since_seconds=since_seconds)
