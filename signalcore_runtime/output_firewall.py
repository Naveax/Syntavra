from __future__ import annotations

import json
import re
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator

from .evidence import EvidenceStore


ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
SECRET_RE = re.compile(
    r"(?i)\b(api[_-]?key|access[_-]?token|authorization|password|secret|bearer)\b\s*[:=]\s*([^\s,;]+)"
)
ERROR_RE = re.compile(r"(?i)\b(error(?:\[[^]]+\])?|failed|failure|panic|assertion|traceback|exception|fatal)\b")
LOCATION_RE = re.compile(r"(?:[A-Za-z]:)?[^\s:]+\.(?:py|rs|ts|tsx|js|jsx|c|cc|cpp|h|hpp|go|java|cs|rb|php):\d+(?::\d+)?")
FINAL_SUMMARY_RE = re.compile(
    r"(?i)(test result:|={2,}.*(?:passed|failed|error)|\b\d+\s+(?:passed|failed|errors?|skipped)\b|"
    r"tests?\s+(?:passed|failed)|build\s+(?:succeeded|failed)|finished\s+in\s+[0-9.]+)"
)
NOISY_SUCCESS_RE = re.compile(r"(?i)^\s*(?:ok\b|pass(?:ed)?\b|test\s+\S+\s+\.\.\.\s+ok\b)")


@dataclass(frozen=True)
class FirewallResult:
    kind: str
    summary: str
    evidence_handle: str
    raw_bytes: int
    visible_bytes: int
    critical_markers: tuple[str, ...]
    scanned_lines: int = 0
    dropped_lines: int = 0


def _clean_line(raw: bytes) -> str:
    text = raw.decode("utf-8", errors="replace").replace("\r", "").rstrip("\n")
    text = ANSI_RE.sub("", text).rstrip()
    return SECRET_RE.sub(lambda match: f"{match.group(1)}=<redacted>", text)


def _clean(text: str) -> str:
    return "\n".join(_clean_line(line + b"\n") for line in text.encode("utf-8", errors="replace").splitlines()).strip()


def _iter_bounded_lines(path: Path, *, max_line_bytes: int = 131072) -> Iterator[bytes]:
    """Yield bounded physical-line fragments without front-deleting a byte buffer."""
    if not path.is_file():
        return
    with path.open("rb") as handle:
        while raw := handle.readline(max_line_bytes):
            yield raw


def classify(command: Iterable[str], sample: str, exit_code: int) -> str:
    argv = " ".join(command).lower()
    if "cargo" in argv and any(word in argv for word in ("test", "check", "build", "clippy")):
        return "cargo"
    if any(word in argv for word in ("pytest", "unittest")):
        return "python-test"
    if any(word in argv for word in ("npm test", "pnpm test", "yarn test", "jest", "vitest")):
        return "javascript-test"
    if argv.startswith("git diff"):
        return "git-diff"
    first = argv.split()[:1]
    if first and first[0] in {"rg", "grep"}:
        return "search"
    if sample.lstrip().startswith(("{", "[")):
        try:
            json.loads(sample)
            return "json"
        except (json.JSONDecodeError, TypeError):
            pass
    return "success" if exit_code == 0 else "failure"


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
    command_tuple = tuple(str(value) for value in command)
    paths = (stdout_path, stderr_path)
    raw_bytes = sum(path.stat().st_size for path in paths if path.is_file())
    critical: list[str] = []
    final_summaries: deque[str] = deque(maxlen=20)
    first_lines: list[str] = []
    tail_raw: deque[bytes] = deque(maxlen=30)
    sample_raw = bytearray()
    scanned_lines = 0
    dropped_lines = 0

    def observe(raw: bytes) -> None:
        nonlocal scanned_lines, dropped_lines
        scanned_lines += 1
        stripped = raw.rstrip(b"\r\n")
        if not stripped:
            return
        if len(sample_raw) < 8192:
            remaining = 8192 - len(sample_raw)
            sample_raw.extend(raw[:remaining])
        if len(first_lines) < 15:
            first_lines.append(_clean_line(raw))
        tail_raw.append(raw)

        # The dominant successful-test path is rejected before UTF-8 decode,
        # ANSI/secret cleanup, and regex evaluation.
        if exit_code == 0 and (
            stripped.endswith((b" ... ok", b" ... OK"))
            or stripped in {b"ok", b"OK", b"pass", b"PASS", b"passed", b"PASSED"}
        ):
            dropped_lines += 1
            return

        line = _clean_line(raw)
        if not line:
            return
        is_error = bool(ERROR_RE.search(line) or LOCATION_RE.search(line))
        is_summary = bool(FINAL_SUMMARY_RE.search(line))
        if is_error and len(critical) < 40:
            critical.append(line)
        elif is_summary:
            final_summaries.append(line)
        elif NOISY_SUCCESS_RE.search(line) or scanned_lines > 45:
            dropped_lines += 1

    def evidence_chunks() -> Iterator[bytes]:
        emitted = False
        for path in paths:
            if not path.is_file():
                continue
            if emitted:
                yield b"\n"
            for raw in _iter_bounded_lines(path):
                observe(raw)
                yield raw
            emitted = True

    # Evidence persistence and semantic filtering share one bounded disk pass.
    handle = evidence.put_stream(
        evidence_chunks(),
        kind="command-output",
        metadata={"command": list(command_tuple), "exit_code": exit_code, "duration_seconds": duration_seconds},
    )

    sample = sample_raw.decode("utf-8", errors="replace").replace("\r", "")
    sample = SECRET_RE.sub(lambda match: f"{match.group(1)}=<redacted>", ANSI_RE.sub("", sample))
    kind = classify(command_tuple, sample, exit_code)
    critical = _bounded_unique(critical, limit=30)
    summaries = _bounded_unique(final_summaries, limit=20)

    if exit_code == 0:
        useful = summaries + critical
        if not useful:
            useful = _bounded_unique((_clean_line(raw) for raw in tail_raw), limit=20)
        budget = success_limit_bytes
    else:
        useful = critical + summaries + first_lines
        useful = _bounded_unique(useful, limit=45)
        budget = failure_limit_bytes

    header = [
        f"Command: {' '.join(command_tuple)}",
        f"Exit code: {exit_code}",
        f"Duration: {duration_seconds:.3f} seconds",
        f"Parser: {kind}",
        f"Scanned: {scanned_lines} lines / {raw_bytes} bytes",
        f"Suppressed: {dropped_lines} low-value lines",
        f"Full log: {handle}",
    ]
    summary = "\n".join(header + (["Excerpt:"] + useful if useful else []))
    encoded = summary.encode("utf-8")
    if len(encoded) > budget:
        suffix = f"\n[truncated; full log: {handle}]"
        keep = max(0, budget - len(suffix.encode("utf-8")))
        summary = encoded[:keep].decode("utf-8", errors="ignore").rstrip() + suffix
    return FirewallResult(
        kind,
        summary,
        handle,
        raw_bytes,
        len(summary.encode("utf-8")),
        tuple(critical),
        scanned_lines,
        dropped_lines,
    )


def validate_critical_invariant(raw: bytes, result: FirewallResult) -> bool:
    text = _clean(raw.decode("utf-8", errors="replace"))
    markers = [line for line in text.splitlines() if ERROR_RE.search(line) or LOCATION_RE.search(line)]
    if not markers:
        return bool(result.evidence_handle)
    return all(marker in result.summary or bool(result.evidence_handle) for marker in markers)
