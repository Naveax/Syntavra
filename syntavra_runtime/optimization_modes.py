from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping

from .util import atomic_write_json, read_json, sha256_bytes, canonical_json


@dataclass(frozen=True)
class OptimizationMode:
    name: str
    description: str
    output_budget_bytes: int
    context_budget_tokens: int
    schema_profile: str
    rewrite_commands: bool
    cache_optimize: bool
    memory_extract: bool
    auto_delegate: bool
    style: str


MODES: dict[str, OptimizationMode] = {
    "full": OptimizationMode("full", "Balanced default with every exact-preserving optimizer enabled.", 24_000, 8_000, "balanced", True, True, True, True, "normal"),
    "lite": OptimizationMode("lite", "Conservative compression with minimal behavior change.", 48_000, 4_000, "balanced", True, True, False, False, "normal"),
    "ultra": OptimizationMode("ultra", "Codex-oriented maximum context economy with exact recovery handles.", 8_000, 1_500, "minimal", True, True, True, True, "terse"),
    "commit": OptimizationMode("commit", "Small diff/status surface for commit preparation.", 12_000, 1_500, "minimal", True, True, False, False, "commit"),
    "review": OptimizationMode("review", "Evidence-rich code review with bounded output.", 32_000, 3_000, "balanced", True, True, True, True, "review"),
    "compress": OptimizationMode("compress", "Output-only compression; routing and delegation disabled.", 10_000, 1_500, "minimal", False, False, False, False, "terse"),
}
ALIASES = {"default": "full", "balanced": "full", "tiny": "ultra", "codex-ultra": "ultra", "codex_ultra": "ultra", "off": "lite"}


def normalize_mode(value: str) -> str:
    name = ALIASES.get(value.strip().casefold(), value.strip().casefold())
    if name not in MODES:
        raise ValueError(f"unknown optimization mode: {value}")
    return name


class OptimizationModeStore:
    def __init__(self, state_root: Path):
        self.path = Path(state_root) / "optimization-mode.json"

    def current(self) -> OptimizationMode:
        raw = read_json(self.path, {}) or {}
        try:
            return MODES[normalize_mode(str(raw.get("mode") or "full"))]
        except ValueError:
            return MODES["full"]

    def set(self, mode: str, *, source: str = "user") -> dict[str, Any]:
        selected = MODES[normalize_mode(mode)]
        body = {
            "mode": selected.name,
            "source": source,
            "updated_at": time.time(),
            "profile": asdict(selected),
        }
        body["receipt_hash"] = sha256_bytes(canonical_json(body))
        atomic_write_json(self.path, body)
        return body

    def manifest(self) -> dict[str, Any]:
        return {
            "active": asdict(self.current()),
            "available": [asdict(MODES[name]) for name in MODES],
            "instant_switch": True,
        }


class SavingsLedger:
    """Content-free, append-only savings events used by statusline/dashboard."""

    def __init__(self, state_root: Path):
        self.path = Path(state_root) / "analytics" / "savings.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def record(
        self,
        *,
        source: str,
        original_tokens: int,
        visible_tokens: int,
        provider_cost_before: float | None = None,
        provider_cost_after: float | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        if original_tokens < 0 or visible_tokens < 0:
            raise ValueError("token counts must be non-negative")
        event = {
            "timestamp": time.time(),
            "source": source,
            "original_tokens": int(original_tokens),
            "visible_tokens": int(visible_tokens),
            "saved_tokens": max(0, int(original_tokens) - int(visible_tokens)),
            "provider_cost_before": provider_cost_before,
            "provider_cost_after": provider_cost_after,
            "metadata": dict(metadata or {}),
        }
        event["event_hash"] = sha256_bytes(canonical_json(event))
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
        return event

    def summary(self, *, since_seconds: float | None = None) -> dict[str, Any]:
        cutoff = time.time() - since_seconds if since_seconds is not None else None
        rows: list[dict[str, Any]] = []
        if self.path.is_file():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if cutoff is None or float(row.get("timestamp", 0)) >= cutoff:
                    rows.append(row)
        original = sum(int(row.get("original_tokens", 0)) for row in rows)
        visible = sum(int(row.get("visible_tokens", 0)) for row in rows)
        saved = sum(int(row.get("saved_tokens", 0)) for row in rows)
        before = sum(float(row["provider_cost_before"]) for row in rows if row.get("provider_cost_before") is not None)
        after = sum(float(row["provider_cost_after"]) for row in rows if row.get("provider_cost_after") is not None)
        by_source: dict[str, int] = {}
        for row in rows:
            key = str(row.get("source") or "unknown")
            by_source[key] = by_source.get(key, 0) + int(row.get("saved_tokens", 0))
        return {
            "events": len(rows),
            "original_tokens": original,
            "visible_tokens": visible,
            "saved_tokens": saved,
            "savings_ratio": (saved / original) if original else 0.0,
            "provider_cost_before": before,
            "provider_cost_after": after,
            "provider_cost_saved": max(0.0, before - after),
            "by_source": dict(sorted(by_source.items())),
        }


def render_statusline(state_root: Path, *, compact: bool = True) -> str:
    mode = OptimizationModeStore(state_root).current()
    summary = SavingsLedger(state_root).summary()
    saved = summary["saved_tokens"]
    if saved >= 1_000_000:
        saved_text = f"{saved / 1_000_000:.1f}m"
    elif saved >= 1_000:
        saved_text = f"{saved / 1_000:.1f}k"
    else:
        saved_text = str(saved)
    cost = float(summary["provider_cost_saved"])
    suffix = f" ${cost:.2f}" if cost > 0 else ""
    if compact:
        return f"[SYN:{mode.name.upper()}] ⇩{saved_text}{suffix}"
    return f"Syntavra mode={mode.name} saved_tokens={saved} saved_cost={cost:.6f}"
