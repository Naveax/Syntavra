from __future__ import annotations

import csv
import io
import json
import re
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

from .evidence import EvidenceStore
from .state import StateDB
from .structural_parsers import ParserRegistry
from .util import canonical_json, sha256_bytes


SECRET_RE = re.compile(r"(?i)\b(api[_-]?key|access[_-]?token|authorization|password|secret|bearer)\b\s*[:=]\s*([^\s,;]+)")
ERROR_RE = re.compile(r"(?i)\b(error|failed|failure|panic|assertion|traceback|exception|fatal|denied)\b")
STACK_RE = re.compile(r"(?:File \"[^\"]+\", line \d+|\bat [\w.$<>]+\([^)]*:\d+\)|[^\s:]+\.(?:py|rs|js|ts|java|cs|go|rb|php):\d+)")
DIFF_RE = re.compile(r"^(?:diff --git|index |--- |\+\+\+ |@@ )")
XML_TAG_RE = re.compile(r"<[^>]+>")
SENTENCE_RE = re.compile(r"(?<=[.!?])\s+|\n+")


@dataclass(frozen=True)
class CompressionResult:
    compression_id: str
    content_type: str
    visible_text: str
    original_bytes: int
    visible_bytes: int
    exact_handle: str
    chunk_count: int
    chunk_size: int
    reversible: bool
    loss_policy: str
    metadata: dict[str, Any]
    receipt_hash: str


