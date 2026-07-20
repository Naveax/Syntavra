from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from .util import atomic_write_json, read_json, sha256_bytes


COUNTERS = (
    "model_turns", "tool_calls", "wait_calls", "command_calls", "compactions",
    "fresh_input_tokens", "cached_input_tokens", "output_tokens", "reasoning_tokens",
    "malformed_lines", "duplicate_events",
)


def _file_identity(path: Path) -> str:
    stat = path.stat()
    return sha256_bytes(f"{path.resolve(strict=True)}|{getattr(stat, 'st_ino', 0)}|{stat.st_dev}".encode())


def _walk(value: Any) -> Iterable[tuple[str, Any]]:
    if isinstance(value, dict):
        for key, item in value.items():
            yield key, item
            yield from _walk(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk(item)


def _first_number(event: dict[str, Any], aliases: tuple[str, ...]) -> int:
    aliases_set = {alias.casefold() for alias in aliases}
    for key, value in _walk(event):
        if key.casefold() in aliases_set and isinstance(value, (int, float)) and value >= 0:
            return int(value)
    return 0


def _event_id(event: dict[str, Any], raw: bytes) -> str:
    for key in ("event_id", "id", "call_id", "response_id"):
        value = event.get(key)
        if isinstance(value, str) and value:
            return f"{key}:{value}"
    return "sha256:" + sha256_bytes(raw)


def normalize_event(event: dict[str, Any]) -> dict[str, int]:
    text = json.dumps(event, ensure_ascii=False).casefold()
    event_type = str(event.get("type") or event.get("event") or event.get("kind") or "").casefold()
    counts = {name: 0 for name in COUNTERS}
    if any(token in event_type for token in ("response.completed", "assistant_message", "model_response", "turn_context")):
        counts["model_turns"] = 1
    if "tool" in event_type or "function_call" in text:
        counts["tool_calls"] = 1
    if any(token in text for token in ('"wait"', 'write_stdin', 'process is still running', 'still running')):
        counts["wait_calls"] = 1
    if any(token in text for token in ('exec_command', 'shell_command', 'command_execution')):
        counts["command_calls"] = 1
    if any(token in text for token in ('compaction', 'compact_context', 'context_compacted')):
        counts["compactions"] = 1
    total_input = _first_number(event, ("input_tokens", "input_token_count", "prompt_tokens"))
    explicit_fresh = _first_number(event, ("uncached_input_tokens", "fresh_input_tokens"))
    cached = _first_number(event, ("cached_input_tokens", "cache_read_input_tokens", "cached_tokens"))
    counts["cached_input_tokens"] = cached
    counts["fresh_input_tokens"] = explicit_fresh if explicit_fresh else max(0, total_input - cached)
    counts["output_tokens"] = _first_number(event, ("output_tokens", "completion_tokens", "output_token_count"))
    counts["reasoning_tokens"] = _first_number(event, ("reasoning_tokens", "reasoning_token_count"))
    return counts


class RolloutTailer:
    def __init__(self, rollout: Path, state_file: Path):
        self.rollout = rollout
        self.state_file = state_file

    def poll(self) -> dict[str, Any]:
        path = self.rollout.resolve(strict=True)
        identity = _file_identity(path)
        state = read_json(self.state_file, {}) or {}
        if state.get("file_identity") != identity or path.stat().st_size < int(state.get("offset", 0)):
            state = {
                "file_identity": identity,
                "offset": 0,
                "partial_hex": "",
                "counters": {name: 0 for name in COUNTERS},
                "seen": [],
            }
        counters = {name: int((state.get("counters") or {}).get(name, 0)) for name in COUNTERS}
        seen_list = list(state.get("seen") or [])[-10000:]
        seen = set(seen_list)
        offset = int(state.get("offset", 0))
        partial = bytes.fromhex(state.get("partial_hex", "")) if state.get("partial_hex") else b""
        with path.open("rb") as handle:
            handle.seek(offset)
            chunk = handle.read()
            safe_offset = handle.tell()
        buffer = partial + chunk
        lines = buffer.split(b"\n")
        trailing = lines.pop() if buffer and not buffer.endswith(b"\n") else b""
        processed = 0
        for raw in lines:
            if not raw.strip():
                continue
            try:
                event = json.loads(raw)
                if not isinstance(event, dict):
                    raise ValueError("event must be object")
            except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
                counters["malformed_lines"] += 1
                continue
            event_id = _event_id(event, raw)
            if event_id in seen:
                counters["duplicate_events"] += 1
                continue
            seen.add(event_id)
            seen_list.append(event_id)
            for key, value in normalize_event(event).items():
                counters[key] += value
            processed += 1
        result = {
            "file_identity": identity,
            "offset": safe_offset,
            "partial_hex": trailing.hex(),
            "counters": counters,
            "seen": seen_list[-10000:],
        }
        atomic_write_json(self.state_file, result)
        return {
            "processed_events": processed,
            "offset": safe_offset,
            "partial_bytes": len(trailing),
            "counters": counters,
            "efficiency": {
                "fresh_fraction": counters["fresh_input_tokens"] / max(1, counters["fresh_input_tokens"] + counters["cached_input_tokens"]),
                "wait_calls_per_turn": counters["wait_calls"] / max(1, counters["model_turns"]),
            },
        }


def discover_rollouts(codex_home: Path) -> list[Path]:
    if not codex_home.exists():
        return []
    candidates = [path for path in codex_home.rglob("*.jsonl") if path.is_file()]
    return sorted(candidates, key=lambda path: path.stat().st_mtime, reverse=True)


def select_active_rollout(candidates: list[Path], *, session_id: str | None = None) -> Path | None:
    if session_id:
        for path in candidates:
            if session_id in path.name or session_id in str(path.parent):
                return path
    return candidates[0] if candidates else None
