from __future__ import annotations

import contextlib
import contextvars
import json
import math
import os
import secrets
import threading
import time
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterator, Mapping

from .security_scan import scan_text
from .util import atomic_write_json


_current_trace: contextvars.ContextVar["TraceContext | None"] = contextvars.ContextVar("signalcore_trace", default=None)


@dataclass(frozen=True)
class TraceContext:
    trace_id: str
    span_id: str
    parent_span_id: str = ""
    request_id: str = ""
    session_id: str = ""
    evidence_handle: str = ""

    @staticmethod
    def root(*, request_id: str = "", session_id: str = "") -> "TraceContext":
        return TraceContext(secrets.token_hex(16), secrets.token_hex(8), "", request_id, session_id)

    def child(self, *, evidence_handle: str = "") -> "TraceContext":
        return TraceContext(
            self.trace_id,
            secrets.token_hex(8),
            self.span_id,
            self.request_id,
            self.session_id,
            evidence_handle or self.evidence_handle,
        )


@dataclass
class Span:
    name: str
    context: TraceContext
    started_at: float
    attributes: dict[str, Any] = field(default_factory=dict)
    ended_at: float = 0.0
    status: str = "unset"
    error: str = ""

    @property
    def duration_ms(self) -> float:
        end = self.ended_at or time.perf_counter()
        return max(0.0, (end - self.started_at) * 1000)


class MetricsRegistry:
    def __init__(self):
        self._lock = threading.RLock()
        self._counters: Counter[tuple[str, tuple[tuple[str, str], ...]]] = Counter()
        self._gauges: dict[tuple[str, tuple[tuple[str, str], ...]], float] = {}
        self._histograms: dict[tuple[str, tuple[tuple[str, str], ...]], list[float]] = defaultdict(list)

    @staticmethod
    def _key(name: str, labels: Mapping[str, Any] | None) -> tuple[str, tuple[tuple[str, str], ...]]:
        normalized = tuple(sorted((str(key), str(value)) for key, value in (labels or {}).items()))
        return name, normalized

    def inc(self, name: str, value: float = 1.0, *, labels: Mapping[str, Any] | None = None) -> None:
        if not math.isfinite(value):
            raise ValueError("metric value must be finite")
        with self._lock:
            self._counters[self._key(name, labels)] += value

    def set(self, name: str, value: float, *, labels: Mapping[str, Any] | None = None) -> None:
        if not math.isfinite(value):
            raise ValueError("metric value must be finite")
        with self._lock:
            self._gauges[self._key(name, labels)] = value

    def observe(self, name: str, value: float, *, labels: Mapping[str, Any] | None = None, max_samples: int = 4096) -> None:
        if not math.isfinite(value):
            raise ValueError("metric value must be finite")
        with self._lock:
            samples = self._histograms[self._key(name, labels)]
            samples.append(value)
            if len(samples) > max_samples:
                del samples[: len(samples) - max_samples]

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "counters": [
                    {"name": name, "labels": dict(labels), "value": value}
                    for (name, labels), value in self._counters.items()
                ],
                "gauges": [
                    {"name": name, "labels": dict(labels), "value": value}
                    for (name, labels), value in self._gauges.items()
                ],
                "histograms": [
                    {
                        "name": name,
                        "labels": dict(labels),
                        "count": len(values),
                        "sum": sum(values),
                        "min": min(values) if values else 0.0,
                        "max": max(values) if values else 0.0,
                        "p50": self._percentile(values, 0.50),
                        "p95": self._percentile(values, 0.95),
                        "p99": self._percentile(values, 0.99),
                    }
                    for (name, labels), values in self._histograms.items()
                ],
            }

    @staticmethod
    def _percentile(values: list[float], q: float) -> float:
        if not values:
            return 0.0
        ordered = sorted(values)
        return ordered[min(len(ordered) - 1, max(0, round((len(ordered) - 1) * q)))]

    def prometheus(self) -> str:
        lines: list[str] = []
        snapshot = self.snapshot()
        for group in ("counters", "gauges"):
            for item in snapshot[group]:
                labels = self._format_labels(item["labels"])
                lines.append(f"{self._metric_name(item['name'])}{labels} {item['value']}")
        for item in snapshot["histograms"]:
            labels = self._format_labels(item["labels"])
            base = self._metric_name(item["name"])
            lines.extend((
                f"{base}_count{labels} {item['count']}",
                f"{base}_sum{labels} {item['sum']}",
                f"{base}_p50{labels} {item['p50']}",
                f"{base}_p95{labels} {item['p95']}",
                f"{base}_p99{labels} {item['p99']}",
            ))
        return "\n".join(lines) + ("\n" if lines else "")

    @staticmethod
    def _metric_name(value: str) -> str:
        return "signalcore_" + "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in value).strip("_")

    @staticmethod
    def _format_labels(labels: Mapping[str, str]) -> str:
        if not labels:
            return ""
        encoded = ",".join(f'{key}="{value.replace(chr(34), chr(92)+chr(34))}"' for key, value in labels.items())
        return "{" + encoded + "}"


