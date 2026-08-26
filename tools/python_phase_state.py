from __future__ import annotations

from typing import Any


def required_internal_capabilities(registry: dict[str, Any]) -> list[dict[str, Any]]:
    capabilities = registry.get("capabilities") or []
    if not isinstance(capabilities, list):
        raise AssertionError("capability registry capabilities must be a list")
    return [
        item
        for item in capabilities
        if isinstance(item, dict)
        and item.get("required_for_python_complete") is True
        and item.get("classification") != "EXTERNAL"
    ]


def compute_python_phase_state(registry: dict[str, Any]) -> dict[str, Any]:
    required = required_internal_capabilities(registry)
    uncertified = [
        str(item.get("id") or "")
        for item in required
        if item.get("state") != "certified"
    ]
    ready = not uncertified
    return {
        "ready": ready,
        "rust_resume_allowed": ready,
        "required_internal": required,
        "required_internal_count": len(required),
        "uncertified_required": uncertified,
        "uncertified_required_count": len(uncertified),
    }


def validate_python_complete_state(registry: dict[str, Any]) -> dict[str, Any]:
    state = compute_python_phase_state(registry)
    persisted = registry.get("python_complete") or {}
    if persisted.get("claim") != "PYTHON_COMPLETE":
        raise AssertionError("Python COMPLETE claim drift")
    if persisted.get("requires_all_internal_required_capabilities_certified") is not True:
        raise AssertionError("Python COMPLETE no longer requires all internal capabilities")
    if persisted.get("external_superiority_required") is not False:
        raise AssertionError("external superiority must not be manufactured as an internal completion gate")
    if persisted.get("ready") is not state["ready"]:
        raise AssertionError("persisted Python COMPLETE readiness disagrees with required capability state")
    if persisted.get("rust_resume_allowed") is not state["rust_resume_allowed"]:
        raise AssertionError("persisted Rust resume readiness disagrees with Python COMPLETE")
    return state
