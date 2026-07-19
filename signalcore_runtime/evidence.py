from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from .util import atomic_write_bytes, atomic_write_json, sha256_bytes, sha256_file


class EvidenceError(RuntimeError):
    pass


class EvidenceStore:
    """Content-addressed exact evidence with project scoping and integrity checks."""

    def __init__(self, root: Path, *, project_id: str):
        self.root = root
        self.project_id = project_id
        self.objects = root / "objects"
        self.metadata = root / "metadata"
        self.objects.mkdir(parents=True, exist_ok=True)
        self.metadata.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _parse_handle(handle: str) -> str:
        prefix = "sc://sha256/"
        if not handle.startswith(prefix):
            raise EvidenceError("invalid evidence handle")
        digest = handle[len(prefix):]
        if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
            raise EvidenceError("invalid evidence digest")
        return digest

    def put(self, data: bytes, *, kind: str = "generic", metadata: dict[str, Any] | None = None) -> str:
        digest = sha256_bytes(data)
        object_path = self.objects / digest[:2] / digest[2:]
        meta_path = self.metadata / f"{digest}.json"
        if not object_path.exists():
            atomic_write_bytes(object_path, data, mode=0o600)
        elif sha256_file(object_path) != digest:
            raise EvidenceError("content-addressed object collision or corruption")
        payload = {
            "schema_version": 1,
            "digest": digest,
            "bytes": len(data),
            "project_id": self.project_id,
            "kind": kind,
            "created_at": time.time(),
            "metadata": metadata or {},
        }
        if not meta_path.exists():
            atomic_write_json(meta_path, payload, mode=0o600)
        return f"sc://sha256/{digest}"

    def put_file(self, path: Path, *, kind: str = "file", metadata: dict[str, Any] | None = None) -> str:
        return self.put(path.read_bytes(), kind=kind, metadata={"source_path": str(path), **(metadata or {})})

    def get(self, handle: str, *, max_bytes: int | None = None) -> bytes:
        digest = self._parse_handle(handle)
        path = self.objects / digest[:2] / digest[2:]
        if not path.is_file():
            raise EvidenceError("evidence object missing")
        if sha256_file(path) != digest:
            raise EvidenceError("evidence object failed integrity verification")
        size = path.stat().st_size
        if max_bytes is not None and size > max_bytes:
            raise EvidenceError(f"evidence exceeds max_bytes: {size} > {max_bytes}")
        return path.read_bytes()

    def describe(self, handle: str) -> dict[str, Any]:
        digest = self._parse_handle(handle)
        meta_path = self.metadata / f"{digest}.json"
        if not meta_path.is_file():
            raise EvidenceError("evidence metadata missing")
        value = json.loads(meta_path.read_text(encoding="utf-8"))
        if value.get("project_id") != self.project_id:
            raise EvidenceError("evidence scope mismatch")
        return value

    def verify(self, handle: str) -> bool:
        try:
            self.get(handle)
            self.describe(handle)
            return True
        except (OSError, ValueError, EvidenceError, json.JSONDecodeError):
            return False
