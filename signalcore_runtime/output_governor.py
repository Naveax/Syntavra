from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from typing import Any, Iterable


PATH_RE = re.compile(r"(?:[A-Za-z]:)?[^\s:]+\.(?:py|rs|ts|tsx|js|jsx|c|cc|cpp|h|hpp|go|java|cs|rb|php|lua|luau):\d+(?::\d+)?")
CRITICAL_RE = re.compile(r"(?i)\b(error|failed|failure|warning|security|unsafe|blocked|not proven|limitation|regression|denied|exception|traceback)\b")
CODE_RE = re.compile(
    r"^(?:\s*(?:[+\-]{1,3}|@@)|\s*(?:def|class|fn|func|function|const|let|var|import|from|"
    r"return|raise|throw|if|for|while|match|case|SELECT|INSERT|UPDATE|DELETE)\b|\s*[A-Za-z0-9_.-]+\s*[=:]\s*[^,]+$)"
)
COMMAND_RE = re.compile(r"^\s*(?:\$|>|PS>|python\b|pytest\b|git\b|npm\b|pnpm\b|cargo\b|go\b|dotnet\b)")
STATUS_RE = re.compile(r"(?i)\b(?:pass(?:ed)?|success|ok|fail(?:ed)?|blocked|skipped|timeout|exit(?:ed)?\s+\d+)\b")
FILLER_RE = re.compile(
    r"(?i)^(?:sure[,!.]?|of course[,!.]?|absolutely[,!.]?|here(?:'s| is)|i(?:'ll| will) (?:now )?|"
    r"as requested[,!.]?|hope this helps[,!.]?|let me know if).*$"
)


@dataclass(frozen=True)
class OutputProfile:
    name: str
    max_bytes: int
    max_sections: int
    max_items_per_section: int
    include_evidence: bool
    include_details: bool


PROFILES = {
    "terse": OutputProfile("terse", 1400, 4, 4, True, False),
    "compact": OutputProfile("compact", 4096, 5, 6, True, False),
    "balanced": OutputProfile("balanced", 12_000, 8, 12, True, True),
    "detailed": OutputProfile("detailed", 32_000, 16, 30, True, True),
    "audit": OutputProfile("audit", 96_000, 64, 100, True, True),
}


CONTRACTS: dict[str, tuple[str, ...]] = {
    "implementation": ("result", "changed_files", "behavior", "verification", "limitations", "evidence"),
    "failure": ("root_cause", "location", "affected_boundary", "next_action", "evidence"),
    "audit": ("claim", "status", "supporting_evidence", "contradicting_evidence", "confidence", "uncertainty"),
    "benchmark": ("status", "workload", "quality", "efficiency", "statistics", "limitations", "evidence"),
    "generic": ("result", "details", "limitations", "evidence"),
}


