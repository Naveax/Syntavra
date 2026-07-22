from __future__ import annotations

import json
import shutil
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping


def _now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True)
class TokenPanel:
    raw_context_tokens: int = 0
    compiled_context_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    externalized_bytes: int = 0
    visible_output_bytes: int = 0
    original_output_bytes: int = 0
    current_cost: float = 0.0
    avoided_estimated_cost: float = 0.0

    @property
    def saved_tokens(self) -> int:
        return max(0, self.raw_context_tokens - self.compiled_context_tokens)

    @property
    def context_reduction(self) -> float:
        return 1.0 - (self.compiled_context_tokens / max(1, self.raw_context_tokens))

    @property
    def output_reduction(self) -> float:
        return 1.0 - (self.visible_output_bytes / max(1, self.original_output_bytes))


@dataclass(frozen=True)
class ConsoleSnapshot:
    generated_at: str
    product: str
    version: str
    channel: str
    task_state: str
    plan: tuple[str, ...]
    active_symbols: tuple[str, ...]
    changed_files: tuple[str, ...]
    tests: dict[str, Any]
    token_panel: TokenPanel
    tool_calls: tuple[dict[str, Any], ...]
    capability_decisions: tuple[dict[str, Any], ...]
    sandbox: dict[str, Any]
    session: dict[str, Any]
    adapters: dict[str, Any]
    risk: str
    retries: int
    claim_boundary: tuple[str, ...] = field(default_factory=tuple)


class InteractiveConsole:
    """One event model for JSON, TUI and future local dashboard transports."""

    def __init__(self, *, product: str = "Syntavra", version: str = "0.0.1", channel: str = "pre-release"):
        self.product = product
        self.version = version
        self.channel = channel

    def snapshot(
        self,
        *,
        task_state: str = "idle",
        plan: list[str] | tuple[str, ...] = (),
        active_symbols: list[str] | tuple[str, ...] = (),
        changed_files: list[str] | tuple[str, ...] = (),
        tests: Mapping[str, Any] | None = None,
        tokens: TokenPanel | None = None,
        tool_calls: list[Mapping[str, Any]] | tuple[Mapping[str, Any], ...] = (),
        capability_decisions: list[Mapping[str, Any]] | tuple[Mapping[str, Any], ...] = (),
        sandbox: Mapping[str, Any] | None = None,
        session: Mapping[str, Any] | None = None,
        adapters: Mapping[str, Any] | None = None,
        risk: str = "low",
        retries: int = 0,
        claim_boundary: list[str] | tuple[str, ...] = (),
    ) -> ConsoleSnapshot:
        return ConsoleSnapshot(
            generated_at=_now(),
            product=self.product,
            version=self.version,
            channel=self.channel,
            task_state=task_state,
            plan=tuple(plan),
            active_symbols=tuple(active_symbols),
            changed_files=tuple(changed_files),
            tests=dict(tests or {}),
            token_panel=tokens or TokenPanel(),
            tool_calls=tuple(dict(item) for item in tool_calls),
            capability_decisions=tuple(dict(item) for item in capability_decisions),
            sandbox=dict(sandbox or {}),
            session=dict(session or {}),
            adapters=dict(adapters or {}),
            risk=risk,
            retries=max(0, int(retries)),
            claim_boundary=tuple(claim_boundary),
        )

    @staticmethod
    def json(snapshot: ConsoleSnapshot) -> str:
        value = asdict(snapshot)
        value["token_panel"]["saved_tokens"] = snapshot.token_panel.saved_tokens
        value["token_panel"]["context_reduction"] = snapshot.token_panel.context_reduction
        value["token_panel"]["output_reduction"] = snapshot.token_panel.output_reduction
        return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2)

    @staticmethod
    def _bar(value: float, width: int = 24) -> str:
        bounded = max(0.0, min(1.0, value))
        filled = round(bounded * width)
        return "█" * filled + "░" * (width - filled)

    def render(self, snapshot: ConsoleSnapshot, *, width: int | None = None) -> str:
        terminal_width = width or shutil.get_terminal_size((100, 30)).columns
        panel = snapshot.token_panel
        header = f"{snapshot.product} {snapshot.version} {snapshot.channel}"
        lines = [header, "═" * min(terminal_width, max(40, len(header)))]
        lines.extend(
            [
                f"State: {snapshot.task_state:<18} Risk: {snapshot.risk:<8} Retries: {snapshot.retries}",
                f"Context: {panel.compiled_context_tokens:,}/{panel.raw_context_tokens:,}  {self._bar(panel.context_reduction)}  saved {panel.saved_tokens:,}",
                f"Output:  {panel.visible_output_bytes:,}/{panel.original_output_bytes:,} bytes  {self._bar(panel.output_reduction)}",
                f"Cache: read {panel.cache_read_tokens:,} · write {panel.cache_write_tokens:,} · externalized {panel.externalized_bytes:,} bytes",
                f"Cost: current {panel.current_cost:.6f} · avoided estimate {panel.avoided_estimated_cost:.6f}",
            ]
        )
        if snapshot.plan:
            lines.append("\nPlan")
            lines.extend(f"  {index + 1}. {item}" for index, item in enumerate(snapshot.plan))
        if snapshot.active_symbols:
            lines.append("\nActive symbols")
            lines.extend(f"  • {item}" for item in snapshot.active_symbols[:20])
        if snapshot.changed_files:
            lines.append("\nChanged files")
            lines.extend(f"  • {item}" for item in snapshot.changed_files[:30])
        if snapshot.tests:
            lines.append("\nTests")
            lines.extend(f"  {key}: {value}" for key, value in sorted(snapshot.tests.items()))
        if snapshot.sandbox:
            lines.append("\nSandbox")
            lines.extend(f"  {key}: {value}" for key, value in sorted(snapshot.sandbox.items()))
        if snapshot.session:
            lines.append("\nSession")
            lines.extend(f"  {key}: {value}" for key, value in sorted(snapshot.session.items()))
        if snapshot.adapters:
            lines.append("\nAdapters")
            lines.extend(f"  {key}: {value}" for key, value in sorted(snapshot.adapters.items()))
        if snapshot.claim_boundary:
            lines.append("\nEvidence gates")
            lines.extend(f"  ! {item}" for item in snapshot.claim_boundary)
        return "\n".join(lines)

    def write_dashboard_payload(self, snapshot: ConsoleSnapshot, destination: Path) -> dict[str, Any]:
        destination.parent.mkdir(parents=True, exist_ok=True)
        payload = self.json(snapshot)
        temporary = destination.with_name(f".{destination.name}.tmp")
        temporary.write_text(payload + "\n", encoding="utf-8")
        temporary.replace(destination)
        return {"ok": True, "path": str(destination), "bytes": len(payload.encode("utf-8"))}


__all__ = ["ConsoleSnapshot", "InteractiveConsole", "TokenPanel"]
