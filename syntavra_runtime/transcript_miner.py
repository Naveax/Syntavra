from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from .command_compactors import CommandCompactorRegistry
from .command_rewriter import CommandRewriteEngine
from .util import canonical_json, sha256_bytes


@dataclass(frozen=True)
class TranscriptOpportunity:
    index: int
    command: str
    kind: str
    estimated_input_tokens: int
    estimated_saved_tokens: int
    recommendation: str
    rule: str | None = None
    compactor: str | None = None


class TranscriptOpportunityMiner:
    def __init__(self) -> None:
        self.rewriter = CommandRewriteEngine()
        self.compactors = CommandCompactorRegistry()

    @staticmethod
    def _events(value: Any) -> list[Mapping[str, Any]]:
        if isinstance(value, list):
            return [row for row in value if isinstance(row, Mapping)]
        if isinstance(value, Mapping):
            rows = value.get("events") or value.get("messages") or value.get("transcript") or []
            return [row for row in rows if isinstance(row, Mapping)] if isinstance(rows, list) else []
        return []

    @classmethod
    def load(cls, source: Path | str | Iterable[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
        if not isinstance(source, (str, Path)):
            return list(source)
        path = Path(source)
        text = path.read_text(encoding="utf-8") if path.is_file() else str(source)
        try:
            return cls._events(json.loads(text))
        except json.JSONDecodeError:
            rows: list[Mapping[str, Any]] = []
            for line in text.splitlines():
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(item, Mapping):
                    rows.append(item)
            return rows

    @staticmethod
    def _command(row: Mapping[str, Any]) -> str:
        for key in ("command", "cmd", "shell_command", "input"):
            if isinstance(row.get(key), str):
                return str(row[key])
        tool_input = row.get("tool_input") or row.get("arguments")
        if isinstance(tool_input, Mapping):
            for key in ("command", "cmd"):
                if isinstance(tool_input.get(key), str):
                    return str(tool_input[key])
        return ""

    @staticmethod
    def _output(row: Mapping[str, Any]) -> str:
        for key in ("output", "stdout", "result", "content"):
            value = row.get(key)
            if isinstance(value, str):
                return value
        return ""

    def analyze(self, source: Path | str | Iterable[Mapping[str, Any]]) -> dict[str, Any]:
        events = self.load(source)
        opportunities: list[TranscriptOpportunity] = []
        for index, row in enumerate(events):
            command = self._command(row)
            if not command:
                continue
            output = self._output(row)
            input_tokens = max(1, (len(command.encode("utf-8")) + len(output.encode("utf-8"))) // 4)
            rewrite = self.rewriter.rewrite(command)
            if rewrite.changed:
                opportunities.append(TranscriptOpportunity(index, command, "pre-tool-rewrite", input_tokens, max(8, len(output) // 20), "rewrite before execution", rewrite.rule))
            lines = output.splitlines()
            compactor, selected, _ = self.compactors.select(command, lines)
            if compactor and lines:
                visible = len("\n".join(selected).encode("utf-8"))
                original = len(output.encode("utf-8"))
                saved = max(0, (original - visible) // 4)
                if saved > 0:
                    opportunities.append(TranscriptOpportunity(index, command, "post-tool-compaction", input_tokens, saved, "capture exact output and return compact view", compactor=compactor))
        total = sum(item.estimated_saved_tokens for item in opportunities)
        body = {
            "events": len(events),
            "commands": sum(1 for row in events if self._command(row)),
            "opportunities": [asdict(item) for item in opportunities],
            "estimated_saved_tokens": total,
            "coverage": {
                "rewrite_rules": self.rewriter.manifest()["count"],
                "compactors": self.compactors.manifest()["count"],
            },
        }
        body["analysis_hash"] = sha256_bytes(canonical_json(body))
        return body
