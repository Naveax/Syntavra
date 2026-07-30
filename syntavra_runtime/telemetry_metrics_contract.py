from __future__ import annotations

import hashlib
import json
from typing import Any, Final

CONTRACT_VERSION: Final = 1
ROUTE: Final = "telemetry.metrics"
CAPABILITY: Final = ROUTE
INPUT_PROFILE: Final = "process-local-empty-metrics-v1"
INPUT_FORMAT: Final = "canonical-output-format"
FORMATS: Final = ("json", "prometheus")


class TelemetryMetricsError(RuntimeError):
    pass


def canonical_format(value: str = "json") -> str:
    selected = str(value).strip().casefold()
    if selected not in FORMATS:
        raise TelemetryMetricsError("TELEMETRY_METRICS_FORMAT_INVALID")
    return selected


def empty_metrics_snapshot() -> dict[str, list[dict[str, Any]]]:
    return {"counters": [], "gauges": [], "histograms": []}


def telemetry_metrics_result(output_format: str = "json") -> dict[str, Any]:
    selected = canonical_format(output_format)
    if selected == "prometheus":
        return {"format": selected, "text": ""}
    return {"format": selected, "metrics": empty_metrics_snapshot()}


def canonical_request_bytes(output_format: str = "json") -> bytes:
    return json.dumps(
        {"route": ROUTE, "format": canonical_format(output_format)},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def request_digest(output_format: str = "json") -> str:
    return hashlib.sha256(canonical_request_bytes(output_format)).hexdigest()


def rust_argv(output_format: str = "json") -> tuple[str, ...]:
    return ("telemetry", "metrics", canonical_format(output_format))


__all__ = [
    "CAPABILITY",
    "CONTRACT_VERSION",
    "FORMATS",
    "INPUT_FORMAT",
    "INPUT_PROFILE",
    "ROUTE",
    "TelemetryMetricsError",
    "canonical_format",
    "canonical_request_bytes",
    "empty_metrics_snapshot",
    "request_digest",
    "rust_argv",
    "telemetry_metrics_result",
]
