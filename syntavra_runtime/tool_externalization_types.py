from __future__ import annotations

import csv
import difflib
import io
import json
import math
import re
import secrets
import shlex
import sqlite3
import time
from collections import Counter
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Protocol, Sequence


_SECRET = re.compile(r"(?i)\b(api[_-]?key|access[_-]?token|authorization|password|secret|bearer|private[_-]?key)\b\s*[:=]\s*([^\s,;]+)")
_ERROR = re.compile(r"(?i)\b(error|failed|failure|panic|assertion|traceback|exception|fatal|denied|timeout|critical|segfault)\b")
_WARNING = re.compile(r"(?i)\b(warn(?:ing)?|deprecated|retry|throttl)\b")
_LOCATION = re.compile(r"(?:[A-Za-z0-9_./\\-]{1,512}\.(?:py|rs|js|jsx|ts|tsx|java|cs|go|rb|php|lua|luau|cpp|c|h|hpp):\d+(?::\d+)?|line \d+)")
_INJECTION = re.compile(
    r"(?is)(ignore\s+(?:all\s+)?(?:previous|prior)\s+instructions|system\s+message|developer\s+message|"
    r"<\/?(?:system|assistant|developer|tool)>|you\s+are\s+chatgpt|do\s+not\s+follow|reveal\s+(?:the\s+)?(?:prompt|secret))"
)
_TEST_COMMAND = re.compile(r"(?:^|\s)(?:pytest|py\.test|unittest|cargo\s+test|go\s+test|npm\s+test|pnpm\s+test|yarn\s+test|vitest|jest)(?:\s|$)", re.I)
_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_.:/-]{1,}")
_TIMESTAMP = re.compile(r"^(?:\[?\d{4}-\d{2}-\d{2}[T ][^\]]+\]?|\[?\d{2}:\d{2}:\d{2})")
_CODE_SUFFIXES = {".py", ".js", ".jsx", ".ts", ".tsx", ".rs", ".go", ".java", ".cs", ".c", ".cpp", ".h", ".hpp", ".rb", ".php", ".lua", ".luau"}
_UNSAFE_SHELL = ("|", "`", "$(", "<<", "\n", "\r")


def _sha256(data: bytes) -> str:
    import hashlib
    return hashlib.sha256(data).hexdigest()


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _merkle(hashes: Sequence[str]) -> str:
    if not hashes:
        return _sha256(b"")
    layer = list(hashes)
    while len(layer) > 1:
        if len(layer) % 2:
            layer.append(layer[-1])
        layer = [_sha256(bytes.fromhex(layer[i]) + bytes.fromhex(layer[i + 1])) for i in range(0, len(layer), 2)]
    return layer[0]


def _merkle_proof(hashes: Sequence[str], index: int) -> list[dict[str, str]]:
    if index < 0 or index >= len(hashes):
        raise IndexError(index)
    proof: list[dict[str, str]] = []
    layer = list(hashes)
    position = index
    while len(layer) > 1:
        if len(layer) % 2:
            layer.append(layer[-1])
        sibling = position - 1 if position % 2 else position + 1
        proof.append({"side": "left" if sibling < position else "right", "hash": layer[sibling]})
        layer = [_sha256(bytes.fromhex(layer[i]) + bytes.fromhex(layer[i + 1])) for i in range(0, len(layer), 2)]
        position //= 2
    return proof


def _verify_merkle_proof(leaf_hash: str, proof: Sequence[Mapping[str, str]], root: str) -> bool:
    current = leaf_hash
    for item in proof:
        sibling = str(item["hash"])
        if item["side"] == "left":
            current = _sha256(bytes.fromhex(sibling) + bytes.fromhex(current))
        elif item["side"] == "right":
            current = _sha256(bytes.fromhex(current) + bytes.fromhex(sibling))
        else:
            return False
    return current == root


