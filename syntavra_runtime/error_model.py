from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any, Mapping


class ErrorCategory(StrEnum):
    CONFIG = "config"
    AUTH = "auth"
    PERMISSION = "permission"
    VALIDATION = "validation"
    PROVIDER = "provider"
    RATE_LIMIT = "rate-limit"
    TIMEOUT = "timeout"
    NETWORK = "network"
    STORAGE = "storage"
    INTEGRITY = "integrity"
    SECURITY = "security"
    SANDBOX = "sandbox"
    MIGRATION = "migration"
    CAPACITY = "capacity"
    INTERNAL = "internal"


@dataclass(frozen=True)
class ErrorDescriptor:
    code: str
    category: ErrorCategory
    message: str
    retryable: bool = False
    fatal: bool = False
    http_status: int = 500
    remediation: tuple[str, ...] = ()
    details: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class SyntavraError(RuntimeError):
    def __init__(self, descriptor: ErrorDescriptor, *, cause: BaseException | None = None):
        super().__init__(descriptor.message)
        self.descriptor = descriptor
        self.__cause__ = cause

    @property
    def code(self) -> str:
        return self.descriptor.code

    def to_dict(self) -> dict[str, Any]:
        return {"error": self.descriptor.to_dict()}


_REGISTRY: dict[str, ErrorDescriptor] = {}


def register_error(descriptor: ErrorDescriptor) -> None:
    if not descriptor.code or descriptor.code in _REGISTRY:
        raise ValueError(f"duplicate or empty error code: {descriptor.code}")
    _REGISTRY[descriptor.code] = descriptor


def error_descriptor(code: str, **details: Any) -> ErrorDescriptor:
    base = _REGISTRY.get(code)
    if base is None:
        return ErrorDescriptor(
            code=code or "SC_INTERNAL_UNKNOWN",
            category=ErrorCategory.INTERNAL,
            message="Unhandled Syntavra error",
            fatal=True,
            details=details,
        )
    return ErrorDescriptor(
        code=base.code,
        category=base.category,
        message=base.message,
        retryable=base.retryable,
        fatal=base.fatal,
        http_status=base.http_status,
        remediation=base.remediation,
        details=details,
    )


def raise_error(code: str, *, cause: BaseException | None = None, **details: Any) -> None:
    raise SyntavraError(error_descriptor(code, **details), cause=cause)


for _descriptor in (
    ErrorDescriptor("SC_CONFIG_INVALID", ErrorCategory.CONFIG, "Syntavra configuration is invalid", False, True, 400),
    ErrorDescriptor("SC_AUTH_REQUIRED", ErrorCategory.AUTH, "Authentication is required", False, False, 401),
    ErrorDescriptor("SC_AUTH_INVALID", ErrorCategory.AUTH, "Authentication failed", False, False, 401),
    ErrorDescriptor("SC_PERMISSION_DENIED", ErrorCategory.PERMISSION, "Operation is not permitted", False, False, 403),
    ErrorDescriptor("SC_REQUEST_INVALID", ErrorCategory.VALIDATION, "Request validation failed", False, False, 400),
    ErrorDescriptor("SC_PROVIDER_RATE_LIMIT", ErrorCategory.RATE_LIMIT, "Provider rate limit reached", True, False, 429),
    ErrorDescriptor("SC_PROVIDER_TIMEOUT", ErrorCategory.TIMEOUT, "Provider request timed out", True, False, 504),
    ErrorDescriptor("SC_PROVIDER_UNAVAILABLE", ErrorCategory.PROVIDER, "Provider is unavailable", True, False, 502),
    ErrorDescriptor("SC_CAPACITY_EXHAUSTED", ErrorCategory.CAPACITY, "Runtime capacity is exhausted", True, False, 503),
    ErrorDescriptor("SC_STORAGE_QUOTA", ErrorCategory.STORAGE, "Storage quota is exhausted", False, True, 507),
    ErrorDescriptor("SC_STORAGE_CORRUPT", ErrorCategory.INTEGRITY, "Stored state failed integrity verification", False, True, 500),
    ErrorDescriptor("SC_EVIDENCE_WRITE_FAILED", ErrorCategory.STORAGE, "Exact evidence could not be committed", False, True, 507),
    ErrorDescriptor("SC_SECURITY_BLOCKED", ErrorCategory.SECURITY, "Security policy blocked the operation", False, True, 403),
    ErrorDescriptor("SC_SANDBOX_UNAVAILABLE", ErrorCategory.SANDBOX, "Required sandbox guarantee is unavailable", False, True, 503),
    ErrorDescriptor("SC_MIGRATION_FAILED", ErrorCategory.MIGRATION, "State migration failed and was rolled back", False, True, 500),
):
    register_error(_descriptor)
