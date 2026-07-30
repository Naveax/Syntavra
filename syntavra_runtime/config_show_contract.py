from __future__ import annotations

import copy
from typing import Any, Mapping

from .unified_config import ConfigError

ROUTE = "config.show"
CANDIDATE_CAPABILITY = "config.show"
INPUT_PROFILE = "live-config-discovery-v1"
INPUT_FORMAT = "R6CFG1"
RESULT_KEYS = frozenset(
    {"schema_version", "values", "provenance", "config_hash", "warnings"}
)


def show_result(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    """Return the deterministic config projection; invocation time is not configuration."""

    actual = frozenset(str(key) for key in snapshot)
    if actual != RESULT_KEYS:
        raise ConfigError(
            f"config show snapshot keys differ from the canonical contract: {sorted(actual)!r}"
        )
    return {
        "schema_version": int(snapshot["schema_version"]),
        "values": copy.deepcopy(snapshot["values"]),
        "provenance": copy.deepcopy(snapshot["provenance"]),
        "config_hash": str(snapshot["config_hash"]),
        "warnings": list(snapshot["warnings"]),
    }


def rust_argv(wire: bytes) -> tuple[str, ...]:
    return ("config", "show", bytes(wire).hex())
