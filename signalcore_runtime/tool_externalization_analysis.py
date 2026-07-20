from __future__ import annotations

import csv
import io
import json
import math
import re
import shlex
from collections import Counter
from pathlib import Path
from typing import Any, Mapping, Sequence

from .tool_externalization_types import (
    ToolPayload, _Segment, _CODE_SUFFIXES, _ERROR, _INJECTION, _LOCATION,
    _SECRET, _TEST_COMMAND, _TIMESTAMP, _UNSAFE_SHELL, _WARNING, _sha256,
)


class ExternalizationAnalysisMixin:
    @staticmethod
    def _normalize_command(command: str) -> str:
        try:
            return " ".join(shlex.split(command, posix=True))
        except ValueError:
            return " ".join(command.split())

    @classmethod
    def _safe_command(cls, command: str) -> str:
        if not command or any(marker in command for marker in _UNSAFE_SHELL):
            return ""
        parts = re.split(r"\s*(?:&&|;)\s*", command)
        return cls._normalize_command(parts[-1] if parts else command).casefold()

    @staticmethod
    def _is_binary(data: bytes) -> bool:
        if not data:
            return False
        sample = data[:8192]
        if b"\x00" in sample:
            return True
        printable = sum(byte in b"\t\n\r" or 32 <= byte <= 126 or byte >= 128 for byte in sample)
        return printable / len(sample) < 0.78

    @classmethod
    def classify(cls, payload: ToolPayload) -> str:
        raw = payload.raw
        if cls._is_binary(raw):
            return "binary"
        command = cls._safe_command(payload.command)
        suffix = Path(payload.path).suffix.casefold()
        text = raw[:131072].decode("utf-8", errors="replace").lstrip()
        if re.search(r"(?:^|\s)git\s+status(?:\s|$)", command):
            return "git-status"
        if re.search(r"(?:^|\s)(?:git|gh)\s+(?:diff|pr\s+diff)(?:\s|$)", command):
            return "diff"
        if re.search(r"(?:^|\s)git\s+log(?:\s|$)", command):
            return "git-log"
        if _TEST_COMMAND.search(command):
            return "test-output"
        if re.search(r"(?:^|\s)(?:grep|rg|find|fd|ls|tree)(?:\s|$)", command):
            return "search-list"
        if suffix in _CODE_SUFFIXES:
            return "code"
        if suffix in {".json", ".jsonl"} or text.startswith(("{", "[")):
            return "json"
        if suffix in {".csv", ".tsv"}:
            return "table"
        first = text.splitlines()[:30]
        if any(line.startswith(("diff --git", "@@ ", "--- ", "+++ ")) for line in first):
            return "diff"
        lines = text.splitlines()
        if len(lines) > 120 or sum(bool(_ERROR.search(line)) for line in lines[:300]) >= 2:
            return "log"
        return "text"

    @staticmethod
    def _redact(text: str) -> str:
        return _SECRET.sub(lambda match: f"{match.group(1)}=<redacted>", text)

    @staticmethod
    def _bounded(text: str, budget: int) -> str:
        raw = text.encode("utf-8")
        if len(raw) <= budget:
            return text
        suffix = "\n[… progressively revealable exact evidence omitted …]"
        keep = max(0, budget - len(suffix.encode("utf-8")))
        return raw[:keep].decode("utf-8", errors="ignore").rstrip() + suffix

    @staticmethod
    def _excerpt(text: str, max_bytes: int = 320) -> str:
        raw = text.encode("utf-8")
        if len(raw) <= max_bytes:
            return text
        marker = f" … <{len(raw)} bytes> … "
        room = max(16, max_bytes - len(marker.encode("utf-8")))
        head = raw[: room * 3 // 4].decode("utf-8", errors="ignore")
        tail = raw[-room // 4 :].decode("utf-8", errors="ignore")
        return head + marker + tail

    @staticmethod
    def _line_units(data: bytes) -> list[tuple[int, int, int, int, bytes]]:
        if not data:
            return [(0, 0, 1, 1, b"")]
        units: list[tuple[int, int, int, int, bytes]] = []
        offset = 0
        line = 1
        for value in data.splitlines(keepends=True):
            end = offset + len(value)
            units.append((offset, end, line, line + value.count(b"\n"), value))
            offset = end
            line += value.count(b"\n")
        if offset < len(data):
            value = data[offset:]
            units.append((offset, len(data), line, line, value))
        return units

    @classmethod
    def _boundary(cls, family: str, text: str) -> bool:
        stripped = text.lstrip()
        if family == "diff":
            return stripped.startswith(("diff --git ", "@@ "))
        if family == "test-output":
            return bool(_ERROR.search(text)) or stripped.startswith(("FAILURES", "FAILED ", "ERRORS", "___"))
        if family == "code":
            return bool(re.match(r"^\s*(?:class |def |async def |function |export |pub fn |fn |interface |struct |enum |impl )", text))
        if family == "log":
            return bool(_ERROR.search(text) or _INJECTION.search(text))
        if family == "search-list":
            return False
        return False

    @classmethod
    def _segment_kind(cls, family: str, text: str) -> tuple[str, float, bool]:
        error = bool(_ERROR.search(text))
        warning = bool(_WARNING.search(text))
        injection = bool(_INJECTION.search(text))
        location = bool(_LOCATION.search(text))
        if family == "binary":
            return "binary-slice", 1.0, False
        if error:
            return "critical", 12.0 + (2.0 if location else 0.0), True
        if injection:
            return "untrusted-instruction", 11.0, True
        if warning:
            return "warning", 6.0 + (1.0 if location else 0.0), False
        if family == "diff":
            return "diff-hunk", 5.0, False
        if family == "test-output":
            return "test-window", 4.5, False
        if family == "json":
            return "json-slice", 2.5, False
        if family == "code":
            return "code-block", 3.0, False
        if family == "search-list":
            return "search-group", 2.0, False
        if family == "log":
            return "log-window", 1.5, False
        return "text-window", 1.0, False

    @classmethod
    def _segments(cls, data: bytes, family: str, target: int) -> list[_Segment]:
        if family == "binary":
            pieces = [(offset, min(len(data), offset + target), 1, 1, data[offset : offset + target]) for offset in range(0, len(data), target)] or [(0, 0, 1, 1, b"")]
        else:
            units = cls._line_units(data)
            pieces: list[tuple[int, int, int, int, bytes]] = []
            current: list[tuple[int, int, int, int, bytes]] = []
            current_bytes = 0

            def flush() -> None:
                nonlocal current, current_bytes
                if not current:
                    return
                pieces.append((current[0][0], current[-1][1], current[0][2], current[-1][3], b"".join(item[4] for item in current)))
                current = []
                current_bytes = 0

            for unit in units:
                raw = unit[4]
                text = raw.decode("utf-8", errors="replace")
                if len(raw) > target:
                    flush()
                    start = unit[0]
                    for offset in range(0, len(raw), target):
                        chunk = raw[offset : offset + target]
                        pieces.append((start + offset, start + offset + len(chunk), unit[2], unit[3], chunk))
                    continue
                if current and (current_bytes + len(raw) > target or cls._boundary(family, text)):
                    flush()
                current.append(unit)
                current_bytes += len(raw)
            flush()

        output: list[_Segment] = []
        for index, (start, end, start_line, end_line, raw) in enumerate(pieces):
            decoded = raw.decode("utf-8", errors="replace")
            kind, salience, critical = cls._segment_kind(family, decoded)
            index_text = cls._redact(decoded)
            if len(index_text.encode("utf-8")) > 65536:
                index_text = cls._excerpt(index_text, 65536)
            output.append(_Segment(index, start, end, start_line, max(start_line, end_line), raw, _sha256(raw), kind, salience, critical, index_text))
        return output

    @staticmethod
    def _entropy(data: bytes) -> float:
        if not data:
            return 0.0
        counts = Counter(data)
        total = len(data)
        return -sum((count / total) * math.log2(count / total) for count in counts.values())

    @classmethod
    def _facets(cls, family: str, data: bytes, segments: Sequence[_Segment], path: str) -> dict[str, Any]:
        if family == "binary":
            return {"bytes": len(data), "entropy_bits_per_byte": round(cls._entropy(data[:262144]), 4), "zero_bytes": data.count(b"\x00"), "suffix": Path(path).suffix.casefold()}
        text = data.decode("utf-8", errors="replace")
        lines = text.splitlines()
        base: dict[str, Any] = {
            "lines": len(lines),
            "critical_segments": sum(segment.critical for segment in segments),
            "warning_lines": sum(bool(_WARNING.search(line)) for line in lines),
            "location_lines": sum(bool(_LOCATION.search(line)) for line in lines),
        }
        if family == "diff":
            files = [line.split(" b/", 1)[-1] for line in lines if line.startswith("diff --git ")]
            base.update({"files": len(files), "file_sample": files[:24], "additions": sum(line.startswith("+") and not line.startswith("+++") for line in lines), "deletions": sum(line.startswith("-") and not line.startswith("---") for line in lines)})
        elif family == "test-output":
            summary = next((line.strip() for line in reversed(lines) if re.search(r"\d+\s+(?:passed|failed|errors?|skipped)", line, re.I)), "")
            base.update({"summary": summary, "failures": sum(bool(_ERROR.search(line)) for line in lines)})
        elif family == "json":
            try:
                value = json.loads(text)
                if isinstance(value, dict):
                    base.update({"root_type": "object", "root_keys": sorted(map(str, value.keys()))[:64], "root_size": len(value)})
                elif isinstance(value, list):
                    keys: Counter[str] = Counter()
                    for item in value[:1000]:
                        if isinstance(item, dict):
                            keys.update(map(str, item.keys()))
                    base.update({"root_type": "array", "root_size": len(value), "common_keys": [key for key, _ in keys.most_common(32)]})
            except json.JSONDecodeError:
                base.update({"root_type": "invalid-json"})
        elif family == "table":
            try:
                dialect = csv.excel_tab if Path(path).suffix.casefold() == ".tsv" else csv.excel
                rows = list(csv.reader(io.StringIO(text), dialect=dialect))
                base.update({"rows": max(0, len(rows) - 1), "columns": len(rows[0]) if rows else 0, "headers": rows[0][:64] if rows else []})
            except csv.Error:
                base.update({"rows": 0, "columns": 0, "headers": []})
        elif family == "log":
            severities = Counter("error" if _ERROR.search(line) else "warning" if _WARNING.search(line) else "info" for line in lines)
            shapes = Counter(re.sub(r"\b(?:0x[0-9a-f]+|\d+(?:\.\d+)?)\b", "<n>", cls._redact(line), flags=re.I) for line in lines if line.strip())
            base.update({"severities": dict(severities), "unique_shapes": len(shapes), "top_shapes": shapes.most_common(12)})
        elif family == "code":
            symbols = [line.strip() for line in lines if re.match(r"^\s*(?:class |def |async def |function |export |pub fn |fn |interface |struct |enum |impl )", line)]
            base.update({"symbols": len(symbols), "symbol_sample": symbols[:40], "todos": sum("TODO" in line or "FIXME" in line for line in lines)})
        elif family == "search-list":
            groups = Counter(line.split(":", 1)[0] if ":" in line else str(Path(line).parent) for line in lines if line.strip())
            base.update({"results": sum(groups.values()), "groups": len(groups), "top_groups": groups.most_common(24)})
        return base

    @classmethod
    def _summary(cls, family: str, data: bytes, facets: Mapping[str, Any], segments: Sequence[_Segment]) -> str:
        if family == "binary":
            head = data[:64].hex(" ")
            return f"Binary bytes={len(data)} entropy={facets.get('entropy_bits_per_byte')} zero_bytes={facets.get('zero_bytes')}\nHex head: {head}"
        lines = data.decode("utf-8", errors="replace").splitlines()
        critical = [cls._excerpt(segment.index_text.strip()) for segment in sorted(segments, key=lambda item: (-item.salience, item.index)) if segment.critical][:24]
        if family == "diff":
            body = [f"Diff files={facets.get('files', 0)} additions={facets.get('additions', 0)} deletions={facets.get('deletions', 0)}"]
            body.extend(f"- {path}" for path in facets.get("file_sample", [])[:20])
        elif family == "test-output":
            body = [f"Tests: {facets.get('summary') or 'summary unavailable'}", f"Failure signals={facets.get('failures', 0)}"]
        elif family == "json":
            body = [f"JSON root={facets.get('root_type')} size={facets.get('root_size', 0)}", "Keys: " + ", ".join(facets.get("root_keys", facets.get("common_keys", []))[:32])]
        elif family == "table":
            body = [f"Table rows={facets.get('rows', 0)} columns={facets.get('columns', 0)}", "Headers: " + " | ".join(facets.get("headers", []))]
        elif family == "log":
            body = [f"Log lines={facets.get('lines', 0)} unique_shapes={facets.get('unique_shapes', 0)} severities={facets.get('severities', {})}"]
            body.extend(f"[{count}x] {cls._excerpt(shape)}" for shape, count in facets.get("top_shapes", [])[:10])
        elif family == "code":
            body = [f"Code lines={facets.get('lines', 0)} symbols={facets.get('symbols', 0)} TODOs={facets.get('todos', 0)}"]
            body.extend(facets.get("symbol_sample", [])[:32])
        elif family == "search-list":
            body = [f"Search results={facets.get('results', 0)} groups={facets.get('groups', 0)}"]
            body.extend(f"[{count}] {group}" for group, count in facets.get("top_groups", [])[:24])
        elif family == "git-status":
            body = [line.strip() for line in lines if line.strip().startswith(("On branch ", "Your branch ", "modified:", "new file:", "deleted:", "Untracked files:", "Changes to be committed:", "Changes not staged"))]
        elif family == "git-log":
            body = [line.strip() for line in lines if re.match(r"^[0-9a-f]{7,40}\s", line) or line.startswith("    ")][:80]
        else:
            unique = list(dict.fromkeys(cls._excerpt(line.strip()) for line in lines if line.strip()))
            body = unique[:24] + (unique[-8:] if len(unique) > 24 else [])
        if critical:
            body = ["Critical evidence:", *critical, *body]
        return "\n".join(body)
