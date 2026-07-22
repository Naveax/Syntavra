from __future__ import annotations

import ast
import base64
import datetime as dt
import difflib
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import time
from collections import Counter, defaultdict
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .release_identity import CHANNEL, VERSION
from .util import atomic_write_json, canonical_json, sha256_bytes

_SECRET_RE = re.compile(
    r"(?i)(?:api[_-]?key|authorization|access[_-]?token|password|secret|bearer)"
    r"\s*[:=]\s*([^\s,;]+)"
)
_ERROR_RE = re.compile(
    r"(?i)\b(error|failed|failure|panic|assertion|traceback|exception|fatal|denied|timeout)\b"
)
_LOCATION_RE = re.compile(
    r"(?:[A-Za-z]:)?[^\s:]+\.(?:py|rs|ts|tsx|js|jsx|go|java|cs|cpp|c|h|rb|php):\d+(?::\d+)?"
)
_DESTRUCTIVE_RE = re.compile(
    r"(?i)(?:\brm\s+-rf\b|\bgit\s+reset\s+--hard\b|\bgit\s+clean\s+-[a-z]*[fdx]"
    r"|\bmkfs\.|\bformat\s+[a-z]:|remove-item\s+.*-recurse.*-force)"
)
_TOKEN_RE = re.compile(r"[A-Za-z0-9_./:-]+")


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


@contextmanager
def _connect(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=30.0)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA foreign_keys=ON")
    db.execute("PRAGMA synchronous=FULL")
    try:
        yield db
        db.commit()
    finally:
        db.close()


def _redact(text: str) -> str:
    return _SECRET_RE.sub(lambda match: match.group(0).split(match.group(1), 1)[0] + "<redacted>", text)


def _tokens(text: str) -> set[str]:
    values: set[str] = set()
    for token in _TOKEN_RE.findall(text.casefold()):
        if len(token) > 1:
            values.add(token)
        values.update(part for part in re.split(r"[._/:-]+", token) if len(part) > 1)
    return values


def _estimate_tokens(text: str, provider: str = "generic") -> int:
    # Conservative deterministic fallback. Provider-specific multipliers avoid
    # pretending that one character ratio is exact across model families.
    ratio = {
        "openai": 3.7,
        "anthropic": 3.5,
        "gemini": 3.8,
        "local": 3.3,
        "generic": 3.5,
    }.get(provider.casefold(), 3.5)
    return max(1, int((len(text.encode("utf-8")) / ratio) + 0.999))



__all__ = [name for name in globals() if not name.startswith("__")]