class OutputGovernor:
    def __init__(self, profile: str = "balanced"):
        if profile not in PROFILES:
            raise ValueError(f"unknown output profile: {profile}")
        self.profile = PROFILES[profile]

    @staticmethod
    def _values(value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            return [value]
        if isinstance(value, dict):
            return [f"{key}: {item}" for key, item in value.items()]
        if isinstance(value, (list, tuple, set)):
            return [str(item) for item in value]
        return [str(value)]

    @staticmethod
    def _dedupe(lines: Iterable[str]) -> list[str]:
        seen: set[str] = set()
        result: list[str] = []
        for raw in lines:
            line = raw.rstrip()
            stripped = line.strip()
            key = re.sub(r"\s+", " ", stripped).casefold()
            if not stripped or key in seen or FILLER_RE.match(stripped):
                continue
            seen.add(key)
            result.append(line)
        return result

    @staticmethod
    def _priority(line: str, index: int) -> tuple[int, int]:
        score = 0
        if CRITICAL_RE.search(line):
            score += 100
        if PATH_RE.search(line):
            score += 90
        if STATUS_RE.search(line):
            score += 55
        if CODE_RE.search(line):
            score += 45
        if COMMAND_RE.search(line):
            score += 40
        return score, -index

    def render(self, payload: dict[str, Any], *, contract: str = "generic") -> dict[str, Any]:
        fields = CONTRACTS.get(contract, CONTRACTS["generic"])
        sections: list[tuple[str, list[str]]] = []
        missing: list[str] = []
        for field in fields:
            values = self._dedupe(self._values(payload.get(field)))
            if not values:
                if field in {"result", "status", "root_cause", "claim", "verification"}:
                    missing.append(field)
                continue
            if not self.profile.include_details and field in {
                "details", "behavior", "supporting_evidence", "contradicting_evidence"
            }:
                values = values[:1 if self.profile.name == "terse" else 2]
            if field == "evidence" and not self.profile.include_evidence:
                continue
            sections.append((field, values[: self.profile.max_items_per_section]))
            if len(sections) >= self.profile.max_sections:
                break
        if missing:
            raise ValueError(f"answer contract missing required fields: {', '.join(missing)}")

        output: list[str] = []
        terse = self.profile.name == "terse"
        for index, (field, values) in enumerate(sections):
            title = field.replace("_", " ").title()
            if terse:
                if len(values) == 1:
                    output.append(f"{title}: {values[0]}")
                else:
                    output.append(f"{title}: " + " | ".join(values))
            elif len(values) == 1:
                output.append(f"{title}: {values[0]}")
            else:
                output.append(f"{title}:")
                output.extend(f"- {value}" for value in values)
            if not terse and index + 1 < len(sections):
                output.append("")
        text = self._truncate_preserving_critical("\n".join(output).strip())
        return {
            "profile": self.profile.name,
            "contract": contract,
            "text": text,
            "bytes": len(text.encode("utf-8")),
            "sections": [field for field, _ in sections],
            "preserved_paths": sorted(set(PATH_RE.findall(text))),
        }

    def compact_text(self, text: str) -> dict[str, Any]:
        lines = self._dedupe(text.splitlines())
        ranked = sorted(
            enumerate(lines),
            key=lambda item: self._priority(item[1], item[0]),
            reverse=True,
        )
        if self.profile.name == "terse":
            selected_indices = {
                index for index, _ in ranked[: max(8, self.profile.max_items_per_section * 3)]
            }
            selected = [line for index, line in enumerate(lines) if index in selected_indices]
        else:
            critical = [
                line for line in lines
                if CRITICAL_RE.search(line) or PATH_RE.search(line) or CODE_RE.search(line) or COMMAND_RE.search(line)
            ]
            normal = [line for line in lines if line not in critical]
            selected = list(dict.fromkeys([*critical, *normal]))
        output = self._truncate_preserving_critical("\n".join(selected))
        return {
            "profile": self.profile.name,
            "text": output,
            "bytes": len(output.encode("utf-8")),
            "removed_lines": max(0, len(text.splitlines()) - len(output.splitlines())),
            "preserved_paths": sorted(set(PATH_RE.findall(output))),
            "preserved_critical_lines": sum(1 for line in output.splitlines() if CRITICAL_RE.search(line)),
            "preserved_code_lines": sum(1 for line in output.splitlines() if CODE_RE.search(line)),
        }

    def _truncate_preserving_critical(self, text: str) -> str:
        encoded = text.encode("utf-8")
        if len(encoded) <= self.profile.max_bytes:
            return text
        lines = text.splitlines()
        ranked = sorted(
            enumerate(lines),
            key=lambda item: self._priority(item[1], item[0]),
            reverse=True,
        )
        suffix = "\n[bounded; exact evidence available]"
        budget = self.profile.max_bytes - len(suffix.encode("utf-8"))
        selected: list[tuple[int, str]] = []
        used = 0
        for index, line in ranked:
            line_bytes = len((line + "\n").encode("utf-8"))
            if line_bytes > budget - used:
                continue
            selected.append((index, line))
            used += line_bytes
            if used >= budget:
                break
        if not selected:
            return encoded[:budget].decode("utf-8", errors="ignore").rstrip() + suffix
        selected.sort(key=lambda item: item[0])
        body = "\n".join(line for _, line in selected)
        return (body + suffix).encode("utf-8")[: self.profile.max_bytes].decode("utf-8", errors="ignore")

    def describe(self) -> dict[str, Any]:
        return asdict(self.profile)


def govern_json(payload_text: str, *, profile: str, contract: str) -> str:
    payload = json.loads(payload_text)
    if not isinstance(payload, dict):
        raise ValueError("output governor payload must be an object")
    return json.dumps(OutputGovernor(profile).render(payload, contract=contract), ensure_ascii=False, sort_keys=True)
