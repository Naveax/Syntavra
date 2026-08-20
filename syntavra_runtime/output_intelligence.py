from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Iterable

from .artifacts import ArtifactStore
from .command_rewriter import CommandRewriteEngine, RewriteResult
from .competitive_fabric import SafeCommandRouter
from .security_scan import scan_text
from .terminal_engine import TerminalOutputEngine
from .util import canonical_json, sha256_bytes


_ERROR_RE = re.compile(
    r"(?i)\b(error|failed|failure|panic|assert(?:ion)?|traceback|exception|fatal|"
    r"denied|forbidden|unauthorized|timeout|segmentation fault|not found|permission denied)\b"
)
_TEST_STATUS_RE = re.compile(
    r"(?i)(?:\b\d+\s+(?:passed|failed|errors?|skipped|xfailed|xpassed)\b|"
    r"test result:\s*[^\n]+|tests?:\s*\d+|failures?:\s*\d+|successes?:\s*\d+)"
)
_PATH_RE = re.compile(
    r"(?:(?:[A-Za-z]:)?[^\s:()]+\.(?:py|rs|js|jsx|ts|tsx|java|cs|go|rb|php|lua|luau|cpp|cc|c|h|hpp)"
    r"(?::\d+(?::\d+)?|\(\d+(?:,\d+)?\)))"
)
_PERMISSION_RE = re.compile(r"(?i)\b(permission denied|access denied|forbidden|unauthorized|denied)\b")
_NEGATION_RE = re.compile(r"(?i)\b(must not|cannot|can't|never|not|no)\b")
_CONSTRAINT_RE = re.compile(
    r"(?i)\b(must|required|only|exact|at least|at most|maximum|minimum|max|min|limit|bounded)\b"
)
_ERROR_ENTITY_RE = re.compile(
    r"\b(?:AssertionError|[A-Za-z_][A-Za-z0-9_]*(?:Error|Exception)|"
    r"SIG(?:SEGV|ABRT|KILL|TERM)|E[A-Z0-9_]{2,})\b"
)
_NUMBER_RE = re.compile(r"(?<![A-Za-z_])[-+]?\d+(?:\.\d+)?(?:%|ms|s|MiB|GiB|MB|GB)?")
_SPACE_RE = re.compile(r"\s+")


class CompressionSafetyClass(str, Enum):
    EXACT_ONLY = "EXACT_ONLY"
    STRUCTURAL_SAFE = "STRUCTURAL_SAFE"
    SEMANTIC_SAFE = "SEMANTIC_SAFE"
    LOSSY_ALLOWED = "LOSSY_ALLOWED"


@dataclass(frozen=True, order=True)
class PreservationFact:
    category: str
    value: str

    @property
    def key(self) -> str:
        return f"{self.category}:{self.value}"


@dataclass(frozen=True)
class SemanticPreservationReport:
    ok: bool
    required_fact_count: int
    preserved_fact_count: int
    missing_facts: tuple[str, ...]
    receipt_hash: str


@dataclass(frozen=True)
class OutputIntelligenceResult:
    decision: str
    compression_safety: str
    artifact_id: str
    exact_recovery: bool
    semantic_preservation: bool
    no_worse: bool
    visible_text: str
    original_bytes: int
    visible_bytes: int
    savings_ratio: float
    requires_exact_reveal: bool
    missing_facts: tuple[str, ...]
    rewrite_rule: str | None
    rewrite_changed: bool
    receipt_hash: str


