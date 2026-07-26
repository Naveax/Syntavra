from __future__ import annotations

import hashlib
import re
from pathlib import Path, PurePath

NON_TEXT_MANIFEST_PREFIX = "benchmarks/results/real-tasks"
_DRIVE_PREFIX = re.compile(r"^[A-Za-z]:")


class CanonicalPathError(ValueError):
    """Raised when a repository-relative path cannot be canonicalized safely."""

    def __init__(self, code: str, value: str):
        super().__init__(f"{code}:{value}")
        self.code = code
        self.value = value


def normalize_repository_path(value: str | PurePath) -> str:
    """Return a language-independent, repository-relative slash path.

    R5 is lexical by design: it does not touch the filesystem, resolve symlinks,
    case-fold names, or perform Unicode normalization.
    """

    raw = str(value)
    if "\x00" in raw:
        raise CanonicalPathError("PATH_NUL", raw)
    portable = raw.replace("\\", "/")
    if portable.startswith("/"):
        raise CanonicalPathError("PATH_ABSOLUTE", raw)
    if _DRIVE_PREFIX.match(portable):
        raise CanonicalPathError("PATH_DRIVE_PREFIX", raw)

    parts: list[str] = []
    for part in portable.split("/"):
        if part in {"", "."}:
            continue
        if part == "..":
            raise CanonicalPathError("PATH_PARENT_TRAVERSAL", raw)
        parts.append(part)
    if not parts:
        raise CanonicalPathError("PATH_EMPTY", raw)
    return "/".join(parts)


def canonical_text_bytes(data: bytes) -> bytes:
    """Normalize UTF-8 text line endings while preserving opaque payloads."""

    if b"\0" in data:
        return data
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return data
    return data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def canonical_manifest_bytes(relative: str | Path, data: bytes) -> bytes:
    """Return exact bytes used by the repository SHA-256 manifest."""

    normalized = normalize_repository_path(relative)
    if normalized == NON_TEXT_MANIFEST_PREFIX or normalized.startswith(
        NON_TEXT_MANIFEST_PREFIX + "/"
    ):
        return data
    return canonical_text_bytes(data)


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def manifest_digest_hex(relative: str | Path, data: bytes) -> str:
    return sha256_hex(canonical_manifest_bytes(relative, data))