class Observability:
    def __init__(self, root: Path, *, service: str = "signalcore", sample_rate: float = 1.0):
        if not 0.0 <= sample_rate <= 1.0:
            raise ValueError("sample_rate must be between 0 and 1")
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.service = service
        self.sample_rate = sample_rate
        self.metrics = MetricsRegistry()
        self.log_path = self.root / "events.jsonl"
        self._lock = threading.RLock()

    @contextlib.contextmanager
    def span(self, name: str, *, attributes: Mapping[str, Any] | None = None) -> Iterator[Span]:
        parent = _current_trace.get()
        context = parent.child() if parent else TraceContext.root()
        token = _current_trace.set(context)
        span = Span(name, context, time.perf_counter(), dict(attributes or {}))
        self.metrics.inc("spans_started_total", labels={"name": name})
        try:
            yield span
            span.status = "ok"
        except Exception as exc:
            span.status = "error"
            span.error = type(exc).__name__
            self.metrics.inc("span_errors_total", labels={"name": name, "error": type(exc).__name__})
            raise
        finally:
            span.ended_at = time.perf_counter()
            self.metrics.observe("span_duration_ms", span.duration_ms, labels={"name": name, "status": span.status})
            self.emit("span", {
                "name": span.name,
                "status": span.status,
                "duration_ms": span.duration_ms,
                "attributes": span.attributes,
                "error": span.error,
                "trace": asdict(span.context),
            })
            _current_trace.reset(token)

    def emit(self, event_type: str, payload: Mapping[str, Any], *, level: str = "info") -> None:
        if self.sample_rate < 1.0:
            marker = int.from_bytes(os.urandom(8), "big") / (2**64 - 1)
            if marker > self.sample_rate:
                return
        redacted = self._redact(payload)
        context = _current_trace.get()
        event = {
            "timestamp": time.time(),
            "level": level,
            "service": self.service,
            "event": event_type,
            "trace": asdict(context) if context else {},
            "payload": redacted,
        }
        encoded = json.dumps(event, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
        with self._lock:
            with self.log_path.open("a", encoding="utf-8") as handle:
                handle.write(encoded)
                handle.flush()

    def diagnostic_bundle(self, destination: Path, *, extra: Mapping[str, Any] | None = None) -> Path:
        bundle = {
            "schema_version": 1,
            "generated_at": time.time(),
            "service": self.service,
            "metrics": self.metrics.snapshot(),
            "extra": self._redact(extra or {}),
            "log_tail": self._log_tail(200),
        }
        atomic_write_json(destination, bundle, mode=0o600)
        return destination

    def _log_tail(self, lines: int) -> list[dict[str, Any]]:
        if not self.log_path.is_file():
            return []
        raw = self.log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:]
        result: list[dict[str, Any]] = []
        for line in raw:
            try:
                result.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return result

    def _redact(self, value: Any) -> Any:
        if isinstance(value, Mapping):
            result: dict[str, Any] = {}
            for key, child in value.items():
                name = str(key)
                if any(marker in name.casefold() for marker in ("token", "secret", "password", "authorization", "api_key", "credential")):
                    result[name] = "[REDACTED]"
                else:
                    result[name] = self._redact(child)
            return result
        if isinstance(value, list):
            return [self._redact(item) for item in value]
        if isinstance(value, tuple):
            return [self._redact(item) for item in value]
        if isinstance(value, str):
            return scan_text(value).redacted_text
        return value