class SemanticPreservationVerifier:
    """Verify that bounded views retain critical machine-actionable output facts."""

    schema_version = 1

    @staticmethod
    def _norm(value: str) -> str:
        return _SPACE_RE.sub(" ", value.strip()).casefold()

    def facts(self, text: str) -> tuple[PreservationFact, ...]:
        redacted = scan_text(text).redacted_text
        facts: set[PreservationFact] = set()
        for raw in redacted.splitlines():
            line = raw.strip()
            if not line:
                continue
            critical = bool(
                _ERROR_RE.search(line)
                or _TEST_STATUS_RE.search(line)
                or _PATH_RE.search(line)
                or _PERMISSION_RE.search(line)
                or _NEGATION_RE.search(line)
                or _CONSTRAINT_RE.search(line)
                or _ERROR_ENTITY_RE.search(line)
            )
            for match in _TEST_STATUS_RE.finditer(line):
                facts.add(PreservationFact("test-status", self._norm(match.group(0))))
            for match in _PATH_RE.finditer(line):
                facts.add(PreservationFact("path", self._norm(match.group(0))))
            for match in _PERMISSION_RE.finditer(line):
                facts.add(PreservationFact("permission", self._norm(match.group(0))))
            for match in _NEGATION_RE.finditer(line):
                facts.add(PreservationFact("negation", self._norm(match.group(0))))
            for match in _CONSTRAINT_RE.finditer(line):
                facts.add(PreservationFact("constraint", self._norm(match.group(0))))
            for match in _ERROR_ENTITY_RE.finditer(line):
                facts.add(PreservationFact("error-entity", self._norm(match.group(0))))
            if _ERROR_RE.search(line):
                for match in _ERROR_RE.finditer(line):
                    facts.add(PreservationFact("error", self._norm(match.group(0))))
            if critical:
                for match in _NUMBER_RE.finditer(line):
                    facts.add(PreservationFact("number", self._norm(match.group(0))))
        return tuple(sorted(facts))

    def verify(self, source: str, candidate: str) -> SemanticPreservationReport:
        required = self.facts(source)
        observed = set(self.facts(candidate))
        missing = tuple(fact.key for fact in required if fact not in observed)
        preserved = len(required) - len(missing)
        body = {
            "schema_version": self.schema_version,
            "required": [fact.key for fact in required],
            "observed_required": [fact.key for fact in required if fact in observed],
            "missing": list(missing),
        }
        return SemanticPreservationReport(
            ok=not missing,
            required_fact_count=len(required),
            preserved_fact_count=preserved,
            missing_facts=missing,
            receipt_hash=sha256_bytes(canonical_json(body)),
        )