class EvidenceLike(Protocol):
    def put(self, data: bytes, *, kind: str = "generic", metadata: dict[str, Any] | None = None) -> str: ...
    def get(self, handle: str, *, max_bytes: int | None = None) -> bytes: ...
    def verify(self, handle: str) -> bool: ...


@dataclass(frozen=True)
class ExternalizationPolicy:
    profile: str = "balanced"
    preview_budget_bytes: int = 4096
    passthrough_threshold_bytes: int = 768
    segment_target_bytes: int = 16 * 1024
    reveal_page_bytes: int = 8192
    min_externalization_ratio: float = 0.10
    max_critical_segments: int = 32
    delta_enabled: bool = True
    deduplicate: bool = True
    continuation_ttl_seconds: int = 900
    search_window_lines: int = 2

    def __post_init__(self) -> None:
        if self.preview_budget_bytes < 256 or self.segment_target_bytes < 1024 or self.reveal_page_bytes < 256:
            raise ValueError("invalid externalization byte limits")
        if not 0 <= self.min_externalization_ratio < 1:
            raise ValueError("min_externalization_ratio must be in [0,1)")
        if self.continuation_ttl_seconds < 30:
            raise ValueError("continuation token ttl too small")

    @classmethod
    def for_profile(cls, profile: str) -> "ExternalizationPolicy":
        profiles = {
            "compact": cls("compact", 2048, 384, 8 * 1024, 4096, 0.06, 20, True, True, 600, 1),
            "balanced": cls(),
            "audit": cls("audit", 8192, 1536, 32 * 1024, 16 * 1024, 0.16, 64, True, True, 1800, 4),
        }
        try:
            return profiles[profile]
        except KeyError as exc:
            raise ValueError(f"unknown externalization profile: {profile}") from exc

    @property
    def digest(self) -> str:
        return _sha256(_canonical(asdict(self)))


@dataclass(frozen=True)
class ToolPayload:
    command: str = ""
    stdout: str | bytes = ""
    stderr: str | bytes = ""
    tool_name: str = "shell"
    path: str = ""
    scope_key: str = "default"
    metadata: dict[str, Any] = field(default_factory=dict)

    @staticmethod
    def _bytes(value: str | bytes) -> bytes:
        return value if isinstance(value, bytes) else value.encode("utf-8")

    @property
    def raw(self) -> bytes:
        out = self._bytes(self.stdout)
        err = self._bytes(self.stderr)
        if out and err:
            return out.rstrip(b"\n") + b"\n[stderr]\n" + err
        return out or err


@dataclass(frozen=True)
class ExternalizedArtifact:
    artifact_id: str
    family: str
    mode: str
    preview: str
    original_bytes: int
    visible_bytes: int
    reduction_ratio: float
    exact_handle: str
    segment_count: int
    content_hash: str
    merkle_root: str
    quality_gate_passed: bool
    repeated: bool
    seen_count: int
    baseline_artifact_id: str | None
    injection_risk: bool
    facets: dict[str, Any]
    metadata: dict[str, Any]


@dataclass(frozen=True)
class SegmentHit:
    artifact_id: str
    segment_index: int
    kind: str
    start_line: int
    end_line: int
    score: float
    text: str
    segment_handle: str
    artifact_handle: str
    match_reasons: tuple[str, ...]


@dataclass(frozen=True)
class SearchPack:
    query: str
    content: str
    visible_bytes: int
    hit_count: int
    artifact_ids: tuple[str, ...]
    segment_handles: tuple[str, ...]
    complete: bool

@dataclass(frozen=True)
class RevealPage:
    artifact_id: str
    lens: str
    content: str
    bytes_returned: int
    segment_indexes: tuple[int, ...]
    continuation_token: str | None
    complete: bool
    exact_handle: str


@dataclass(frozen=True)
class _Segment:
    index: int
    start_byte: int
    end_byte: int
    start_line: int
    end_line: int
    data: bytes
    content_hash: str
    kind: str
    salience: float
    critical: bool
    index_text: str
