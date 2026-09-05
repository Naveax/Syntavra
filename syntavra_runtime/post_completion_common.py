from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, is_dataclass
from typing import Any, Mapping, Sequence


def canonical_json(value: Any) -> str:
    if is_dataclass(value):
        value = asdict(value)
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def sha256_digest(value: Any) -> str:
    if isinstance(value, bytes):
        data = value
    elif isinstance(value, str):
        data = value.encode("utf-8")
    else:
        data = canonical_json(value).encode("utf-8")
    return "sha256:" + hashlib.sha256(data).hexdigest()


def content_receipt(claim: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    body = {"claim": claim, **dict(payload)}
    return {**body, "receipt_id": sha256_digest(body)}


def require_nonblank(value: str, name: str) -> str:
    normalized = str(value).strip()
    if not normalized:
        raise ValueError(f"{name} is required")
    return normalized


def sorted_unique(values: Sequence[str]) -> tuple[str, ...]:
    return tuple(sorted({str(value).strip() for value in values if str(value).strip()}))


def bounded_text(value: Any, limit: int = 512) -> str:
    text = str(value)
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)] + "…"


def is_sha256(value: str) -> bool:
    text = str(value).lower()
    if text.startswith("sha256:"):
        text = text[7:]
    return len(text) == 64 and all(char in "0123456789abcdef" for char in text)