class OutputIntelligenceEngine:
    """Canonical output policy composed over existing exact-capture authorities."""

    schema_version = 1

    def __init__(self, store: ArtifactStore):
        self.store = store
        self.terminal = TerminalOutputEngine(store)
        self.rewriter = CommandRewriteEngine()
        self.verifier = SemanticPreservationVerifier()
        self.router = SafeCommandRouter()

    @classmethod
    def status(cls) -> dict[str, object]:
        return {
            "exact_output_store_reused": True,
            "terminal_output_engine_reused": True,
            "command_compactor_registry_reused": True,
            "command_rewriter_reused": True,
            "semantic_preservation_verifier": True,
            "compression_safety_classes": True,
            "no_worse_guard": True,
            "bounded_visible_output": True,
            "fail_closed_on_verification_failure": True,
            "content_addressed_receipt": True,
            "parallel_persistent_store": False,
            "public_cli_route": False,
        }

    def plan_command_rewrite(self, command: str | Iterable[str]) -> RewriteResult:
        return self.rewriter.rewrite(command)

    @staticmethod
    def _bounded_marker(text: str, budget_bytes: int) -> str:
        raw = text.encode("utf-8")
        if len(raw) <= budget_bytes:
            return text
        marker = "\n[… exact reveal required …]"
        keep = max(0, budget_bytes - len(marker.encode("utf-8")))
        return raw[:keep].decode("utf-8", errors="ignore").rstrip() + marker

    def _safety_class(self, command: str | Iterable[str], report: SemanticPreservationReport) -> CompressionSafetyClass:
        if not report.ok:
            return CompressionSafetyClass.EXACT_ONLY
        family = self.router.route(command).family
        if family in {"test", "build", "git", "search", "read"}:
            return CompressionSafetyClass.STRUCTURAL_SAFE
        if report.required_fact_count:
            return CompressionSafetyClass.SEMANTIC_SAFE
        return CompressionSafetyClass.LOSSY_ALLOWED

    def process(
        self,
        command: str | Iterable[str],
        stdout: bytes | str,
        stderr: bytes | str = b"",
        *,
        exit_code: int = 0,
        duration_ms: float = 0.0,
        budget_bytes: int = 4096,
    ) -> OutputIntelligenceResult:
        if budget_bytes < 256:
            raise ValueError("budget_bytes must be at least 256")

        command_text = command if isinstance(command, str) else " ".join(str(item) for item in command)
        stdout_bytes = stdout.encode("utf-8") if isinstance(stdout, str) else bytes(stdout)
        stderr_bytes = stderr.encode("utf-8") if isinstance(stderr, str) else bytes(stderr)
        source = stdout_bytes.decode("utf-8", errors="replace")
        if stderr_bytes:
            source += ("\n[stderr]\n" if source else "[stderr]\n") + stderr_bytes.decode("utf-8", errors="replace")
        sanitized_raw = scan_text(source).redacted_text
        raw_visible_bytes = len(sanitized_raw.encode("utf-8"))

        session = self.terminal.open(
            tool=self.router.route(command).family,
            command=command_text,
            exit_code=exit_code,
            duration_ms=duration_ms,
            preview_budget_bytes=budget_bytes,
        )
        try:
            if stdout_bytes:
                session.feed(stdout_bytes, stream="stdout")
            if stderr_bytes:
                session.feed(stderr_bytes, stream="stderr")
            capture = session.finalize()
        except Exception:
            session.abort()
            raise

        if not capture.exact_recovery:
            raise RuntimeError("exact output artifact verification failed")

        candidate = capture.compact_view
        report = self.verifier.verify(sanitized_raw, candidate)
        candidate_bytes = len(candidate.encode("utf-8"))

        if raw_visible_bytes <= budget_bytes and (not report.ok or candidate_bytes >= raw_visible_bytes):
            decision = "PASSTHROUGH"
            visible = sanitized_raw
            report = self.verifier.verify(sanitized_raw, visible)
            requires_exact = False
        elif report.ok:
            decision = "COMPACT"
            visible = candidate
            requires_exact = False
        else:
            decision = "EXACT_REQUIRED"
            visible = self._bounded_marker(
                "\n".join(
                    (
                        "Syntavra output decision=EXACT_REQUIRED",
                        f"Exact output: artifact://{capture.artifact_id}",
                        f"Missing critical facts: {len(report.missing_facts)}",
                    )
                ),
                budget_bytes,
            )
            requires_exact = True

        visible_bytes = len(visible.encode("utf-8"))
        no_worse = visible_bytes <= raw_visible_bytes if raw_visible_bytes else visible_bytes == 0
        if not no_worse:
            visible = sanitized_raw
            visible_bytes = raw_visible_bytes
            decision = "PASSTHROUGH"
            requires_exact = False
            report = self.verifier.verify(sanitized_raw, visible)
            no_worse = True

        safety = self._safety_class(command, report)
        if decision == "EXACT_REQUIRED":
            safety = CompressionSafetyClass.EXACT_ONLY

        rewrite = self.plan_command_rewrite(command)
        original_bytes = len(stdout_bytes) + len(stderr_bytes)
        savings_ratio = 0.0 if original_bytes == 0 else max(0.0, 1.0 - visible_bytes / original_bytes)
        body = {
            "schema_version": self.schema_version,
            "decision": decision,
            "compression_safety": safety.value,
            "artifact_id": capture.artifact_id,
            "exact_recovery": capture.exact_recovery,
            "semantic_preservation": report.ok,
            "semantic_receipt_hash": report.receipt_hash,
            "missing_facts": list(report.missing_facts),
            "no_worse": no_worse,
            "original_bytes": original_bytes,
            "visible_bytes": visible_bytes,
            "requires_exact_reveal": requires_exact,
            "command": command_text,
            "exit_code": int(exit_code),
            "rewrite_rule": rewrite.rule,
            "rewrite_changed": rewrite.changed,
        }
        return OutputIntelligenceResult(
            decision=decision,
            compression_safety=safety.value,
            artifact_id=capture.artifact_id,
            exact_recovery=capture.exact_recovery,
            semantic_preservation=report.ok,
            no_worse=no_worse,
            visible_text=visible,
            original_bytes=original_bytes,
            visible_bytes=visible_bytes,
            savings_ratio=savings_ratio,
            requires_exact_reveal=requires_exact,
            missing_facts=report.missing_facts,
            rewrite_rule=rewrite.rule,
            rewrite_changed=rewrite.changed,
            receipt_hash=sha256_bytes(canonical_json(body)),
        )


__all__ = [
    "CompressionSafetyClass",
    "OutputIntelligenceEngine",
    "OutputIntelligenceResult",
    "PreservationFact",
    "SemanticPreservationReport",
    "SemanticPreservationVerifier",
]
