from __future__ import annotations

import codecs
import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Any, Iterable, Mapping

from .security_scan import IncrementalSecurityScanner, scan_text
from .util import canonical_json


class StreamError(RuntimeError):
    pass


@dataclass(frozen=True)
class StreamEvent:
    sequence: int
    event: str
    data: str
    event_id: str
    retry_ms: int | None
    event_hash: str
    chain_hash: str


@dataclass(frozen=True)
class StreamSemanticSummary:
    event_count: int
    chain_root: str
    usage: dict[str, int]
    secret_types: tuple[str, ...]
    injection_risk: bool
    done_seen: bool
    parse_errors: tuple[str, ...]


class SSEParser:
    """Incremental UTF-8/SSE parser with bounded event size and hash chaining."""

    def __init__(self, *, max_event_bytes: int = 2 * 1024 * 1024):
        if max_event_bytes < 1024:
            raise ValueError("max_event_bytes must be at least 1024")
        self.max_event_bytes = max_event_bytes
        self._decoder = codecs.getincrementaldecoder("utf-8")("strict")
        self._buffer = ""
        self._lines: list[str] = []
        self._line_bytes = 0
        self._sequence = 0
        self._chain = "0" * 64

    def feed(self, chunk: bytes) -> list[StreamEvent]:
        try:
            text = self._decoder.decode(chunk, final=False)
        except UnicodeDecodeError as exc:
            raise StreamError("stream contains invalid UTF-8") from exc
        self._buffer += text
        events: list[StreamEvent] = []
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            if line.endswith("\r"):
                line = line[:-1]
            if line == "":
                event = self._emit()
                if event is not None:
                    events.append(event)
                continue
            self._line_bytes += len(line.encode("utf-8")) + 1
            if self._line_bytes > self.max_event_bytes:
                raise StreamError("SSE event exceeds configured maximum")
            if not line.startswith(":"):
                self._lines.append(line)
        return events

    def finalize(self) -> list[StreamEvent]:
        try:
            self._buffer += self._decoder.decode(b"", final=True)
        except UnicodeDecodeError as exc:
            raise StreamError("stream ends with invalid UTF-8") from exc
        if self._buffer:
            line = self._buffer[:-1] if self._buffer.endswith("\r") else self._buffer
            self._line_bytes += len(line.encode("utf-8"))
            if self._line_bytes > self.max_event_bytes:
                raise StreamError("SSE event exceeds configured maximum")
            if not line.startswith(":"):
                self._lines.append(line)
        self._buffer = ""
        event = self._emit()
        return [event] if event is not None else []

    def _emit(self) -> StreamEvent | None:
        if not self._lines:
            self._line_bytes = 0
            return None
        event_name = "message"
        event_id = ""
        retry: int | None = None
        data_lines: list[str] = []
        for line in self._lines:
            field, _, value = line.partition(":")
            if value.startswith(" "):
                value = value[1:]
            if field == "event":
                event_name = value
            elif field == "id" and "\x00" not in value:
                event_id = value
            elif field == "retry" and value.isdigit():
                retry = int(value)
            elif field == "data":
                data_lines.append(value)
        self._lines = []
        self._line_bytes = 0
        if not data_lines and event_name == "message" and not event_id and retry is None:
            return None
        self._sequence += 1
        data = "\n".join(data_lines)
        payload = canonical_json({
            "sequence": self._sequence,
            "event": event_name,
            "data": data,
            "id": event_id,
            "retry": retry,
        })
        event_hash = hashlib.sha256(payload).hexdigest()
        self._chain = hashlib.sha256(bytes.fromhex(self._chain) + bytes.fromhex(event_hash)).hexdigest()
        return StreamEvent(self._sequence, event_name, data, event_id, retry, event_hash, self._chain)


class StreamSemanticProcessor:
    def __init__(self, *, content_type: str, max_event_bytes: int = 2 * 1024 * 1024):
        self.content_type = content_type.casefold()
        self.parser = SSEParser(max_event_bytes=max_event_bytes) if "event-stream" in self.content_type else None
        self.events: list[StreamEvent] = []
        self.secrets: set[str] = set()
        self.injection_risk = False
        self.done_seen = False
        self.usage: dict[str, int] = {}
        self.parse_errors: list[str] = []
        self._text_decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self._security = IncrementalSecurityScanner()

    def feed(self, chunk: bytes) -> None:
        text = self._text_decoder.decode(chunk, final=False)
        self._scan(text)
        if self.parser is None:
            return
        try:
            events = self.parser.feed(chunk)
        except StreamError as exc:
            self.parse_errors.append(str(exc))
            raise
        for event in events:
            self._accept(event)

    def finalize(self) -> StreamSemanticSummary:
        tail = self._text_decoder.decode(b"", final=True)
        self._scan(tail)
        if self.parser is not None:
            try:
                for event in self.parser.finalize():
                    self._accept(event)
            except StreamError as exc:
                self.parse_errors.append(str(exc))
                raise
        return StreamSemanticSummary(
            event_count=len(self.events),
            chain_root=self.events[-1].chain_hash if self.events else "0" * 64,
            usage=dict(sorted(self.usage.items())),
            secret_types=tuple(sorted(self.secrets)),
            injection_risk=self.injection_risk,
            done_seen=self.done_seen,
            parse_errors=tuple(self.parse_errors),
        )

    def _scan(self, text: str) -> None:
        if not text:
            return
        self._security.feed(text)
        security = self._security.result()
        self.secrets.update(security.secret_types)
        self.injection_risk = self.injection_risk or security.injection_risk

    def _accept(self, event: StreamEvent) -> None:
        self.events.append(event)
        if event.data.strip() == "[DONE]":
            self.done_seen = True
            return
        try:
            value = json.loads(event.data)
        except (json.JSONDecodeError, TypeError):
            return
        self._collect_usage(value)

    def _collect_usage(self, value: Any) -> None:
        if isinstance(value, Mapping):
            for key, child in value.items():
                normalized = str(key).casefold()
                if normalized in {
                    "input_tokens", "prompt_tokens", "output_tokens", "completion_tokens",
                    "cached_tokens", "cache_read_input_tokens", "cache_creation_input_tokens",
                    "reasoning_tokens", "total_tokens",
                } and isinstance(child, int) and child >= 0:
                    self.usage[normalized] = max(self.usage.get(normalized, 0), child)
                else:
                    self._collect_usage(child)
        elif isinstance(value, list):
            for child in value:
                self._collect_usage(child)
