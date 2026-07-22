from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from .errors import ValidationError


@dataclass(frozen=True, slots=True)
class ValidationResult:
    validator: str
    passed: bool
    detail: str


@dataclass(frozen=True, slots=True)
class ProofNode:
    requirement: str
    evidence: tuple[str, ...]
    execution: str
    artifact_hash: str
    validator_results: tuple[ValidationResult, ...]
    observed_result: str
    residual_risk: str


def validate_response_schema(response: Mapping[str, Any], required: set[str]) -> ValidationResult:
    missing = required - set(response)
    return ValidationResult("response_schema", not missing, "missing=" + ",".join(sorted(missing)))


def validate_artifact_hash(content: bytes, expected: str) -> ValidationResult:
    actual = hashlib.sha256(content).hexdigest()
    return ValidationResult("artifact_hash", actual == expected, actual)


def validate_capability(executed: set[str], authorized: set[str]) -> ValidationResult:
    extra = executed - authorized
    return ValidationResult("capability", not extra, "extra=" + ",".join(sorted(extra)))


def validate_budget(used: int, limit: int) -> ValidationResult:
    return ValidationResult("budget", used <= limit, f"{used}/{limit}")


def require_all(results: tuple[ValidationResult, ...]) -> None:
    failed = [result for result in results if not result.passed]
    if failed:
        raise ValidationError("; ".join(f"{item.validator}:{item.detail}" for item in failed))
