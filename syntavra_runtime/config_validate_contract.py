from __future__ import annotations

from typing import Any, Mapping

ROUTE = "config.validate"
INPUT_PROFILE = "live-config-discovery-v1"
INPUT_FORMAT = "R6CFG1"
CANDIDATE_CAPABILITY = "config.resolve"
RESULT_KEYS = frozenset({"ok", "config_hash", "warnings"})


def validation_result(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    """Project one deterministic validation result from a canonical config snapshot."""

    return {
        "ok": True,
        "config_hash": str(snapshot["config_hash"]),
        "warnings": list(snapshot.get("warnings", [])),
    }


def rust_argv(wire: bytes) -> tuple[str, ...]:
    """Use the already-proven Rust config.resolve primitive for validation parity."""

    return ("config", "resolve", bytes(wire).hex())
