from __future__ import annotations

import copy
import unicodedata
from typing import Any, Mapping

from .unified_config import ConfigError

ROUTE = "config.explain"
CANDIDATE_CAPABILITY = "config.explain"
INPUT_PROFILE = "live-config-discovery-v1"
INPUT_FORMAT = "R6CFG1"
MAX_EXPLAIN_PATH_BYTES = 512
FOUND_KEYS = frozenset({"path", "value", "source", "scope"})
NOT_FOUND_KEYS = frozenset({"found", "path"})


def validate_explain_path(value: str) -> str:
    path = str(value)
    encoded = path.encode("utf-8")
    if not encoded:
        raise ConfigError("config explain path must not be empty")
    if len(encoded) > MAX_EXPLAIN_PATH_BYTES:
        raise ConfigError("config explain path exceeds the input limit")
    if any(unicodedata.category(character) == "Cc" for character in path):
        raise ConfigError("config explain path contains a control character")
    if any(not segment for segment in path.split(".")):
        raise ConfigError("config explain path contains an empty segment")
    return path


def explain_result(snapshot: Mapping[str, Any], dotted_path: str) -> dict[str, Any]:
    path = validate_explain_path(dotted_path)
    provenance = snapshot.get("provenance", [])
    if not isinstance(provenance, list):
        raise ConfigError("config snapshot provenance must be a list")
    for raw in reversed(provenance):
        if not isinstance(raw, Mapping):
            raise ConfigError("config snapshot provenance entry must be an object")
        if raw.get("path") == path:
            return {
                "path": path,
                "value": copy.deepcopy(raw.get("value")),
                "source": str(raw.get("source")),
                "scope": str(raw.get("scope")),
            }
    return {"found": False, "path": path}


def rust_argv(wire: bytes, dotted_path: str) -> tuple[str, ...]:
    path = validate_explain_path(dotted_path)
    return ("config", "explain", bytes(wire).hex(), path.encode("utf-8").hex())
