from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Any, Mapping

from .errors import SchemaError

SCHEMA_VERSION = 2
_MAX_TEXT = 4096
_MAX_ITEMS = 64
_ALLOWED_RISK = {"R0", "R1", "R2", "R3", "R4", "R5"}
_ALLOWED_PRIVACY = {"PUBLIC", "PROJECT", "SENSITIVE", "LOCAL_ONLY"}


def _bounded_text(name: str, value: Any, minimum: int = 1, maximum: int = _MAX_TEXT) -> str:
    text = str(value).strip()
    if not minimum <= len(text) <= maximum:
        raise SchemaError(f"{name} length is outside [{minimum}, {maximum}]")
    return text


def _string_tuple(name: str, value: Any, *, allow_empty: bool = False) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise SchemaError(f"{name} must be a list")
    if len(value) > _MAX_ITEMS or (not allow_empty and not value):
        raise SchemaError(f"{name} has an invalid item count")
    items = tuple(sorted({_bounded_text(name, item, 1, 128) for item in value}))
    if not allow_empty and not items:
        raise SchemaError(f"{name} cannot be empty")
    return items


def _budget(name: str, value: Any, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= maximum:
        raise SchemaError(f"{name} must be an integer in [0, {maximum}]")
    return value


@dataclass(frozen=True, slots=True)
class RobloxTaskState:
    schema_version: int
    task_id: str
    session_id: str
    place_id: str
    project_id: str
    project_fingerprint: str
    studio_process_id: int
    intent: str
    requested_capabilities: tuple[str, ...]
    authorized_capabilities: tuple[str, ...]
    evidence_requirements: tuple[str, ...]
    risk_class: str
    privacy_class: str
    execution_constraints: tuple[str, ...]
    token_budget: int
    request_budget: int
    transfer_budget: int
    gpu_budget: int
    wall_time_budget: int
    required_validators: tuple[str, ...]
    rollback_requirements: tuple[str, ...]
    completion_conditions: tuple[str, ...]

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "RobloxTaskState":
        required = {
            "schema_version", "task_id", "session_id", "place_id", "project_id",
            "project_fingerprint", "studio_process_id", "intent",
            "requested_capabilities", "authorized_capabilities", "evidence_requirements",
            "risk_class", "privacy_class", "execution_constraints", "token_budget",
            "request_budget", "transfer_budget", "gpu_budget", "wall_time_budget",
            "required_validators", "rollback_requirements", "completion_conditions",
        }
        if set(value) != required:
            missing = sorted(required - set(value))
            unknown = sorted(set(value) - required)
            raise SchemaError(f"task-state fields mismatch; missing={missing}, unknown={unknown}")
        if value["schema_version"] != SCHEMA_VERSION:
            raise SchemaError("unsupported task-state schema version")
        if isinstance(value["studio_process_id"], bool) or not isinstance(value["studio_process_id"], int) or value["studio_process_id"] <= 0:
            raise SchemaError("studio_process_id must be a positive integer")
        risk = _bounded_text("risk_class", value["risk_class"], 2, 2)
        privacy = _bounded_text("privacy_class", value["privacy_class"], 3, 32)
        if risk not in _ALLOWED_RISK:
            raise SchemaError("unsupported risk class")
        if privacy not in _ALLOWED_PRIVACY:
            raise SchemaError("unsupported privacy class")
        requested = _string_tuple("requested_capabilities", value["requested_capabilities"])
        authorized = _string_tuple("authorized_capabilities", value["authorized_capabilities"])
        if not set(requested).issubset(authorized):
            raise SchemaError("requested capabilities exceed authorization")
        return cls(
            schema_version=SCHEMA_VERSION,
            task_id=_bounded_text("task_id", value["task_id"], 8, 128),
            session_id=_bounded_text("session_id", value["session_id"], 8, 128),
            place_id=_bounded_text("place_id", value["place_id"], 1, 128),
            project_id=_bounded_text("project_id", value["project_id"], 1, 128),
            project_fingerprint=_bounded_text("project_fingerprint", value["project_fingerprint"], 16, 256),
            studio_process_id=value["studio_process_id"],
            intent=_bounded_text("intent", value["intent"], 1, _MAX_TEXT),
            requested_capabilities=requested,
            authorized_capabilities=authorized,
            evidence_requirements=_string_tuple("evidence_requirements", value["evidence_requirements"]),
            risk_class=risk,
            privacy_class=privacy,
            execution_constraints=_string_tuple("execution_constraints", value["execution_constraints"], allow_empty=True),
            token_budget=_budget("token_budget", value["token_budget"], 2_000_000),
            request_budget=_budget("request_budget", value["request_budget"], 10_000),
            transfer_budget=_budget("transfer_budget", value["transfer_budget"], 10_000_000_000),
            gpu_budget=_budget("gpu_budget", value["gpu_budget"], 86_400_000),
            wall_time_budget=_budget("wall_time_budget", value["wall_time_budget"], 86_400),
            required_validators=_string_tuple("required_validators", value["required_validators"]),
            rollback_requirements=_string_tuple("rollback_requirements", value["rollback_requirements"], allow_empty=True),
            completion_conditions=_string_tuple("completion_conditions", value["completion_conditions"]),
        )

    def canonical_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    def canonical_hash(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()

    def to_mapping(self) -> dict[str, Any]:
        return asdict(self)


def migrate_task_state(value: Mapping[str, Any]) -> dict[str, Any]:
    version = value.get("schema_version")
    if version == SCHEMA_VERSION:
        return dict(value)
    if version != 1:
        raise SchemaError("no migration path for task state")
    migrated = dict(value)
    migrated["schema_version"] = SCHEMA_VERSION
    migrated.setdefault("project_id", migrated.get("place_id", "unknown-project"))
    migrated.setdefault("privacy_class", "PROJECT")
    migrated.setdefault("gpu_budget", 0)
    migrated.setdefault("rollback_requirements", [])
    return migrated
