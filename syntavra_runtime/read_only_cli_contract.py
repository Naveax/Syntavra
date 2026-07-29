from __future__ import annotations

import copy
from typing import Any

ROUTING_PHASE = "R24"
ROUTING_SCHEMA_VERSION = 12

_PIPELINE_DESCRIPTION: dict[str, Any] = {
    "canonical": True,
    "exact_evidence": True,
    "fail_closed": True,
    "schema_version": 1,
    "stages": [
        "validate",
        "authorize",
        "configure",
        "request-security",
        "request-evidence",
        "context",
        "provider",
        "response-evidence",
        "response-security",
        "data-route",
        "deliver",
    ],
    "typed_delivery": True,
}

_PLUGIN_INVENTORY: dict[str, Any] = {
    "discovery": "explicit-only",
    "plugins": [],
}

_ROUTE_RESULTS: dict[str, dict[str, Any]] = {
    "pipeline.describe": _PIPELINE_DESCRIPTION,
    "plugins.list": _PLUGIN_INVENTORY,
}

_ROUTE_RUST_ARGV: dict[str, tuple[str, ...]] = {
    "pipeline.describe": ("pipeline", "describe"),
    "plugins.list": ("plugins", "list"),
}


def pipeline_description() -> dict[str, Any]:
    return copy.deepcopy(_PIPELINE_DESCRIPTION)


def plugin_inventory() -> dict[str, Any]:
    return copy.deepcopy(_PLUGIN_INVENTORY)


def static_route_result(route: str) -> dict[str, Any]:
    normalized = str(route).strip().casefold()
    try:
        return copy.deepcopy(_ROUTE_RESULTS[normalized])
    except KeyError as exc:
        raise ValueError(f"unsupported R24 static read-only route: {normalized}") from exc


def static_route_rust_argv(route: str) -> tuple[str, ...]:
    normalized = str(route).strip().casefold()
    try:
        return _ROUTE_RUST_ARGV[normalized]
    except KeyError as exc:
        raise ValueError(f"unsupported R24 static read-only route: {normalized}") from exc


def static_routes() -> tuple[str, ...]:
    return tuple(sorted(_ROUTE_RESULTS))