class ReversibleContentStore:
    def __init__(self, path: Path, *, evidence: EvidenceStore, chunk_size: int = 64 * 1024):
        self.state = StateDB(path)
        self.evidence = evidence
        self.chunk_size = max(1024, chunk_size)
        with self.state.transaction(immediate=True) as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS compressions(
                    compression_id TEXT PRIMARY KEY,
                    content_type TEXT NOT NULL,
                    exact_handle TEXT NOT NULL,
                    original_bytes INTEGER NOT NULL,
                    visible_text TEXT NOT NULL,
                    chunk_size INTEGER NOT NULL,
                    chunk_count INTEGER NOT NULL,
                    metadata_json TEXT NOT NULL,
                    receipt_hash TEXT NOT NULL,
                    created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS compression_chunks(
                    compression_id TEXT NOT NULL,
                    chunk_index INTEGER NOT NULL,
                    chunk_handle TEXT NOT NULL,
                    chunk_bytes INTEGER NOT NULL,
                    PRIMARY KEY(compression_id,chunk_index),
                    FOREIGN KEY(compression_id) REFERENCES compressions(compression_id) ON DELETE CASCADE
                );
                """
            )

    def put(
        self,
        data: bytes,
        *,
        content_type: str,
        visible_text: str,
        metadata: dict[str, Any] | None = None,
        loss_policy: str = "exact-externalized",
    ) -> CompressionResult:
        compression_id = "ccr-" + uuid.uuid4().hex
        exact = self.evidence.put(data, kind=f"compressed-source:{content_type}", metadata=metadata)
        chunks = [data[offset: offset + self.chunk_size] for offset in range(0, len(data), self.chunk_size)] or [b""]
        chunk_handles = [
            self.evidence.put(chunk, kind="compression-chunk", metadata={"compression_id": compression_id, "chunk_index": index})
            for index, chunk in enumerate(chunks)
        ]
        payload = {
            "compression_id": compression_id,
            "content_type": content_type,
            "original_bytes": len(data),
            "visible_bytes": len(visible_text.encode("utf-8")),
            "exact_handle": exact,
            "chunk_handles": chunk_handles,
            "chunk_size": self.chunk_size,
            "loss_policy": loss_policy,
            "metadata": metadata or {},
        }
        receipt = sha256_bytes(canonical_json(payload))
        with self.state.transaction(immediate=True) as db:
            db.execute(
                """
                INSERT INTO compressions(
                    compression_id,content_type,exact_handle,original_bytes,visible_text,
                    chunk_size,chunk_count,metadata_json,receipt_hash,created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    compression_id,
                    content_type,
                    exact,
                    len(data),
                    visible_text,
                    self.chunk_size,
                    len(chunks),
                    json.dumps(metadata or {}, ensure_ascii=False, sort_keys=True),
                    receipt,
                    time.time(),
                ),
            )
            db.executemany(
                "INSERT INTO compression_chunks(compression_id,chunk_index,chunk_handle,chunk_bytes) VALUES(?,?,?,?)",
                [(compression_id, index, handle, len(chunks[index])) for index, handle in enumerate(chunk_handles)],
            )
        return CompressionResult(
            compression_id,
            content_type,
            visible_text,
            len(data),
            len(visible_text.encode("utf-8")),
            exact,
            len(chunks),
            self.chunk_size,
            True,
            loss_policy,
            metadata or {},
            receipt,
        )

    def describe(self, compression_id: str) -> dict[str, Any]:
        with self.state.read() as db:
            row = db.execute("SELECT * FROM compressions WHERE compression_id=?", (compression_id,)).fetchone()
            if not row:
                raise KeyError(compression_id)
            chunks = [dict(item) for item in db.execute("SELECT * FROM compression_chunks WHERE compression_id=? ORDER BY chunk_index", (compression_id,))]
        value = dict(row)
        value["metadata"] = json.loads(value.pop("metadata_json"))
        value["chunks"] = chunks
        return value

    def restore(self, compression_id: str, *, chunk: int | None = None) -> bytes:
        value = self.describe(compression_id)
        if chunk is None:
            return self.evidence.get(value["exact_handle"])
        chunks = value["chunks"]
        if chunk < 0 or chunk >= len(chunks):
            raise IndexError(chunk)
        return self.evidence.get(chunks[chunk]["chunk_handle"])

    def verify_roundtrip(self, compression_id: str) -> bool:
        value = self.describe(compression_id)
        full = self.evidence.get(value["exact_handle"])
        rebuilt = b"".join(self.evidence.get(row["chunk_handle"]) for row in value["chunks"])
        payload = {
            "compression_id": compression_id,
            "content_type": value["content_type"],
            "original_bytes": value["original_bytes"],
            "visible_bytes": len(value["visible_text"].encode("utf-8")),
            "exact_handle": value["exact_handle"],
            "chunk_handles": [row["chunk_handle"] for row in value["chunks"]],
            "chunk_size": value["chunk_size"],
            "loss_policy": "exact-externalized",
            "metadata": value["metadata"],
        }
        return full == rebuilt and len(full) == value["original_bytes"] and sha256_bytes(canonical_json(payload)) == value["receipt_hash"]


