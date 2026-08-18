from __future__ import annotations

import hashlib
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from .artifacts import ArtifactStore
from .secret_redaction import SecretRedactor


OPERATION_KINDS = ("call", "map", "parallel", "filter", "reduce")
_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$")


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _canonical(value: Any) -> bytes:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise TypeError("programmatic execution values must be canonical JSON values") from exc


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


@dataclass(frozen=True)
class ArtifactReference:
    artifact_id: str

    def __post_init__(self) -> None:
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", self.artifact_id):
            raise ValueError("artifact reference must be a sha256 content identity")

    @property
    def uri(self) -> str:
        return f"artifact://{self.artifact_id}"


@dataclass(frozen=True)
class ProgrammaticStep:
    step_id: str
    operation: str
    function: str
    arguments: tuple[Any, ...] = ()
    keyword_arguments: dict[str, Any] = field(default_factory=dict)
    items: tuple[Any, ...] = ()
    initial: Any = None
    has_initial: bool = False
    max_workers: int = 4

    def __post_init__(self) -> None:
        if not _NAME_RE.fullmatch(self.step_id):
            raise ValueError("invalid programmatic step_id")
        if self.operation not in OPERATION_KINDS:
            raise ValueError(f"unsupported programmatic operation: {self.operation}")
        if not _NAME_RE.fullmatch(self.function):
            raise ValueError("invalid programmatic function name")
        if self.max_workers < 1 or self.max_workers > 64:
            raise ValueError("max_workers must be between 1 and 64")
        _canonical(self.to_dict())

    @staticmethod
    def _encode(value: Any) -> Any:
        if isinstance(value, ArtifactReference):
            return {"$artifact": value.artifact_id}
        if isinstance(value, tuple):
            return [ProgrammaticStep._encode(item) for item in value]
        if isinstance(value, list):
            return [ProgrammaticStep._encode(item) for item in value]
        if isinstance(value, Mapping):
            return {str(key): ProgrammaticStep._encode(child) for key, child in value.items()}
        return value

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "operation": self.operation,
            "function": self.function,
            "arguments": self._encode(self.arguments),
            "keyword_arguments": self._encode(self.keyword_arguments),
            "items": self._encode(self.items),
            "initial": self._encode(self.initial) if self.has_initial else None,
            "has_initial": self.has_initial,
            "max_workers": self.max_workers,
        }

    @property
    def step_sha256(self) -> str:
        return _sha256(_canonical(self.to_dict()))


@dataclass(frozen=True)
class ProgrammaticFunction:
    name: str
    function: Callable[..., Any]
    pure: bool = True
    description: str = ""


class ProgrammaticFunctionRegistry:
    """Explicit callable registry. No dynamic import, eval, or implicit discovery."""

    def __init__(self) -> None:
        self._functions: dict[str, ProgrammaticFunction] = {}

    def register(
        self,
        name: str,
        function: Callable[..., Any],
        *,
        pure: bool = True,
        description: str = "",
    ) -> ProgrammaticFunction:
        if not _NAME_RE.fullmatch(name):
            raise ValueError("invalid programmatic function name")
        if not callable(function):
            raise TypeError("programmatic function must be callable")
        if name in self._functions:
            raise ValueError(f"programmatic function already registered: {name}")
        spec = ProgrammaticFunction(name=name, function=function, pure=bool(pure), description=str(description))
        self._functions[name] = spec
        return spec

    def require(self, name: str) -> ProgrammaticFunction:
        try:
            return self._functions[name]
        except KeyError as exc:
            raise KeyError(f"unknown programmatic function: {name}") from exc

    def catalog(self) -> tuple[dict[str, Any], ...]:
        return tuple(
            {"name": item.name, "pure": item.pure, "description": item.description}
            for item in sorted(self._functions.values(), key=lambda value: value.name)
        )


@dataclass(frozen=True)
class ProgrammaticResult:
    sha256: str
    byte_count: int
    item_count: int | None
    inline_value: Any | None
    artifact: ArtifactReference | None
    preview: str
    exact_recovery: bool

    @property
    def externalized(self) -> bool:
        return self.artifact is not None


