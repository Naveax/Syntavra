from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

from .util import canonical_json, sha256_bytes


CONFIG_CANDIDATES = (
    "AGENTS.md", "CLAUDE.md", "GEMINI.md", ".cursorrules", ".clinerules",
    ".github/copilot-instructions.md", ".windsurfrules", ".continue/rules",
    ".cursor/rules", ".roo/rules", ".kilocode/rules", ".qwen/rules",
)
_PATH_RE = re.compile(r"(?<![\w.-])(?:\.?[\w.-]+/)+(?:[\w.-]+)(?![\w.-])")
_SYMBOL_RE = re.compile(r"`([A-Za-z_][A-Za-z0-9_.:]{2,})`")


@dataclass(frozen=True)
class ConfigFinding:
    path: str
    severity: str
    kind: str
    message: str
    line: int | None = None
    estimated_tokens: int = 0


class AgentConfigAuditor:
    def __init__(self, project: Path):
        self.project = Path(project).resolve(strict=True)

    def discover(self) -> list[Path]:
        found: set[Path] = set()
        for candidate in CONFIG_CANDIDATES:
            path = self.project / candidate
            if path.is_file():
                found.add(path)
            elif path.is_dir():
                found.update(item for item in path.rglob("*") if item.is_file())
        return sorted(found)

    @staticmethod
    def _normalize_line(line: str) -> str:
        return re.sub(r"\s+", " ", re.sub(r"^\s*(?:[-*#>]+|\d+[.)])\s*", "", line)).strip().casefold()

    def audit(self, *, files: Iterable[Path] | None = None) -> dict[str, object]:
        paths = list(files or self.discover())
        findings: list[ConfigFinding] = []
        normalized_seen: dict[str, tuple[str, int]] = {}
        total_bytes = 0
        total_tokens = 0
        for path in paths:
            relative = path.relative_to(self.project).as_posix()
            text = path.read_text(encoding="utf-8", errors="replace")
            total_bytes += len(text.encode("utf-8"))
            tokens = max(1, len(text.encode("utf-8")) // 4)
            total_tokens += tokens
            if tokens > 2_000:
                findings.append(ConfigFinding(relative, "warning", "oversized-config", f"configuration is approximately {tokens} tokens", estimated_tokens=tokens))
            for number, line in enumerate(text.splitlines(), 1):
                normalized = self._normalize_line(line)
                if len(normalized) >= 24:
                    previous = normalized_seen.get(normalized)
                    if previous:
                        findings.append(ConfigFinding(relative, "warning", "duplicate-instruction", f"duplicates {previous[0]}:{previous[1]}", number, max(1, len(line) // 4)))
                    else:
                        normalized_seen[normalized] = (relative, number)
                for match in _PATH_RE.finditer(line):
                    raw = match.group(0).rstrip(".,:;)")
                    target = (self.project / raw).resolve(strict=False)
                    try:
                        target.relative_to(self.project)
                    except ValueError:
                        continue
                    if not target.exists() and not any(char in raw for char in "*{}[]"):
                        findings.append(ConfigFinding(relative, "error", "stale-path", f"referenced path does not exist: {raw}", number))
                if re.search(r"(?i)\b(always|never|must)\b.*\b(always|never|must)\b", line) and len(line) > 240:
                    findings.append(ConfigFinding(relative, "warning", "overloaded-rule", "one rule combines multiple absolute constraints", number, max(1, len(line) // 4)))
            if "ignore previous" in text.casefold() or "disregard all" in text.casefold():
                findings.append(ConfigFinding(relative, "error", "instruction-injection", "configuration contains an instruction-override phrase"))
        estimated_waste = sum(item.estimated_tokens for item in findings if item.kind in {"duplicate-instruction", "oversized-config", "overloaded-rule"})
        body: dict[str, object] = {
            "files": [path.relative_to(self.project).as_posix() for path in paths],
            "file_count": len(paths),
            "bytes": total_bytes,
            "estimated_tokens": total_tokens,
            "estimated_reclaimable_tokens": estimated_waste,
            "findings": [asdict(item) for item in findings],
            "counts": {severity: sum(1 for item in findings if item.severity == severity) for severity in ("error", "warning", "info")},
        }
        body["audit_hash"] = sha256_bytes(canonical_json(body))
        return body
