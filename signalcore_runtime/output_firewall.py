from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .evidence import EvidenceStore


ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
SECRET_RE = re.compile(
    r"(?i)\b(api[_-]?key|access[_-]?token|authorization|password|secret)\b\s*[:=]\s*([^\s,;]+)"
)
ERROR_RE = re.compile(r"(?i)(error(?:\[[^]]+\])?|failed|failure|panic|assertion|traceback|exception|fatal)")
LOCATION_RE = re.compile(r"(?:[A-Za-z]:)?[^\s:]+\.(?:py|rs|ts|tsx|js|jsx|c|cc|cpp|h|hpp|go|java|cs|rb|php):\d+(?::\d+)?")
SUMMARY_RE = re.compile(r"(?i)(test result:|tests? (?:passed|failed)|\d+ passed|\d+ failed|finished in|build (?:succeeded|failed))")


@dataclass(frozen=True)
class FirewallResult:
    kind: str
    summary: str
    evidence_handle: str
    raw_bytes: int
    visible_bytes: int
    critical_markers: tuple[str, ...]


def _clean(text: str) -> str:
    text = ANSI_RE.sub("", text).replace("\r\n", "\n").replace("\r", "\n")
    text = SECRET_RE.sub(lambda m: f"{m.group(1)}=<redacted>", text)
    lines: list[str] = []
    blank = False
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line:
            if not blank:
                lines.append("")
            blank = True
            continue
        blank = False
        lines.append(line)
    return "\n".join(lines).strip()


def classify(command: Iterable[str], text: str, exit_code: int) -> str:
    argv = " ".join(command).lower()
    if "cargo" in argv and any(word in argv for word in ("test", "check", "build", "clippy")):
        return "cargo"
    if any(word in argv for word in ("pytest", "unittest")):
        return "python-test"
    if any(word in argv for word in ("npm test", "pnpm test", "yarn test", "jest", "vitest")):
        return "javascript-test"
    if argv.startswith("git diff"):
        return "git-diff"
    if any(tool in argv.split()[:1] for tool in ("rg", "grep")):
        return "search"
    if text.lstrip().startswith(("{", "[")):
        try:
            json.loads(text)
            return "json"
        except Exception:
            pass
    return "success" if exit_code == 0 else "failure"


def _critical_lines(lines: list[str], exit_code: int) -> list[str]:
    selected: list[str] = []
    for line in lines:
        if ERROR_RE.search(line) or LOCATION_RE.search(line) or SUMMARY_RE.search(line):
            selected.append(line)
    if exit_code != 0 and not selected:
        selected.extend(lines[:20])
    return selected


def _bounded_unique(lines: Iterable[str], *, limit: int) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for line in lines:
        key = line.strip()
        if not key or key in seen:
            continue
        seen.add(key)
        output.append(line)
        if len(output) >= limit:
            break
    return output


def summarize(
    command: Iterable[str],
    *,
    stdout_path: Path,
    stderr_path: Path,
    exit_code: int,
    duration_seconds: float,
    evidence: EvidenceStore,
    success_limit_bytes: int = 4096,
    failure_limit_bytes: int = 16384,
) -> FirewallResult:
    stdout = stdout_path.read_bytes() if stdout_path.is_file() else b""
    stderr = stderr_path.read_bytes() if stderr_path.is_file() else b""
    combined = stdout + (b"\n" if stdout and stderr else b"") + stderr
    handle = evidence.put(
        combined,
        kind="command-output",
        metadata={"command": list(command), "exit_code": exit_code, "duration_seconds": duration_seconds},
    )
    text = _clean(combined.decode("utf-8", errors="replace"))
    lines = text.splitlines()
    kind = classify(command, text, exit_code)
    critical = _bounded_unique(_critical_lines(lines, exit_code), limit=30)

    if exit_code == 0:
        useful = critical or _bounded_unique(lines[-30:], limit=20)
        budget = success_limit_bytes
    else:
        useful = critical + _bounded_unique(lines[:15], limit=15)
        useful = _bounded_unique(useful, limit=45)
        budget = failure_limit_bytes

    header = [
        f"Command: {' '.join(command)}",
        f"Exit code: {exit_code}",
        f"Duration: {duration_seconds:.3f} seconds",
        f"Parser: {kind}",
        f"Full log: {handle}",
    ]
    summary = "\n".join(header + (["Excerpt:"] + useful if useful else []))
    encoded = summary.encode("utf-8")
    if len(encoded) > budget:
        encoded = encoded[:budget]
        summary = encoded.decode("utf-8", errors="ignore").rstrip() + f"\n[truncated; full log: {handle}]"
    return FirewallResult(kind, summary, handle, len(combined), len(summary.encode("utf-8")), tuple(critical))


def validate_critical_invariant(raw: bytes, result: FirewallResult) -> bool:
    text = _clean(raw.decode("utf-8", errors="replace"))
    markers = _critical_lines(text.splitlines(), 1)
    if not markers:
        return bool(result.evidence_handle)
    return all(marker in result.summary or bool(result.evidence_handle) for marker in markers)
