from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass
from typing import Any, Mapping, Sequence

from .util import canonical_json, sha256_bytes


@dataclass(frozen=True)
class RedactionMatch:
    kind: str
    start: int
    end: int
    fingerprint: str


_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("openai-key", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b")),
    ("anthropic-key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}\b")),
    ("github-token", re.compile(r"\b(?:ghp|github_pat|gho|ghu|ghs)_[A-Za-z0-9_]{20,}\b")),
    ("aws-access-key", re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),
    ("google-api-key", re.compile(r"\bAIza[0-9A-Za-z_-]{30,}\b")),
    ("slack-token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")),
    ("private-key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----[\s\S]*?-----END (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("authorization", re.compile(r"(?i)\b(?:authorization|api[-_ ]?key|token|secret|password)\s*[:=]\s*[\"']?([^\s\"']{8,})")),
    ("connection-uri", re.compile(r"\b(?:postgres|postgresql|mysql|mongodb(?:\+srv)?|redis)://[^\s:@]+:[^\s@]+@[^\s]+")),
)


class SecretRedactor:
    replacement = "<redacted:{kind}:{fingerprint}>"

    @staticmethod
    def _entropy(value: str) -> float:
        if not value:
            return 0.0
        counts = {ch: value.count(ch) for ch in set(value)}
        return -sum((count / len(value)) * math.log2(count / len(value)) for count in counts.values())

    def redact_text(self, text: str) -> tuple[str, list[RedactionMatch]]:
        matches: list[tuple[int, int, str, str]] = []
        for kind, pattern in _PATTERNS:
            for match in pattern.finditer(text):
                value = match.group(0)
                matches.append((match.start(), match.end(), kind, sha256_bytes(value.encode("utf-8"))[:12]))
        # Catch high-entropy bearer-like strings only when they are long enough to avoid source-code false positives.
        for match in re.finditer(r"\b[A-Za-z0-9_+/=-]{32,}\b", text):
            value = match.group(0)
            if self._entropy(value) >= 4.3 and not any(start <= match.start() < end for start, end, _, _ in matches):
                matches.append((match.start(), match.end(), "high-entropy-secret", sha256_bytes(value.encode("utf-8"))[:12]))
        matches.sort(key=lambda item: (item[0], -(item[1] - item[0])))
        selected: list[tuple[int, int, str, str]] = []
        cursor = -1
        for item in matches:
            if item[0] >= cursor:
                selected.append(item)
                cursor = item[1]
        parts: list[str] = []
        cursor = 0
        records: list[RedactionMatch] = []
        for start, end, kind, fingerprint in selected:
            parts.append(text[cursor:start])
            parts.append(self.replacement.format(kind=kind, fingerprint=fingerprint))
            records.append(RedactionMatch(kind, start, end, fingerprint))
            cursor = end
        parts.append(text[cursor:])
        return "".join(parts), records

    def redact(self, value: Any) -> tuple[Any, dict[str, Any]]:
        records: list[RedactionMatch] = []

        def visit(item: Any) -> Any:
            if isinstance(item, str):
                rendered, found = self.redact_text(item)
                records.extend(found)
                return rendered
            if isinstance(item, Mapping):
                return {str(key): visit(child) for key, child in item.items()}
            if isinstance(item, tuple):
                return tuple(visit(child) for child in item)
            if isinstance(item, Sequence) and not isinstance(item, (bytes, bytearray)):
                return [visit(child) for child in item]
            return item

        redacted = visit(value)
        receipt = {
            "redacted": bool(records),
            "count": len(records),
            "types": sorted({record.kind for record in records}),
            "fingerprints": sorted({record.fingerprint for record in records}),
            "original_hash": sha256_bytes(canonical_json(value)),
            "redacted_hash": sha256_bytes(canonical_json(redacted)),
        }
        return redacted, receipt