class ContentRouter:
    def __init__(self, store: ReversibleContentStore, *, repository_root: Path | None = None):
        self.store = store
        self.repository_root = repository_root
        self.parsers = ParserRegistry(repository_root) if repository_root else None

    @staticmethod
    def detect(data: bytes, *, hint: str = "", path: str = "") -> str:
        lower_hint = hint.casefold()
        suffix = Path(path).suffix.casefold()
        sample = data[:65536].decode("utf-8", errors="replace")
        stripped = sample.lstrip()
        if lower_hint:
            aliases = {
                "yaml": "yaml", "yml": "yaml", "json": "json", "csv": "table", "tsv": "table",
                "code": "code", "diff": "diff", "log": "log", "stack": "stack-trace",
                "xml": "xml", "html": "xml", "text": "text", "rag": "rag",
            }
            if lower_hint in aliases:
                return aliases[lower_hint]
        if suffix in {".json", ".jsonl"} or stripped.startswith(("{", "[")):
            try:
                json.loads(sample)
                return "json"
            except json.JSONDecodeError:
                if suffix == ".jsonl":
                    return "jsonl"
        if suffix in {".csv", ".tsv"}:
            return "table"
        if suffix in {".py", ".js", ".jsx", ".ts", ".tsx", ".rs", ".go", ".java", ".cs", ".c", ".cpp", ".h", ".hpp", ".rb", ".php", ".lua", ".luau"}:
            return "code"
        lines = sample.splitlines()
        if any(DIFF_RE.match(line) for line in lines[:20]):
            return "diff"
        if sum(bool(STACK_RE.search(line)) for line in lines[:100]) >= 2:
            return "stack-trace"
        if stripped.startswith("<") and ">" in stripped[:500]:
            return "xml"
        if sum(bool(ERROR_RE.search(line)) for line in lines[:100]) >= 2 or len(lines) > 200:
            return "log"
        return "text"

    @staticmethod
    def _redact(text: str) -> str:
        return SECRET_RE.sub(lambda match: f"{match.group(1)}=<redacted>", text)

    def compress(
        self,
        data: bytes | str,
        *,
        hint: str = "",
        path: str = "",
        budget_bytes: int = 8192,
        metadata: dict[str, Any] | None = None,
    ) -> CompressionResult:
        raw = data.encode("utf-8") if isinstance(data, str) else bytes(data)
        content_type = self.detect(raw, hint=hint, path=path)
        text = raw.decode("utf-8", errors="replace")
        method = getattr(self, f"_compress_{content_type.replace('-', '_')}", self._compress_text)
        visible, details = method(text, budget_bytes=budget_bytes, path=path)
        placeholder = f"[Syntavra CCR {content_type}: {{compression_id}} | exact={{exact_handle}} | chunks={{chunk_count}}]"
        # Store first, then render stable handles into the final visible text by a metadata-only update.
        provisional = self.store.put(
            raw,
            content_type=content_type,
            visible_text=visible,
            metadata={"path": path, "hint": hint, **(metadata or {}), **details},
        )
        header = placeholder.format(
            compression_id=provisional.compression_id,
            exact_handle=provisional.exact_handle,
            chunk_count=provisional.chunk_count,
        )
        final_visible = self._bounded(header + "\n" + visible, budget_bytes)
        if final_visible == provisional.visible_text:
            return provisional
        description = self.store.describe(provisional.compression_id)
        receipt_payload = {
            "compression_id": provisional.compression_id,
            "content_type": provisional.content_type,
            "original_bytes": provisional.original_bytes,
            "visible_bytes": len(final_visible.encode("utf-8")),
            "exact_handle": provisional.exact_handle,
            "chunk_handles": [row["chunk_handle"] for row in description["chunks"]],
            "chunk_size": provisional.chunk_size,
            "loss_policy": provisional.loss_policy,
            "metadata": provisional.metadata,
        }
        receipt = sha256_bytes(canonical_json(receipt_payload))
        with self.store.state.transaction(immediate=True) as db:
            db.execute(
                "UPDATE compressions SET visible_text=?,receipt_hash=? WHERE compression_id=?",
                (final_visible, receipt, provisional.compression_id),
            )
        return CompressionResult(
            provisional.compression_id,
            provisional.content_type,
            final_visible,
            provisional.original_bytes,
            len(final_visible.encode("utf-8")),
            provisional.exact_handle,
            provisional.chunk_count,
            provisional.chunk_size,
            True,
            provisional.loss_policy,
            provisional.metadata,
            receipt,
        )

    @staticmethod
    def _bounded(text: str, budget: int) -> str:
        encoded = text.encode("utf-8")
        if len(encoded) <= budget:
            return text
        suffix = "\n[visible view truncated; use CCR handle for exact restoration]"
        keep = max(0, budget - len(suffix.encode("utf-8")))
        return encoded[:keep].decode("utf-8", errors="ignore").rstrip() + suffix

    def _compress_json(self, text: str, *, budget_bytes: int, path: str) -> tuple[str, dict[str, Any]]:
        value = json.loads(text)

        def summarize(value: Any, depth: int = 0) -> Any:
            if depth >= 5:
                if isinstance(value, (dict, list)):
                    return f"<{type(value).__name__}:{len(value)}>"
                return value
            if isinstance(value, dict):
                result: dict[str, Any] = {}
                for index, key in enumerate(sorted(value, key=str)):
                    if index >= 40:
                        result["<omitted_keys>"] = len(value) - index
                        break
                    result[str(key)] = summarize(value[key], depth + 1)
                return result
            if isinstance(value, list):
                if len(value) <= 12:
                    return [summarize(item, depth + 1) for item in value]
                return {
                    "<array_length>": len(value),
                    "<head>": [summarize(item, depth + 1) for item in value[:5]],
                    "<tail>": [summarize(item, depth + 1) for item in value[-3:]],
                }
            if isinstance(value, str) and len(value) > 500:
                return value[:240] + f"…<{len(value)} chars>…" + value[-120:]
            return value

        visible = json.dumps(summarize(value), ensure_ascii=False, indent=2, sort_keys=True)
        return self._bounded(self._redact(visible), budget_bytes), {"records": len(value) if isinstance(value, (list, dict)) else 1}

    def _compress_jsonl(self, text: str, *, budget_bytes: int, path: str) -> tuple[str, dict[str, Any]]:
        rows = []
        invalid = 0
        for line in text.splitlines():
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                invalid += 1
        summary, _ = self._compress_json(json.dumps(rows, ensure_ascii=False), budget_bytes=budget_bytes, path=path)
        return summary, {"records": len(rows), "invalid_records": invalid}

    def _compress_table(self, text: str, *, budget_bytes: int, path: str) -> tuple[str, dict[str, Any]]:
        dialect = csv.excel_tab if Path(path).suffix.casefold() == ".tsv" else csv.excel
        rows = list(csv.reader(io.StringIO(text), dialect=dialect))
        if not rows:
            return "<empty table>", {"rows": 0, "columns": 0}
        header = rows[0]
        body = rows[1:]
        samples = body[:5] + (body[-2:] if len(body) > 7 else [])
        widths = [max([len(header[index]) if index < len(header) else 0, *[len(row[index]) if index < len(row) else 0 for row in samples]]) for index in range(len(header))]
        rendered = [f"Rows: {len(body)} | Columns: {len(header)}", " | ".join(header)]
        rendered.append(" | ".join("-" * min(30, max(3, width)) for width in widths))
        rendered.extend(" | ".join(row[: len(header)]) for row in samples)
        if len(body) > len(samples):
            rendered.append(f"… {len(body) - len(samples)} rows externalized …")
        return self._bounded(self._redact("\n".join(rendered)), budget_bytes), {"rows": len(body), "columns": len(header)}

    def _compress_log(self, text: str, *, budget_bytes: int, path: str) -> tuple[str, dict[str, Any]]:
        counts: dict[str, int] = {}
        order: list[str] = []
        critical: list[str] = []
        for raw in text.splitlines():
            line = self._redact(raw.strip())
            if not line:
                continue
            normalized = re.sub(r"\b\d+(?:\.\d+)?\b", "<n>", line)
            if normalized not in counts:
                counts[normalized] = 0
                order.append(normalized)
            counts[normalized] += 1
            if ERROR_RE.search(line) and len(critical) < 40:
                critical.append(line)
        visible = [f"Log lines: {len(text.splitlines())} | Unique event shapes: {len(counts)}"]
        if critical:
            visible.append("Critical:")
            visible.extend(dict.fromkeys(critical))
        visible.append("Event shapes:")
        for shape in order[:50]:
            visible.append(f"[{counts[shape]}x] {shape}")
        if len(order) > 50:
            visible.append(f"… {len(order)-50} event shapes externalized …")
        return self._bounded("\n".join(visible), budget_bytes), {"lines": len(text.splitlines()), "unique_shapes": len(counts), "critical": len(critical)}

    def _compress_stack_trace(self, text: str, *, budget_bytes: int, path: str) -> tuple[str, dict[str, Any]]:
        lines = [self._redact(line.rstrip()) for line in text.splitlines() if line.strip()]
        root = next((line for line in reversed(lines) if ERROR_RE.search(line)), lines[-1] if lines else "")
        frames = [line for line in lines if STACK_RE.search(line)]
        unique = list(dict.fromkeys(frames))
        visible = "\n".join([f"Root cause: {root}", f"Frames: {len(frames)} ({len(unique)} unique)", *unique[:40]])
        return self._bounded(visible, budget_bytes), {"frames": len(frames), "unique_frames": len(unique)}

    def _compress_diff(self, text: str, *, budget_bytes: int, path: str) -> tuple[str, dict[str, Any]]:
        lines = text.splitlines()
        headers = [line for line in lines if DIFF_RE.match(line)]
        changes = [line for line in lines if line.startswith(("+", "-")) and not line.startswith(("+++", "---"))]
        visible = [f"Diff lines: {len(lines)} | Changed lines: {len(changes)}", *headers[:80], *changes[:120]]
        if len(changes) > 120:
            visible.append(f"… {len(changes)-120} changed lines externalized …")
        return self._bounded(self._redact("\n".join(visible)), budget_bytes), {"lines": len(lines), "changed_lines": len(changes)}

    def _compress_code(self, text: str, *, budget_bytes: int, path: str) -> tuple[str, dict[str, Any]]:
        if not self.parsers or not path:
            return self._compress_text(text, budget_bytes=budget_bytes, path=path)
        result = self.parsers.parse(path, text)
        lines = [f"Language: {result.language} | Parser: {result.parser} | Symbols: {len(result.symbols)} | Edges: {len(result.edges)}"]
        for symbol in result.symbols[:100]:
            lines.append(f"{symbol.line}:{symbol.end_line} {symbol.kind} {symbol.qualified_name} {symbol.signature}".rstrip())
        if result.diagnostics:
            lines.append("Diagnostics: " + "; ".join(result.diagnostics))
        return self._bounded("\n".join(lines), budget_bytes), {"language": result.language, "parser": result.parser, "symbols": len(result.symbols), "edges": len(result.edges)}

    def _compress_xml(self, text: str, *, budget_bytes: int, path: str) -> tuple[str, dict[str, Any]]:
        tags: dict[str, int] = {}
        for match in re.finditer(r"</?([A-Za-z_:][\w:.-]*)", text):
            tags[match.group(1)] = tags.get(match.group(1), 0) + 1
        plain = XML_TAG_RE.sub(" ", text)
        plain = re.sub(r"\s+", " ", plain).strip()
        visible = "Tags: " + ", ".join(f"{key}={value}" for key, value in sorted(tags.items())[:50]) + "\nText: " + plain[:4000]
        return self._bounded(self._redact(visible), budget_bytes), {"tags": len(tags)}

    def _compress_rag(self, text: str, *, budget_bytes: int, path: str) -> tuple[str, dict[str, Any]]:
        blocks = [block.strip() for block in re.split(r"\n{2,}", text) if block.strip()]
        scored = sorted(enumerate(blocks), key=lambda item: (-len(set(re.findall(r"\w+", item[1].casefold()))), item[0]))
        selected = [block for _, block in scored[:10]]
        return self._bounded(self._redact("\n\n---\n\n".join(selected)), budget_bytes), {"blocks": len(blocks), "selected": len(selected)}

    def _compress_text(self, text: str, *, budget_bytes: int, path: str) -> tuple[str, dict[str, Any]]:
        sentences = [self._redact(item.strip()) for item in SENTENCE_RE.split(text) if item.strip()]
        unique = list(dict.fromkeys(sentences))
        critical = [sentence for sentence in unique if ERROR_RE.search(sentence)]
        selected = list(dict.fromkeys([*critical[:20], *unique[:20], *unique[-8:]]))
        visible = "\n".join(selected)
        if len(unique) > len(selected):
            visible += f"\n… {len(unique)-len(selected)} segments externalized …"
        return self._bounded(visible, budget_bytes), {"segments": len(sentences), "unique_segments": len(unique)}
