from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any, BinaryIO, Iterable, Iterator

from .util import atomic_write_bytes, atomic_write_json, sha256_bytes, sha256_file


class EvidenceError(RuntimeError):
    pass


class EvidenceStore:
    """Project-scoped, content-addressed exact evidence.

    Large objects are streamed to a temporary file and atomically installed. The
    runtime never needs to materialize complete command output in model memory.
    """

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

    def _metadata_payload(
        self,
        digest: str,
        size: int,
        *,
        kind: str,
        metadata: dict[str, Any] | None,
    ) -> dict[str, Any]:
        return {
            "schema_version": 2,
            "digest": digest,
            "bytes": size,
            "project_id": self.project_id,
            "kind": kind,
            "created_at": time.time(),
            "metadata": metadata or {},
        }

    def put(self, data: bytes, *, kind: str = "generic", metadata: dict[str, Any] | None = None) -> str:
        return self.put_stream((data,), kind=kind, metadata=metadata)

    def put_stream(
        self,
        chunks: Iterable[bytes],
        *,
        kind: str = "generic",
        metadata: dict[str, Any] | None = None,
    ) -> str:
        self.root.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=".evidence-", dir=self.root)
        temp = Path(temp_name)
        digest = hashlib.sha256()
        size = 0
        try:
            with os.fdopen(fd, "wb") as handle:
                for chunk in chunks:
                    if not isinstance(chunk, (bytes, bytearray, memoryview)):
                        raise TypeError("evidence stream must yield bytes")
                    value = bytes(chunk)
                    if not value:
                        continue
                    digest.update(value)
                    handle.write(value)
                    size += len(value)
                handle.flush()
                os.fsync(handle.fileno())
            hexdigest = digest.hexdigest()
            object_path = self.objects / hexdigest[:2] / hexdigest[2:]
            object_path.parent.mkdir(parents=True, exist_ok=True)
            if object_path.exists():
                if sha256_file(object_path) != hexdigest:
                    raise EvidenceError("content-addressed object collision or corruption")
                temp.unlink(missing_ok=True)
            else:
                try:
                    os.chmod(temp, 0o600)
                except OSError:
                    pass
                os.replace(temp, object_path)
            meta_path = self.metadata / f"{hexdigest}.json"
            if not meta_path.exists():
                atomic_write_json(
                    meta_path,
                    self._metadata_payload(hexdigest, size, kind=kind, metadata=metadata),
                    mode=0o600,
                )
            return f"sc://sha256/{hexdigest}"
        finally:
            temp.unlink(missing_ok=True)

    @staticmethod
    def file_chunks(path: Path, *, chunk_size: int = 1024 * 1024) -> Iterator[bytes]:
        with path.open("rb") as handle:
            while chunk := handle.read(chunk_size):
                yield chunk

    def put_files(
        self,
        paths: Iterable[Path],
        *,
        separator: bytes = b"\n",
        kind: str = "files",
        metadata: dict[str, Any] | None = None,
    ) -> str:
        ordered = tuple(Path(path) for path in paths)

        def chunks() -> Iterator[bytes]:
            emitted = False
            for path in ordered:
                if not path.is_file():
                    continue
                if emitted and separator:
                    yield separator
                yield from self.file_chunks(path)
                emitted = True

        merged_metadata = {"source_paths": [str(path) for path in ordered], **(metadata or {})}
        return self.put_stream(chunks(), kind=kind, metadata=merged_metadata)

    def put_file(self, path: Path, *, kind: str = "file", metadata: dict[str, Any] | None = None) -> str:
        return self.put_stream(
            self.file_chunks(path),
            kind=kind,
            metadata={"source_path": str(path), **(metadata or {})},
        )

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

    def open(self, handle: str) -> BinaryIO:
        digest = self._parse_handle(handle)
        path = self.objects / digest[:2] / digest[2:]
        if not path.is_file() or sha256_file(path) != digest:
            raise EvidenceError("evidence object missing or corrupt")
        return path.open("rb")

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
            with self.open(handle):
                pass
            self.describe(handle)
            return True
        except (OSError, ValueError, EvidenceError, json.JSONDecodeError):
            return False