@dataclass(frozen=True)
class ProgrammaticExecutionReceipt:
    receipt_id: str
    step_id: str
    step_sha256: str
    operation: str
    function: str
    started_at: str
    duration_ms: float
    ok: bool
    result: ProgrammaticResult | None
    error_type: str
    error_message: str
    input_items: int
    pure_function: bool


class ProgrammaticExecutionPlane:
    """Typed bounded execution over explicitly registered Python callables.

    Results are canonical JSON. Small results stay inline; large results are
    externalized to the existing ArtifactStore and represented by a content
    address. Batch operations preserve input order. Parallel execution is only
    allowed for callables explicitly registered as pure.
    """

    def __init__(
        self,
        state_root: Path,
        *,
        registry: ProgrammaticFunctionRegistry | None = None,
        artifact_store: ArtifactStore | None = None,
        max_inline_bytes: int = 16 * 1024,
        max_preview_bytes: int = 2048,
        max_items: int = 10_000,
        max_workers: int = 16,
    ) -> None:
        self.state_root = state_root.resolve(strict=False)
        self.state_root.mkdir(parents=True, exist_ok=True)
        self.registry = registry or ProgrammaticFunctionRegistry()
        self.artifacts = artifact_store or ArtifactStore(self.state_root / "artifacts")
        self.max_inline_bytes = max(128, int(max_inline_bytes))
        self.max_preview_bytes = max(64, int(max_preview_bytes))
        self.max_items = max(1, int(max_items))
        self.max_workers = max(1, min(64, int(max_workers)))
        self.redactor = SecretRedactor()

    def _resolve(self, value: Any) -> Any:
        if isinstance(value, ArtifactReference):
            record = self.artifacts.record(value.artifact_id)
            if record.media_type != "application/json":
                raise TypeError("programmatic artifact input must be application/json")
            return json.loads(self.artifacts.read(value.artifact_id).decode("utf-8"))
        if isinstance(value, tuple):
            return tuple(self._resolve(item) for item in value)
        if isinstance(value, list):
            return [self._resolve(item) for item in value]
        if isinstance(value, Mapping):
            return {str(key): self._resolve(child) for key, child in value.items()}
        return value

    def _items(self, step: ProgrammaticStep) -> list[Any]:
        values = [self._resolve(item) for item in step.items]
        if len(values) > self.max_items:
            raise ValueError(f"programmatic item limit exceeded: {len(values)}>{self.max_items}")
        return values

    @staticmethod
    def _item_count(value: Any) -> int | None:
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            return len(value)
        if isinstance(value, Mapping):
            return len(value)
        return None

    def _preview(self, value: Any) -> str:
        if isinstance(value, list) and len(value) > 6:
            display: Any = {"type": "array", "items": len(value), "sample": value[:3]}
        elif isinstance(value, Mapping) and len(value) > 12:
            display = {"type": "object", "keys": sorted(str(key) for key in value)[:12], "key_count": len(value)}
        else:
            display = value
        rendered = _canonical(display).decode("utf-8")
        raw = rendered.encode("utf-8")
        if len(raw) <= self.max_preview_bytes:
            return rendered
        clipped = raw[: self.max_preview_bytes].decode("utf-8", errors="ignore")
        return clipped + "…"

    def _result(self, value: Any, *, step: ProgrammaticStep) -> ProgrammaticResult:
        data = _canonical(value)
        digest = _sha256(data)
        preview = self._preview(value)
        if len(data) <= self.max_inline_bytes:
            return ProgrammaticResult(
                sha256=digest,
                byte_count=len(data),
                item_count=self._item_count(value),
                inline_value=json.loads(data.decode("utf-8")),
                artifact=None,
                preview=preview,
                exact_recovery=True,
            )
        record = self.artifacts.put(
            data,
            media_type="application/json",
            kind="programmatic-result",
            metadata={
                "step_id": step.step_id,
                "operation": step.operation,
                "function": step.function,
                "result_sha256": digest,
            },
        )
        reference = ArtifactReference(record.artifact_id)
        exact = self.artifacts.verify(record.artifact_id)["ok"] and record.sha256 == digest
        return ProgrammaticResult(
            sha256=digest,
            byte_count=len(data),
            item_count=self._item_count(value),
            inline_value=None,
            artifact=reference,
            preview=preview,
            exact_recovery=bool(exact),
        )

    def _invoke(self, step: ProgrammaticStep, spec: ProgrammaticFunction) -> Any:
        arguments = tuple(self._resolve(item) for item in step.arguments)
        keyword_arguments = {str(key): self._resolve(value) for key, value in step.keyword_arguments.items()}
        if step.operation == "call":
            return spec.function(*arguments, **keyword_arguments)

        items = self._items(step)
        if step.operation == "map":
            return [spec.function(item, *arguments, **keyword_arguments) for item in items]
        if step.operation == "parallel":
            if not spec.pure:
                raise PermissionError("parallel execution requires a callable registered as pure")
            workers = min(step.max_workers, self.max_workers, max(1, len(items)))
            with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="syntavra-programmatic") as pool:
                return list(pool.map(lambda item: spec.function(item, *arguments, **keyword_arguments), items))
        if step.operation == "filter":
            return [item for item in items if bool(spec.function(item, *arguments, **keyword_arguments))]
        if step.operation == "reduce":
            if step.has_initial:
                accumulator = self._resolve(step.initial)
                remaining = items
            else:
                if not items:
                    raise ValueError("reduce requires a non-empty item set when no initial value is supplied")
                accumulator = items[0]
                remaining = items[1:]
            for item in remaining:
                accumulator = spec.function(accumulator, item, *arguments, **keyword_arguments)
            return accumulator
        raise AssertionError(step.operation)

    def execute(self, step: ProgrammaticStep) -> ProgrammaticExecutionReceipt:
        spec = self.registry.require(step.function)
        started_at = _now()
        started = time.monotonic()
        result: ProgrammaticResult | None = None
        error_type = ""
        error_message = ""
        ok = False
        try:
            value = self._invoke(step, spec)
            result = self._result(value, step=step)
            ok = True
        except Exception as exc:
            error_type = type(exc).__name__
            redacted, _ = self.redactor.redact_text(str(exc))
            error_message = redacted[:2000]
        duration_ms = round((time.monotonic() - started) * 1000, 3)
        identity = {
            "step_sha256": step.step_sha256,
            "ok": ok,
            "result_sha256": result.sha256 if result else None,
            "error_type": error_type,
            "error_message": error_message,
        }
        receipt_id = "sha256:" + _sha256(_canonical(identity))
        input_items = len(step.items) if step.operation != "call" else 0
        return ProgrammaticExecutionReceipt(
            receipt_id=receipt_id,
            step_id=step.step_id,
            step_sha256=step.step_sha256,
            operation=step.operation,
            function=step.function,
            started_at=started_at,
            duration_ms=duration_ms,
            ok=ok,
            result=result,
            error_type=error_type,
            error_message=error_message,
            input_items=input_items,
            pure_function=spec.pure,
        )

    def require(self, step: ProgrammaticStep) -> ProgrammaticExecutionReceipt:
        receipt = self.execute(step)
        if not receipt.ok:
            raise RuntimeError(f"programmatic execution failed: {receipt.error_type}: {receipt.error_message}")
        return receipt

    def recover(self, reference: ArtifactReference) -> Any:
        record = self.artifacts.record(reference.artifact_id)
        if record.kind != "programmatic-result" or record.media_type != "application/json":
            raise TypeError("artifact is not a programmatic execution result")
        return json.loads(self.artifacts.read(reference.artifact_id).decode("utf-8"))


__all__ = [
    "ArtifactReference",
    "OPERATION_KINDS",
    "ProgrammaticExecutionPlane",
    "ProgrammaticExecutionReceipt",
    "ProgrammaticFunction",
    "ProgrammaticFunctionRegistry",
    "ProgrammaticResult",
    "ProgrammaticStep",
]
